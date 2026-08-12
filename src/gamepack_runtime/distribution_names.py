"""Pure Python distribution-name normalization for standalone boundary checks."""

from __future__ import annotations

import re

_DISTRIBUTION_NAME = re.compile(r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)")


def normalize_distribution_name(value: str) -> str:
    """Return the PEP 503 comparison key for a distribution name."""

    return re.sub(r"[-_.]+", "-", value).casefold()


def requirement_distribution_name(requirement: str) -> str | None:
    """Extract and normalize the leading PEP 508 distribution name."""

    match = _DISTRIBUTION_NAME.match(requirement.strip())
    if match is None:
        return None
    return normalize_distribution_name(match.group(1))


__all__ = ["normalize_distribution_name", "requirement_distribution_name"]
