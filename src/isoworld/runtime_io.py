from __future__ import annotations

import json
import math
import os
import stat
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 16 * 1024 * 1024


class RuntimeIOError(ValueError):
    """Raised when runtime input violates its bounded file contract."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def read_json_object(
    path: str | Path,
    *,
    limit: int = MAX_JSON_BYTES,
) -> dict[str, Any]:
    """Read one bounded UTF-8 JSON object without accepting ambiguous numbers or keys."""

    source = Path(path)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("not a standalone regular file")
        if info.st_size > limit:
            raise OSError(f"exceeds the {limit}-byte limit")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            payload = stream.read(limit + 1)
        if len(payload) > limit:
            raise OSError(f"exceeds the {limit}-byte limit")
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeIOError(f"Could not read {source}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise RuntimeIOError(f"{source} must contain a JSON object")
    return value
