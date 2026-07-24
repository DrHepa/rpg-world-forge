"""Native exclusive directory publication for supported desktop platforms."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath

import isoworld.content.file_stat as file_stat_module
from isoworld.content.file_stat import (
    FileStat,
    descriptor_file_stat,
    file_identity,
    is_link_or_reparse,
    path_file_stat,
)
from isoworld.content.publication_journal import (
    PublicationJournalError,
    journal_frame,
    recover_last_complete_payload,
)

DirectoryIdentity = tuple[int, int]


class DirectoryPublishError(OSError):
    """Raised when a directory cannot be published without replacement."""


class DirectoryPublishIndeterminateError(DirectoryPublishError):
    """Raised when a native publication mutated names but durability is unproven."""


def _read_descriptor_bytes(descriptor: int, *, limit: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    payload = bytearray()
    while len(payload) <= limit:
        chunk = os.read(
            descriptor,
            min(1024 * 1024, limit + 1 - len(payload)),
        )
        if not chunk:
            break
        payload.extend(chunk)
    if len(payload) > limit:
        raise DirectoryPublishError("Append-only journal exceeds its byte limit")
    return bytes(payload)


def _last_complete_journal_payload(
    payload: bytes,
    *,
    max_record_bytes: int,
) -> bytes:
    try:
        return recover_last_complete_payload(
            payload,
            max_record_bytes=max_record_bytes,
        )
    except PublicationJournalError as exc:
        raise DirectoryPublishError(str(exc)) from exc


def _journal_frame(payload: bytes) -> bytes:
    try:
        return journal_frame(payload)
    except PublicationJournalError as exc:
        raise DirectoryPublishError(str(exc)) from exc


def _write_descriptor_bytes(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short append-only journal write")
        view = view[written:]


def _require_journal_binding(
    path: Path,
    descriptor: int,
    expected_identity: DirectoryIdentity,
) -> None:
    retained = descriptor_file_stat(descriptor)
    named = path_file_stat(path)
    if (
        is_link_or_reparse(retained)
        or is_link_or_reparse(named)
        or not stat.S_ISREG(retained.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or retained.st_nlink != 1
        or named.st_nlink != 1
        or file_identity(retained) != expected_identity
        or file_identity(named) != expected_identity
    ):
        raise DirectoryPublishError("Append-only journal path binding changed")


def read_append_only_journal(
    path: Path,
    *,
    max_record_bytes: int,
    max_file_bytes: int,
) -> tuple[bytes, DirectoryIdentity] | None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        retained = descriptor_file_stat(descriptor)
        identity = file_identity(retained)
        _require_journal_binding(path, descriptor, identity)
        file_payload = _read_descriptor_bytes(descriptor, limit=max_file_bytes)
        _require_journal_binding(path, descriptor, identity)
        return (
            _last_complete_journal_payload(
                file_payload,
                max_record_bytes=max_record_bytes,
            ),
            identity,
        )
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(f"Could not read append-only journal {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            _close_descriptors(((descriptor, "append-only journal descriptor cleanup"),))


def create_append_only_journal(
    path: Path,
    payload: bytes,
    *,
    max_record_bytes: int,
) -> DirectoryIdentity:
    if not payload or len(payload) > max_record_bytes:
        raise DirectoryPublishError("Append-only journal record exceeds its byte limit")
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        retained = descriptor_file_stat(descriptor)
        identity = file_identity(retained)
        if (
            is_link_or_reparse(retained)
            or not stat.S_ISREG(retained.st_mode)
            or retained.st_nlink != 1
            or retained.st_size != 0
        ):
            raise DirectoryPublishError("New append-only journal is unsafe")
        _write_descriptor_bytes(descriptor, payload)
        os.fsync(descriptor)
        _require_journal_binding(path, descriptor, identity)
        if _read_descriptor_bytes(descriptor, limit=max_record_bytes) != payload:
            raise DirectoryPublishError("New append-only journal bytes changed")
        return identity
    except (DirectoryPublishError, FileExistsError):
        raise
    except OSError as exc:
        raise DirectoryPublishError(f"Could not create append-only journal {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            _close_descriptors(((descriptor, "new append-only journal descriptor cleanup"),))


def append_append_only_journal(
    path: Path,
    *,
    expected_identity: DirectoryIdentity,
    expected_payload: bytes,
    updated_payload: bytes,
    max_record_bytes: int,
    max_file_bytes: int,
) -> DirectoryIdentity:
    if not updated_payload or len(updated_payload) > max_record_bytes:
        raise DirectoryPublishError("Append-only journal record exceeds its byte limit")
    flags = (
        os.O_RDWR
        | os.O_APPEND
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        _require_journal_binding(path, descriptor, expected_identity)
        before = _read_descriptor_bytes(descriptor, limit=max_file_bytes)
        if _last_complete_journal_payload(
            before,
            max_record_bytes=max_record_bytes,
        ) != expected_payload or not (
            before == expected_payload or before.endswith(_journal_frame(expected_payload))
        ):
            raise DirectoryPublishError("Append-only journal changed before transition")
        _require_journal_binding(path, descriptor, expected_identity)
        frame = _journal_frame(updated_payload)
        if len(before) + len(frame) > max_file_bytes:
            raise DirectoryPublishError("Append-only journal exceeds its byte limit")
        os.lseek(descriptor, 0, os.SEEK_END)
        _write_descriptor_bytes(descriptor, frame)
        os.fsync(descriptor)
        _require_journal_binding(path, descriptor, expected_identity)
        after = _read_descriptor_bytes(descriptor, limit=max_file_bytes)
        if (
            _last_complete_journal_payload(
                after,
                max_record_bytes=max_record_bytes,
            )
            != updated_payload
        ):
            raise DirectoryPublishError("Append-only journal transition is incomplete")
        return expected_identity
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(f"Could not append journal transition {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            _close_descriptors(((descriptor, "append-only journal transition cleanup"),))


_AT_REMOVEDIR = 0x200
_AT_EMPTY_PATH = 0x1000


class _FileDispositionInfo(ctypes.Structure):
    _fields_ = [("delete_file", ctypes.c_ubyte)]


class _FileRenameInformation(ctypes.Structure):
    _fields_ = [
        ("replace_if_exists", ctypes.c_int),
        ("root_directory", ctypes.c_void_p),
        ("filename_length", ctypes.c_uint32),
        ("filename", ctypes.c_wchar * 1),
    ]


_WindowsTreeState = tuple[DirectoryIdentity, int, int, int, int]


@dataclass(frozen=True)
class _WindowsPayloadHandle:
    relative: str
    handle: int
    directory: bool
    expected: _WindowsTreeState


@dataclass
class _WindowsRetainedTree:
    source: Path
    source_identity: DirectoryIdentity
    parent_identity: DirectoryIdentity
    source_handle: int
    parent_handle: int
    payload_handles: list[_WindowsPayloadHandle]
    expected_tree: dict[str, _WindowsTreeState]
    flush_file_buffers: Callable[[ctypes.c_void_p], int]
    close_handle: Callable[[ctypes.c_void_p], int]
    set_information: Callable[[ctypes.c_void_p, int, object, int], int]
    namespace_mutated: bool = False

    def _require_root_handles(self, *, context: str) -> None:
        _require_expected_directory(
            file_stat_module._windows_handle_stat(self.source_handle),  # noqa: SLF001
            self.source_identity,
            context=f"{context} source",
        )
        _require_expected_directory(
            file_stat_module._windows_handle_stat(self.parent_handle),  # noqa: SLF001
            self.parent_identity,
            context=f"{context} parent",
        )

    def _require_payload_handles(self, *, context: str) -> None:
        for retained in self.payload_handles:
            opened = file_stat_module._windows_handle_stat(retained.handle)  # noqa: SLF001
            expected = retained.expected
            if (
                is_link_or_reparse(opened)
                or file_identity(opened) != expected[0]
                or stat.S_IFMT(opened.st_mode) != expected[1]
                or opened.st_nlink != expected[2]
                or opened.st_size != expected[3]
                or opened.st_mtime_ns != expected[4]
            ):
                raise DirectoryPublishError(
                    f"{context} payload identity changed: {retained.relative}"
                )

    def _flush_handle(self, handle: int, context: str) -> None:
        if not self.flush_file_buffers(ctypes.c_void_p(handle)):
            error = ctypes.get_last_error()
            raise DirectoryPublishError(
                f"Could not durably flush {context}: {_windows_error_detail(error)}"
            )

    def flush_payload_tree(self) -> tuple[str, ...]:
        self._require_root_handles(context="retained Windows publication")
        self._require_payload_handles(context="retained Windows publication")
        if _windows_tree_snapshot(self.source) != self.expected_tree:
            raise DirectoryPublishError(
                "Windows publication payload tree changed before durable flush"
            )
        ordered = sorted(
            self.payload_handles,
            key=lambda item: (
                item.directory,
                -len(PurePath(*item.relative.split("/")).parts) if item.directory else 0,
                item.relative,
            ),
        )
        for retained in ordered:
            self._flush_handle(
                retained.handle,
                "Windows publication "
                f"{'directory' if retained.directory else 'payload'} {retained.relative}",
            )
        self._require_payload_handles(context="durably retained Windows publication")
        if _windows_tree_snapshot(self.source) != self.expected_tree:
            raise DirectoryPublishError(
                "Windows publication payload tree changed during durable flush"
            )
        self._flush_handle(self.source_handle, "Windows publication stage")
        self._flush_handle(self.parent_handle, "Windows publication stage parent")
        self._require_root_handles(context="durably retained Windows publication")
        self._require_payload_handles(context="durably retained Windows publication")
        if _windows_tree_snapshot(self.source) != self.expected_tree:
            raise DirectoryPublishError(
                "Windows publication payload tree changed after durable flush"
            )
        return tuple(retained.relative for retained in ordered if not retained.directory)

    def rename_noreplace(self, destination: Path) -> DirectoryIdentity:
        if self.source.parent != destination.parent:
            raise DirectoryPublishError("Windows directory publication must stay within one parent")
        if not destination.is_absolute():
            raise DirectoryPublishError(
                "Windows directory publication destination must be absolute"
            )
        self.flush_payload_tree()
        self._require_root_handles(context="pre-rename Windows publication")
        self._require_payload_handles(context="pre-rename Windows publication")
        if _windows_tree_snapshot(self.source) != self.expected_tree:
            raise DirectoryPublishError(
                "Windows publication payload tree changed before handle-bound rename"
            )
        encoded = str(destination).encode("utf-16-le")
        offset = _FileRenameInformation.filename.offset
        buffer = ctypes.create_string_buffer(
            max(ctypes.sizeof(_FileRenameInformation), offset + len(encoded))
        )
        information = _FileRenameInformation.from_buffer(buffer)
        information.replace_if_exists = False
        # SetFileInformationByHandle requires a fully-qualified name when used
        # from Win32. A root-directory-relative FILE_RENAME_INFO request is an
        # NT-native contract and is rejected here with ERROR_INVALID_PARAMETER.
        information.root_directory = None
        information.filename_length = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
        if not self.set_information(
            ctypes.c_void_p(self.source_handle),
            3,  # FileRenameInfo
            buffer,
            len(buffer),
        ):
            error = ctypes.get_last_error()
            if error in {80, 183}:
                raise FileExistsError(
                    error,
                    "destination already exists",
                    destination,
                )
            if error == 5:
                try:
                    path_file_stat(destination)
                except OSError:
                    pass
                else:
                    raise FileExistsError(
                        error,
                        "destination already exists",
                        destination,
                    ) from None
            raise DirectoryPublishError(
                error,
                _windows_error_detail(error),
                destination,
            )

        try:
            self.namespace_mutated = True
            self._require_root_handles(context="renamed Windows publication")
            published_info = file_stat_module._windows_handle_stat(  # noqa: SLF001
                self.source_handle
            )
            self._flush_handle(self.source_handle, "published Windows directory")
            self._flush_handle(self.parent_handle, "Windows publication parent")
            self._require_root_handles(context="durably published Windows publication")
            try:
                destination_info = path_file_stat(destination)
            except OSError as exc:
                raise DirectoryPublishError(
                    f"Could not validate published Windows directory {destination}: {exc}"
                ) from exc
            _require_expected_directory(
                destination_info,
                self.source_identity,
                context="published Windows destination",
            )
            if _windows_tree_snapshot(destination) != self.expected_tree:
                raise DirectoryPublishError(
                    "Published Windows payload tree changed during handle-bound rename"
                )
            try:
                path_file_stat(self.source)
            except FileNotFoundError:
                pass
            else:
                raise DirectoryPublishError(
                    "Published Windows directory retained its private stage name"
                )
            return file_identity(published_info)
        except BaseException as exc:
            if isinstance(exc, DirectoryPublishIndeterminateError):
                raise
            raise DirectoryPublishIndeterminateError(
                "Windows directory publication outcome is indeterminate after "
                "SetFileInformationByHandle; no rollback was attempted for "
                f"{self.source} and {destination}: {exc}"
            ) from exc

    def close(self) -> None:
        primary = sys.exception()
        cleanup_error: DirectoryPublishError | None = None
        for handle, context in (
            *(
                (
                    retained.handle,
                    f"Windows publication payload handle cleanup for {retained.relative}",
                )
                for retained in reversed(self.payload_handles)
            ),
            (self.source_handle, "Windows publication source handle cleanup"),
            (self.parent_handle, "Windows publication parent handle cleanup"),
        ):
            if not self.close_handle(ctypes.c_void_p(handle)):
                error = ctypes.get_last_error()
                detail = f"{context} failed: {_windows_error_detail(error)}"
                if primary is not None:
                    primary.add_note(detail)
                elif cleanup_error is not None:
                    cleanup_error.add_note(detail)
                else:
                    cleanup_error = DirectoryPublishError(detail)
        if (
            primary is not None
            and self.namespace_mutated
            and not isinstance(primary, DirectoryPublishIndeterminateError)
        ):
            raise DirectoryPublishIndeterminateError(
                "Windows directory publication outcome is indeterminate after "
                "SetFileInformationByHandle and retained-handle cleanup"
            ) from primary
        if cleanup_error is not None:
            if self.namespace_mutated:
                raise DirectoryPublishIndeterminateError(
                    "Windows directory publication outcome is indeterminate after "
                    "SetFileInformationByHandle because retained-handle cleanup failed"
                ) from cleanup_error
            raise cleanup_error


_POSIX_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_POSIX_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


@dataclass
class RetainedDirectory:
    """One directory and its parent retained by identity-bound descriptors."""

    path: Path
    parent_fd: int
    fd: int
    parent_identity: DirectoryIdentity
    identity: DirectoryIdentity

    def require_binding(self) -> None:
        _require_expected_directory(
            path_file_stat(self.path.parent),
            self.parent_identity,
            context="retained directory parent path",
        )
        _require_expected_directory(
            descriptor_file_stat(self.parent_fd),
            self.parent_identity,
            context="retained directory parent",
        )
        try:
            named = os.stat(
                self.path.name,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise DirectoryPublishError(
                f"Retained directory path binding changed: {self.path}: {exc}"
            ) from exc
        _require_expected_directory(
            named,
            self.identity,
            context="retained directory path",
        )
        _require_expected_directory(
            descriptor_file_stat(self.fd),
            self.identity,
            context="retained directory descriptor",
        )

    def close(self) -> None:
        _close_descriptors(
            (
                (self.fd, "retained directory descriptor cleanup"),
                (self.parent_fd, "retained directory parent cleanup"),
            )
        )


@dataclass
class DirectoryClaim:
    """An exclusively-created destination retained through its parent and own FD."""

    path: Path
    parent_fd: int
    fd: int
    parent_identity: DirectoryIdentity
    identity: DirectoryIdentity

    def require_binding(self) -> None:
        _require_expected_directory(
            path_file_stat(self.path.parent),
            self.parent_identity,
            context="claimed destination parent path",
        )
        _require_expected_directory(
            descriptor_file_stat(self.parent_fd),
            self.parent_identity,
            context="claimed destination parent",
        )
        try:
            named = os.stat(
                self.path.name,
                dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise DirectoryPublishError(
                f"Claimed destination path binding changed: {self.path}: {exc}"
            ) from exc
        _require_expected_directory(
            named,
            self.identity,
            context="claimed destination path",
        )
        _require_expected_directory(
            descriptor_file_stat(self.fd),
            self.identity,
            context="claimed destination descriptor",
        )

    def fsync(self) -> None:
        self.require_binding()
        try:
            os.fsync(self.fd)
            os.fsync(self.parent_fd)
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not durably flush claimed destination {self.path}: {exc}"
            ) from exc
        self.require_binding()

    def close(self) -> None:
        _close_descriptors(
            (
                (self.fd, "claimed destination descriptor cleanup"),
                (self.parent_fd, "claimed destination parent cleanup"),
            )
        )


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


def _linux_rename_retained_noreplace(
    retained: RetainedDirectory,
    destination: Path,
) -> DirectoryIdentity:
    """Rename one validated stage name with Linux RENAME_NOREPLACE.

    Linux does not provide a source-FD-bound rename primitive here. The retained
    stage descriptor is post-mutation evidence; the source name remains subject
    to a final pathname race that is detected, never described as prevented.
    """

    if retained.path.parent != destination.parent:
        raise DirectoryPublishError("Linux directory publication must stay within one parent")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise DirectoryPublishError("Linux RENAME_NOREPLACE publication is unavailable") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int

    retained.require_binding()
    try:
        os.stat(
            destination.name,
            dir_fd=retained.parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not inspect publication destination {destination}: {exc}"
        ) from exc
    else:
        raise FileExistsError(errno.EEXIST, "destination already exists", destination)

    ctypes.set_errno(0)
    if (
        renameat2(
            retained.parent_fd,
            os.fsencode(retained.path.name),
            retained.parent_fd,
            os.fsencode(destination.name),
            1,  # RENAME_NOREPLACE
        )
        != 0
    ):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, "destination already exists", destination)
        if error in {
            errno.ENOSYS,
            errno.EINVAL,
            getattr(errno, "ENOTSUP", errno.EINVAL),
            getattr(errno, "EOPNOTSUPP", errno.EINVAL),
        }:
            raise DirectoryPublishError("Linux RENAME_NOREPLACE publication is unavailable")
        raise DirectoryPublishError(
            error,
            f"Could not publish retained Linux directory: {os.strerror(error)}",
            destination,
        )

    def require_published_state(*, context: str) -> None:
        _require_expected_directory(
            descriptor_file_stat(retained.fd),
            retained.identity,
            context=f"{context} stage descriptor",
        )
        _require_expected_directory(
            descriptor_file_stat(retained.parent_fd),
            retained.parent_identity,
            context=f"{context} parent descriptor",
        )
        _require_expected_directory(
            path_file_stat(destination.parent),
            retained.parent_identity,
            context=f"{context} lexical parent",
        )
        _require_expected_directory(
            os.stat(
                destination.name,
                dir_fd=retained.parent_fd,
                follow_symlinks=False,
            ),
            retained.identity,
            context=f"{context} destination through retained parent",
        )
        _require_expected_directory(
            path_file_stat(destination),
            retained.identity,
            context=f"{context} logical destination",
        )
        try:
            os.stat(
                retained.path.name,
                dir_fd=retained.parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise DirectoryPublishError(f"{context} retained both stage and destination names")
        try:
            path_file_stat(retained.path)
        except FileNotFoundError:
            pass
        else:
            raise DirectoryPublishError(f"{context} retained the logical stage name")

    try:
        require_published_state(context="published Linux")
        os.fsync(retained.fd)
        os.fsync(retained.parent_fd)
        require_published_state(context="durably published Linux")
    except Exception as exc:
        raise DirectoryPublishIndeterminateError(
            "Linux directory publication outcome is indeterminate after "
            f"RENAME_NOREPLACE; evidence retained at {retained.path} and {destination}: {exc}"
        ) from exc
    return retained.identity


def _posix_mkdir_noreplace(parent_fd: int, name: str, mode: int) -> None:
    os.mkdir(name, mode=mode, dir_fd=parent_fd)


@contextmanager
def open_expected_directory(
    source: Path,
    expected_identity: DirectoryIdentity,
) -> Iterator[RetainedDirectory]:
    """Open one expected Linux directory without trusting its name afterward."""

    if (
        not sys.platform.startswith("linux")
        or os.name != "posix"
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
    ):
        raise DirectoryPublishError(
            "Identity-bound retained directory access is unavailable on this platform"
        )
    parent_fd: int | None = None
    source_fd: int | None = None
    try:
        try:
            parent_fd = os.open(source.parent, _POSIX_DIRECTORY_FLAGS)
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not retain publication source parent {source.parent}: {exc}"
            ) from exc
        try:
            parent_info = descriptor_file_stat(parent_fd)
            if is_link_or_reparse(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
                raise DirectoryPublishError("Publication source parent is not a real directory")
            parent_identity = file_identity(parent_info)
            source_fd = os.open(source.name, _POSIX_DIRECTORY_FLAGS, dir_fd=parent_fd)
            _require_expected_directory(
                descriptor_file_stat(source_fd),
                expected_identity,
                context="retained publication source",
            )
            retained = RetainedDirectory(
                path=source,
                parent_fd=parent_fd,
                fd=source_fd,
                parent_identity=parent_identity,
                identity=expected_identity,
            )
            retained.require_binding()
        except DirectoryPublishError:
            raise
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not retain publication source {source}: {exc}"
            ) from exc
        yield retained
    finally:
        descriptors: list[tuple[int, str]] = []
        if source_fd is not None:
            descriptors.append((source_fd, "retained publication source cleanup"))
        if parent_fd is not None:
            descriptors.append((parent_fd, "retained publication source parent cleanup"))
        _close_descriptors(tuple(descriptors))


@contextmanager
def claim_directory_noreplace(
    destination: Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> Iterator[DirectoryClaim]:
    """Exclusively create and retain one Linux destination directory."""

    if (
        not sys.platform.startswith("linux")
        or os.name != "posix"
        or os.mkdir not in os.supports_dir_fd
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
    ):
        raise DirectoryPublishError(
            "Identity-bound exclusive destination claims are unavailable on this platform"
        )
    if expected_parent_identity is None:
        expected_parent_identity = directory_identity(
            destination.parent,
            context="publication destination parent",
        )
    parent_fd: int | None = None
    destination_fd: int | None = None
    try:
        try:
            parent_fd = os.open(destination.parent, _POSIX_DIRECTORY_FLAGS)
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not retain publication destination parent {destination.parent}: {exc}"
            ) from exc
        try:
            parent_info = descriptor_file_stat(parent_fd)
            if is_link_or_reparse(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
                raise DirectoryPublishError(
                    "Publication destination parent is not a real directory"
                )
            parent_identity = file_identity(parent_info)
            if parent_identity != expected_parent_identity:
                raise DirectoryPublishError("Publication destination parent identity changed")
            try:
                _posix_mkdir_noreplace(parent_fd, destination.name, 0o700)
            except FileExistsError as exc:
                raise FileExistsError(
                    errno.EEXIST,
                    "destination already exists",
                    destination,
                ) from exc
            destination_fd = os.open(
                destination.name,
                _POSIX_DIRECTORY_FLAGS,
                dir_fd=parent_fd,
            )
            destination_info = descriptor_file_stat(destination_fd)
            if is_link_or_reparse(destination_info) or not stat.S_ISDIR(destination_info.st_mode):
                raise DirectoryPublishError("Claimed destination is not a real directory")
            claim = DirectoryClaim(
                path=destination,
                parent_fd=parent_fd,
                fd=destination_fd,
                parent_identity=parent_identity,
                identity=file_identity(destination_info),
            )
            claim.require_binding()
        except (DirectoryPublishError, FileExistsError):
            raise
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not claim publication destination {destination}: {exc}"
            ) from exc
        yield claim
    finally:
        descriptors: list[tuple[int, str]] = []
        if destination_fd is not None:
            descriptors.append((destination_fd, "claimed publication destination cleanup"))
        if parent_fd is not None:
            descriptors.append((parent_fd, "claimed publication destination parent cleanup"))
        _close_descriptors(tuple(descriptors))


def _stable_source_state(info: FileStat) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _copy_regular_file_noreplace(
    source_fd: int,
    destination_fd: int,
    name: str,
    before: FileStat,
) -> None:
    source_file: int | None = None
    destination_file: int | None = None
    try:
        source_file = os.open(name, _POSIX_FILE_FLAGS, dir_fd=source_fd)
        opened = descriptor_file_stat(source_file)
        if (
            is_link_or_reparse(opened)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or file_identity(opened) != file_identity(before)
        ):
            raise DirectoryPublishError(f"Publication source file identity changed: {name!r}")
        destination_file = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            stat.S_IMODE(opened.st_mode),
            dir_fd=destination_fd,
        )
        while True:
            chunk = os.read(source_file, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_file, view)
                if written <= 0:
                    raise DirectoryPublishError(f"Could not copy publication source file: {name!r}")
                view = view[written:]
        os.fchmod(destination_file, stat.S_IMODE(opened.st_mode))
        os.fsync(destination_file)
        after = descriptor_file_stat(source_file)
        named_after = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if _stable_source_state(opened) != _stable_source_state(after) or _stable_source_state(
            opened
        ) != _stable_source_state(named_after):
            raise DirectoryPublishError(f"Publication source file changed while copying: {name!r}")
        copied = descriptor_file_stat(destination_file)
        named_copied = os.stat(
            name,
            dir_fd=destination_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(copied.st_mode)
            or copied.st_nlink != 1
            or copied.st_size != opened.st_size
            or stat.S_IMODE(copied.st_mode) != stat.S_IMODE(opened.st_mode)
            or file_identity(named_copied) != file_identity(copied)
            or _stable_source_state(named_copied) != _stable_source_state(copied)
        ):
            raise DirectoryPublishError(
                f"Published file does not match its source metadata: {name!r}"
            )
    except DirectoryPublishError:
        raise
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not copy publication source file {name!r}: {exc}"
        ) from exc
    finally:
        descriptors: list[tuple[int, str]] = []
        if destination_file is not None:
            descriptors.append((destination_file, "published file descriptor cleanup"))
        if source_file is not None:
            descriptors.append((source_file, "publication source file cleanup"))
        _close_descriptors(tuple(descriptors))


def _copy_retained_tree(source_fd: int, destination_fd: int) -> None:
    try:
        with os.scandir(source_fd) as entries:
            names = sorted(entry.name for entry in entries)
    except OSError as exc:
        raise DirectoryPublishError(f"Could not enumerate publication source: {exc}") from exc

    for name in names:
        try:
            before = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not inspect publication source entry {name!r}: {exc}"
            ) from exc
        if stat.S_ISLNK(before.st_mode):
            raise DirectoryPublishError(f"Publication source contains a symbolic link: {name!r}")
        if stat.S_ISREG(before.st_mode):
            if before.st_nlink != 1:
                raise DirectoryPublishError(
                    f"Publication source contains a hard-linked file: {name!r}"
                )
            _copy_regular_file_noreplace(
                source_fd,
                destination_fd,
                name,
                before,
            )
            continue
        if not stat.S_ISDIR(before.st_mode):
            raise DirectoryPublishError(f"Publication source contains a special file: {name!r}")

        source_child: int | None = None
        destination_child: int | None = None
        try:
            source_child = os.open(
                name,
                _POSIX_DIRECTORY_FLAGS,
                dir_fd=source_fd,
            )
            opened = descriptor_file_stat(source_child)
            _require_expected_directory(
                opened,
                file_identity(before),
                context=f"publication source directory {name!r}",
            )
            os.mkdir(
                name,
                mode=0o700,
                dir_fd=destination_fd,
            )
            destination_child = os.open(
                name,
                _POSIX_DIRECTORY_FLAGS,
                dir_fd=destination_fd,
            )
            _copy_retained_tree(source_child, destination_child)
            os.fchmod(destination_child, stat.S_IMODE(opened.st_mode))
            os.fsync(destination_child)
            copied_child = descriptor_file_stat(destination_child)
            named_copied_child = os.stat(
                name,
                dir_fd=destination_fd,
                follow_symlinks=False,
            )
            if file_identity(named_copied_child) != file_identity(
                copied_child
            ) or _stable_source_state(named_copied_child) != _stable_source_state(copied_child):
                raise DirectoryPublishError(
                    f"Published directory identity changed while copying: {name!r}"
                )
            after = descriptor_file_stat(source_child)
            named_after = os.stat(
                name,
                dir_fd=source_fd,
                follow_symlinks=False,
            )
            if _stable_source_state(opened) != _stable_source_state(after) or _stable_source_state(
                opened
            ) != _stable_source_state(named_after):
                raise DirectoryPublishError(
                    f"Publication source directory changed while copying: {name!r}"
                )
        except DirectoryPublishError:
            raise
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not copy publication source directory {name!r}: {exc}"
            ) from exc
        finally:
            descriptors = []
            if destination_child is not None:
                descriptors.append((destination_child, "published directory descriptor cleanup"))
            if source_child is not None:
                descriptors.append((source_child, "publication source directory cleanup"))
            _close_descriptors(tuple(descriptors))


def copy_retained_tree_noreplace(
    source: RetainedDirectory,
    destination: DirectoryClaim,
) -> DirectoryIdentity:
    """Copy an immutable retained tree into an empty claimed destination."""

    destination.require_binding()
    source_info = descriptor_file_stat(source.fd)
    _copy_retained_tree(source.fd, destination.fd)
    os.fchmod(destination.fd, stat.S_IMODE(source_info.st_mode))
    try:
        os.fsync(destination.fd)
    except OSError as exc:
        raise DirectoryPublishError(
            f"Could not durably flush published directory {destination.path}: {exc}"
        ) from exc
    if _stable_source_state(source_info) != _stable_source_state(descriptor_file_stat(source.fd)):
        raise DirectoryPublishError("Publication source directory changed while copying")
    destination.fsync()
    return destination.identity


def _windows_tree_snapshot(root: Path) -> dict[str, _WindowsTreeState]:
    def fail_walk(error: OSError) -> None:
        raise DirectoryPublishError(
            f"Could not inspect Windows publication payload tree: {error}"
        ) from error

    result: dict[str, _WindowsTreeState] = {}
    root_type = type(root)
    for current, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=fail_walk,
        followlinks=False,
    ):
        directory_names.sort()
        file_names.sort()
        current_path = root_type(current)
        for name, directory in (
            *((name, True) for name in directory_names),
            *((name, False) for name in file_names),
        ):
            path = current_path / name
            info = path_file_stat(path)
            expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
            if (
                is_link_or_reparse(info)
                or not expected_type
                or (not directory and info.st_nlink != 1)
            ):
                raise DirectoryPublishError(f"Windows publication payload is unsafe: {path}")
            result[path.relative_to(root).as_posix()] = (
                file_identity(info),
                stat.S_IFMT(info.st_mode),
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
            )
    return result


@contextmanager
def _open_windows_retained_tree(
    source: Path,
    *,
    source_identity: DirectoryIdentity,
    parent_identity: DirectoryIdentity,
    delete_source: bool,
) -> Iterator[_WindowsRetainedTree]:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise DirectoryPublishError(
            "Identity-bound Windows directory access is unavailable on this system"
        )
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
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    flush_file_buffers = kernel32.FlushFileBuffers
    flush_file_buffers.argtypes = [ctypes.c_void_p]
    flush_file_buffers.restype = ctypes.c_int
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    set_information.restype = ctypes.c_int
    invalid_handle = ctypes.c_void_p(-1).value

    def open_directory(path: Path, *, delete: bool, share_write: bool) -> int:
        access = 0x80000000 | 0x40000000 | 0x00100000
        if delete:
            access |= 0x00010000
        share = 0x00000001
        if share_write:
            share |= 0x00000002
        handle = create_file(
            str(path),
            access,
            share,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if handle in {None, invalid_handle}:
            error = ctypes.get_last_error()
            raise DirectoryPublishError(
                f"Could not retain Windows publication directory {path}: "
                f"{_windows_error_detail(error)}"
            )
        return int(handle)

    def open_tree_entry(path: Path, *, directory: bool) -> int:
        flags = 0x00200000
        if directory:
            flags |= 0x02000000
        share = 0x00000001
        if delete_source:
            # The retained descendants must permit the source-directory rename
            # while continuing to deny every writer until publication and
            # post-rename identity validation finish.
            share |= 0x00000004
        handle = create_file(
            str(path),
            0x80000000 | 0x40000000 | 0x00100000,
            share,
            None,
            3,
            flags,
            None,
        )
        if handle in {None, invalid_handle}:
            error = ctypes.get_last_error()
            raise DirectoryPublishError(
                f"Could not retain Windows publication payload {path}: "
                f"{_windows_error_detail(error)}"
            )
        return int(handle)

    source_handle: int | None = None
    parent_handle: int | None = None
    payload_handles: list[_WindowsPayloadHandle] = []
    retained: _WindowsRetainedTree | None = None
    try:
        source_handle = open_directory(
            source,
            delete=delete_source,
            share_write=False,
        )
        _require_expected_directory(
            file_stat_module._windows_handle_stat(source_handle),  # noqa: SLF001
            source_identity,
            context="retained Windows publication source",
        )
        parent_handle = open_directory(
            source.parent,
            delete=False,
            # The native rename internally requests write access to its target
            # directory. Retain identity and deletion exclusion while sharing
            # that write access with the kernel's rename open.
            share_write=True,
        )
        _require_expected_directory(
            file_stat_module._windows_handle_stat(parent_handle),  # noqa: SLF001
            parent_identity,
            context="retained Windows publication parent",
        )
        expected_tree = _windows_tree_snapshot(source)
        for relative, expected in sorted(expected_tree.items()):
            payload_path = source / PurePath(*relative.split("/"))
            directory = expected[1] == stat.S_IFDIR
            handle = open_tree_entry(payload_path, directory=directory)
            payload_handles.append(
                _WindowsPayloadHandle(
                    relative=relative,
                    handle=handle,
                    directory=directory,
                    expected=expected,
                )
            )
            opened = file_stat_module._windows_handle_stat(handle)  # noqa: SLF001
            if (
                is_link_or_reparse(opened)
                or file_identity(opened) != expected[0]
                or stat.S_IFMT(opened.st_mode) != expected[1]
                or opened.st_nlink != expected[2]
                or opened.st_size != expected[3]
                or opened.st_mtime_ns != expected[4]
            ):
                raise DirectoryPublishError(
                    f"Windows publication payload identity changed: {relative}"
                )
        if _windows_tree_snapshot(source) != expected_tree:
            raise DirectoryPublishError(
                "Windows publication payload tree changed while retaining handles"
            )
        retained = _WindowsRetainedTree(
            source=source,
            source_identity=source_identity,
            parent_identity=parent_identity,
            source_handle=source_handle,
            parent_handle=parent_handle,
            payload_handles=payload_handles,
            expected_tree=expected_tree,
            flush_file_buffers=flush_file_buffers,
            close_handle=close_handle,
            set_information=set_information,
        )
        yield retained
    finally:
        if retained is not None:
            retained.close()
        else:
            primary = sys.exception()
            cleanup_error: DirectoryPublishError | None = None
            handles = (
                *(
                    (
                        item.handle,
                        f"Windows publication payload handle cleanup for {item.relative}",
                    )
                    for item in reversed(payload_handles)
                ),
                (source_handle, "Windows publication source handle cleanup"),
                (parent_handle, "Windows publication parent handle cleanup"),
            )
            for handle, context in handles:
                if handle is None:
                    continue
                if not close_handle(ctypes.c_void_p(handle)):
                    error = ctypes.get_last_error()
                    detail = f"{context} failed: {_windows_error_detail(error)}"
                    if primary is not None:
                        primary.add_note(detail)
                    elif cleanup_error is not None:
                        cleanup_error.add_note(detail)
                    else:
                        cleanup_error = DirectoryPublishError(detail)
            if cleanup_error is not None:
                raise cleanup_error


def flush_windows_directory_tree(
    source: Path,
    *,
    expected_source_identity: DirectoryIdentity,
) -> tuple[str, ...]:
    """Durably flush one exact Windows payload tree while retaining every handle."""

    if os.name != "nt":
        raise DirectoryPublishError(
            "Identity-bound Windows directory durability is unavailable on this platform"
        )
    parent_identity = directory_identity(
        source.parent,
        context="Windows publication stage parent",
    )
    with _open_windows_retained_tree(
        source,
        source_identity=expected_source_identity,
        parent_identity=parent_identity,
        delete_source=False,
    ) as retained:
        return retained.flush_payload_tree()


def _windows_rename_noreplace(
    source: Path,
    destination: Path,
    *,
    source_identity: DirectoryIdentity,
    parent_identity: DirectoryIdentity,
) -> DirectoryIdentity:
    with _open_windows_retained_tree(
        source,
        source_identity=source_identity,
        parent_identity=parent_identity,
        delete_source=True,
    ) as retained:
        return retained.rename_noreplace(destination)


def publish_directory_noreplace(
    source: Path,
    destination: Path,
    *,
    expected_source_identity: DirectoryIdentity,
) -> DirectoryIdentity:
    """Publish one directory with supported native no-replace semantics."""

    source_identity = expected_source_identity
    linux_publication = False
    windows_publication = False

    if sys.platform.startswith("linux") and os.name == "posix":
        with open_expected_directory(source, source_identity) as retained:
            parent_identity = retained.parent_identity
            published_identity = _linux_rename_retained_noreplace(retained, destination)
            linux_publication = True
    elif os.name == "nt":
        if source.parent != destination.parent:
            raise DirectoryPublishError("Windows directory publication must stay within one parent")
        parent_identity = directory_identity(source.parent, context="publication parent")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(errno.EEXIST, "destination already exists", destination)
        published_identity = _windows_rename_noreplace(
            source,
            destination,
            source_identity=source_identity,
            parent_identity=parent_identity,
        )
        windows_publication = True
    else:
        raise DirectoryPublishError(
            "Safe exclusive directory publication is supported only on Linux and Windows"
        )

    try:
        if published_identity != source_identity:
            raise DirectoryPublishError("Published directory identity changed unexpectedly")
        if directory_identity(destination.parent, context="publication parent") != parent_identity:
            raise DirectoryPublishError("Publication parent identity changed unexpectedly")
        if directory_identity(destination, context="published directory") != source_identity:
            raise DirectoryPublishError("Published directory identity changed unexpectedly")
    except DirectoryPublishError as exc:
        if linux_publication:
            raise DirectoryPublishIndeterminateError(
                "Linux directory publication outcome became indeterminate after "
                f"RENAME_NOREPLACE; evidence retained at {source} and {destination}: {exc}"
            ) from exc
        if windows_publication:
            raise DirectoryPublishIndeterminateError(
                "Windows directory publication outcome became indeterminate after "
                "SetFileInformationByHandle; no rollback was attempted for "
                f"{source} and {destination}: {exc}"
            ) from exc
        raise
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
    if recursive:
        raise DirectoryPublishError(
            "Refusing to mutate a non-empty retained directory without a "
            "pre-recorded retained child snapshot"
        )
    raise DirectoryPublishError("Claimed empty directory is no longer empty")


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
        try:
            has_children = next(path.iterdir(), None) is not None
        except OSError as exc:
            raise DirectoryPublishError(
                f"Could not inspect claimed Windows directory {path}: {exc}"
            ) from exc
        if has_children:
            if recursive:
                raise DirectoryPublishError(
                    "Refusing to mutate a non-empty retained directory without a "
                    "pre-recorded retained child snapshot"
                )
            raise DirectoryPublishError("Claimed empty Windows directory is no longer empty")
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
    _remove_claimed_directory(
        path.parent,
        path.name,
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
    try:
        _remove_claimed_directory(
            path.parent,
            path.name,
            expected_identity,
            recursive=False,
        )
    except DirectoryPublishError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return
        raise
