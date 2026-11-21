"""Private, filesystem-backed queue for a single VoXtract process."""

import errno
import json
import os
import shutil
from collections.abc import AsyncIterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from starlette.concurrency import run_in_threadpool

MIN_FREE_BYTES = 1024**3


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
            yield

    def new_upload(self, job_id: str) -> Path:
        path = self.jobs_dir / f".upload-{job_id}"
        self._check_space(0)
        path.mkdir(mode=0o700)
        return path

    def _check_space(self, size: int) -> None:
        if shutil.disk_usage(self.jobs_dir).free - size < MIN_FREE_BYTES:
            raise OSError(errno.ENOSPC, "Insufficient disk space")

    def _write(self, file: BinaryIO, data: bytes) -> None:
        self._check_space(len(data))
        if file.write(data) != len(data):
            raise OSError(errno.EIO, "Incomplete disk write")

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
        if job["status"] != "queued":
            raise JobNotDeletable
        pending = self.jobs_dir / f".deleted-{job_id}"
        os.rename(self.jobs_dir / job_id, pending)
        sync_directory(self.jobs_dir)
        self.discard(pending)
