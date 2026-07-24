"""Native exclusive directory publication for supported desktop platforms."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

import isoworld.content.file_stat as file_stat_module
from isoworld.content.file_stat import (
    FileStat,
    descriptor_file_stat,
    file_identity,
    is_link_or_reparse,
    path_file_stat,
)

DirectoryIdentity = tuple[int, int]


class DirectoryPublishError(OSError):
    """Raised when a directory cannot be published without replacement."""


_AT_REMOVEDIR = 0x200
_AT_EMPTY_PATH = 0x1000


class _FileDispositionInfo(ctypes.Structure):
    _fields_ = [("delete_file", ctypes.c_ubyte)]


def _validated_directory_state(path: Path, *, context: str) -> FileStat:
    try:
        info = path_file_stat(path)
    except OSError as exc:
        raise DirectoryPublishError(f"Could not inspect {context} {path}: {exc}") from exc
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise DirectoryPublishError(f"{context} must be a real directory: {path}")
    return info


def directory_identity(path: Path, *, context: str) -> DirectoryIdentity:
    return file_identity(_validated_directory_state(path, context=context))


def _require_expected_directory(
    info: FileStat,
    expected_identity: DirectoryIdentity,
    *,
    context: str,
) -> None:
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise DirectoryPublishError(f"{context} is no longer a real directory")
    if file_identity(info) != expected_identity:
        raise DirectoryPublishError(f"{context} identity changed unexpectedly")


def _windows_error_detail(error: int) -> str:
    formatter = getattr(ctypes, "FormatError", None)
    return formatter(error) if formatter is not None else f"Windows error {error}"


def _close_descriptors(descriptors: tuple[tuple[int, str], ...]) -> None:
    primary = sys.exception()
    cleanup_error: DirectoryPublishError | None = None
    cleanup_cause: OSError | None = None
    for descriptor, context in descriptors:
        try:
            os.close(descriptor)
        except OSError as exc:
            detail = f"{context} failed: {exc}"
            if primary is not None:
                primary.add_note(detail)
            elif cleanup_error is not None:
                cleanup_error.add_note(detail)
            else:
                cleanup_error = DirectoryPublishError(detail)
                cleanup_cause = exc
    if cleanup_error is not None:
        raise cleanup_error from cleanup_cause


def _close_descriptor(descriptor: int, *, context: str) -> None:
    _close_descriptors(((descriptor, context),))


def _posix_fsync_directory(
    path: Path,
    *,
    expected_identity: DirectoryIdentity,
) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not open directory for durable metadata flush {path}: {exc}"
        ) from exc
    try:
        _require_expected_directory(
            descriptor_file_stat(descriptor),
            expected_identity,
            context="directory opened for durable metadata flush",
        )
        os.fsync(descriptor)
        _require_expected_directory(
            descriptor_file_stat(descriptor),
            expected_identity,
            context="directory flushed for durable metadata",
        )
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not durably flush directory metadata {path}: {exc}"
        ) from exc
    finally:
        _close_descriptor(
            descriptor,
            context="directory durability descriptor cleanup",
        )


def _windows_fsync_directory(
    path: Path,
    *,
    expected_identity: DirectoryIdentity,
) -> None:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise DirectoryPublishError("Windows directory durability API is unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
        0x00000001 | 0x00000002,  # share reads/writes, but never deletion
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in {None, invalid_handle}:
        error = ctypes.get_last_error()
        raise DirectoryPublishError(
            "Could not open Windows directory for durable metadata flush "
            f"{path}: {_windows_error_detail(error)}"
        )

    handle_value = int(handle)
    flush_file_buffers = kernel32.FlushFileBuffers
    flush_file_buffers.argtypes = [ctypes.c_void_p]
    flush_file_buffers.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    try:
        _require_expected_directory(
            file_stat_module._windows_handle_stat(handle_value),  # noqa: SLF001
            expected_identity,
            context="Windows directory opened for durable metadata flush",
        )
        if not flush_file_buffers(ctypes.c_void_p(handle_value)):
            error = ctypes.get_last_error()
            raise DirectoryPublishError(
                "Windows filesystem could not durably flush directory metadata "
                f"{path}: {_windows_error_detail(error)}"
            )
        _require_expected_directory(
            file_stat_module._windows_handle_stat(handle_value),  # noqa: SLF001
            expected_identity,
            context="Windows directory flushed for durable metadata",
        )
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not validate Windows directory durability handle {path}: {exc}"
        ) from exc
    finally:
        primary = sys.exception()
        if not close_handle(ctypes.c_void_p(handle_value)):
            error = ctypes.get_last_error()
            detail = (
                "Windows directory durability handle cleanup failed: "
                f"{_windows_error_detail(error)}"
            )
            if primary is not None:
                primary.add_note(detail)
            else:
                raise DirectoryPublishError(detail)


def fsync_directory(path: Path, *, context: str) -> None:
    """Durably flush one unchanged real directory on a supported platform."""

    expected_identity = directory_identity(path, context=context)
    if sys.platform.startswith("linux") and os.name == "posix":
        _posix_fsync_directory(path, expected_identity=expected_identity)
    elif os.name == "nt":
        _windows_fsync_directory(path, expected_identity=expected_identity)
    else:
        raise DirectoryPublishError(
            "Durable directory metadata flush is supported only on Linux and Windows"
        )
    if directory_identity(path, context=context) != expected_identity:
        raise DirectoryPublishError(f"{context} identity changed after durable metadata flush")


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
    if error == 5:  # ERROR_ACCESS_DENIED can be Windows' directory-collision result.
        try:
            destination.lstat()
        except OSError:
            pass
        else:
            raise FileExistsError(error, "destination already exists", destination)
    detail = _windows_error_detail(error)
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


def _posix_unlink_descriptor_raw(descriptor: int, *, directory: bool) -> int | None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        unlinkat = libc.unlinkat
    except (AttributeError, OSError) as exc:
        raise DirectoryPublishError(
            "Identity-bound POSIX descriptor deletion is unavailable"
        ) from exc
    unlinkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    unlinkat.restype = ctypes.c_int
    flags = _AT_EMPTY_PATH | (_AT_REMOVEDIR if directory else 0)
    ctypes.set_errno(0)
    if unlinkat(descriptor, b"", flags) == 0:
        return None
    return ctypes.get_errno()


def _posix_descriptor_deletion_unavailable(error: int) -> bool:
    return error in {
        errno.EINVAL,
        errno.ENOENT,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }


def _raise_posix_descriptor_deletion_error(error: int) -> None:
    if _posix_descriptor_deletion_unavailable(error):
        raise DirectoryPublishError("Identity-bound POSIX descriptor deletion is unavailable")
    detail = os.strerror(error) if error else "unknown POSIX deletion error"
    raise DirectoryPublishError(f"Could not delete the retained POSIX cleanup object: {detail}")


def _verify_posix_descriptor_deleted(descriptor: int) -> None:
    try:
        remaining_links = descriptor_file_stat(descriptor).st_nlink
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not verify retained POSIX cleanup object deletion: {exc}"
        ) from exc
    if remaining_links != 0:
        raise DirectoryPublishError(
            "Identity-bound POSIX descriptor deletion did not remove the retained object"
        )


def _posix_unlink_descriptor(descriptor: int, *, directory: bool) -> None:
    """Delete exactly one open object, or fail without touching its pathname."""

    error = _posix_unlink_descriptor_raw(descriptor, directory=directory)
    if error is not None:
        _raise_posix_descriptor_deletion_error(error)
    _verify_posix_descriptor_deleted(descriptor)


def _posix_preflight_directory_deletion(
    descriptor: int,
    *,
    recursive: bool,
) -> bool:
    """Prove retained-FD deletion support before recursively mutating a tree."""

    directory_error = _posix_unlink_descriptor_raw(descriptor, directory=True)
    if directory_error is None:
        _verify_posix_descriptor_deleted(descriptor)
        return True
    if _posix_descriptor_deletion_unavailable(directory_error):
        _raise_posix_descriptor_deletion_error(directory_error)
    if directory_error not in {errno.ENOTEMPTY, errno.EEXIST}:
        _raise_posix_descriptor_deletion_error(directory_error)
    if not recursive:
        raise DirectoryPublishError("Claimed empty directory is no longer empty")

    file_error = _posix_unlink_descriptor_raw(descriptor, directory=False)
    if file_error is None:
        raise DirectoryPublishError(
            "POSIX descriptor deletion unexpectedly removed a directory as a regular file"
        )
    if _posix_descriptor_deletion_unavailable(file_error):
        _raise_posix_descriptor_deletion_error(file_error)
    if file_error != errno.EISDIR:
        _raise_posix_descriptor_deletion_error(file_error)
    return False


def _posix_remove_directory_contents(descriptor: int) -> None:
    try:
        with os.scandir(descriptor) as entries:
            names = sorted(entry.name for entry in entries)
    except OSError as exc:
        raise DirectoryPublishError(f"Could not enumerate claimed directory: {exc}") from exc

    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    for name in names:
        try:
            before = os.stat(
                name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not inspect claimed directory entry {name!r}: {exc}"
            ) from exc
        if stat.S_ISLNK(before.st_mode):
            raise DirectoryPublishError(f"Claimed directory entry became a symbolic link: {name!r}")
        if stat.S_ISDIR(before.st_mode):
            try:
                child_descriptor = os.open(
                    name,
                    directory_flags,
                    dir_fd=descriptor,
                )
            except OSError as exc:
                raise DirectoryPublishError(
                    f"Could not open claimed child directory {name!r}: {exc}"
                ) from exc
            try:
                opened = descriptor_file_stat(child_descriptor)
                _require_expected_directory(
                    opened,
                    file_identity(before),
                    context=f"claimed child directory {name!r}",
                )
                _posix_remove_directory_contents(child_descriptor)
                _posix_unlink_descriptor(child_descriptor, directory=True)
            except DirectoryPublishError:
                raise
            except OSError as exc:
                raise DirectoryPublishError(
                    f"Could not remove claimed child directory {name!r}: {exc}"
                ) from exc
            finally:
                _close_descriptor(
                    child_descriptor,
                    context="claimed child directory descriptor cleanup",
                )
            continue
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise DirectoryPublishError(
                f"Claimed directory entry is not an owned regular file: {name!r}"
            )
        try:
            file_descriptor = os.open(
                name,
                file_flags,
                dir_fd=descriptor,
            )
        except OSError as exc:
            raise DirectoryPublishError(f"Could not open claimed file {name!r}: {exc}") from exc
        try:
            opened = descriptor_file_stat(file_descriptor)
            if (
                is_link_or_reparse(opened)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or file_identity(opened) != file_identity(before)
            ):
                raise DirectoryPublishError(f"Claimed regular file identity changed: {name!r}")
            _posix_unlink_descriptor(file_descriptor, directory=False)
        except DirectoryPublishError:
            raise
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not remove claimed regular file {name!r}: {exc}"
            ) from exc
        finally:
            _close_descriptor(
                file_descriptor,
                context="claimed regular file descriptor cleanup",
            )


def _posix_remove_claimed_directory(
    parent: Path,
    claim_name: str,
    expected_identity: DirectoryIdentity,
    *,
    recursive: bool,
) -> None:
    required_dir_fd = (os.open, os.stat)
    if (
        not sys.platform.startswith("linux")
        or os.name != "posix"
        or any(operation not in os.supports_dir_fd for operation in required_dir_fd)
        or os.scandir not in os.supports_fd
    ):
        raise DirectoryPublishError("Identity-bound POSIX directory removal is unavailable")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_descriptor = os.open(parent, flags)
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not open claimed directory parent {parent}: {exc}"
        ) from exc
    claim_descriptor: int | None = None
    try:
        parent_info = descriptor_file_stat(parent_descriptor)
        if is_link_or_reparse(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
            raise DirectoryPublishError("Claimed directory parent is not a real directory")
        claim_descriptor = os.open(
            claim_name,
            flags,
            dir_fd=parent_descriptor,
        )
        opened = descriptor_file_stat(claim_descriptor)
        _require_expected_directory(
            opened,
            expected_identity,
            context="claimed directory",
        )
        if _posix_preflight_directory_deletion(
            claim_descriptor,
            recursive=recursive,
        ):
            return
        _posix_remove_directory_contents(claim_descriptor)
        _posix_unlink_descriptor(claim_descriptor, directory=True)
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not remove claimed directory {parent / claim_name}: {exc}"
        ) from exc
    finally:
        descriptors = (
            (
                (claim_descriptor, "claimed directory descriptor cleanup"),
                (
                    parent_descriptor,
                    "claimed directory parent descriptor cleanup",
                ),
            )
            if claim_descriptor is not None
            else (
                (
                    parent_descriptor,
                    "claimed directory parent descriptor cleanup",
                ),
            )
        )
        _close_descriptors(descriptors)


def _windows_open_delete_handle(
    path: Path,
    expected_identity: DirectoryIdentity,
    *,
    directory: bool,
) -> int:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise DirectoryPublishError("Windows identity-bound deletion API is unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    flags = 0x00200000  # FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= 0x02000000  # FILE_FLAG_BACKUP_SEMANTICS
    handle = create_file(
        str(path),
        0x00010000 | 0x00000080,  # DELETE | FILE_READ_ATTRIBUTES
        0x00000001 | 0x00000002,  # share reads/writes, but never deletion
        None,
        3,  # OPEN_EXISTING
        flags,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in {None, invalid_handle}:
        error = ctypes.get_last_error()
        raise DirectoryPublishError(
            f"Could not guard Windows cleanup target {path}: {_windows_error_detail(error)}"
        )
    handle_value = int(handle)
    try:
        info = file_stat_module._windows_handle_stat(handle_value)  # noqa: SLF001
        expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if (
            is_link_or_reparse(info)
            or not expected_type
            or (not directory and info.st_nlink != 1)
            or file_identity(info) != expected_identity
        ):
            raise DirectoryPublishError("Windows cleanup target identity changed")
    except BaseException as original:
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        if not close_handle(ctypes.c_void_p(handle_value)):
            error = ctypes.get_last_error()
            original.add_note(
                f"Windows cleanup handle cleanup failed: {_windows_error_detail(error)}"
            )
        raise
    return handle_value


def _windows_mark_handle_for_deletion(handle: int, path: Path) -> None:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise DirectoryPublishError("Windows identity-bound deletion API is unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)

    disposition = _FileDispositionInfo(1)
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_information.restype = ctypes.c_int
    if not set_information(
        ctypes.c_void_p(handle),
        4,  # FileDispositionInfo
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        error = ctypes.get_last_error()
        raise DirectoryPublishError(
            f"Could not mark Windows cleanup target for deletion {path}: "
            f"{_windows_error_detail(error)}"
        )


def _windows_close_cleanup_handle(handle: int) -> None:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise DirectoryPublishError("Windows cleanup handle API is unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    if not close_handle(ctypes.c_void_p(handle)):
        error = ctypes.get_last_error()
        raise DirectoryPublishError(
            f"Windows cleanup handle cleanup failed: {_windows_error_detail(error)}"
        )


def _windows_remove_claimed_file(path: Path, expected_identity: DirectoryIdentity) -> None:
    handle = _windows_open_delete_handle(
        path,
        expected_identity,
        directory=False,
    )
    try:
        _windows_mark_handle_for_deletion(handle, path)
    finally:
        primary = sys.exception()
        try:
            _windows_close_cleanup_handle(handle)
        except DirectoryPublishError as cleanup_error:
            if primary is not None:
                primary.add_note(str(cleanup_error))
            else:
                raise


def _windows_remove_claimed_directory(
    path: Path,
    expected_identity: DirectoryIdentity,
    *,
    recursive: bool,
) -> None:
    handle = _windows_open_delete_handle(
        path,
        expected_identity,
        directory=True,
    )
    try:
        if recursive:
            try:
                children = tuple(sorted(path.iterdir(), key=lambda child: child.name))
            except OSError as exc:
                raise DirectoryPublishError(
                    f"Could not enumerate guarded Windows directory {path}: {exc}"
                ) from exc
            for child in children:
                info = path_file_stat(child)
                if is_link_or_reparse(info):
                    raise DirectoryPublishError(
                        f"Guarded Windows cleanup entry became a reparse point: {child}"
                    )
                identity = file_identity(info)
                if stat.S_ISDIR(info.st_mode):
                    _windows_remove_claimed_directory(
                        child,
                        identity,
                        recursive=True,
                    )
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    _windows_remove_claimed_file(child, identity)
                else:
                    raise DirectoryPublishError(
                        f"Guarded Windows cleanup entry is not owned: {child}"
                    )
        else:
            try:
                if next(path.iterdir(), None) is not None:
                    raise DirectoryPublishError(
                        "Claimed empty Windows directory is no longer empty"
                    )
            except OSError as exc:
                raise DirectoryPublishError(
                    f"Could not inspect claimed empty Windows directory {path}: {exc}"
                ) from exc
        _require_expected_directory(
            file_stat_module._windows_handle_stat(handle),  # noqa: SLF001
            expected_identity,
            context="guarded Windows cleanup directory",
        )
        _windows_mark_handle_for_deletion(handle, path)
    finally:
        primary = sys.exception()
        try:
            _windows_close_cleanup_handle(handle)
        except DirectoryPublishError as cleanup_error:
            if primary is not None:
                primary.add_note(str(cleanup_error))
            else:
                raise


def _remove_claimed_directory(
    parent: Path,
    claim_name: str,
    expected_identity: DirectoryIdentity,
    *,
    recursive: bool,
) -> None:
    if sys.platform.startswith("linux") and os.name == "posix":
        _posix_remove_claimed_directory(
            parent,
            claim_name,
            expected_identity,
            recursive=recursive,
        )
        return
    if os.name == "nt":
        _windows_remove_claimed_directory(
            parent / claim_name,
            expected_identity,
            recursive=recursive,
        )
        return
    raise DirectoryPublishError(
        "Identity-bound directory removal is supported only on Linux and Windows"
    )


def quarantine_and_remove_owned_directory(
    path: Path,
    expected_identity: DirectoryIdentity,
    *,
    verify: Callable[[Path], None],
) -> None:
    """Remove only a verified owned directory through an identity-bound primitive."""

    if directory_identity(path, context="rollback directory") != expected_identity:
        raise DirectoryPublishError("Rollback directory identity no longer matches its journal")
    verify(path)
    if sys.platform.startswith("linux") and os.name == "posix":
        _remove_claimed_directory(
            path.parent,
            path.name,
            expected_identity,
            recursive=True,
        )
        return
    if os.name != "nt":
        raise DirectoryPublishError(
            "Identity-bound directory removal is supported only on Linux and Windows"
        )
    quarantine = path.parent / f".{path.name}.rollback-{uuid.uuid4().hex}"
    moved_identity = publish_directory_noreplace(path, quarantine)
    if moved_identity != expected_identity:
        raise DirectoryPublishError("Quarantined directory identity no longer matches its journal")
    verify(quarantine)
    _remove_claimed_directory(
        quarantine.parent,
        quarantine.name,
        expected_identity,
        recursive=True,
    )


def remove_owned_empty_directory(path: Path, expected_identity: DirectoryIdentity) -> None:
    """Claim and remove an empty owned directory without deleting a replacement."""

    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DirectoryPublishError(f"Could not inspect created directory {path}: {exc}") from exc
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise DirectoryPublishError("Created directory is no longer a real directory")
    if file_identity(info) != expected_identity:
        raise DirectoryPublishError("Created directory identity no longer matches its journal")
    try:
        next(path.iterdir())
    except StopIteration:
        pass
    except FileNotFoundError:
        return
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not inspect created directory contents {path}: {exc}"
        ) from exc
    else:
        return

    if sys.platform.startswith("linux") and os.name == "posix":
        _remove_claimed_directory(
            path.parent,
            path.name,
            expected_identity,
            recursive=False,
        )
        return
    if os.name != "nt":
        raise DirectoryPublishError(
            "Identity-bound directory removal is supported only on Linux and Windows"
        )
    quarantine = path.parent / f".{path.name}.empty-cleanup-{uuid.uuid4().hex}"
    try:
        moved_identity = publish_directory_noreplace(path, quarantine)
    except DirectoryPublishError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return
        raise
    if moved_identity != expected_identity:
        raise DirectoryPublishError("Claimed directory identity no longer matches its journal")
    _remove_claimed_directory(
        quarantine.parent,
        quarantine.name,
        expected_identity,
        recursive=False,
    )
