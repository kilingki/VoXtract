"""Coordinate upload validation and the persisted pending queue."""

import asyncio
import logging
from collections.abc import AsyncIterable
from os import stat_result
from pathlib import Path, PurePath
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from backend.app.services.audio import InvalidAudio, validate_audio
from backend.app.storage.jobs import JobStore, RecordingResult

logger = logging.getLogger(__name__)


class UploadBusy(Exception):
    pass


class Jobs:
    def __init__(self, store: JobStore) -> None:
        self.store = store
        self.upload_lock = asyncio.Lock()
        self.queue_lock = asyncio.Lock()

    async def upload(self, name: str, chunks: AsyncIterable[bytes]) -> dict:
        name = PurePath(name.replace("\\", "/")).name
        suffix = PurePath(name).suffix.lower()
        if suffix not in {".m4a", ".wav"}:
            raise InvalidAudio("unsupported filename extension")
        if len(name) > 255 or not name.isprintable():
            raise InvalidAudio("invalid display filename")
        if self.upload_lock.locked():
            raise UploadBusy

        async with self.upload_lock:
            job_id = uuid4().hex
            directory = await run_in_threadpool(self.store.new_upload, job_id)
            try:
                size = await self.store.store_input(directory, chunks)
                if size == 0:
                    raise InvalidAudio("empty request body")
                seconds = await validate_audio(directory / "original", suffix)
                async with self.queue_lock:
                    job = await run_in_threadpool(
                        self.store.publish, directory, job_id, name, size, seconds
                    )
            except BaseException:
                try:
                    await run_in_threadpool(self.store.discard, directory)
                except OSError:
                    logger.warning("An unfinished upload could not be cleaned up")
                raise
        return job

    async def list(self) -> list[dict]:
        async with self.queue_lock:
            return await run_in_threadpool(self.store.list)

    async def get(self, job_id: str) -> dict:
        async with self.queue_lock:
            return await run_in_threadpool(self.store.get, job_id)

    async def result(self, job_id: str) -> RecordingResult:
        async with self.queue_lock:
            return await run_in_threadpool(self.store.get_result, job_id)

    async def audio(self, job_id: str) -> tuple[Path, str, stat_result]:
        async with self.queue_lock:
            return await run_in_threadpool(self.store.original_audio, job_id)

    async def delete(self, job_id: str) -> None:
        async with self.queue_lock:
            await run_in_threadpool(self.store.delete, job_id)
