"""Private, filesystem-backed queue for a single VoXtract process."""

import errno
import hashlib
import json
import os
import shutil
import struct
import wave
from collections.abc import AsyncIterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import BinaryIO
from uuid import UUID, uuid4

from starlette.concurrency import run_in_threadpool

MIN_FREE_BYTES = 1024**3
ACTIVE_STATES = {"preparing", "transcribing", "aligning"}
IO_BLOCK_BYTES = 64 * 1024
WAV_HEADER_BYTES = 44


def sync_directory(path: Path) -> None:
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    with path.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("VoXtract could not lock its data directory") from exc
        yield


def is_job_id(value: str) -> bool:
    try:
        return UUID(value, version=4).hex == value
    except ValueError:
        return False


class JobNotFound(Exception):
    pass


class JobNotDeletable(Exception):
    pass


class JobStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.space_lock = RLock()
        self.reserved_bytes = 0

    @contextmanager
    def open(self) -> Iterator[None]:
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with exclusive_lock(self.data_dir / ".lock"):
            self.jobs_dir.mkdir(mode=0o700, exist_ok=True)
            sync_directory(self.data_dir)
            for path in self.jobs_dir.iterdir():
                prefix, _, job_id = path.name.rpartition("-")
                if prefix in {".upload", ".deleted"} and is_job_id(job_id):
                    self.discard(path)
                elif is_job_id(path.name):
                    for folder in (path, path / "inference"):
                        for temp in folder.glob(".*.tmp"):
                            temp.unlink(missing_ok=True)
            yield

    def new_upload(self, job_id: str) -> Path:
        path = self.jobs_dir / f".upload-{job_id}"
        self._check_space(0)
        path.mkdir(mode=0o700)
        return path

    def _check_space(self, size: int) -> None:
        with self.space_lock:
            if shutil.disk_usage(self.jobs_dir).free - size - self.reserved_bytes < MIN_FREE_BYTES:
                raise OSError(errno.ENOSPC, "Insufficient disk space")

    def _write(self, file: BinaryIO, data: bytes, reserve: list[int] | None = None) -> None:
        with self.space_lock:
            credit = min(len(data), reserve[0]) if reserve is not None else 0
            self._check_space(len(data) - credit)
            if file.write(data) != len(data):
                raise OSError(errno.EIO, "Incomplete disk write")
            if reserve is not None:
                reserve[0] -= credit
                self.reserved_bytes -= credit

    @contextmanager
    def reserve(self, size: int) -> Iterator[list[int]]:
        with self.space_lock:
            self._check_space(size)
            self.reserved_bytes += size
        remaining = [size]
        try:
            yield remaining
        finally:
            with self.space_lock:
                self.reserved_bytes -= remaining[0]

    @contextmanager
    def atomic_file(self, path: Path) -> Iterator[BinaryIO]:
        temp = path.with_name(f".{path.name}.tmp")
        try:
            with temp.open("xb", buffering=0) as file:
                yield file
                os.fsync(file.fileno())
                self._check_space(0)
            os.replace(temp, path)
            sync_directory(path.parent)
        finally:
            temp.unlink(missing_ok=True)

    def save_bytes(self, path: Path, data: bytes) -> None:
        with self.atomic_file(path) as file:
            self._write(file, data)

    def save_json(self, path: Path, data: dict) -> None:
        self.save_bytes(path, json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"))

    @staticmethod
    def read_bytes(path: Path, limit: int | None = None) -> bytes | None:
        try:
            with path.open("rb") as file:
                if limit is None:
                    return file.read()
                data = file.read(limit + 1)
                return data if len(data) <= limit else None
        except FileNotFoundError:
            return None

    @staticmethod
    def file_hash(path: Path) -> str:
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()

    @staticmethod
    @contextmanager
    def open_audio(path: Path):
        with path.open("rb") as file:
            yield os.fstat(file.fileno()).st_size, iter(lambda: file.read(IO_BLOCK_BYTES), b"")

    @contextmanager
    def wave_output(self, path: Path, sample_rate: int, estimate: int):
        with self.reserve(WAV_HEADER_BYTES + 2 * estimate) as reserve:
            with self.atomic_file(path) as file:
                self._write(file, bytes(WAV_HEADER_BYTES), reserve)

                def write(pcm: bytes) -> None:
                    self._write(file, pcm, reserve)

                yield write
                pcm_bytes = file.tell() - WAV_HEADER_BYTES
                if pcm_bytes <= 0 or pcm_bytes % 2:
                    raise ValueError("incomplete canonical PCM")
                file.seek(0)
                header = struct.pack(
                    "<4sI4s4sIHHIIHH4sI",
                    b"RIFF", 36 + pcm_bytes, b"WAVE", b"fmt ", 16, 1, 1, sample_rate,
                    2 * sample_rate, 2, 16, b"data", pcm_bytes,
                )
                if file.write(header) != len(header):
                    raise OSError(errno.EIO, "Incomplete WAV header write")

    @staticmethod
    def pcm_blocks(path: Path, offset: int, frames: int) -> Iterator[bytes]:
        with wave.open(str(path), "rb") as source:
            source.setpos(offset)
            while frames:
                count = min(frames, IO_BLOCK_BYTES // 2)
                data = source.readframes(count)
                if len(data) != count * 2:
                    raise OSError(errno.EIO, "Incomplete PCM segment")
                yield data
                frames -= count

    def save_job(self, job: dict) -> None:
        self.save_json(self.jobs_dir / job["id"] / "job.json", job)

    def work_dir(self, job_id: str) -> Path:
        path = self.jobs_dir / job_id / "inference"
        if not path.exists():
            path.mkdir(mode=0o700)
            sync_directory(path.parent)
        return path

    async def store_input(self, directory: Path, chunks: AsyncIterable[bytes]) -> int:
        path = directory / "original"
        total = 0
        file = await run_in_threadpool(path.open, "xb", 0)
        try:
            async for chunk in chunks:
                if chunk:
                    await run_in_threadpool(self._write, file, chunk)
                    total += len(chunk)
            await run_in_threadpool(os.fsync, file.fileno())
            await run_in_threadpool(self._check_space, 0)
        finally:
            await run_in_threadpool(file.close)
        return total

    def discard(self, path: Path) -> None:
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            if path.exists():
                raise
        sync_directory(self.jobs_dir)

    def list(self) -> list[dict]:
        records = [
            self.get(path.name)
            for path in self.jobs_dir.iterdir()
            if is_job_id(path.name)
        ]
        return sorted(records, key=lambda job: job["sequence"])

    def get(self, job_id: str) -> dict:
        if not is_job_id(job_id):
            raise JobNotFound
        try:
            with (self.jobs_dir / job_id / "job.json").open(encoding="utf-8") as file:
                return json.load(file)
        except FileNotFoundError as exc:
            raise JobNotFound from exc

    def publish(
        self, directory: Path, job_id: str, name: str, size: int, seconds: float
    ) -> dict:
        records = self.list()
        job = {
            "schema_version": 1,
            "id": job_id,
            "sequence": records[-1]["sequence"] + 1 if records else 1,
            "status": "queued",
            "name": name,
            "size_bytes": size,
            "duration_seconds": seconds,
            "created_at": datetime.now(UTC).isoformat(),
        }
        metadata = json.dumps(job, ensure_ascii=False).encode("utf-8")
        self._check_space(len(metadata))
        with (directory / "job.json").open("xb", buffering=0) as file:
            self._write(file, metadata)
            os.fsync(file.fileno())
        sync_directory(directory)
        self._check_space(0)
        destination = self.jobs_dir / job_id
        os.rename(directory, destination)
        try:
            sync_directory(self.jobs_dir)
        except OSError:
            os.rename(destination, directory)
            raise
        return job

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if job["status"] in ACTIVE_STATES or job.get("external_pending") or job.get("run_requested"):
            raise JobNotDeletable
        pending = self.jobs_dir / f".deleted-{job_id}"
        os.rename(self.jobs_dir / job_id, pending)
        sync_directory(self.jobs_dir)
        self.discard(pending)
