"""Render the stored ASR and FA result for the app and offline HTML."""

import json
from html import escape
from typing import Literal

from backend.app.storage.jobs import RecordingResult

ExportFormat = Literal["html", "txt", "json"]
EXPORT_MEDIA_TYPES = {
    "html": "text/html",
    "txt": "text/plain",
    "json": "application/json",
}
PUBLIC_RESULT_FIELDS = (
    "schema_version", "time_reference", "time_unit", "sample_rate", "num_samples",
    "language", "empty_transcript", "text", "items", "models", "attempt",
)


def reading_segments(items: list[dict]):
    segment = []
    end = 0
    for item in items:
        if segment and (
            len(segment) >= 32 or item["start"] - segment[0]["start"] >= 15
            or item["start"] - end >= 2
        ):
            yield segment
            segment = []
        end = max(end, item["end"]) if segment else item["end"]
        segment.append(item)
    if segment:
        yield segment


def timestamp(seconds: float) -> str:
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    time = f"{minutes:02}:{seconds:02}"
    return f"{hours}:{time}" if hours else time


def render_word(item: dict) -> str:
    attributes = ""
    if item["end"] > item["start"]:
        attributes = f' data-start="{item["start"]}" data-end="{item["end"]}"'
    return f'<span{attributes}>{escape(item["text"])}</span>'


def render_segments(result: dict, interactive: bool) -> str:
    if result["empty_transcript"]:
        return "<p>전사된 내용이 없습니다.</p>"

    rows = []
    for segment in reading_segments(result["items"]):
        timed = [item for item in segment if item["end"] > item["start"]]
        bounds = ""
        time = '<span class="seek">시간 미확정</span>'
        if timed:
            start = timed[0]["start"]
            end = max(item["end"] for item in timed)
            bounds = f' data-start="{start}" data-end="{end}"'
            label = timestamp(start)
            time = (
                f'<button type="button" class="seek" aria-label="{label} 위치로 이동">{label}</button>'
                if interactive else f'<span class="seek">{label}</span>'
            )
        words = " ".join(render_word(item) for item in segment)
        rows.append(f'<div class="segment"{bounds}>{time}<p class="segment-text">{words}</p></div>')
    return "\n".join(rows)


def render_reader(recording: RecordingResult, *, offline_css: str | None = None) -> str:
    result = recording.result
    job_id = recording.job["id"]
    title = escape(recording.job["name"])
    interactive = offline_css is None
    if interactive:
        head = (
            '<link rel="stylesheet" href="/static/styles.css">'
            '<link rel="stylesheet" href="/static/reader.css">'
            '<script defer src="/static/reader.js"></script>'
        )
        downloads = " ".join(
            f'<a href="/api/jobs/{job_id}/export/{extension}" download>{extension.upper()}</a>'
            for extension in EXPORT_MEDIA_TYPES
        )
        controls = f"""
        <p><a href="/">작업 목록</a></p>
        <h1>{title}</h1>
        <nav class="downloads" aria-label="다운로드">{downloads}</nav>
        <div class="reader-controls">
          <audio controls preload="metadata" aria-label="원본 녹음" src="/api/jobs/{job_id}/audio"></audio>
          <p id="audio-status" role="status" hidden></p>
          <div class="search" hidden>
            <label for="transcript-search">전사 검색</label>
            <input type="search" id="transcript-search" autocomplete="off">
            <p id="search-status" role="status" hidden></p>
          </div>
        </div>"""
    else:
        head = f"<style>{offline_css}</style>"
        controls = f"<h1>{title}</h1>"

    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · VoXtract</title>
{head}
</head>
<body><main class="reader">
{controls}
<div id="transcript" aria-label="시간이 표시된 전사">
{render_segments(result, interactive)}
</div>
<details>
  <summary>ASR 원문</summary>
  <p class="source-text">{escape(result["text"])}</p>
</details>
</main></body></html>"""


def export_result(recording: RecordingResult, extension: ExportFormat, *, css: str = "") -> str:
    if extension == "html":
        return render_reader(recording, offline_css=css)
    if extension == "txt":
        return recording.result["text"]
    return json.dumps(
        {key: recording.result[key] for key in PUBLIC_RESULT_FIELDS},
        ensure_ascii=False,
        allow_nan=False,
    )
