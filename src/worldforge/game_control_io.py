"""Atomic game control-file writes without persistent lock sidecars."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from isoworld.content.file_stat import descriptor_file_stat, path_file_stat
from isoworld.content.resource_snapshot import note_cleanup_failure
from worldforge.integrity import canonical_json_bytes


class GameControlIOError(ValueError):
    """Raised when a game control file cannot be published safely."""


def _close_descriptor(descriptor: int, *, context: str) -> None:
    primary = sys.exception()
    try:
        os.close(descriptor)
    except OSError as cleanup_error:
        if not note_cleanup_failure(primary, cleanup_error, context=context):
            raise GameControlIOError(f"{context} failed: {cleanup_error}") from cleanup_error


def write_game_control_json(path: Path, value: object) -> tuple[int, int]:
    """Publish one canonical JSON control file without replacing any name."""

    parent = path.parent
    parent_info = path_file_stat(parent)
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise GameControlIOError(f"game control parent is unsafe: {parent}")
    parent_identity = (parent_info.st_dev, parent_info.st_ino)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        opened = descriptor_file_stat(descriptor)
        identity = (opened.st_dev, opened.st_ino)
        payload = canonical_json_bytes(value)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short game control write")
            view = view[written:]
        os.fsync(descriptor)
        sealed = descriptor_file_stat(descriptor)
        if (
            not stat.S_ISREG(sealed.st_mode)
            or sealed.st_nlink != 1
            or (sealed.st_dev, sealed.st_ino) != identity
            or sealed.st_size != len(payload)
        ):
            raise GameControlIOError("new game control file changed while writing")
        _close_descriptor(descriptor, context="game control descriptor cleanup")
        descriptor = None
        current_parent = path_file_stat(parent)
        if (current_parent.st_dev, current_parent.st_ino) != parent_identity:
            raise GameControlIOError("game control parent changed during publication")
        published = path_file_stat(path)
        if (
            not stat.S_ISREG(published.st_mode)
            or published.st_nlink != 1
            or (published.st_dev, published.st_ino) != identity
            or published.st_size != len(payload)
        ):
            raise GameControlIOError("published game control identity changed")
        if os.name == "posix":
            parent_descriptor = os.open(
                parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(parent_descriptor)
            finally:
                _close_descriptor(
                    parent_descriptor,
                    context="game control parent descriptor cleanup",
                )
        return identity
    except GameControlIOError:
        raise
    except OSError as exc:
        raise GameControlIOError(f"could not publish game control file: {exc}") from exc
    finally:
        if descriptor is not None:
            _close_descriptor(
                descriptor,
                context="game control descriptor cleanup",
            )
        # An uncertain publication is retained. Deleting by name could remove
        # a foreign replacement, so later exact verification fails closed.


__all__ = [
    "GameControlIOError",
    "write_game_control_json",
]
