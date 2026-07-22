from __future__ import annotations

import json
import math
from typing import Any, BinaryIO

from worldforge.studio.errors import StudioError, invalid_request

MAX_NDJSON_LINE_BYTES = 1024 * 1024


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"JSON float overflows: {value}")
    return parsed


def decode_ndjson_object(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise invalid_request(f"Malformed NDJSON request: {exc}") from exc
    if not isinstance(value, dict):
        raise invalid_request("NDJSON request root must be an object")
    return value


def encode_ndjson_object(value: dict[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StudioError("internal_error", "Could not encode Studio response") from exc
    if len(payload) > MAX_NDJSON_LINE_BYTES:
        raise StudioError("internal_error", "Studio response exceeds the NDJSON line limit")
    return payload + b"\n"


def read_ndjson_line(stream: BinaryIO) -> bytes | None:
    chunk = stream.readline(MAX_NDJSON_LINE_BYTES + 2)
    if chunk == b"":
        return None
    content_length = len(chunk) - (1 if chunk.endswith(b"\n") else 0)
    oversized = content_length > MAX_NDJSON_LINE_BYTES or (
        len(chunk) == MAX_NDJSON_LINE_BYTES + 2 and not chunk.endswith(b"\n")
    )
    if oversized:
        while chunk and not chunk.endswith(b"\n"):
            chunk = stream.readline(MAX_NDJSON_LINE_BYTES + 2)
        raise invalid_request(f"NDJSON request exceeds the {MAX_NDJSON_LINE_BYTES}-byte line limit")
    return chunk[:-1] if chunk.endswith(b"\n") else chunk
