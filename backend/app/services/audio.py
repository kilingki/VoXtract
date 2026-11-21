"""Verify uploaded audio with local media tools and bounded memory."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fractions import Fraction
from pathlib import Path

MAX_DURATION_SECONDS = 7200
WAV_CODECS = {
    "pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le",
}
PROBE_LIMIT = 64 * 1024
AAC_FRAME_SAMPLES = 2048


class InvalidAudio(Exception):
    pass


class AudioTooLong(InvalidAudio):
    pass


class MediaToolUnavailable(Exception):
    pass


@asynccontextmanager
async def media_process(*args: str, timeout: int) -> AsyncIterator[asyncio.subprocess.Process]:
    try:
        async with asyncio.timeout(timeout):
            try:
                process = await asyncio.create_subprocess_exec(
                    *args,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except FileNotFoundError as exc:
                raise MediaToolUnavailable from exc
            try:
                yield process
                if await process.wait():
                    raise InvalidAudio(f"{args[0]} rejected the input")
            finally:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.communicate()
    except TimeoutError as exc:
        raise InvalidAudio(f"{args[0]} timed out") from exc


def media_input(path: Path, suffix: str) -> tuple[str, ...]:
    if suffix == ".m4a":
        input_args = ("-f", "mov", "-enable_drefs", "0", "-use_absolute_path", "0")
    else:
        input_args = ("-f", "wav")
    return (*input_args, "-protocol_whitelist", "file", "-i", str(path))


async def validate_audio(path: Path, suffix: str) -> float:
    input_args = media_input(path, suffix)
    codecs = {"aac"} if suffix == ".m4a" else WAV_CODECS
    async with media_process(
        "ffprobe", "-v", "error", *input_args, "-show_entries",
        "stream=codec_type,codec_name,sample_rate,duration_ts,time_base",
        "-of", "json", timeout=30,
    ) as probe:
        metadata = bytearray()
        while chunk := await probe.stdout.read(8192):
            metadata.extend(chunk)
            if len(metadata) > PROBE_LIMIT:
                raise InvalidAudio("probe output exceeded limit")

    try:
        streams = json.loads(metadata)["streams"]
        if len(streams) != 1:
            raise InvalidAudio("expected exactly one media stream")
        stream = streams[0]
        if stream["codec_type"] != "audio":
            raise InvalidAudio("stream is not audio")
        if stream["codec_name"] not in codecs:
            raise InvalidAudio("unsupported audio codec")
        sample_rate = int(stream["sample_rate"])
        if sample_rate <= 0:
            raise InvalidAudio("invalid audio sample rate")
        track_duration = None
        if suffix == ".m4a":
            track_duration = int(stream["duration_ts"]) * Fraction(stream["time_base"])
            if track_duration <= 0:
                raise InvalidAudio("nonpositive AAC track duration")
            if track_duration > MAX_DURATION_SECONDS:
                raise AudioTooLong("AAC track duration exceeded two hours")
    except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise InvalidAudio("could not read audio stream metadata") from exc

    max_samples = MAX_DURATION_SECONDS * sample_rate
    if track_duration is not None:
        # Only allow extra decoded samples when the track ends within one frame
        # of the limit and the extra samples can be final AAC padding.
        max_samples = max(max_samples, int(track_duration * sample_rate) + AAC_FRAME_SAMPLES)
    limit = max_samples * 2
    decoded_bytes = 0
    async with media_process(
        "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
        *input_args, "-map", "0:a:0", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
        "-f", "s16le", "pipe:1", timeout=300,
    ) as decoder:
        while chunk := await decoder.stdout.read(65536):
            decoded_bytes += len(chunk)
            if decoded_bytes > limit:
                raise AudioTooLong("decoded duration exceeded two hours")
    if not decoded_bytes or decoded_bytes % 2:
        raise InvalidAudio("decoder produced empty or incomplete audio")
    decoded_duration = Fraction(decoded_bytes, 2 * sample_rate)
    if track_duration is None:
        return float(decoded_duration)
    return float(track_duration)
