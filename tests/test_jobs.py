"""HTTP and filesystem checks for stage 2 using generated audio."""

import errno
import io
import json
import socket
import struct
import subprocess
import time
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from test_scaffold import http_server

from backend.app.storage.jobs import MIN_FREE_BYTES, JobStore


def wave_header(samples: int, rate: int = 8000) -> bytes:
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + samples, b"WAVE", b"fmt ", 16, 1, 1,
        rate, rate, 1, 8, b"data", samples,
    )


def recording() -> bytes:
    pcm = io.BytesIO()
    with wave.open(pcm, "wb") as output:
        output.setparams((1, 1, 8000, 0, "NONE", "not compressed"))
        output.writeframes(bytes(8000))
    return pcm.getvalue()


def upload(url: str, name: str, data):
    return Request(
        f"{url}/api/jobs", data=data, method="POST",
        headers={"Content-Type": "application/octet-stream", "X-Filename": quote(name)},
    )


class JobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.data = self.root / "data"
        self.jobs = self.data / "jobs"

    def assert_rejected(self, request: Request, status: int, code: str) -> None:
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=60)
        with error.exception as response:
            self.assertEqual(response.status, status)
            self.assertEqual(json.load(response), {"detail": code})

    def get_records(self, url: str) -> list:
        with urlopen(f"{url}/api/jobs", timeout=5) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            return json.load(response)["jobs"]

    def test_audio_upload_lookup_restart_and_delete(self) -> None:
        wav = recording()
        m4a_path = self.root / "fixture.m4a"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "wav", "-i", "pipe:0",
             "-c:a", "aac", str(m4a_path)],
            input=wav, check=True, timeout=10,
        )
        with http_server(self.data) as url:
            self.assertEqual(self.get_records(url), [])
            with urlopen(upload(url, "../../녹음.wav", iter([wav[:10], wav[10:]]))) as response:
                first = json.load(response)
                self.assertEqual(response.status, 201)
                self.assertEqual(response.headers["Location"], f"/api/jobs/{first['id']}")
            self.assertEqual(first["name"], "녹음.wav")
            self.assertEqual(first["size_bytes"], len(wav))
            self.assertEqual(first["duration_seconds"], 1)
            self.assertEqual(first["status"], "queued")
            location = f"/api/jobs/{first['id']}"
            with urlopen(f"{url}{location}", timeout=5) as response:
                self.assertEqual(json.load(response), first)
            with urlopen(upload(url, r"fakepath\voice.m4a", m4a_path.read_bytes())) as response:
                second = json.load(response)
            self.assertEqual(second["name"], "voice.m4a")
            self.assertEqual(second["duration_seconds"], 1)
            self.assertEqual(self.get_records(url), [first, second])
            self.assert_rejected(Request(f"{url}/api/jobs/%2E%2E"), 404, "job_not_found")
            with self.assertRaises(HTTPError) as error:
                urlopen(f"{url}/data/jobs/{first['id']}/original", timeout=5)
            error.exception.close()
            self.assertEqual(error.exception.status, 404)
        self.assertEqual((self.jobs / first["id"] / "original").read_bytes(), wav)
        self.assertEqual((self.jobs / second["id"] / "original").read_bytes(), m4a_path.read_bytes())
        abandoned = self.jobs / f".upload-{uuid4().hex}"
        deleted = self.jobs / f".deleted-{uuid4().hex}"
        for directory in (abandoned, deleted):
            directory.mkdir()
            (directory / "original").write_bytes(b"partial")
        with http_server(self.data) as url:
            self.assertFalse(abandoned.exists())
            self.assertFalse(deleted.exists())
            self.assertEqual(self.get_records(url), [first, second])
            with urlopen(Request(f"{url}{location}", method="DELETE"), timeout=5) as response:
                self.assertEqual(response.status, 204)
            self.assert_rejected(Request(f"{url}{location}"), 404, "job_not_found")
            self.assertEqual(self.get_records(url), [second])
            self.assertFalse((self.jobs / first["id"]).exists())

    def test_rejects_invalid_audio_and_disallowed_formats(self) -> None:
        video = self.root / "fixture.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=size=16x16:rate=1",
             "-f", "wav", "-i", "pipe:0", "-c:v", "mpeg4", "-c:a", "aac",
             "-shortest", str(video)],
            input=recording(), check=True, timeout=10,
        )
        with http_server(self.data) as url:
            for name, data in [
                ("empty.wav", b""), ("bogus.wav", b"not audio"),
                ("truncated.wav", recording()[:500]), ("wrong.m4a", recording()),
                ("video.m4a", video.read_bytes()), ("wrong.txt", recording()),
                ("zero.wav", wave_header(0)),
            ]:
                with self.subTest(name=name):
                    self.assert_rejected(upload(url, name, data), 422, "invalid_audio")
            self.assert_rejected(
                Request(f"{url}/api/jobs", data=recording()),
                415, "unsupported_upload_type",
            )
            self.assertEqual(self.get_records(url), [])
            self.assertEqual(list(self.jobs.iterdir()), [])

    def test_two_hour_boundary(self) -> None:
        rate = 32000
        samples = 7200 * rate
        source = self.root / "boundary.wav"
        with http_server(self.data) as url:
            for frames in (samples, samples + 2):
                with source.open("wb") as file:
                    file.write(wave_header(frames, rate))
                    file.truncate(44 + frames)
                with source.open("rb") as file:
                    request = upload(url, "boundary.wav", file)
                    if frames == samples:
                        with urlopen(request, timeout=60) as response:
                            job = json.load(response)
                            self.assertEqual(job["duration_seconds"], 7200)
                            self.assertGreater(job["size_bytes"], 200 * 1024**2)
                    else:
                        self.assert_rejected(request, 422, "audio_too_long")
            self.assertEqual(len(self.get_records(url)), 1)
            self.assertEqual(len(list(self.jobs.iterdir())), 1)

    def test_aac_boundary_includes_playable_audio_only(self) -> None:
        with http_server(self.data) as url:
            for seconds in (7200, 7201):
                source = self.root / f"{seconds}.m4a"
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=11025:cl=mono",
                     "-t", str(seconds), "-c:a", "aac", str(source)],
                    check=True, timeout=60,
                )
                with source.open("rb") as file:
                    request = upload(url, source.name, file)
                    if seconds == 7200:
                        with urlopen(request, timeout=60) as response:
                            self.assertEqual(json.load(response)["duration_seconds"], 7200)
                    else:
                        self.assert_rejected(request, 422, "audio_too_long")

    def test_aac_accepts_track_timing_that_differs_from_decoded_samples(self) -> None:
        source = self.root / "gap.m4a"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
             "sine=frequency=440:sample_rate=8000:duration=2", "-af",
             r"asetpts=PTS+gte(N\,4096)*0.75/TB", "-c:a", "aac", str(source)],
            check=True, timeout=10,
        )
        with http_server(self.data) as url:
            with urlopen(upload(url, source.name, source.read_bytes()), timeout=10) as response:
                self.assertEqual(response.status, 201)
                self.assertEqual(json.load(response)["duration_seconds"], 2.75)

    def test_space_reserve_and_disk_write_failure(self) -> None:
        with http_server(self.data) as url:
            with patch("backend.app.storage.jobs.shutil.disk_usage") as disk_usage:
                disk_usage.return_value.free = MIN_FREE_BYTES + 1
                self.assert_rejected(
                    upload(url, "reserve.wav", recording()), 507, "insufficient_storage"
                )
            with patch.object(JobStore, "_write", side_effect=OSError(errno.ENOSPC, "test")):
                self.assert_rejected(
                    upload(url, "full.wav", recording()), 507, "insufficient_storage"
                )
            self.assertEqual(self.get_records(url), [])
            self.assertEqual(list(self.jobs.iterdir()), [])

    def test_disconnect_and_duplicate_server_lock(self) -> None:
        with http_server(self.data) as url:
            port = urlsplit(url).port
            with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                connection.sendall(
                    b"POST /api/jobs HTTP/1.1\r\nHost: localhost\r\n"
                    b"Content-Type: application/octet-stream\r\n"
                    b"X-Filename: partial.wav\r\nContent-Length: 50000\r\n\r\n"
                    + recording()
                )
                self.wait_for(lambda: any(path.stat().st_size for path in self.jobs.glob("*/original")))
                self.assertEqual(self.get_records(url), [])
                with self.assertRaises(RuntimeError), JobStore(self.data).open():
                    self.fail("Another instance obtained the data lock")
                self.assert_rejected(upload(url, "parallel.wav", recording()), 409, "upload_busy")
            self.wait_for(lambda: not list(self.jobs.iterdir()))
            with urlopen(upload(url, "retry.wav", recording()), timeout=10) as response:
                self.assertEqual(response.status, 201)

    def wait_for(self, predicate) -> None:
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Expected filesystem state did not appear")
            time.sleep(0.02)
