"""HTTP entry point for the local recording queue."""

import errno
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from backend.app.services.audio import AudioTooLong, InvalidAudio, MediaToolUnavailable
from backend.app.services.inference import (
    InferenceUnavailable, JobNotHeld, JobNotRunnable, Runner, public_job,
)
from backend.app.services.jobs import Jobs, UploadBusy
from backend.app.services.results import EXPORT_MEDIA_TYPES, ExportFormat, export_result, render_reader
from backend.app.storage.jobs import JobNotDeletable, JobNotFound, JobResultUnavailable, JobStore

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
    JobResultUnavailable: (409, "job_result_unavailable"),
    JobNotRunnable: (409, "job_not_runnable"),
    JobNotHeld: (409, "job_not_held"),
    InferenceUnavailable: (503, "inference_unavailable"),
    ClientDisconnect: (400, "upload_disconnected"),
}


class IdleConfirmation(BaseModel):
    runtime_idle_confirmed: Literal[True]


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
            app.state.runner = Runner(store, app.state.jobs.queue_lock)
            async with app.state.runner.open():
                yield

    offline_css = "\n".join(
        (FRONTEND_DIR / name).read_text(encoding="utf-8") for name in ("styles.css", "reader.css")
    )
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

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "manifest.webmanifest", media_type="application/manifest+json")

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
        return JSONResponse({
            "jobs": [public_job(job) for job in jobs],
            "inference_enabled": request.app.state.runner.enabled,
            "inference_held": any(
                job.get("external_pending") and job["status"] in {"failed", "interrupted"}
                for job in jobs
            ),
        }, headers=PRIVATE_HEADERS)

    @app.get("/api/jobs/{job_id}")
    async def get_job(request: Request, job_id: str) -> JSONResponse:
        job = await request.app.state.jobs.get(job_id)
        return JSONResponse(public_job(job), headers=PRIVATE_HEADERS)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def read_transcript(request: Request, job_id: str) -> HTMLResponse:
        recording = await request.app.state.jobs.result(job_id)
        html = await run_in_threadpool(render_reader, recording)
        return HTMLResponse(html, headers=PRIVATE_HEADERS)

    @app.head("/api/jobs/{job_id}/audio", include_in_schema=False)
    @app.get("/api/jobs/{job_id}/audio", response_class=FileResponse)
    async def get_audio(request: Request, job_id: str) -> FileResponse:
        path, media_type, stat = await request.app.state.jobs.audio(job_id)
        return FileResponse(path, media_type=media_type, stat_result=stat, headers=PRIVATE_HEADERS)

    @app.get("/api/jobs/{job_id}/export/{extension}")
    async def download_result(request: Request, job_id: str, extension: ExportFormat) -> Response:
        recording = await request.app.state.jobs.result(job_id)
        name = quote(f"{Path(recording.job['name']).stem}.{extension}", safe="")
        css = offline_css if extension == "html" else ""
        content = await run_in_threadpool(export_result, recording, extension, css=css)
        return Response(
            content,
            media_type=EXPORT_MEDIA_TYPES[extension],
            headers={
                **PRIVATE_HEADERS,
                "Content-Disposition": f"attachment; filename=voxtract.{extension}; filename*=UTF-8''{name}",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/jobs/{job_id}/run", status_code=202)
    async def run_job(request: Request, job_id: str) -> JSONResponse:
        media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
        if media_type != "application/json":
            raise HTTPException(415, "unsupported_request_type", headers=PRIVATE_HEADERS)
        job = await request.app.state.runner.request(job_id)
        return JSONResponse(public_job(job), status_code=202, headers=PRIVATE_HEADERS)

    @app.post("/api/jobs/{job_id}/resolve")
    async def resolve_job(request: Request, job_id: str, _confirmation: IdleConfirmation) -> JSONResponse:
        job = await request.app.state.runner.resolve(job_id)
        return JSONResponse(public_job(job), headers=PRIVATE_HEADERS)

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete_job(request: Request, job_id: str) -> Response:
        await request.app.state.jobs.delete(job_id)
        return Response(status_code=204, headers=PRIVATE_HEADERS)

    return app


app = create_app()
