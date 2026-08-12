"""Strict bounded JSON ingestion for immutable gamepack bytes."""

from __future__ import annotations

import json
import unicodedata

from gamepack_runtime.contracts import (
    MAX_GAMEPACK_BYTES,
    MAX_SAFE_INTEGER,
    GameLogicError,
    validate_runtime_gamepack,
)

# Diagnostic labels are bounded independently from the gamepack payload.  The
# 4,096-codepoint limit accommodates a full portable path while capping every
# subsequent Unicode and UTF-8 validation pass.
MAX_SOURCE_LABEL_CODEPOINTS = 4_096


def _duplicate_safe_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise GameLogicError("json_duplicate_key", f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _strict_integer(lexeme: str) -> int:
    try:
        value = int(lexeme)
    except (ValueError, OverflowError) as exc:
        raise GameLogicError("json_integer_unsupported", "JSON integer is invalid") from exc
    if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
        raise GameLogicError(
            "json_integer_unsupported",
            "JSON integer is outside the JavaScript-safe range",
        )
    return value


def _reject_float(_lexeme: str) -> float:
    raise GameLogicError(
        "json_float_unsupported",
        "decimal and exponent JSON numbers are unsupported",
    )


def _reject_non_finite(_lexeme: str) -> float:
    raise GameLogicError("json_non_finite", "non-finite JSON numbers are unsupported")


def load_gamepack_bytes(
    payload: bytes,
    *,
    source: str = "<gamepack bytes>",
) -> dict[str, object]:
    """Parse and validate one complete immutable gamepack byte sequence."""

    if type(payload) is not bytes:
        raise GameLogicError("json_bytes_invalid", "gamepack input must be exact bytes")
    if type(source) is not str or not source or len(source) > MAX_SOURCE_LABEL_CODEPOINTS:
        raise GameLogicError(
            "json_source_invalid",
            f"gamepack source must be an exact NFC scalar string of at most "
            f"{MAX_SOURCE_LABEL_CODEPOINTS} codepoints",
        )
    for character in source:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF or unicodedata.category(character) == "Cc":
            raise GameLogicError(
                "json_source_invalid",
                "gamepack source contains a surrogate or control character",
            )
    try:
        if unicodedata.normalize("NFC", source) != source:
            raise GameLogicError(
                "json_source_invalid",
                "gamepack source must use NFC normalization",
            )
        source.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise GameLogicError(
            "json_source_invalid",
            "gamepack source is not valid UTF-8",
        ) from exc
    if len(payload) > MAX_GAMEPACK_BYTES:
        raise GameLogicError(
            "gamepack_bytes_exceeded",
            f"{source} exceeds {MAX_GAMEPACK_BYTES} bytes",
        )
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise GameLogicError("json_utf8_invalid", f"{source} is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_safe_object,
            parse_int=_strict_integer,
            parse_float=_reject_float,
            parse_constant=_reject_non_finite,
        )
    except GameLogicError:
        raise
    except (
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        raise GameLogicError("json_syntax_invalid", f"{source}: {exc}") from exc
    if type(value) is not dict:
        raise GameLogicError("json_root_invalid", f"{source} must contain a JSON object")
    return validate_runtime_gamepack(value)
