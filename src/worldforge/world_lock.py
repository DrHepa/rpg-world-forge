from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from isoworld.content.file_stat import descriptor_file_stat, path_file_stat


@contextmanager
def exclusive_world_lifecycle(
    project_root: str | Path,
    *,
    error_type: type[ValueError] = ValueError,
) -> Iterator[Path]:
    """Own one world lifecycle snapshot without deleting a replacement lock."""

    root_input = Path(project_root)
    if root_input.is_symlink():
        raise error_type("The world project root cannot be a symbolic link")
    root = root_input.resolve()
    if not root.is_dir():
        raise error_type(f"The world project does not exist: {root}")
    control_root = root / ".worldforge"
    if control_root.is_symlink() or not control_root.is_dir():
        raise error_type("The world project has no safe .worldforge control directory")
    lock_path = control_root / "lifecycle.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise error_type("Another world lifecycle operation is already in progress") from exc
    except OSError as exc:
        raise error_type(f"Could not acquire the world lifecycle lock: {exc}") from exc
    identity = descriptor_file_stat(descriptor)
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        yield root
    finally:
        os.close(descriptor)
        try:
            current = path_file_stat(lock_path)
        except FileNotFoundError:
            current = None
        if current is not None and (current.st_dev, current.st_ino) == (
            identity.st_dev,
            identity.st_ino,
        ):
            lock_path.unlink()
