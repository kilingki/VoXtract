"""Exercise the stage 3 HTTP flow with contract-based fake runtimes."""

import errno
import io
import json
import os
import socket
import subprocess
import threading
import time
import unittest
import wave
from contextlib import contextmanager, suppress
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.app.integrations.inferswap import ContractError, parse_asr, parse_fa
from backend.app.services.preparation import merge_items, partitions
from backend.app.storage.jobs import JobStore
from test_jobs import recording, upload
from test_scaffold import http_server


class FakeInferSwap(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FakeHandler)
        self.url = f"http://127.0.0.1:{self.server_port}"
        self.calls = []
        self.behavior = None
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()


class FakeHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        data = self.rfile.read(int(self.headers["Content-Length"]))
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + data
        )
        fields = {
            part.get_param("name", header="Content-Disposition"): part.get_payload(decode=True)
            for part in message.iter_parts()
        }
        with wave.open(io.BytesIO(fields["file"]), "rb") as audio:
            frames = audio.getnframes()
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
                self.send_error(400)
                return
        audio_meta = {"sample_rate": 16000, "channels": 1, "num_samples": frames}
        if self.path == "/infer?model=qwen-asr":
            stage = "asr"
            text = "테스트"
            chunks = [{"index": 0, "start_sample": 0, "end_sample": frames, "text": text, "language": "ko"}]
            response = {
                "task": "transcribe", "language": "ko", "duration": frames / 16000,
                "text": text, "segments": [], "audio": audio_meta, "chunks": chunks,
            }
        elif self.path == "/infer?model=qwen-fa":
            stage = "fa"
            source = json.loads(fields["payload"])
            chunks = [
                {
                    **chunk, "language": "Korean", "status": "aligned", "items": [{
                        "text": chunk["text"], "start_time": 0.0, "end_time": min(0.04, frames / 16000),
                    }],
                } for chunk in source["chunks"]
            ]
            response = {
                "audio": audio_meta, "time_reference": "audio_start",
                "overlap_deduplicated": False, "chunks": chunks,
            }
        else:
            self.send_error(404)
            return

        with self.server.lock:
            self.server.active += 1
            self.server.max_active = max(self.server.active, self.server.max_active)
        try:
            self.server.calls.append((stage, fields))
            status = 200
            if self.server.behavior is not None:
                status, response = self.server.behavior(stage, response)
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            with suppress(OSError):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        finally:
            with self.server.lock:
                self.server.active -= 1

    def log_message(self, *_args):
        pass


@contextmanager
def fake_inferswap():
    with FakeInferSwap() as fake:
        thread = threading.Thread(target=fake.serve_forever, daemon=True)
        thread.start()
        try:
            yield fake
        finally:
            fake.shutdown()
            thread.join()


def call(url, *, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {} if data is None else {"Content-Type": "application/json"}
    with urlopen(Request(url, method=method, data=data, headers=headers), timeout=3) as response:
        return response.status, json.load(response) if response.status != 204 else None


class InferenceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.fake = self.enterContext(fake_inferswap())
        self.enterContext(patch.dict(os.environ, {
            "VOXTRACT_INFERSWAP_URL": self.fake.url,
            "VOXTRACT_SPLIT_SECONDS": "1800",
            "VOXTRACT_INFER_TIMEOUT_SECONDS": "2",
        }))

    def add(self, url, name="synthetic.wav", payload=None):
        with urlopen(upload(url, name, recording() if payload is None else payload), timeout=5) as response:
            return json.load(response)

    def path(self, job):
        return self.root / "jobs" / job["id"] / "inference"

    def job_url(self, url, job):
        return f"{url}/api/jobs/{job['id']}"

    def run_job(self, url, job):
        self.assertEqual(call(self.job_url(url, job) + "/run", method="POST", body={})[0], 202)

    def wait(self, url, job, expected):
        for _ in range(120):
            current = call(self.job_url(url, job))[1]
            if current["status"] == expected:
                return current
            time.sleep(0.025)
        self.fail(f"Expected {expected}; found {current['status']} ({current.get('error')})")

    def assert_code(self, status, url, *, method="POST", body=None):
        with self.assertRaises(HTTPError) as raised:
            call(url, method=method, body=body)
        with raised.exception as response:
            self.assertEqual(response.code, status)

    def test_existing_m4a_requires_explicit_run_and_preserves_contract(self):
        media = self.root / "synthetic.m4a"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c:a", "aac", str(media)],
            input=recording(), check=True, timeout=10,
        )
        with patch.dict(os.environ, {"VOXTRACT_INFERSWAP_URL": ""}):
            with http_server(self.root) as url:
                job = self.add(url, "synthetic.m4a", media.read_bytes())
                self.assert_code(503, self.job_url(url, job) + "/run", body={})
        with http_server(self.root) as url:
            self.assertEqual(call(self.job_url(url, job))[1]["status"], "queued")
            self.assertEqual(self.fake.calls, [])
            self.run_job(url, job)
            result = self.wait(url, job, "completed")
            self.assertNotIn("inference_settings", result)
            self.assert_code(404, url + f"/data/jobs/{job['id']}/inference/result.json", method="GET")
        (asr, asr_fields), (fa, fa_fields) = self.fake.calls
        self.assertEqual([asr, fa], ["asr", "fa"])
        self.assertEqual({k: v for k, v in asr_fields.items() if k != "file"}, {
            "model": b"qwen3-asr", "response_format": b"verbose_json",
            "include_chunks": b"true", "language": b"ko",
        })
        self.assertEqual(asr_fields["file"], fa_fields["file"])
        self.assertEqual(fa_fields["payload"], (self.path(job) / "attempt-1-part-1-asr.raw").read_bytes())
        private = json.loads((self.path(job) / "result.json").read_bytes())
        self.assertEqual(private["items"][0]["text"], "테스트")
        self.assertEqual(private["time_reference"], "canonical_audio_start")
        self.assertEqual(private["num_samples"], round(job["duration_seconds"] * 16000))

    def test_split_order_wave_identity_and_absolute_offsets(self):
        with patch.dict(os.environ, {"VOXTRACT_SPLIT_SECONDS": "1"}):
            with http_server(self.root) as url:
                output = io.BytesIO()
                with wave.open(output, "wb") as audio:
                    audio.setparams((1, 2, 16000, 0, "NONE", ""))
                    audio.writeframes(b"\0\0" * 32000)
                job = self.add(url, payload=output.getvalue())
                self.run_job(url, job)
                self.wait(url, job, "completed")
        self.assertEqual([stage for stage, _ in self.fake.calls], ["asr", "asr", "fa", "fa"])
        for (_, asr), (_, fa) in zip(self.fake.calls[:2], self.fake.calls[2:], strict=True):
            self.assertEqual(asr["file"], fa["file"])
        result = json.loads((self.path(job) / "result.json").read_bytes())
        self.assertEqual([item["start"] for item in result["items"]], [0, 0.9])
        self.assertEqual(self.fake.max_active, 1)

    def test_fa_rejection_keeps_asr_and_retry_does_not_resend_it(self):
        self.fake.behavior = lambda stage, response: (400 if stage == "fa" else 200, response)
        with http_server(self.root) as url:
            job = self.add(url)
            self.run_job(url, job)
            failed = self.wait(url, job, "failed")
            self.assertFalse(failed["external_pending"])
            self.assertEqual(failed["error"]["stage"], "aligning")
            self.assertFalse((self.path(job) / "result.json").exists())
        self.fake.behavior = None
        with http_server(self.root) as url:
            self.run_job(url, job)
            finished = self.wait(url, job, "completed")
            self.assertEqual(finished["attempt"], 2)
        self.assertEqual([stage for stage, _ in self.fake.calls], ["asr", "fa", "fa"])

    def test_unknown_timeout_holds_queue_after_restart(self):
        entered = threading.Event()
        release = threading.Event()

        def stall(_stage, response):
            entered.set()
            release.wait(5)
            return 200, response

        self.fake.behavior = stall
        self.addCleanup(release.set)
        with patch.dict(os.environ, {"VOXTRACT_INFER_TIMEOUT_SECONDS": "0.5"}):
            with http_server(self.root) as url:
                first, second = self.add(url), self.add(url)
                self.run_job(url, first)
                self.assertTrue(entered.wait(2))
                self.assert_code(409, self.job_url(url, first) + "/run", body={})
                self.run_job(url, second)
                failed = self.wait(url, first, "failed")
                self.assertTrue(failed["external_pending"])
                self.assertEqual(failed["error"]["code"], "external_execution_unknown")
        with http_server(self.root) as url:
            first_url = self.job_url(url, first)
            self.assertTrue(call(url + "/api/jobs")[1]["inference_held"])
            self.assertEqual(len(self.fake.calls), 1)
            self.assert_code(409, first_url, method="DELETE")
            self.assert_code(409, first_url + "/run", body={})
            self.assert_code(422, first_url + "/resolve", body={"runtime_idle_confirmed": False})
            self.fake.behavior = None
            release.set()
            deadline = time.monotonic() + 2
            while self.fake.active and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(self.fake.active, 0)
            call(first_url + "/resolve", method="POST", body={"runtime_idle_confirmed": True})
            self.wait(url, second, "completed")
        self.assertEqual([stage for stage, _ in self.fake.calls], ["asr", "asr", "fa"])

    def test_server_shutdown_preserves_external_marker(self):
        entered = threading.Event()
        release = threading.Event()

        def stall(_stage, response):
            entered.set()
            release.wait(5)
            return 200, response

        self.fake.behavior = stall
        self.addCleanup(release.set)
        with http_server(self.root) as url:
            job = self.add(url)
            self.run_job(url, job)
            self.assertTrue(entered.wait(2))
        release.set()
        with http_server(self.root) as url:
            interrupted = self.wait(url, job, "interrupted")
            self.assertTrue(interrupted["external_pending"])
            self.assertEqual(len(self.fake.calls), 1)

    def test_restart_recovers_unclean_exit_before_claiming_another_job(self):
        with http_server(self.root) as url:
            first, second = self.add(url), self.add(url)
        store = JobStore(self.root)
        saved = store.get(first["id"])
        saved.update(status="transcribing", external_pending=True)
        store.save_job(saved)
        with http_server(self.root) as url:
            self.run_job(url, second)
            interrupted = self.wait(url, first, "interrupted")
            self.assertTrue(interrupted["external_pending"])
            self.assertTrue(call(self.job_url(url, second))[1]["run_requested"])
            self.assertEqual(self.fake.calls, [])

    def test_connection_refused_before_sending_does_not_hold_queue(self):
        with socket.socket() as bound:
            bound.bind(("127.0.0.1", 0))
            origin = f"http://127.0.0.1:{bound.getsockname()[1]}"
            with patch.dict(os.environ, {"VOXTRACT_INFERSWAP_URL": origin}):
                with http_server(self.root) as url:
                    job = self.add(url)
                    self.run_job(url, job)
                    failed = self.wait(url, job, "failed")
                    self.assertEqual(failed["error"]["code"], "inference_unreachable")
                    self.assertFalse(failed["external_pending"])

    def test_successful_http_with_invalid_fa_result_does_not_complete(self):
        def invalid(stage, response):
            if stage == "fa":
                response["chunks"][0]["items"] = []
            return 200, response

        self.fake.behavior = invalid
        with http_server(self.root) as url:
            job = self.add(url)
            self.run_job(url, job)
            failed = self.wait(url, job, "failed")
            self.assertEqual(failed["error"]["code"], "invalid_inference_response")
            self.assertFalse(failed["external_pending"])
            self.assertFalse((self.path(job) / "result.json").exists())

    def test_asr_error_stops_fa_and_leaves_execution_unconfirmed(self):
        self.fake.behavior = lambda _stage, _response: (500, {"detail": "synthetic error"})
        with http_server(self.root) as url:
            job = self.add(url)
            self.run_job(url, job)
            self.assertTrue(self.wait(url, job, "failed")["external_pending"])
        self.assertEqual([stage for stage, _ in self.fake.calls], ["asr"])

    def test_result_disk_failure_does_not_complete_and_retry_uses_checkpoints(self):
        original = JobStore.save_json

        def no_room(store, path, data):
            if path.name == "result.json":
                raise OSError(errno.ENOSPC, "synthetic disk failure")
            return original(store, path, data)

        with http_server(self.root) as url:
            job = self.add(url)
            with patch.object(JobStore, "save_json", no_room):
                self.run_job(url, job)
                failed = self.wait(url, job, "failed")
                self.assertEqual(failed["error"]["code"], "insufficient_storage")
                self.assertFalse(failed["external_pending"])
                self.assertFalse((self.path(job) / "result.json").exists())
            self.run_job(url, job)
            self.wait(url, job, "completed")
        self.assertEqual([stage for stage, _ in self.fake.calls], ["asr", "fa"])


class AlignmentTests(unittest.TestCase):
    def test_partition_boundaries_and_core_coverage(self):
        threshold = 1800 * 16000
        for frames, count in [(threshold - 1, 1), (threshold, 1), (threshold + 1, 2), (4 * threshold, 4)]:
            parts = partitions(frames, threshold)
            self.assertEqual(len(parts), count)
            self.assertEqual(parts[0]["core_start"], 0)
            self.assertEqual(parts[-1]["core_end"], frames)
            self.assertEqual(sum(part["core_end"] - part["core_start"] for part in parts), frames)

    def test_alignment_rejects_bad_audio_and_nonfinite_times(self):
        audio = {"sample_rate": 16000, "channels": 1, "num_samples": 16000}
        chunk = {"index": 0, "start_sample": 0, "end_sample": 16000, "text": "테스트", "language": "ko"}
        source = {"audio": audio, "text": "테스트", "language": "ko", "chunks": [chunk]}
        transcript = parse_asr(json.dumps(source).encode(), 16000)
        with self.assertRaises(ContractError):
            parse_asr(json.dumps(source).encode(), 8000)
        fa = {
            "audio": audio, "time_reference": "audio_start", "overlap_deduplicated": False,
            "chunks": [{
                **chunk, "status": "aligned", "language": "Korean",
                "items": [{"text": "테스트", "start_time": 0.1, "end_time": float("nan")}],
            }],
        }
        with self.assertRaises(ContractError):
            parse_fa(json.dumps(fa).encode(), 16000, transcript, 0)

    def test_merge_collapses_overlapping_duplicates_and_keeps_real_repetitions(self):
        def token(start, end, chunk):
            return {"text": "말", "start": start, "end": end, "part": 1, "chunk": chunk,
                    "overlap_start": 0.5, "overlap_end": 2}

        items = [token(0.7, 0.8, 0), token(0.72, 0.83, 1), token(0.84, 0.9, 1), token(1.8, 1.9, 1)]
        self.assertEqual([item["start"] for item in merge_items(items)], [0.7, 0.84, 1.8])
