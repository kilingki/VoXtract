"""Run explicitly selected jobs through one persistent ASR → FA worker."""

import asyncio
import errno
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from backend.app.integrations.inferswap import (
    MAX_RESPONSE_BYTES, SAMPLE_RATE, ContractError, EndpointUnavailable, InferSwap, UnconfirmedRequest,
    parse_asr, parse_fa,
)
from backend.app.services.audio import InvalidAudio, MediaToolUnavailable
from backend.app.services.preparation import content_hash, merge_items, prepare
from backend.app.storage.jobs import ACTIVE_STATES, JobStore

logger = logging.getLogger(__name__)
REJECTED_BEFORE_INFERENCE = {400, 404, 413, 422, 429}


class InferenceUnavailable(Exception):
    pass


class JobNotRunnable(Exception):
    pass


class JobNotHeld(Exception):
    pass


class InferenceFailed(Exception):
    pass


class Runner:
    def __init__(self, store: JobStore, lock: asyncio.Lock) -> None:
        self.store = store
        self.lock = lock
        self.wake = asyncio.Event()
        self.faulted = False
        origin = os.getenv("VOXTRACT_INFERSWAP_URL")
        self.settings = None
        self.timeout = 7200.0
        if origin:
            endpoint = InferSwap(
                origin,
                os.getenv("VOXTRACT_ASR_MODEL", "qwen-asr"),
                os.getenv("VOXTRACT_ASR_SERVED_NAME", "qwen3-asr"),
                os.getenv("VOXTRACT_FA_MODEL", "qwen-fa"),
                float(os.getenv("VOXTRACT_INFER_TIMEOUT_SECONDS", "7200")),
            )
            split = int(os.getenv("VOXTRACT_SPLIT_SECONDS", "1800"))
            if not 1 <= split <= 7200:
                raise ValueError("VOXTRACT_SPLIT_SECONDS must be in 1..7200")
            self.settings = asdict(endpoint)
            self.timeout = self.settings.pop("timeout")
            self.settings["split_seconds"] = split

    @property
    def enabled(self) -> bool:
        return self.settings is not None and not self.faulted

    @asynccontextmanager
    async def open(self):
        for job in self.store.list():
            if job["status"] in ACTIVE_STATES:
                self.interrupt(job)
        worker = asyncio.create_task(self.work()) if self.enabled else None
        try:
            yield
        finally:
            if worker is not None:
                worker.cancel()
                with suppress(asyncio.CancelledError):
                    await worker

    def interrupt(self, job: dict) -> None:
        job["error"] = {"stage": job["status"], "code": "interrupted"}
        job["status"] = "interrupted"
        self.store.save_job(job)

    async def request(self, job_id: str) -> dict:
        async with self.lock:
            job = self.store.get(job_id)
            if not self.enabled:
                raise InferenceUnavailable
            if (
                job["status"] not in {"queued", "failed", "interrupted"}
                or job.get("run_requested") or job.get("external_pending")
            ):
                raise JobNotRunnable
            if "inference_settings" not in job:
                job["inference_settings"] = self.settings
            job.update(status="queued", run_requested=True)
            job.pop("error", None)
            self.store.save_job(job)
            self.wake.set()
            return job

    async def resolve(self, job_id: str) -> dict:
        async with self.lock:
            job = self.store.get(job_id)
            if job["status"] not in {"failed", "interrupted"} or not job.get("external_pending"):
                raise JobNotHeld
            job["external_pending"] = False
            job["external_released_at"] = datetime.now(UTC).isoformat()
            self.store.save_job(job)
            self.wake.set()
            return job

    async def claim(self) -> dict | None:
        async with self.lock:
            jobs = self.store.list()
            if any(job.get("external_pending") for job in jobs):
                return None
            for job in jobs:
                if job["status"] == "queued" and job.get("run_requested"):
                    job.update(
                        status="preparing", run_requested=False,
                        attempt=job.get("attempt", 0) + 1,
                        external_pending=False,
                    )
                    job.pop("progress", None)
                    self.store.save_job(job)
                    return job
        return None

    async def work(self) -> None:
        self.wake.set()
        try:
            while True:
                await self.wake.wait()
                self.wake.clear()
                while (job := await self.claim()) is not None:
                    await self.execute(job)
        except OSError:
            self.faulted = True
            logger.error("Inference worker stopped after a storage error")

    async def execute(self, job: dict) -> None:
        try:
            await self.pipeline(job)
        except asyncio.CancelledError:
            try:
                self.interrupt(job)
            except OSError:
                logger.error("Inference interruption could not be saved")
            raise
        except UnconfirmedRequest:
            self.fail(job, "external_execution_unknown")
        except EndpointUnavailable:
            job["external_pending"] = False
            self.fail(job, "inference_unreachable")
        except InferenceFailed:
            self.fail(job, "inference_rejected")
        except ContractError:
            self.fail(job, "invalid_inference_response")
        except MediaToolUnavailable:
            self.fail(job, "media_tool_unavailable")
        except InvalidAudio:
            self.fail(job, "audio_preparation_failed")
        except OSError as exc:
            full = exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}
            code = "insufficient_storage" if full else "storage_error"
            self.fail(job, code)
        except Exception:
            # The transcript, flat files and remote response are private.
            logger.error("Inference pipeline encountered an unexpected error")
            self.fail(job, "inference_error")

    def fail(self, job: dict, code: str) -> None:
        job.update(status="failed", error={"stage": job["status"], "code": code})
        self.store.save_job(job)

    async def pipeline(self, job: dict) -> None:
        work = self.store.work_dir(job["id"])
        plan = await prepare(self.store, work, job)
        settings = {key: value for key, value in plan["settings"].items() if key != "split_seconds"}
        client = InferSwap(**settings, timeout=self.timeout)
        texts = []
        language = None
        has_chunk_text = False
        items = []
        for index, part in enumerate(plan["parts"], start=1):
            transcript = await self.stage(
                client, job, work, plan, index, "asr",
                lambda data: parse_asr(data, part["frames"]),
            )
            texts.append(transcript["text"])
            language = language or transcript["language"]
            has_chunk_text = has_chunk_text or any(chunk["text"].strip() for chunk in transcript["chunks"])
        for index, part in enumerate(plan["parts"], start=1):
            raw = self.read_checkpoint(work, plan["asr"][str(index)])
            if raw is None:
                raise OSError(errno.EIO, "ASR checkpoint no longer matches its digest")
            transcript = parse_asr(raw, part["frames"])
            aligned = await self.stage(
                client, job, work, plan, index, "fa",
                lambda data: parse_fa(data, part["frames"], transcript, part["start"]), raw,
            )
            items.extend({**item, "part": index} for item in aligned)
        result = {
            "schema_version": 1,
            "sample_rate": SAMPLE_RATE,
            "num_samples": plan["num_samples"],
            "time_reference": "canonical_audio_start",
            "time_unit": "seconds",
            "text": "\n".join(text for text in texts if text.strip()),
            "empty_transcript": not has_chunk_text,
            "language": language,
            "items": merge_items(items),
            "models": {"asr": client.asr_model, "asr_served": client.asr_served_name, "fa": client.fa_model},
            "attempt": job["attempt"],
            "plan": "plan.json",
        }
        self.store.save_json(work / "result.json", result)
        job["status"] = "completed"
        job.pop("progress", None)
        self.store.save_job(job)

    def read_checkpoint(self, work: Path, checkpoint: dict) -> bytes | None:
        raw = self.store.read_bytes(work / checkpoint["file"], MAX_RESPONSE_BYTES)
        return raw if raw is not None and content_hash(raw) == checkpoint["sha256"] else None

    async def stage(
        self, client: InferSwap, job: dict, work: Path, plan: dict,
        index: int, stage: str, validate, payload: bytes = b"",
    ):
        job.update(
            status="transcribing" if stage == "asr" else "aligning",
            progress={"part": index, "total": len(plan["parts"])},
        )
        self.store.save_job(job)
        key = str(index)
        upstream = content_hash(payload) if stage == "fa" else None
        checkpoint = plan[stage].get(key)
        if checkpoint is not None and checkpoint["upstream"] == upstream:
            if (raw := self.read_checkpoint(work, checkpoint)) is not None:
                return validate(raw)

        part = plan["parts"][index - 1]
        with self.store.open_audio(work / part["audio"]) as (size, audio):
            job["external_pending"] = True
            self.store.save_job(job)
            status, raw = await client.infer(stage, size, audio, payload)
        success = status == 200
        if success or status in REJECTED_BEFORE_INFERENCE:
            job["external_pending"] = False
            self.store.save_job(job)
        name = f"attempt-{job['attempt']}-part-{index}-{stage}.raw"
        self.store.save_bytes(work / name, raw)
        if not success:
            if job["external_pending"]:
                raise UnconfirmedRequest
            raise InferenceFailed
        parsed = validate(raw)
        plan[stage][key] = {"file": name, "sha256": content_hash(raw), "upstream": upstream}
        self.store.save_json(work / "plan.json", plan)
        return parsed


def public_job(job: dict) -> dict:
    return {key: value for key, value in job.items() if key != "inference_settings"}
