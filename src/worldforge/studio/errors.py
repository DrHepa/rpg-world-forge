from __future__ import annotations

from typing import Any

ERROR_CODES = frozenset(
    {"invalid_request", "not_found", "conflict", "invalid_state", "internal_error"}
)


class StudioContractError(ValueError):
    """Raised when a public Studio document violates its closed contract."""


class StudioError(Exception):
    """Structured application error safe to return over the Studio protocol."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"Unknown Studio error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def invalid_request(message: str, **details: Any) -> StudioError:
    return StudioError("invalid_request", message, details=details)


def not_found(message: str, **details: Any) -> StudioError:
    return StudioError("not_found", message, details=details)


def conflict(message: str, **details: Any) -> StudioError:
    return StudioError("conflict", message, details=details)


def invalid_state(message: str, **details: Any) -> StudioError:
    return StudioError("invalid_state", message, details=details)
