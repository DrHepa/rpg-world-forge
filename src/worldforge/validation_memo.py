from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TypeVar

from worldforge.integrity import canonical_json_bytes

_MAX_VALIDATED_DOCUMENTS = 256
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_T = TypeVar("_T")
_DocumentKey = tuple[str, int, str, str]
_MemoKey = tuple[str, tuple[_DocumentKey, ...]]


@dataclass(slots=True)
class _RequestValidationMemo:
    values: dict[_MemoKey, object] = field(default_factory=dict)

    def get(self, key: _MemoKey) -> object | None:
        value = self.values.get(key)
        return None if value is None else copy.deepcopy(value)

    def put(self, key: _MemoKey, value: object) -> None:
        if len(self.values) < _MAX_VALIDATED_DOCUMENTS:
            self.values[key] = copy.deepcopy(value)


_CURRENT_MEMO: ContextVar[_RequestValidationMemo | None] = ContextVar(
    "worldforge_request_validation_memo",
    default=None,
)


def _document_key(value: object) -> _DocumentKey | None:
    if not isinstance(value, Mapping):
        return None
    format_name = value.get("format")
    format_version = value.get("format_version")
    content_hash = value.get("content_hash")
    if (
        not isinstance(format_name, str)
        or not format_name
        or isinstance(format_version, bool)
        or not isinstance(format_version, int)
        or format_version < 1
        or not isinstance(content_hash, str)
        or _SHA256_PATTERN.fullmatch(content_hash) is None
    ):
        return None
    try:
        serialized_hash = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except (TypeError, ValueError, RecursionError, UnicodeError):
        return None
    return format_name, format_version, content_hash, serialized_hash


@contextmanager
def validation_memo_scope() -> Iterator[None]:
    """Share bounded pure-document validation only within one explicit request."""

    if _CURRENT_MEMO.get() is not None:
        yield
        return
    token = _CURRENT_MEMO.set(_RequestValidationMemo())
    try:
        yield
    finally:
        _CURRENT_MEMO.reset(token)


def memoize_document_validation(
    operation: str,
    value: object,
    validator: Callable[[object], _T],
    *,
    dependencies: Sequence[object] = (),
) -> _T:
    """Reuse an exact pure result while detecting same-hash object mutation."""

    memo = _CURRENT_MEMO.get()
    document_key = _document_key(value)
    dependency_keys = tuple(_document_key(dependency) for dependency in dependencies)
    if memo is None or document_key is None or any(key is None for key in dependency_keys):
        return validator(value)
    exact_dependency_keys = tuple(key for key in dependency_keys if key is not None)
    key = operation, (document_key, *exact_dependency_keys)
    cached = memo.get(key)
    if cached is not None:
        return cached  # type: ignore[return-value]
    checked = validator(value)
    if _document_key(checked) == document_key:
        memo.put(key, checked)
    return checked


__all__ = ["memoize_document_validation", "validation_memo_scope"]
