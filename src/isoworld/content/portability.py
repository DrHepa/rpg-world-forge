from __future__ import annotations

import unicodedata
from pathlib import PurePosixPath

WINDOWS_RESERVED_NAMES = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


def is_portable_path_component(value: object) -> bool:
    """Return whether one name is safe as a cross-platform path component."""

    if not isinstance(value, str) or not value or value in {".", ".."}:
        return False
    device_name = value.split(".", 1)[0].casefold()
    return not (
        unicodedata.normalize("NFC", value) != value
        or len(value.encode("utf-8")) > 255
        or value.endswith((" ", "."))
        or any(ord(character) < 32 or character in '<>:"/\\|?*' for character in value)
        or device_name in WINDOWS_RESERVED_NAMES
    )


def portable_relative_path(value: object) -> PurePosixPath | None:
    """Return a canonical portable relative path, or ``None`` when unsafe."""

    if not isinstance(value, str) or not value or "\\" in value:
        return None
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or not relative.parts
        or any(not is_portable_path_component(part) for part in relative.parts)
    ):
        return None
    return relative


def portable_path_key(path: PurePosixPath) -> tuple[str, ...]:
    """Return the NFC/casefold collision key used by cross-platform bundles."""

    return tuple(unicodedata.normalize("NFC", part).casefold() for part in path.parts)
