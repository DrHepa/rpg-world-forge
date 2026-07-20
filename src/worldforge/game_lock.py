from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class GameMutationLockError(ValueError):
    """Raised when an external game mutation cannot acquire exclusive ownership."""


@contextmanager
def exclusive_game_mutation(root: Path, operation: str) -> Iterator[None]:
    """Serialize Forge-owned runtime and bundle mutations for one game repository."""

    lock_path = root / ".isoworld-mutation.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise GameMutationLockError(
            "Another Forge-owned game mutation is already in progress"
        ) from exc
    except OSError as exc:
        raise GameMutationLockError(f"Could not acquire the game mutation lock: {exc}") from exc
    identity = os.fstat(descriptor)
    try:
        os.write(descriptor, f"pid={os.getpid()} operation={operation}\n".encode())
        yield
    finally:
        os.close(descriptor)
        try:
            current = lock_path.lstat()
        except FileNotFoundError:
            current = None
        if current is not None and (current.st_dev, current.st_ino) == (
            identity.st_dev,
            identity.st_ino,
        ):
            lock_path.unlink()
