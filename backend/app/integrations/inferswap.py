"""InferSwap's synchronous HTTP contract and Qwen response validation."""

import asyncio
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import quote, urlsplit
from uuid import uuid4

import h11

SAMPLE_RATE = 16_000
FA_CHUNK_MAX = 180 * SAMPLE_RATE
MAX_RESPONSE_BYTES = 16 * 1024**2
BLOCK_BYTES = 64 * 1024


class ContractError(ValueError):
    pass


class UnconfirmedRequest(Exception):
    """A request may still be running at the backend."""


class EndpointUnavailable(Exception):
    """The HTTP connection failed before any request was sent."""


@dataclass(frozen=True)
class InferSwap:
    url: str
    asr_model: str = "qwen-asr"
    asr_served_name: str = "qwen3-asr"
    fa_model: str = "qwen-fa"
    timeout: float = 7200

    def __post_init__(self):
        endpoint = urlsplit(self.url)
        if (
            endpoint.scheme not in {"http", "https"} or not endpoint.hostname
            or endpoint.username is not None or endpoint.password is not None
            or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment
            or not math.isfinite(self.timeout) or self.timeout <= 0
            or not all(value.strip() for value in (self.asr_model, self.asr_served_name, self.fa_model))
        ):
            raise ValueError("Invalid InferSwap origin or request timeout")
        endpoint.port  # Reject invalid explicit ports before the worker starts.

    async def infer(
        self, stage: str, audio_size: int, audio: Iterable[bytes], payload: bytes,
    ) -> tuple[int, bytes]:
        boundary = uuid4().hex.encode("ascii")
        if stage == "asr":
            model = self.asr_model
            fields = {
                "model": self.asr_served_name.encode("utf-8"),
                "response_format": b"verbose_json",
                "include_chunks": b"true",
                "language": b"ko",
            }
        else:
            model = self.fa_model
            fields = {"payload": payload}

        prefix = b"".join(
            b"--" + boundary + b'\r\nContent-Disposition: form-data; name="'
            + key.encode("ascii") + b'"\r\n\r\n' + value + b"\r\n"
            for key, value in fields.items()
        )
        prefix += (
            b"--" + boundary
            + b'\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"'
            + b"\r\nContent-Type: audio/wav\r\n\r\n"
        )
        suffix = b"\r\n--" + boundary + b"--\r\n"
        endpoint = urlsplit(self.url)
        tls = endpoint.scheme == "https"
        protocol = h11.Connection(h11.CLIENT, max_incomplete_event_size=64 * 1024)

        connected = False
        content_length = len(prefix) + audio_size + len(suffix)
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await asyncio.open_connection(
                    endpoint.hostname, endpoint.port or (443 if tls else 80), ssl=tls
                )
                connected = True
                try:
                    writer.write(protocol.send(h11.Request(
                        method="POST",
                        target=f"/infer?model={quote(model, safe='')}",
                        headers=[
                            ("Host", endpoint.netloc),
                            ("Connection", "close"),
                            ("Content-Type", b"multipart/form-data; boundary=" + boundary),
                            ("Content-Length", str(content_length)),
                        ],
                    )))
                    writer.write(protocol.send(h11.Data(data=prefix)))
                    for block in audio:
                        writer.write(protocol.send(h11.Data(data=block)))
                        await writer.drain()
                    writer.write(protocol.send(h11.Data(data=suffix)))
                    writer.write(protocol.send(h11.EndOfMessage()))
                    await writer.drain()
                    return await read_response(reader, protocol)
                finally:
                    writer.transport.abort()
        except (OSError, h11.ProtocolError) as exc:
            if not connected:
                raise EndpointUnavailable from exc
            raise UnconfirmedRequest from exc


async def read_response(reader: asyncio.StreamReader, protocol: h11.Connection) -> tuple[int, bytes]:
    status = None
    body = bytearray()
    while True:
        event = protocol.next_event()
        if event is h11.NEED_DATA:
            protocol.receive_data(await reader.read(BLOCK_BYTES))
        elif isinstance(event, h11.Response):
            status = event.status_code
        elif isinstance(event, h11.Data):
            body.extend(event.data)
            if len(body) > MAX_RESPONSE_BYTES:
                raise UnconfirmedRequest
        elif isinstance(event, h11.EndOfMessage) and status is not None:
            return status, bytes(body)
        elif not isinstance(event, h11.InformationalResponse):
            raise UnconfirmedRequest


def _integer(value, expected: int | None = None) -> bool:
    return type(value) is int and (expected is None or value == expected)


def _audio(response: dict, frames: int) -> None:
    audio = response["audio"]
    if not (
        _integer(audio["sample_rate"], SAMPLE_RATE)
        and _integer(audio["channels"], 1)
        and _integer(audio["num_samples"], frames)
    ):
        raise ContractError("bad audio metadata")


def parse_asr(raw: bytes, frames: int) -> dict:
    try:
        response = json.loads(raw)
        _audio(response, frames)
        chunks = response["chunks"]
        if not isinstance(chunks, list) or not chunks:
            raise ContractError("missing ASR chunks")
        cursor = 0
        indexes = set()
        for chunk in chunks:
            index, start, end = (chunk[key] for key in ("index", "start_sample", "end_sample"))
            if (
                not all(_integer(value) for value in (index, start, end))
                or index < 0 or index in indexes or not 0 <= start <= cursor < end <= frames
                or end - start > FA_CHUNK_MAX or not isinstance(chunk["text"], str)
                or (chunk["language"] is not None and not isinstance(chunk["language"], str))
                or (chunk["text"].strip() and not chunk["language"])
            ):
                raise ContractError("bad ASR chunk")
            indexes.add(index)
            cursor = end
        if (
            cursor != frames or not isinstance(response["text"], str)
            or (response["language"] is not None and not isinstance(response["language"], str))
        ):
            raise ContractError("bad ASR transcript")
        return {key: response[key] for key in ("text", "language", "chunks")}
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise ContractError("invalid ASR response") from exc


def parse_fa(raw: bytes, frames: int, transcript: dict, offset: int) -> list[dict]:
    try:
        response = json.loads(raw)
        _audio(response, frames)
        chunks = response["chunks"]
        if (
            response["time_reference"] != "audio_start"
            or response["overlap_deduplicated"] is not False
            or not isinstance(chunks, list) or len(chunks) != len(transcript["chunks"])
        ):
            raise ContractError("bad FA chunk list or time reference")
        items = []
        for asr, fa in zip(transcript["chunks"], chunks, strict=True):
            for key in ("index", "start_sample", "end_sample"):
                if not _integer(fa[key], asr[key]):
                    raise ContractError("FA chunk does not match ASR")
            text = fa["text"]
            if text != asr["text"]:
                raise ContractError("FA chunk text changed")
            if not isinstance(fa["items"], list):
                raise ContractError("missing FA items")
            if not text.strip():
                if fa["status"] != "skipped_empty_text" or fa["items"] or fa["language"] is not None:
                    raise ContractError("bad empty FA chunk")
                continue
            if (
                fa["status"] != "aligned" or not fa["items"]
                or not isinstance(fa["language"], str) or not fa["language"].strip()
            ):
                raise ContractError("nonempty chunk did not align")
            lower = asr["start_sample"] / SAMPLE_RATE
            # The runtime computes duration + offset in two floating point operations.
            upper = lower + (asr["end_sample"] - asr["start_sample"]) / SAMPLE_RATE
            previous = lower
            for item in fa["items"]:
                start, end = item["start_time"], item["end_time"]
                if (
                    not isinstance(item["text"], str) or not item["text"].strip()
                    or type(start) not in (int, float) or type(end) not in (int, float)
                    or not previous <= start <= end <= upper
                ):
                    raise ContractError("bad or unordered FA item")
                previous = start
                items.append({
                    "text": item["text"],
                    "start": start + offset / SAMPLE_RATE,
                    "end": end + offset / SAMPLE_RATE,
                    "chunk": fa["index"],
                    "overlap_start": lower + offset / SAMPLE_RATE,
                    "overlap_end": upper + offset / SAMPLE_RATE,
                })
        return items
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise ContractError("invalid FA response") from exc
