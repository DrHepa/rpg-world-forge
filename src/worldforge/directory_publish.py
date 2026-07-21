"""Native exclusive directory publication for supported desktop platforms."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

DirectoryIdentity = tuple[int, int]


class DirectoryPublishError(OSError):
    """Raised when a directory cannot be published without replacement."""


def directory_identity(path: Path, *, context: str) -> DirectoryIdentity:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DirectoryPublishError(f"Could not inspect {context} {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DirectoryPublishError(f"{context} must be a real directory: {path}")
    return info.st_dev, info.st_ino


def _linux_rename_noreplace(source: Path, destination: Path) -> None:
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as exc:
        raise DirectoryPublishError(
            "Safe exclusive directory publication is unavailable on this Linux system"
        ) from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if (
        renameat2(
            -100,  # AT_FDCWD
            os.fsencode(source),
            -100,
            os.fsencode(destination),
            1,  # RENAME_NOREPLACE
        )
        == 0
    ):
        return
    error = ctypes.get_errno()
    if error in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error, "destination already exists", destination)
    unsupported = {errno.EINVAL, errno.ENOSYS}
    if hasattr(errno, "ENOTSUP"):
        unsupported.add(errno.ENOTSUP)
    if hasattr(errno, "EOPNOTSUPP"):
        unsupported.add(errno.EOPNOTSUPP)
    if error in unsupported:
        raise DirectoryPublishError(
            "Safe exclusive directory publication is unsupported by this Linux filesystem"
        )
    raise DirectoryPublishError(error, os.strerror(error), destination)


def _windows_rename_noreplace(source: Path, destination: Path) -> None:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise DirectoryPublishError(
            "Safe exclusive directory publication is unavailable on this Windows system"
        )
    kernel32 = win_dll("kernel32", use_last_error=True)
    move_file = kernel32.MoveFileExW
    move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move_file.restype = ctypes.c_int
    movefile_write_through = 0x00000008
    if move_file(str(source), str(destination), movefile_write_through):
        return
    get_last_error = getattr(ctypes, "get_last_error", None)
    error = get_last_error() if get_last_error is not None else 0
    if error in {80, 183}:  # ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS
        raise FileExistsError(error, "destination already exists", destination)
    formatter = getattr(ctypes, "FormatError", None)
    detail = formatter(error) if formatter is not None else f"Windows error {error}"
    raise DirectoryPublishError(error, detail, destination)


def publish_directory_noreplace(source: Path, destination: Path) -> DirectoryIdentity:
    """Atomically move a directory to an absent destination or fail closed."""

    source_identity = directory_identity(source, context="publication source")
    if source.parent != destination.parent:
        raise DirectoryPublishError("Directory publication must stay within one parent")
    parent_identity = directory_identity(source.parent, context="publication parent")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(errno.EEXIST, "destination already exists", destination)

    if sys.platform.startswith("linux") and os.name == "posix":
        _linux_rename_noreplace(source, destination)
    elif os.name == "nt":
        _windows_rename_noreplace(source, destination)
    else:
        raise DirectoryPublishError(
            "Safe exclusive directory publication is supported only on Linux and Windows"
        )

    published_identity = directory_identity(destination, context="published directory")
    if published_identity != source_identity:
        raise DirectoryPublishError("Published directory identity changed unexpectedly")
    if directory_identity(destination.parent, context="publication parent") != parent_identity:
        raise DirectoryPublishError("Publication parent identity changed unexpectedly")
    return published_identity


def quarantine_and_remove_owned_directory(
    path: Path,
    expected_identity: DirectoryIdentity,
    *,
    verify: Callable[[Path], None],
) -> None:
    """Remove only a verified owned directory after atomically quarantining it."""

    if directory_identity(path, context="rollback directory") != expected_identity:
        raise DirectoryPublishError("Rollback directory identity no longer matches its journal")
    verify(path)
    quarantine = path.parent / f".{path.name}.rollback-{uuid.uuid4().hex}"
    moved_identity = publish_directory_noreplace(path, quarantine)
    if moved_identity != expected_identity:
        raise DirectoryPublishError("Quarantined directory identity no longer matches its journal")
    verify(quarantine)
    if directory_identity(quarantine, context="quarantined directory") != expected_identity:
        raise DirectoryPublishError("Quarantined directory changed before rollback")
    shutil.rmtree(quarantine)


def remove_owned_empty_directory(path: Path, expected_identity: DirectoryIdentity) -> None:
    """Remove an empty directory only while it retains the recorded identity."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DirectoryPublishError(f"Could not inspect created directory {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DirectoryPublishError("Created directory is no longer a real directory")
    if (info.st_dev, info.st_ino) != expected_identity:
        raise DirectoryPublishError("Created directory identity no longer matches its journal")
    try:
        path.rmdir()
    except OSError:
        return
