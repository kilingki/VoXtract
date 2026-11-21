"""HTTP entry point for the local upload and pending queue."""

import errno
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect

from backend.app.services.audio import AudioTooLong, InvalidAudio, MediaToolUnavailable
from backend.app.services.jobs import Jobs, UploadBusy
from backend.app.storage.jobs import JobNotDeletable, JobNotFound, JobStore

ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"
PRIVATE_HEADERS = {"Cache-Control": "no-store"}
logger = logging.getLogger("uvicorn.error")
JOB_ERRORS = {
    AudioTooLong: (422, "audio_too_long"),
    InvalidAudio: (422, "invalid_audio"),
    MediaToolUnavailable: (503, "media_tool_unavailable"),
    UploadBusy: (409, "upload_busy"),
    JobNotFound: (404, "job_not_found"),
    JobNotDeletable: (409, "job_not_deletable"),
    ClientDisconnect: (400, "upload_disconnected"),
}


async def job_error(_request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, OSError):
        status, code = (
            (507, "insufficient_storage")
            if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}
            else (500, "storage_error")
        )
    else:
        status, code = JOB_ERRORS[type(exc)]
        if isinstance(exc, InvalidAudio):
            logger.info("Upload rejected: %s", exc)
    return JSONResponse({"detail": code}, status_code=status, headers=PRIVATE_HEADERS)


def create_app(data_dir: Path = ROOT_DIR / "data") -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = JobStore(data_dir)
        with store.open():
            app.state.jobs = Jobs(store)
            yield

    app = FastAPI(title="VoXtract", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
    for error_type in (*JOB_ERRORS, OSError):
        app.add_exception_handler(error_type, job_error)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Application liveness, independent of InferSwap/runtime readiness."""
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.post("/api/jobs", status_code=201)
    async def upload(request: Request) -> JSONResponse:
        if request.headers.get("content-type") != "application/octet-stream":
            raise HTTPException(415, "unsupported_upload_type", headers=PRIVATE_HEADERS)
        encoded_name = request.headers.get("x-filename", "")
        if not 0 < len(encoded_name) <= 3060:
            raise InvalidAudio("missing or oversized filename header")
        try:
            name = unquote(encoded_name, errors="strict")
        except UnicodeError as exc:
            raise InvalidAudio("filename is not valid UTF-8") from exc
        job = await request.app.state.jobs.upload(name, request.stream())
        return JSONResponse(
            job, status_code=201,
            headers={**PRIVATE_HEADERS, "Location": f"/api/jobs/{job['id']}"},
        )

    @app.get("/api/jobs")
    async def list_jobs(request: Request) -> JSONResponse:
        jobs = await request.app.state.jobs.list()
        return JSONResponse({"jobs": jobs}, headers=PRIVATE_HEADERS)

    @app.get("/api/jobs/{job_id}")
    async def get_job(request: Request, job_id: str) -> JSONResponse:
        job = await request.app.state.jobs.get(job_id)
        return JSONResponse(job, headers=PRIVATE_HEADERS)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete_job(request: Request, job_id: str) -> Response:
        await request.app.state.jobs.delete(job_id)
        return Response(status_code=204, headers=PRIVATE_HEADERS)

    return app


app = create_app()
