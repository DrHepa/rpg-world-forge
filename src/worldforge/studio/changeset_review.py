from __future__ import annotations

import difflib
import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_CHANGESET_DIFF_BYTES = 768 * 1024
MAX_DIFF_JSON_DEPTH = 64
MAX_DIFF_JSON_NODES = 100_000
MAX_DIFF_SEQUENCE_LINES = 20_000
_REVIEW_FIELDS = (
    "path",
    "operation",
    "base_sha256",
    "base_size",
    "proposed_sha256",
    "size",
)
_MISSING = object()


class ReviewDiffError(ValueError):
    """Raised when immutable changeset review evidence is invalid or too large."""


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as exc:
        raise ReviewDiffError(f"Could not encode changeset review evidence: {exc}") from exc


def compute_review_sha256(operations: list[dict[str, Any]]) -> str:
    """Commit to the ordered immutable operation fields of a v2 changeset."""

    try:
        projected = [
            {field: operation[field] for field in _REVIEW_FIELDS} for operation in operations
        ]
    except (KeyError, TypeError) as exc:
        raise ReviewDiffError("Changeset review operations are incomplete") from exc
    descriptor = {
        "format": "rpg-world-forge.studio_changeset_review",
        "format_version": 1,
        "operations": projected,
    }
    return hashlib.sha256(_canonical_json(descriptor)).hexdigest()


def build_changeset_diff(
    record: dict[str, Any],
    snapshots: list[tuple[bytes | None, bytes | None]],
    *,
    max_bytes: int = MAX_CHANGESET_DIFF_BYTES,
) -> dict[str, Any]:
    """Build an exact bounded diff from retained snapshots without workspace reads."""

    operations = record.get("operations")
    if not isinstance(operations, list) or len(operations) != len(snapshots):
        raise ReviewDiffError("Changeset diff snapshots do not match its operations")
    if record.get("format_version") != 2:
        raise ReviewDiffError("Only changeset v2 has immutable diff snapshots")
    review_sha256 = compute_review_sha256(operations)
    if record.get("review_sha256") != review_sha256:
        raise ReviewDiffError("Changeset review hash does not match its operations")
    entries = [
        _operation_diff(operation, base, proposed)
        for operation, (base, proposed) in zip(operations, snapshots, strict=True)
    ]
    result = {
        "changeset_id": record.get("changeset_id"),
        "changeset_format_version": 2,
        "available": True,
        "unavailable_reason": None,
        "review_sha256": review_sha256,
        "operations": entries,
    }
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ReviewDiffError("Changeset diff byte limit is invalid")
    size = len(_canonical_json(result))
    if size > max_bytes:
        raise ReviewDiffError(f"Changeset exact diff exceeds the {max_bytes}-byte limit")
    return result


def unavailable_v1_diff(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "changeset_id": record.get("changeset_id"),
        "changeset_format_version": 1,
        "available": False,
        "unavailable_reason": "legacy_base_bytes_not_retained",
        "review_sha256": None,
        "operations": [],
    }


def _operation_diff(
    operation: dict[str, Any], base: bytes | None, proposed: bytes | None
) -> dict[str, Any]:
    base_text = _decode_snapshot(base, "base")
    proposed_text = _decode_snapshot(proposed, "proposed")
    return {
        **{field: operation[field] for field in _REVIEW_FIELDS},
        "text_hunks": _text_hunks(base_text, proposed_text),
        "json_pointer_changes": _json_pointer_changes(operation["path"], base, proposed),
    }


def _decode_snapshot(payload: bytes | None, side: str) -> str:
    if payload is None:
        return ""
    try:
        return payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ReviewDiffError(f"Changeset {side} snapshot is not UTF-8") from exc


def _text_hunks(base: str, proposed: str) -> list[dict[str, Any]]:
    base_lines = base.splitlines(keepends=True)
    proposed_lines = proposed.splitlines(keepends=True)
    if len(base_lines) + len(proposed_lines) > MAX_DIFF_SEQUENCE_LINES:
        return [_replacement_hunk(base_lines, proposed_lines)]
    matcher = difflib.SequenceMatcher(None, base_lines, proposed_lines, autojunk=False)
    hunks: list[dict[str, Any]] = []
    for group in matcher.get_grouped_opcodes(n=3):
        first = group[0]
        last = group[-1]
        lines: list[dict[str, str]] = []
        for tag, base_start, base_end, proposed_start, proposed_end in group:
            if tag == "equal":
                lines.extend(
                    {"kind": "context", "text": line} for line in base_lines[base_start:base_end]
                )
            else:
                if tag in {"replace", "delete"}:
                    lines.extend(
                        {"kind": "remove", "text": line} for line in base_lines[base_start:base_end]
                    )
                if tag in {"replace", "insert"}:
                    lines.extend(
                        {"kind": "add", "text": line}
                        for line in proposed_lines[proposed_start:proposed_end]
                    )
        hunks.append(
            {
                "base_start": first[1] + 1,
                "base_count": last[2] - first[1],
                "proposed_start": first[3] + 1,
                "proposed_count": last[4] - first[3],
                "lines": lines,
            }
        )
    return hunks


def _replacement_hunk(base_lines: list[str], proposed_lines: list[str]) -> dict[str, Any]:
    return {
        "base_start": 1,
        "base_count": len(base_lines),
        "proposed_start": 1,
        "proposed_count": len(proposed_lines),
        "lines": [
            *({"kind": "remove", "text": line} for line in base_lines),
            *({"kind": "add", "text": line} for line in proposed_lines),
        ],
    }


def _json_pointer_changes(
    path: object, base: bytes | None, proposed: bytes | None
) -> list[dict[str, Any]] | None:
    if not isinstance(path, str) or not path.casefold().endswith(".json"):
        return None
    try:
        base_value = _MISSING if base is None else _decode_strict_json(base)
        proposed_value = _MISSING if proposed is None else _decode_strict_json(proposed)
    except (ReviewDiffError, RecursionError):
        return None
    changes: list[dict[str, Any]] = []
    _compare_json(base_value, proposed_value, "", changes, depth=0)
    return changes


def _decode_strict_json(payload: bytes) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON object key: {key!r}")
            value[key] = item
        return value

    def finite_decimal(value: str) -> Decimal:
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:  # pragma: no cover - json syntax is validated first
            raise ValueError("invalid JSON number") from exc
        if not parsed.is_finite():
            raise ValueError("non-finite JSON number")
        return parsed

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
            parse_float=finite_decimal,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReviewDiffError(f"Snapshot is not strict JSON: {exc}") from exc
    _validate_json_bounds(value)
    return value


def _validate_json_bounds(value: Any) -> None:
    pending: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > MAX_DIFF_JSON_NODES or depth > MAX_DIFF_JSON_DEPTH:
            raise ReviewDiffError("Strict JSON diff exceeds its structural bounds")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def _compare_json(
    base: Any,
    proposed: Any,
    pointer: str,
    output: list[dict[str, Any]],
    *,
    depth: int,
) -> None:
    if depth > MAX_DIFF_JSON_DEPTH:
        raise ReviewDiffError("Strict JSON diff exceeds its depth bound")
    if base is _MISSING:
        output.append({"operation": "add", "pointer": pointer, "value": _json_diff_value(proposed)})
        return
    if proposed is _MISSING:
        output.append(
            {"operation": "remove", "pointer": pointer, "old_value": _json_diff_value(base)}
        )
        return
    if _is_json_number(base) and _is_json_number(proposed):
        if Decimal(base) != Decimal(proposed):
            output.append(
                {
                    "operation": "replace",
                    "pointer": pointer,
                    "old_value": _json_diff_value(base),
                    "value": _json_diff_value(proposed),
                }
            )
        return
    if type(base) is not type(proposed):
        output.append(
            {
                "operation": "replace",
                "pointer": pointer,
                "old_value": _json_diff_value(base),
                "value": _json_diff_value(proposed),
            }
        )
        return
    if isinstance(base, dict):
        base_keys = set(base)
        proposed_keys = set(proposed)
        for key in sorted(base_keys - proposed_keys):
            _compare_json(base[key], _MISSING, _join_pointer(pointer, key), output, depth=depth + 1)
        for key in sorted(base_keys & proposed_keys):
            _compare_json(
                base[key], proposed[key], _join_pointer(pointer, key), output, depth=depth + 1
            )
        for key in sorted(proposed_keys - base_keys):
            _compare_json(
                _MISSING, proposed[key], _join_pointer(pointer, key), output, depth=depth + 1
            )
        return
    if isinstance(base, list):
        shared = min(len(base), len(proposed))
        for index in range(shared):
            _compare_json(
                base[index],
                proposed[index],
                _join_pointer(pointer, str(index)),
                output,
                depth=depth + 1,
            )
        for index in range(len(base) - 1, shared - 1, -1):
            _compare_json(
                base[index], _MISSING, _join_pointer(pointer, str(index)), output, depth=depth + 1
            )
        for index in range(shared, len(proposed)):
            _compare_json(
                _MISSING,
                proposed[index],
                _join_pointer(pointer, str(index)),
                output,
                depth=depth + 1,
            )
        return
    if base != proposed:
        output.append(
            {
                "operation": "replace",
                "pointer": pointer,
                "old_value": _json_diff_value(base),
                "value": _json_diff_value(proposed),
            }
        )


def _is_json_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | Decimal)


def _json_diff_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"json_number": _canonical_decimal(value)}
    if isinstance(value, list):
        return [_json_diff_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_diff_value(item) for key, item in value.items()}
    return value


def _canonical_decimal(value: Decimal) -> str:
    if value.is_zero():
        return "0"
    sign, digits_tuple, exponent = value.as_tuple()
    digits = list(digits_tuple)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    exact = Decimal((sign, tuple(digits), exponent))
    text = str(exact)
    if "E" not in text:
        return text
    coefficient, raw_exponent = text.split("E", 1)
    return f"{coefficient}e{int(raw_exponent)}"


def _join_pointer(pointer: str, component: str) -> str:
    escaped = component.replace("~", "~0").replace("/", "~1")
    return f"{pointer}/{escaped}"


__all__ = [
    "MAX_CHANGESET_DIFF_BYTES",
    "ReviewDiffError",
    "build_changeset_diff",
    "compute_review_sha256",
    "unavailable_v1_diff",
]
