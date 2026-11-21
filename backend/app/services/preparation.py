"""Prepare canonical WAV files and assemble the private transcription result."""

import asyncio
import hashlib
import json
import math
import unicodedata
from pathlib import Path

from backend.app.integrations.inferswap import BLOCK_BYTES, SAMPLE_RATE
from backend.app.services.audio import (
    MAX_DURATION_SECONDS, AudioTooLong, InvalidAudio, media_input, media_process,
)
from backend.app.storage.jobs import JobStore

MAX_FRAMES = MAX_DURATION_SECONDS * SAMPLE_RATE
PCM_ARGS = ("-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le")


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def partitions(frames: int, limit: int) -> list[dict]:
    count = math.ceil(frames / limit)
    context = min(2 * SAMPLE_RATE, limit // 10)
    ranges = []
    for index in range(count):
        core_start = index * frames // count
        core_end = (index + 1) * frames // count
        start = max(0, core_start - context)
        end = min(frames, core_end + context)
        ranges.append({
            "core_start": core_start, "core_end": core_end, "start": start,
            "frames": end - start, "audio": "canonical.wav" if count == 1 else f"part-{index + 1}.wav",
        })
    return ranges


async def canonicalize(store: JobStore, original: Path, path: Path, job: dict) -> tuple[int, str]:
    suffix = Path(job["name"]).suffix.lower()
    estimate = min(MAX_FRAMES, max(1, round(job["duration_seconds"] * SAMPLE_RATE)))
    # AAC decoders can emit padding beyond the playable track. Cap the converted
    # samples at the admitted track length while leaving WAV samples intact.
    trim = ("-af", f"aresample={SAMPLE_RATE},atrim=end_sample={estimate}") if suffix == ".m4a" else ()
    async with media_process("ffmpeg", "-version", timeout=10) as version:
        tool = (await version.stdout.read()).decode("utf-8", errors="replace").splitlines()[0]
    with store.wave_output(path, SAMPLE_RATE, estimate) as write:
        async with media_process(
            "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
            *media_input(original, suffix), *PCM_ARGS, *trim, "-f", "s16le", "pipe:1", timeout=300,
        ) as decoder:
            byte_count = 0
            while block := await decoder.stdout.read(BLOCK_BYTES):
                byte_count += len(block)
                if byte_count > 2 * MAX_FRAMES:
                    raise AudioTooLong("canonical PCM exceeded two hours")
                write(block)
        if not byte_count or byte_count % 2:
            raise InvalidAudio("incomplete canonical PCM")
    return byte_count // 2, tool


async def prepare(store: JobStore, work: Path, job: dict) -> dict:
    manifest = work / "plan.json"
    original = work.parent / "original"
    original_hash = await asyncio.to_thread(store.file_hash, original)
    if (saved := store.read_bytes(manifest)) is not None:
        plan = json.loads(saved)
        if plan["original_sha256"] != original_hash or plan["settings"] != job["inference_settings"]:
            raise InvalidAudio("saved input or settings changed")
        for audio, expected in plan["audio_hashes"].items():
            if await asyncio.to_thread(store.file_hash, work / audio) != expected:
                raise InvalidAudio("prepared audio changed")
        return plan

    canonical = work / "canonical.wav"
    frames, tool = await canonicalize(store, original, canonical, job)
    parts = partitions(frames, job["inference_settings"]["split_seconds"] * SAMPLE_RATE)
    hashes = {"canonical.wav": await asyncio.to_thread(store.file_hash, canonical)}
    if len(parts) > 1:
        for part in parts:
            path = work / part["audio"]
            with store.wave_output(path, SAMPLE_RATE, part["frames"]) as write:
                for block in store.pcm_blocks(canonical, part["start"], part["frames"]):
                    write(block)
                    await asyncio.sleep(0)
            hashes[part["audio"]] = await asyncio.to_thread(store.file_hash, path)
    plan = {
        "schema_version": 1,
        "settings": job["inference_settings"],
        "original_sha256": original_hash,
        "audio_hashes": hashes,
        "source_stream": "0:a:0",
        "conversion": {
            "tool": tool, "sample_rate": SAMPLE_RATE, "channels": 1, "codec": "pcm_s16le",
            "aac_capped_at_playable_track": Path(job["name"]).suffix.lower() == ".m4a",
        },
        "num_samples": frames,
        "parts": parts,
        "asr": {},
        "fa": {},
    }
    store.save_json(manifest, plan)
    return plan


def merge_items(items: list[dict]) -> list[dict]:
    result = []
    recent: dict[str, list[dict]] = {}
    for item in sorted(items, key=lambda item: (item["start"], item["end"], item["part"], item["chunk"])):
        key = unicodedata.normalize("NFC", item["text"]).strip().casefold()
        candidates = [
            previous for previous in recent.get(key, []) if previous["end"] > item["start"]
        ]
        duplicate = next((
            previous for previous in candidates
            if (previous["part"], previous["chunk"]) != (item["part"], item["chunk"])
            and item["end"] > item["start"]
            and max(item["start"], item["overlap_start"], previous["overlap_start"])
            < min(item["end"], previous["end"], item["overlap_end"], previous["overlap_end"])
        ), None)
        if duplicate is None:
            result.append(item)
            candidates.append(item)
        else:
            # Consume one match so multiple repetitions cannot collapse into one.
            candidates.remove(duplicate)
        recent[key] = candidates
    return [
        {key: item[key] for key in ("text", "start", "end", "part", "chunk")}
        for item in result
    ]
