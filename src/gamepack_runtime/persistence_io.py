from __future__ import annotations

import ctypes
import errno
import json
import os
import secrets
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from gamepack_runtime.contracts import GameLogicError
from gamepack_runtime.file_stat import (
    FileStat,
    descriptor_file_stat,
    windows_handle_file_stat,
)

_DIR_FD_PUBLICATION = os.name == "posix" and all(
    function in os.supports_dir_fd for function in (os.open, os.mkdir, os.rename, os.stat)
)
_DIR_FD_PACKAGE_PUBLICATION = os.name == "posix" and all(
    function in os.supports_dir_fd for function in (os.link, os.open, os.stat, os.unlink)
)
_AT_EMPTY_PATH = 0x1000
_MAX_IMMUTABLE_COLLISION_BYTES = 8 * 1024 * 1024


class PersistenceIOError(GameLogicError):
    """Raised when generic game persistence violates its file contract."""

    def __init__(
        self,
        detail: str,
        *,
        reason_code: str = "persistence_io_error",
    ) -> None:
        super().__init__(reason_code, detail)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _reject_json_float(value: str) -> None:
    raise ValueError(f"decimal and exponent JSON numbers are unsupported: {value}")


def decode_json_object(
    payload: bytes,
    *,
    source: str | Path,
    limit: int,
) -> dict[str, Any]:
    """Decode strict UTF-8 JSON bytes while requiring an object root."""

    if type(payload) is not bytes:
        raise PersistenceIOError(
            "persistence input must be exact bytes",
            reason_code="json_bytes_invalid",
        )
    if type(limit) is not int or type(limit) is bool or limit < 1:
        raise PersistenceIOError(
            "persistence byte limit must be a positive exact integer",
            reason_code="persistence_limit_invalid",
        )
    if len(payload) > limit:
        raise PersistenceIOError(
            f"{source} exceeds the {limit}-byte limit",
            reason_code="persistence_bytes_exceeded",
        )
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json_constant,
            parse_float=_reject_json_float,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as exc:
        raise PersistenceIOError(
            f"Could not read {source}: {exc}",
            reason_code=(
                "json_depth_exceeded" if isinstance(exc, RecursionError) else "json_invalid"
            ),
        ) from exc
    if type(value) is not dict:
        raise PersistenceIOError(
            f"{source} must contain a JSON object",
            reason_code="json_root_invalid",
        )
    return value


def _encode_json(value: object) -> bytes:
    try:
        document = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise PersistenceIOError(f"Could not encode strict JSON: {exc}") from exc
    try:
        return (document + "\n").encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise PersistenceIOError(f"Could not encode strict JSON: {exc}") from exc


def _entry_identity(info: FileStat) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x00000400)
    )


def _directory_identity(info: FileStat, path: Path) -> tuple[int, int]:
    if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise PersistenceIOError(
            f"Persistence parent is not a safe directory: {path}",
            reason_code="persistence_parent_unsafe",
        )
    return _entry_identity(info)


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _close_posix_descriptors(descriptors: list[int] | tuple[int, ...]) -> None:
    first_error: OSError | None = None
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


def _open_posix_ancestry(
    path: Path,
    *,
    create: bool,
) -> tuple[list[int], tuple[tuple[int, int], ...]]:
    descriptors: list[int] = []
    identities: list[tuple[int, int]] = []
    flags = _directory_open_flags()
    try:
        anchor = Path(path.anchor)
        descriptor = os.open(anchor, flags)
        descriptors.append(descriptor)
        identities.append(_directory_identity(descriptor_file_stat(descriptor), anchor))
        current = anchor
        for part in path.parts[1:]:
            current /= part
            try:
                descriptor = os.open(part, flags, dir_fd=descriptors[-1])
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptors[-1])
                except FileExistsError:
                    pass
                descriptor = os.open(part, flags, dir_fd=descriptors[-1])
            descriptors.append(descriptor)
            identities.append(_directory_identity(descriptor_file_stat(descriptor), current))
        return descriptors, tuple(identities)
    except BaseException:
        _close_posix_descriptors(descriptors)
        raise


class _WindowsUnicodeString(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ushort),
        ("maximum_length", ctypes.c_ushort),
        ("buffer", ctypes.c_wchar_p),
    ]


class _WindowsObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("root_directory", ctypes.c_void_p),
        ("object_name", ctypes.POINTER(_WindowsUnicodeString)),
        ("attributes", ctypes.c_ulong),
        ("security_descriptor", ctypes.c_void_p),
        ("security_quality_of_service", ctypes.c_void_p),
    ]


class _WindowsIoStatusBlock(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_void_p),
        ("information", ctypes.c_size_t),
    ]


class _WindowsFileRenameInformation(ctypes.Structure):
    _fields_ = [
        ("replace_if_exists", ctypes.c_ubyte),
        ("root_directory", ctypes.c_void_p),
        ("filename_length", ctypes.c_uint32),
        ("filename", ctypes.c_uint16 * 1),
    ]


class _WindowsFileDispositionInformation(ctypes.Structure):
    _fields_ = [("delete_file", ctypes.c_ubyte)]


class _WindowsPersistenceApi:
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _DELETE = 0x00010000
    _FILE_LIST_DIRECTORY = 0x00000001
    _FILE_TRAVERSE = 0x00000020
    _FILE_READ_ATTRIBUTES = 0x00000080
    _SYNCHRONIZE = 0x00100000
    _SHARE_READ = 0x00000001
    _SHARE_WRITE = 0x00000002
    _SHARE_DELETE = 0x00000004
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _FILE_OPEN = 1
    _FILE_CREATE = 2
    _FILE_OPEN_IF = 3
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _DUPLICATE_SAME_ACCESS = 0x00000002
    _FILE_DISPOSITION_INFORMATION = 4
    _FILE_RENAME_INFORMATION = 10
    _FILE_NAMES_INFORMATION = 12
    _STATUS_BUFFER_OVERFLOW = 0x80000005
    _STATUS_NO_MORE_FILES = 0x80000006
    _QUERY_BUFFER_BYTES = 64 * 1024
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    def __init__(self) -> None:
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise PersistenceIOError("secure persistence primitives are unavailable")
        try:
            self.kernel32 = win_dll("kernel32", use_last_error=True)
            self.ntdll = win_dll("ntdll", use_last_error=True)
        except OSError as exc:
            raise PersistenceIOError("secure persistence primitives are unavailable") from exc
        self.create_file = self.kernel32.CreateFileW
        self.create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self.create_file.restype = ctypes.c_void_p
        self.close_handle = self.kernel32.CloseHandle
        self.close_handle.argtypes = [ctypes.c_void_p]
        self.close_handle.restype = ctypes.c_int
        self.duplicate_handle = self.kernel32.DuplicateHandle
        self.duplicate_handle.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        self.duplicate_handle.restype = ctypes.c_int
        self.current_process = self.kernel32.GetCurrentProcess
        self.current_process.argtypes = []
        self.current_process.restype = ctypes.c_void_p
        self.flush_file_buffers = self.kernel32.FlushFileBuffers
        self.flush_file_buffers.argtypes = [ctypes.c_void_p]
        self.flush_file_buffers.restype = ctypes.c_int
        self.set_information = self.kernel32.SetFileInformationByHandle
        self.set_information.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self.set_information.restype = ctypes.c_int
        try:
            self.nt_create_file = self.ntdll.NtCreateFile
            self.nt_query_directory = self.ntdll.NtQueryDirectoryFile
            self.nt_set_information = self.ntdll.NtSetInformationFile
            self.nt_status_to_dos_error = self.ntdll.RtlNtStatusToDosError
        except AttributeError as exc:
            raise PersistenceIOError("secure persistence primitives are unavailable") from exc
        self.nt_create_file.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_ulong,
            ctypes.POINTER(_WindowsObjectAttributes),
            ctypes.POINTER(_WindowsIoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        self.nt_create_file.restype = ctypes.c_long
        self.nt_query_directory.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(_WindowsIoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_ubyte,
            ctypes.c_void_p,
            ctypes.c_ubyte,
        ]
        self.nt_query_directory.restype = ctypes.c_long
        self.nt_set_information.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WindowsIoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_int,
        ]
        self.nt_set_information.restype = ctypes.c_int32
        self.nt_status_to_dos_error.argtypes = [ctypes.c_int32]
        self.nt_status_to_dos_error.restype = ctypes.c_uint32

    @property
    def share_all(self) -> int:
        return self._SHARE_READ | self._SHARE_WRITE | self._SHARE_DELETE

    @staticmethod
    def _handle_value(value: object) -> int:
        result = ctypes.cast(value, ctypes.c_void_p).value
        if result in {None, _WindowsPersistenceApi._INVALID_HANDLE}:
            raise PersistenceIOError("secure persistence handle creation failed")
        return int(result)

    def _state(self, handle: int, *, directory: bool, context: str) -> FileStat:
        try:
            info = windows_handle_file_stat(handle)
        except OSError as exc:
            raise PersistenceIOError(f"Could not inspect {context}: {exc}") from exc
        expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if _is_link_or_reparse(info) or not expected:
            kind = "directory" if directory else "regular file"
            raise PersistenceIOError(
                f"{context} is not a safe {kind}",
                reason_code=(
                    "persistence_parent_unsafe" if directory else "persistence_target_unsafe"
                ),
            )
        return info

    def open_anchor(self, path: Path) -> int:
        handle = self.create_file(
            str(path),
            self._FILE_LIST_DIRECTORY
            | self._FILE_TRAVERSE
            | self._FILE_READ_ATTRIBUTES
            | self._SYNCHRONIZE,
            self.share_all,
            None,
            self._OPEN_EXISTING,
            self._FILE_FLAG_BACKUP_SEMANTICS | self._FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        result = self._handle_value(handle)
        try:
            self._state(result, directory=True, context=f"persistence ancestor {path}")
        except BaseException:
            self.close(result)
            raise
        return result

    @staticmethod
    def _relative_name(
        parent: int,
        name: str,
    ) -> tuple[
        ctypes.Array[ctypes.c_wchar],
        _WindowsUnicodeString,
        _WindowsObjectAttributes,
    ]:
        if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise PersistenceIOError(
                "Windows persistence entry component is invalid",
                reason_code="persistence_path_identity_invalid",
            )
        encoded = name.encode("utf-16-le", errors="strict")
        if len(encoded) > 65_532:
            raise PersistenceIOError("Windows persistence entry component is too long")
        buffer = ctypes.create_unicode_buffer(name)
        unicode_name = _WindowsUnicodeString(
            len(encoded),
            len(encoded) + 2,
            ctypes.cast(buffer, ctypes.c_wchar_p),
        )
        attributes = _WindowsObjectAttributes(
            ctypes.sizeof(_WindowsObjectAttributes),
            ctypes.c_void_p(parent),
            ctypes.pointer(unicode_name),
            _WindowsPersistenceApi._OBJ_CASE_INSENSITIVE,
            None,
            None,
        )
        return buffer, unicode_name, attributes

    def _open_relative(
        self,
        parent: int,
        name: str,
        *,
        access: int,
        disposition: int,
        share: int,
        options: int,
        context: str,
    ) -> int:
        buffer, unicode_name, attributes = self._relative_name(parent, name)
        io_status = _WindowsIoStatusBlock()
        output = ctypes.c_void_p()
        status_code = int(
            self.nt_create_file(
                ctypes.byref(output),
                access,
                ctypes.byref(attributes),
                ctypes.byref(io_status),
                None,
                self._FILE_ATTRIBUTE_NORMAL,
                share,
                disposition,
                options | self._FILE_OPEN_REPARSE_POINT | self._FILE_SYNCHRONOUS_IO_NONALERT,
                None,
                0,
            )
        )
        del buffer, unicode_name
        if status_code < 0:
            error = int(self.nt_status_to_dos_error(ctypes.c_int32(status_code)))
            if disposition == self._FILE_CREATE and error in {80, 183}:
                raise FileExistsError(error, "entry already exists", name)
            if error in {2, 3}:
                raise FileNotFoundError(error, "entry is missing", name)
            raise PersistenceIOError(f"Could not {context}: error {error}")
        return self._handle_value(output)

    def open_relative_directory(
        self,
        parent: int,
        name: str,
        *,
        create: bool,
        writable: bool = False,
    ) -> int:
        access = (
            self._FILE_LIST_DIRECTORY
            | self._FILE_TRAVERSE
            | self._FILE_READ_ATTRIBUTES
            | self._SYNCHRONIZE
        )
        if writable:
            access |= self._GENERIC_WRITE
        result = self._open_relative(
            parent,
            name,
            access=access,
            disposition=self._FILE_OPEN_IF if create else self._FILE_OPEN,
            share=self.share_all,
            options=self._FILE_DIRECTORY_FILE,
            context=f"open or create persistence ancestor {name}",
        )
        try:
            self._state(result, directory=True, context=f"persistence ancestor {name}")
        except BaseException:
            self.close(result)
            raise
        return result

    def open_ancestry(
        self,
        path: Path,
        *,
        create: bool,
    ) -> tuple[list[int], tuple[tuple[int, int], ...]]:
        handles: list[int] = []
        identities: list[tuple[int, int]] = []
        try:
            handle = self.open_anchor(Path(path.anchor))
            handles.append(handle)
            identities.append(
                _entry_identity(
                    self._state(
                        handle,
                        directory=True,
                        context=f"persistence ancestor {path.anchor}",
                    )
                )
            )
            for part in path.parts[1:]:
                handle = self.open_relative_directory(handles[-1], part, create=create)
                handles.append(handle)
                identities.append(
                    _entry_identity(
                        self._state(
                            handle,
                            directory=True,
                            context=f"persistence ancestor {part}",
                        )
                    )
                )
            return handles, tuple(identities)
        except BaseException:
            self.close_many(handles)
            raise

    def create_temporary(self, parent: int, name: str) -> int:
        return self._open_relative(
            parent,
            name,
            access=self._GENERIC_READ
            | self._GENERIC_WRITE
            | self._DELETE
            | self._FILE_READ_ATTRIBUTES
            | self._SYNCHRONIZE,
            disposition=self._FILE_CREATE,
            share=self._SHARE_READ,
            options=self._FILE_NON_DIRECTORY_FILE,
            context=f"create temporary persistence output {name}",
        )

    def open_lock(self, parent: int, name: str) -> int:
        return self._open_relative(
            parent,
            name,
            access=self._GENERIC_READ
            | self._GENERIC_WRITE
            | self._FILE_READ_ATTRIBUTES
            | self._SYNCHRONIZE,
            disposition=self._FILE_OPEN_IF,
            share=self.share_all,
            options=self._FILE_NON_DIRECTORY_FILE,
            context=f"open persistence lock {name}",
        )

    def open_existing_entry(self, parent: int, name: str) -> int:
        return self._open_relative(
            parent,
            name,
            access=self._GENERIC_READ | self._FILE_READ_ATTRIBUTES | self._SYNCHRONIZE,
            disposition=self._FILE_OPEN,
            share=self.share_all,
            options=0,
            context=f"open persistence entry {name}",
        )

    def open_existing_file(
        self,
        parent: int,
        name: str,
        *,
        writable: bool = False,
        share_write: bool = True,
        share_delete: bool = True,
    ) -> int:
        access = self._GENERIC_READ | self._FILE_READ_ATTRIBUTES | self._SYNCHRONIZE
        if writable:
            access |= self._GENERIC_WRITE
        share = self._SHARE_READ
        if share_write:
            share |= self._SHARE_WRITE
        if share_delete:
            share |= self._SHARE_DELETE
        return self._open_relative(
            parent,
            name,
            access=access,
            disposition=self._FILE_OPEN,
            share=share,
            options=self._FILE_NON_DIRECTORY_FILE,
            context=f"open persistence file {name}",
        )

    def entry_info(self, handle: int, *, context: str) -> FileStat:
        try:
            return windows_handle_file_stat(handle)
        except OSError as exc:
            raise PersistenceIOError(f"Could not inspect {context}: {exc}") from exc

    @staticmethod
    def _unsigned_status(status: int) -> int:
        return ctypes.c_uint32(status).value

    def directory_names(self, handle: int, *, context: str) -> tuple[str, ...]:
        names: list[str] = []
        first_query = True
        while True:
            io_status = _WindowsIoStatusBlock()
            buffer = ctypes.create_string_buffer(self._QUERY_BUFFER_BYTES)
            status = int(
                self.nt_query_directory(
                    ctypes.c_void_p(handle),
                    None,
                    None,
                    None,
                    ctypes.byref(io_status),
                    buffer,
                    len(buffer),
                    self._FILE_NAMES_INFORMATION,
                    0,
                    None,
                    int(first_query),
                )
            )
            first_query = False
            unsigned = self._unsigned_status(status)
            if unsigned == self._STATUS_NO_MORE_FILES:
                break
            if unsigned not in {0, self._STATUS_BUFFER_OVERFLOW}:
                error = int(self.nt_status_to_dos_error(ctypes.c_int32(status)))
                raise PersistenceIOError(
                    f"Could not enumerate {context}: error {error}",
                    reason_code="persistence_generation_inventory_unsafe",
                )
            used = int(io_status.information)
            if used < 0 or used > len(buffer):
                raise PersistenceIOError(
                    f"Windows returned an invalid inventory for {context}",
                    reason_code="persistence_generation_inventory_unsafe",
                )
            offset = 0
            while offset < used:
                if used - offset < 12:
                    raise PersistenceIOError(
                        f"Windows returned a truncated inventory for {context}",
                        reason_code="persistence_generation_inventory_unsafe",
                    )
                next_offset = int.from_bytes(buffer.raw[offset : offset + 4], "little")
                name_bytes = int.from_bytes(buffer.raw[offset + 8 : offset + 12], "little")
                name_end = offset + 12 + name_bytes
                if (
                    name_bytes % 2
                    or name_end > used
                    or (
                        next_offset != 0
                        and (next_offset < 12 + name_bytes or offset + next_offset > used)
                    )
                ):
                    raise PersistenceIOError(
                        f"Windows returned an invalid entry for {context}",
                        reason_code="persistence_generation_inventory_unsafe",
                    )
                try:
                    name = buffer.raw[offset + 12 : name_end].decode(
                        "utf-16-le",
                        errors="strict",
                    )
                except UnicodeError as exc:
                    raise PersistenceIOError(
                        f"Windows returned a non-Unicode entry for {context}",
                        reason_code="persistence_generation_inventory_unsafe",
                    ) from exc
                if name not in {".", ".."}:
                    self._relative_name(handle, name)
                    names.append(name)
                if next_offset == 0:
                    break
                offset += next_offset
            if unsigned == 0 and used == 0:
                break
        if len(names) != len(set(names)):
            raise PersistenceIOError(
                f"Windows returned duplicate entries for {context}",
                reason_code="persistence_generation_inventory_unsafe",
            )
        try:
            return tuple(sorted(names, key=lambda item: item.encode("utf-8", errors="strict")))
        except UnicodeError as exc:
            raise PersistenceIOError(
                f"Windows returned an invalid Unicode entry for {context}",
                reason_code="persistence_generation_inventory_unsafe",
            ) from exc

    def flush_relative_directory(
        self,
        parent: int,
        name: str,
        expected_identity: tuple[int, int],
        *,
        context: str,
    ) -> None:
        handle = self.open_relative_directory(
            parent,
            name,
            create=False,
            writable=True,
        )
        try:
            before = self._state(handle, directory=True, context=context)
            if _entry_identity(before) != expected_identity:
                raise PersistenceIOError(
                    f"{context} identity changed before durable flush",
                    reason_code="persistence_parent_unsafe",
                )
            if not self.flush_file_buffers(ctypes.c_void_p(handle)):
                error = ctypes.get_last_error()
                raise PersistenceIOError(
                    f"Could not durably flush {context}: error {error}",
                    reason_code="persistence_durability_unavailable",
                )
            after = self._state(handle, directory=True, context=context)
            if _entry_identity(after) != expected_identity:
                raise PersistenceIOError(
                    f"{context} identity changed after durable flush",
                    reason_code="persistence_parent_unsafe",
                )
        finally:
            self.close(handle)

    def duplicate_to_descriptor(self, handle: int, *, writable: bool) -> int:
        duplicate = ctypes.c_void_p()
        process = self.current_process()
        if not self.duplicate_handle(
            process,
            ctypes.c_void_p(handle),
            process,
            ctypes.byref(duplicate),
            0,
            False,
            self._DUPLICATE_SAME_ACCESS,
        ):
            raise PersistenceIOError("Could not duplicate a Windows persistence handle")
        try:
            import msvcrt

            flags = os.O_BINARY | (os.O_RDWR if writable else os.O_RDONLY)
            return msvcrt.open_osfhandle(int(duplicate.value), flags)
        except Exception as exc:
            self.close(int(duplicate.value))
            raise PersistenceIOError("Could not convert a Windows persistence handle") from exc

    def rename(
        self,
        handle: int,
        parent_handle: int,
        destination_name: str,
        *,
        replace: bool,
    ) -> None:
        encoded = destination_name.encode("utf-16-le", errors="strict")
        offset = _WindowsFileRenameInformation.filename.offset
        buffer = ctypes.create_string_buffer(
            max(ctypes.sizeof(_WindowsFileRenameInformation), offset + len(encoded))
        )
        information = _WindowsFileRenameInformation.from_buffer(buffer)
        information.replace_if_exists = replace
        information.root_directory = parent_handle
        information.filename_length = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
        io_status = _WindowsIoStatusBlock()
        status = ctypes.c_int32(
            int(
                self.nt_set_information(
                    ctypes.c_void_p(handle),
                    ctypes.byref(io_status),
                    buffer,
                    len(buffer),
                    self._FILE_RENAME_INFORMATION,
                )
            )
        ).value
        if status < 0:
            error = int(self.nt_status_to_dos_error(status))
            if not replace and error in {80, 183}:
                raise FileExistsError(error, "entry already exists", destination_name)
            raise PersistenceIOError(f"Could not publish Windows persistence: error {error}")

    def mark_delete_on_close(self, handle: int) -> None:
        disposition = _WindowsFileDispositionInformation(1)
        if not self.set_information(
            ctypes.c_void_p(handle),
            self._FILE_DISPOSITION_INFORMATION,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            error = ctypes.get_last_error()
            raise PersistenceIOError(
                f"Could not retain exact Windows temporary cleanup: error {error}"
            )

    def close(self, handle: int) -> None:
        if handle and not self.close_handle(ctypes.c_void_p(handle)):
            raise PersistenceIOError("Could not close a Windows persistence handle")

    def close_many(self, handles: list[int] | tuple[int, ...]) -> None:
        first_error: PersistenceIOError | None = None
        for handle in reversed(handles):
            try:
                self.close(handle)
            except PersistenceIOError as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


@dataclass(slots=True)
class _PinnedOutputParent:
    path: Path
    identities: tuple[tuple[int, int], ...]
    posix_descriptors: tuple[int, ...] = ()
    windows_api: _WindowsPersistenceApi | None = None
    windows_handles: tuple[int, ...] = ()

    @property
    def parent_fd(self) -> int | None:
        return self.posix_descriptors[-1] if self.posix_descriptors else None

    @property
    def windows_parent_handle(self) -> int | None:
        return self.windows_handles[-1] if self.windows_handles else None

    def assert_current(self) -> None:
        try:
            if self.posix_descriptors:
                verification, visible = _open_posix_ancestry(self.path, create=False)
                try:
                    if visible != self.identities:
                        raise PersistenceIOError(
                            f"Persistence ancestry changed: {self.path}",
                            reason_code="persistence_parent_unsafe",
                        )
                finally:
                    _close_posix_descriptors(verification)
                return
            if self.windows_api is None or not self.windows_handles:
                raise PersistenceIOError("secure persistence primitives are unavailable")
            verification, visible = self.windows_api.open_ancestry(
                self.path,
                create=False,
            )
            try:
                if visible != self.identities:
                    raise PersistenceIOError(
                        f"Persistence ancestry changed: {self.path}",
                        reason_code="persistence_parent_unsafe",
                    )
            finally:
                self.windows_api.close_many(verification)
        except PersistenceIOError as exc:
            if exc.reason_code == "persistence_parent_unsafe":
                raise
            raise PersistenceIOError(
                f"Persistence ancestry is no longer safe: {self.path}: {exc.detail}",
                reason_code="persistence_parent_unsafe",
            ) from exc
        except OSError as exc:
            raise PersistenceIOError(
                f"Persistence ancestry is no longer safe: {self.path}: {exc}",
                reason_code="persistence_parent_unsafe",
            ) from exc

    def close(self) -> None:
        if self.posix_descriptors:
            _close_posix_descriptors(self.posix_descriptors)
        elif self.windows_api is not None:
            self.windows_api.close_many(self.windows_handles)


def _fsync_retained_ancestry(parent: _PinnedOutputParent) -> None:
    """Durably flush every retained directory below the filesystem anchor."""

    parent.assert_current()
    if parent.posix_descriptors:
        for descriptor in parent.posix_descriptors[1:]:
            os.fsync(descriptor)
    elif parent.windows_api is not None and parent.windows_handles:
        parts = parent.path.parts
        if len(parts) != len(parent.windows_handles):
            raise PersistenceIOError(
                "Retained Windows persistence ancestry is inconsistent",
                reason_code="persistence_parent_unsafe",
            )
        for index in range(1, len(parent.windows_handles)):
            parent.windows_api.flush_relative_directory(
                parent.windows_handles[index - 1],
                parts[index],
                parent.identities[index],
                context=f"persistence directory {Path(*parts[: index + 1])}",
            )
    else:
        raise PersistenceIOError(
            "Durable persistence ancestry flush is unavailable",
            reason_code="persistence_durability_unavailable",
        )
    parent.assert_current()


@contextmanager
def _open_verified_output_parent(
    path: Path,
    *,
    create: bool = True,
) -> Iterator[_PinnedOutputParent]:
    """Retain the complete ancestry and fail closed without safe primitives."""

    absolute = Path(os.path.abspath(path))
    pinned: _PinnedOutputParent | None = None
    try:
        if os.name == "posix":
            if not _DIR_FD_PUBLICATION:
                raise PersistenceIOError("secure persistence primitives are unavailable")
            descriptors, identities = _open_posix_ancestry(absolute, create=create)
            pinned = _PinnedOutputParent(
                absolute,
                identities,
                posix_descriptors=tuple(descriptors),
            )
        elif os.name == "nt":
            api = _WindowsPersistenceApi()
            handles, identities = api.open_ancestry(absolute, create=create)
            pinned = _PinnedOutputParent(
                absolute,
                identities,
                windows_api=api,
                windows_handles=tuple(handles),
            )
        else:
            raise PersistenceIOError("secure persistence primitives are unavailable")
        pinned.assert_current()
        if create:
            _fsync_retained_ancestry(pinned)
        yield pinned
        pinned.assert_current()
    except PersistenceIOError:
        raise
    except (OSError, ValueError) as exc:
        unsafe = not create or (
            isinstance(exc, OSError) and exc.errno in {errno.ELOOP, errno.ENOTDIR}
        )
        raise PersistenceIOError(
            f"Persistence parent could not be retained safely: {absolute}: {exc}",
            reason_code=("persistence_parent_unsafe" if unsafe else "persistence_io_error"),
        ) from exc
    finally:
        if pinned is not None:
            primary = sys.exception()
            try:
                pinned.close()
            except (OSError, PersistenceIOError) as exc:
                if primary is not None:
                    primary.add_note(f"Persistence ancestry cleanup failed: {exc}")
                else:
                    raise PersistenceIOError(
                        f"Could not release persistence ancestry {absolute}: {exc}"
                    ) from exc


def _entry_info(parent: _PinnedOutputParent, name: str) -> FileStat | None:
    handle: int | None = None
    try:
        if parent.parent_fd is not None:
            return os.stat(name, dir_fd=parent.parent_fd, follow_symlinks=False)
        if parent.windows_api is None or parent.windows_parent_handle is None:
            raise PersistenceIOError("secure persistence primitives are unavailable")
        handle = parent.windows_api.open_existing_entry(
            parent.windows_parent_handle,
            name,
        )
        return parent.windows_api.entry_info(
            handle,
            context=f"persistence entry {parent.path / name}",
        )
    except FileNotFoundError:
        return None
    finally:
        if handle is not None and parent.windows_api is not None:
            parent.windows_api.close(handle)


def _publication_component(name: str) -> str:
    if (
        type(name) is not str
        or not name
        or len(name) > 255
        or name in {".", ".."}
        or name.endswith(".")
        or any(
            not (character.isascii() and (character.isalnum() or character in "._-"))
            for character in name
        )
    ):
        raise PersistenceIOError(
            "Retained publication names must be portable filename components",
            reason_code="persistence_target_unsafe",
        )
    stem = name.split(".", 1)[0].casefold()
    if stem in {"aux", "con", "nul", "prn"} or (
        len(stem) == 4 and stem[:3] in {"com", "lpt"} and stem[3] in "123456789"
    ):
        raise PersistenceIOError(
            "Retained publication name uses a reserved filename component",
            reason_code="persistence_target_unsafe",
        )
    return name


@dataclass(slots=True)
class _RetainedPublicationParent:
    """Internal retained ancestry used by exact byte publication."""

    _parent: _PinnedOutputParent

    @property
    def path(self) -> Path:
        return self._parent.path

    @property
    def identity(self) -> tuple[int, int]:
        return self._parent.identities[-1]

    def assert_current(self) -> None:
        self._parent.assert_current()

    def flush(self) -> None:
        if self._parent.parent_fd is not None:
            os.fsync(self._parent.parent_fd)
            return
        if self._parent.windows_api is None or self._parent.windows_parent_handle is None:
            raise PersistenceIOError(
                "Secure retained package durability is unavailable",
                reason_code="persistence_durability_unavailable",
            )
        handle = self._parent.windows_parent_handle
        before = self._parent.windows_api._state(
            handle,
            directory=True,
            context=f"retained publication parent {self.path}",
        )
        if _entry_identity(before) != self.identity:
            raise PersistenceIOError(
                "Retained package parent identity changed before flush",
                reason_code="persistence_parent_unsafe",
            )
        if not self._parent.windows_api.flush_file_buffers(ctypes.c_void_p(handle)):
            error = ctypes.get_last_error()
            raise PersistenceIOError(
                f"Could not flush retained package parent: error {error}",
                reason_code="persistence_durability_unavailable",
            )
        after = self._parent.windows_api._state(
            handle,
            directory=True,
            context=f"retained publication parent {self.path}",
        )
        if _entry_identity(after) != self.identity:
            raise PersistenceIOError(
                "Retained package parent identity changed after flush",
                reason_code="persistence_parent_unsafe",
            )


@contextmanager
def _retained_publication_parent(
    path: str | Path,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> Iterator[_RetainedPublicationParent]:
    """Retain package output ancestry for identity-relative publication."""

    absolute = Path(os.path.abspath(Path(path)))
    if os.name == "posix" and not _DIR_FD_PACKAGE_PUBLICATION:
        raise PersistenceIOError(
            "Secure retained package publication is unavailable",
            reason_code="persistence_atomic_replace_unavailable",
        )
    parent: _PinnedOutputParent | None = None
    try:
        try:
            if os.name == "posix":
                descriptors, identities = _open_posix_ancestry(absolute, create=False)
                parent = _PinnedOutputParent(
                    absolute,
                    identities,
                    posix_descriptors=tuple(descriptors),
                )
            elif os.name == "nt":
                api = _WindowsPersistenceApi()
                handles, identities = api.open_ancestry(absolute, create=False)
                parent = _PinnedOutputParent(
                    absolute,
                    identities,
                    windows_api=api,
                    windows_handles=tuple(handles),
                )
            else:
                raise PersistenceIOError(
                    "Secure retained package publication is unavailable",
                    reason_code="persistence_atomic_replace_unavailable",
                )
            if expected_identity is not None and parent.identities[-1] != expected_identity:
                raise PersistenceIOError(
                    "Retained package parent differs from the expected identity",
                    reason_code="persistence_parent_unsafe",
                )
            if parent.windows_api is not None:
                if len(parent.windows_handles) < 2:
                    raise PersistenceIOError(
                        "Windows filesystem-root package publication is unsupported",
                        reason_code="persistence_atomic_replace_unavailable",
                    )
                writable = parent.windows_api.open_relative_directory(
                    parent.windows_handles[-2],
                    absolute.name,
                    create=False,
                    writable=True,
                )
                try:
                    info = parent.windows_api._state(
                        writable,
                        directory=True,
                        context=f"retained publication parent {absolute}",
                    )
                    if _entry_identity(info) != parent.identities[-1]:
                        raise PersistenceIOError(
                            "Writable retained package parent identity differs",
                            reason_code="persistence_parent_unsafe",
                        )
                except BaseException:
                    parent.windows_api.close(writable)
                    raise
                parent.windows_api.close(parent.windows_handles[-1])
                parent.windows_handles = (*parent.windows_handles[:-1], writable)
            retained = _RetainedPublicationParent(parent)
            retained.assert_current()
        except PersistenceIOError:
            raise
        except (OSError, ValueError) as exc:
            raise PersistenceIOError(
                f"Package publication parent could not be retained safely: {absolute}: {exc}",
                reason_code="persistence_parent_unsafe",
            ) from exc
        yield retained
        retained.assert_current()
    finally:
        if parent is not None:
            primary = sys.exception()
            try:
                parent.close()
            except (OSError, PersistenceIOError) as exc:
                if primary is not None:
                    primary.add_note(f"Package publication ancestry cleanup failed: {exc}")
                else:
                    raise


def _validated_target_identity(
    info: FileStat | None,
    destination: Path,
) -> tuple[int, int] | None:
    if info is None:
        return None
    if _is_link_or_reparse(info):
        raise PersistenceIOError(
            f"Refusing to replace symbolic link or reparse point {destination}",
            reason_code="persistence_target_unsafe",
        )
    if not stat.S_ISREG(info.st_mode):
        raise PersistenceIOError(
            f"Refusing to replace non-regular file {destination}",
            reason_code="persistence_target_unsafe",
        )
    if info.st_nlink != 1:
        raise PersistenceIOError(
            f"Refusing to replace hard-linked file {destination}",
            reason_code="persistence_target_unsafe",
        )
    return _entry_identity(info)


def read_json_object(
    path: str | Path,
    *,
    limit: int,
) -> dict[str, Any]:
    """Read one bounded strict JSON object through a fully retained ancestry."""

    source = Path(os.path.abspath(Path(path)))
    descriptor: int | None = None
    windows_handle: int | None = None
    try:
        with _open_verified_output_parent(source.parent, create=False) as parent:
            parent.assert_current()
            before = _entry_info(parent, source.name)
            before_identity = _validated_target_identity(before, source)
            if before_identity is None:
                raise FileNotFoundError(source)
            if parent.parent_fd is not None:
                descriptor = os.open(
                    source.name,
                    os.O_RDONLY
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=parent.parent_fd,
                )
                info = descriptor_file_stat(descriptor)
            elif parent.windows_api is not None and parent.windows_parent_handle is not None:
                windows_handle = parent.windows_api.open_existing_file(
                    parent.windows_parent_handle,
                    source.name,
                )
                info = parent.windows_api._state(
                    windows_handle,
                    directory=False,
                    context=f"persistence file {source}",
                )
                descriptor = parent.windows_api.duplicate_to_descriptor(
                    windows_handle,
                    writable=False,
                )
            else:
                raise PersistenceIOError("secure persistence primitives are unavailable")
            current_identity = _validated_target_identity(
                _entry_info(parent, source.name),
                source,
            )
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or _entry_identity(info) != before_identity
                or current_identity != before_identity
            ):
                raise PersistenceIOError(
                    f"Refusing to read unsafe persistence file {source}",
                    reason_code="persistence_target_unsafe",
                )
            if info.st_size > limit:
                raise OSError(f"exceeds the {limit}-byte limit")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                payload = stream.read(limit + 1)
                after = descriptor_file_stat(stream.fileno())
            if len(payload) > limit:
                raise OSError(f"exceeds the {limit}-byte limit")
            final_identity = _validated_target_identity(
                _entry_info(parent, source.name),
                source,
            )
            if (
                not stat.S_ISREG(after.st_mode)
                or after.st_nlink != 1
                or _entry_identity(after) != before_identity
                or final_identity != before_identity
                or after.st_size != info.st_size
                or after.st_mtime_ns != info.st_mtime_ns
                or after.st_ctime_ns != info.st_ctime_ns
            ):
                raise PersistenceIOError(
                    f"Persistence file changed while being read: {source}",
                    reason_code="persistence_target_unsafe",
                )
            parent.assert_current()
    except PersistenceIOError:
        raise
    except OSError as exc:
        raise PersistenceIOError(f"Could not read {source}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if windows_handle is not None:
            try:
                if "parent" in locals() and parent.windows_api is not None:
                    parent.windows_api.close(windows_handle)
            except PersistenceIOError:
                if sys.exception() is None:
                    raise
    return decode_json_object(payload, source=source, limit=limit)


def inspect_safe_entry(path: str | Path) -> str | None:
    """Return ``file``/``directory``/``None`` through one retained parent."""

    source = Path(os.path.abspath(Path(path)))
    try:
        with _open_verified_output_parent(source.parent, create=False) as parent:
            parent.assert_current()
            info = _entry_info(parent, source.name)
            if info is None:
                return None
            if _is_link_or_reparse(info):
                raise PersistenceIOError(
                    f"Persistence entry is a link or reparse point: {source}",
                    reason_code="persistence_target_unsafe",
                )
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise PersistenceIOError(
                        f"Persistence entry is hard-linked: {source}",
                        reason_code="persistence_target_unsafe",
                    )
                kind = "file"
            elif stat.S_ISDIR(info.st_mode):
                kind = "directory"
            else:
                raise PersistenceIOError(
                    f"Persistence entry has an unsupported type: {source}",
                    reason_code="persistence_target_unsafe",
                )
            parent.assert_current()
            return kind
    except PersistenceIOError:
        raise
    except OSError as exc:
        raise PersistenceIOError(f"Could not inspect {source}: {exc}") from exc


def _directory_names(parent: _PinnedOutputParent) -> tuple[str, ...]:
    parent.assert_current()
    try:
        if parent.parent_fd is not None:
            names = os.listdir(parent.parent_fd)
            try:
                result = tuple(
                    sorted(names, key=lambda item: item.encode("utf-8", errors="strict"))
                )
            except UnicodeError as exc:
                raise PersistenceIOError(
                    f"Persistence directory contains a non-Unicode entry: {parent.path}",
                    reason_code="persistence_generation_inventory_unsafe",
                ) from exc
        elif parent.windows_api is not None and parent.windows_parent_handle is not None:
            result = parent.windows_api.directory_names(
                parent.windows_parent_handle,
                context=f"persistence directory {parent.path}",
            )
        else:
            raise PersistenceIOError("secure persistence primitives are unavailable")
    except PersistenceIOError:
        raise
    except OSError as exc:
        raise PersistenceIOError(
            f"Could not enumerate persistence directory {parent.path}: {exc}",
            reason_code="persistence_generation_inventory_unsafe",
        ) from exc
    if len(result) != len(set(result)):
        raise PersistenceIOError(
            f"Persistence directory returned duplicate entries: {parent.path}",
            reason_code="persistence_generation_inventory_unsafe",
        )
    parent.assert_current()
    return result


def _read_pinned_file(
    parent: _PinnedOutputParent,
    name: str,
    *,
    limit: int,
    deny_mutation_sharing: bool = False,
) -> bytes:
    source = parent.path / name
    descriptor: int | None = None
    windows_handle: int | None = None
    try:
        before = _entry_info(parent, name)
        before_identity = _validated_target_identity(before, source)
        if before_identity is None:
            raise PersistenceIOError(
                f"Persistence inventory entry disappeared: {source}",
                reason_code="persistence_generation_inventory_unsafe",
            )
        assert before is not None
        if before.st_size > limit:
            raise PersistenceIOError(
                f"{source} exceeds the {limit}-byte limit",
                reason_code="persistence_bytes_exceeded",
            )
        if parent.parent_fd is not None:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=parent.parent_fd,
            )
            opened = descriptor_file_stat(descriptor)
        elif parent.windows_api is not None and parent.windows_parent_handle is not None:
            windows_handle = parent.windows_api.open_existing_file(
                parent.windows_parent_handle,
                name,
                share_write=not deny_mutation_sharing,
                share_delete=not deny_mutation_sharing,
            )
            opened = parent.windows_api._state(
                windows_handle,
                directory=False,
                context=f"persistence inventory file {source}",
            )
            descriptor = parent.windows_api.duplicate_to_descriptor(
                windows_handle,
                writable=False,
            )
        else:
            raise PersistenceIOError("secure persistence primitives are unavailable")
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _entry_identity(opened) != before_identity
        ):
            raise PersistenceIOError(
                f"Persistence inventory entry is unsafe: {source}",
                reason_code="persistence_generation_inventory_unsafe",
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            payload = stream.read(limit + 1)
            repeated: bytes | None = None
            if deny_mutation_sharing:
                stream.seek(0)
                repeated = stream.read(limit + 1)
            after = descriptor_file_stat(stream.fileno())
        if len(payload) > limit:
            raise PersistenceIOError(
                f"{source} exceeds the {limit}-byte limit",
                reason_code="persistence_bytes_exceeded",
            )
        current = _entry_info(parent, name)
        current_identity = _validated_target_identity(current, source)
        if (
            len(payload) != before.st_size
            or (repeated is not None and repeated != payload)
            or not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or _entry_identity(after) != before_identity
            or current_identity != before_identity
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise PersistenceIOError(
                f"Persistence inventory entry changed while being read: {source}",
                reason_code="persistence_generation_inventory_unsafe",
            )
        parent.assert_current()
        return payload
    except PersistenceIOError:
        raise
    except OSError as exc:
        raise PersistenceIOError(
            f"Could not read persistence inventory entry {source}: {exc}",
            reason_code="persistence_generation_inventory_unsafe",
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if windows_handle is not None and parent.windows_api is not None:
            parent.windows_api.close(windows_handle)


def read_immutable_file_bytes(
    path: str | Path,
    *,
    limit: int,
) -> bytes:
    """Read one exact immutable file without following links or reparse points.

    The complete parent ancestry and the leaf are retained for the read. Native
    Windows opens deny write and delete sharing, and file state comes from the
    retained kernel handle so NTFS ChangeTime and reparse attributes participate
    in the before/after contract.
    """

    if type(limit) is not int or type(limit) is bool or limit < 1:
        raise PersistenceIOError(
            "Immutable file byte limit must be a positive exact integer",
            reason_code="persistence_limit_invalid",
        )
    source = Path(os.path.abspath(Path(path)))
    if not source.name:
        raise PersistenceIOError(
            "Immutable file path must identify one leaf",
            reason_code="persistence_target_unsafe",
        )
    with _open_verified_output_parent(source.parent, create=False) as parent:
        parent.assert_current()
        payload = _read_pinned_file(
            parent,
            source.name,
            limit=limit,
            deny_mutation_sharing=True,
        )
        parent.assert_current()
        return payload


def read_directory_files(
    path: str | Path,
    *,
    maximum_entries: int,
    maximum_file_bytes: int,
    maximum_total_bytes: int,
) -> dict[str, bytes]:
    """Read a bounded immutable directory inventory through retained handles."""

    if (
        type(maximum_entries) is not int
        or type(maximum_file_bytes) is not int
        or type(maximum_total_bytes) is not int
        or min(maximum_entries, maximum_file_bytes, maximum_total_bytes) < 1
    ):
        raise PersistenceIOError(
            "Persistence inventory limits must be positive exact integers",
            reason_code="persistence_limit_invalid",
        )
    directory = Path(os.path.abspath(Path(path)))
    with _open_verified_output_parent(directory, create=False) as parent:
        names = _directory_names(parent)
        if len(names) > maximum_entries:
            raise PersistenceIOError(
                f"Persistence inventory exceeds {maximum_entries} entries",
                reason_code="persistence_generation_limit",
            )
        payloads: dict[str, bytes] = {}
        identities: dict[str, tuple[int, int]] = {}
        total = 0
        for name in names:
            info = _entry_info(parent, name)
            if (
                info is None
                or _is_link_or_reparse(info)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
            ):
                raise PersistenceIOError(
                    f"Persistence inventory contains an unsafe entry: {directory / name}",
                    reason_code="persistence_generation_inventory_unsafe",
                )
            total += info.st_size
            if total > maximum_total_bytes:
                raise PersistenceIOError(
                    f"Persistence inventory exceeds {maximum_total_bytes} bytes",
                    reason_code="persistence_generation_limit",
                )
            payloads[name] = _read_pinned_file(
                parent,
                name,
                limit=maximum_file_bytes,
            )
            identities[name] = _entry_identity(info)
        if _directory_names(parent) != names:
            raise PersistenceIOError(
                f"Persistence inventory changed while being read: {directory}",
                reason_code="persistence_generation_inventory_unsafe",
            )
        for name, expected in identities.items():
            current = _entry_info(parent, name)
            if (
                current is None
                or _is_link_or_reparse(current)
                or not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or _entry_identity(current) != expected
            ):
                raise PersistenceIOError(
                    f"Persistence inventory entry changed after retained read: {directory / name}",
                    reason_code="persistence_generation_inventory_unsafe",
                )
        return payloads


def read_directory_entries(path: str | Path) -> dict[str, str]:
    """Return one safe retained directory's exact name-to-kind inventory."""

    directory = Path(os.path.abspath(Path(path)))
    with _open_verified_output_parent(directory, create=False) as parent:
        names = _directory_names(parent)
        result: dict[str, str] = {}
        identities: dict[str, tuple[int, int]] = {}
        for name in names:
            info = _entry_info(parent, name)
            if info is None or _is_link_or_reparse(info):
                raise PersistenceIOError(
                    f"Persistence directory contains an unsafe entry: {directory / name}",
                    reason_code="persistence_generation_inventory_unsafe",
                )
            if stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise PersistenceIOError(
                        f"Persistence directory contains a hard-linked file: {directory / name}",
                        reason_code="persistence_generation_inventory_unsafe",
                    )
                result[name] = "file"
            elif stat.S_ISDIR(info.st_mode):
                result[name] = "directory"
            else:
                raise PersistenceIOError(
                    f"Persistence directory contains an unsupported entry: {directory / name}",
                    reason_code="persistence_generation_inventory_unsafe",
                )
            identities[name] = _entry_identity(info)
        if _directory_names(parent) != names:
            raise PersistenceIOError(
                f"Persistence directory changed while being inspected: {directory}",
                reason_code="persistence_generation_inventory_unsafe",
            )
        for name, expected in identities.items():
            current = _entry_info(parent, name)
            if current is None or _entry_identity(current) != expected:
                raise PersistenceIOError(
                    "Persistence directory entry changed while being inspected: "
                    f"{directory / name}",
                    reason_code="persistence_generation_inventory_unsafe",
                )
        return result


@dataclass(slots=True)
class _TemporaryEntry:
    descriptor: int
    identity: tuple[int, int]
    stage_prefix: str
    name: str | None = None
    windows_handle: int | None = None
    published: bool = False
    discard: bool = False
    retained: bool = False


def _release_unreturned_entry(
    descriptor: int | None,
    windows_handle: int | None,
    windows_api: _WindowsPersistenceApi | None,
) -> None:
    primary = sys.exception()
    errors: list[BaseException] = []
    if descriptor is not None:
        try:
            os.close(descriptor)
        except OSError as exc:
            errors.append(exc)
    if windows_handle is not None and windows_api is not None:
        try:
            windows_api.mark_delete_on_close(windows_handle)
        except PersistenceIOError as exc:
            errors.append(exc)
        try:
            windows_api.close(windows_handle)
        except PersistenceIOError as exc:
            errors.append(exc)
    if errors:
        if primary is not None:
            primary.add_note(f"Persistence entry cleanup failed: {errors[0]}")
        else:
            raise PersistenceIOError(
                f"Could not release an unreturned persistence entry: {errors[0]}"
            )


def _create_temporary_entry(
    parent: _PinnedOutputParent,
    prefix: str,
) -> _TemporaryEntry:
    parent.assert_current()
    if parent.parent_fd is not None:
        if not sys.platform.startswith("linux") or not getattr(os, "O_TMPFILE", 0):
            raise PersistenceIOError(
                "Anonymous persistence publication is unavailable on this platform",
                reason_code="persistence_atomic_replace_unavailable",
            )
        descriptor: int | None = None
        try:
            descriptor = os.open(
                ".",
                os.O_RDWR
                | os.O_TMPFILE
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent.parent_fd,
            )
            info = descriptor_file_stat(descriptor)
            if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 0:
                raise PersistenceIOError(
                    "Anonymous temporary output is not an unlinked regular file"
                )
            parent.assert_current()
            return _TemporaryEntry(
                descriptor=descriptor,
                identity=_entry_identity(info),
                stage_prefix=prefix,
            )
        except PersistenceIOError:
            _release_unreturned_entry(descriptor, None, None)
            raise
        except OSError as exc:
            _release_unreturned_entry(descriptor, None, None)
            raise PersistenceIOError(
                f"Could not allocate an anonymous temporary output in {parent.path}: {exc}",
                reason_code="persistence_atomic_replace_unavailable",
            ) from exc

    for _ in range(100):
        parent.assert_current()
        name = f"{prefix}{secrets.token_hex(16)}"
        descriptor: int | None = None
        windows_handle: int | None = None
        try:
            if parent.windows_api is not None and parent.windows_parent_handle is not None:
                windows_handle = parent.windows_api.create_temporary(
                    parent.windows_parent_handle,
                    name,
                )
                info = parent.windows_api._state(
                    windows_handle,
                    directory=False,
                    context=f"temporary persistence output {parent.path / name}",
                )
                descriptor = parent.windows_api.duplicate_to_descriptor(
                    windows_handle,
                    writable=True,
                )
            else:
                raise PersistenceIOError("secure persistence primitives are unavailable")
        except FileExistsError:
            continue
        except PersistenceIOError:
            _release_unreturned_entry(
                descriptor,
                windows_handle,
                parent.windows_api,
            )
            raise
        except OSError as exc:
            _release_unreturned_entry(
                descriptor,
                windows_handle,
                parent.windows_api,
            )
            raise PersistenceIOError(
                f"Could not create a temporary output in {parent.path}: {exc}"
            ) from exc
        try:
            assert descriptor is not None
            descriptor_info = descriptor_file_stat(descriptor)
            visible = _entry_info(parent, name)
            if (
                visible is None
                or _is_link_or_reparse(info)
                or _is_link_or_reparse(descriptor_info)
                or _is_link_or_reparse(visible)
                or not stat.S_ISREG(info.st_mode)
                or not stat.S_ISREG(descriptor_info.st_mode)
                or not stat.S_ISREG(visible.st_mode)
                or info.st_nlink != 1
                or descriptor_info.st_nlink != 1
                or visible.st_nlink != 1
                or info.st_size != 0
                or descriptor_info.st_size != 0
                or visible.st_size != 0
                or _entry_identity(descriptor_info) != _entry_identity(info)
                or _entry_identity(visible) != _entry_identity(info)
            ):
                raise PersistenceIOError("Temporary output is not a standalone regular file")
            parent.assert_current()
        except BaseException:
            _release_unreturned_entry(
                descriptor,
                windows_handle,
                parent.windows_api,
            )
            raise
        assert descriptor is not None
        return _TemporaryEntry(
            descriptor=descriptor,
            identity=_entry_identity(info),
            stage_prefix=prefix,
            name=name,
            windows_handle=windows_handle,
        )
    raise PersistenceIOError(f"Could not allocate a temporary output in {parent.path}")


def _write_all(target: BinaryIO, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = target.write(remaining)
        if written is None or written <= 0:
            raise OSError("short write while publishing runtime JSON")
        remaining = remaining[written:]
    target.flush()
    os.fsync(target.fileno())


def _verify_owned_entry(
    parent: _PinnedOutputParent,
    name: str,
    identity: tuple[int, int],
) -> None:
    try:
        info = _entry_info(parent, name)
    except OSError as exc:
        raise PersistenceIOError(
            f"Could not verify temporary output {parent.path / name}: {exc}"
        ) from exc
    if (
        info is None
        or _is_link_or_reparse(info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or _entry_identity(info) != identity
    ):
        raise PersistenceIOError(f"Temporary output changed: {parent.path / name}")


def _verify_temporary_descriptor(
    parent: _PinnedOutputParent,
    temporary: _TemporaryEntry,
) -> None:
    parent.assert_current()
    try:
        info = descriptor_file_stat(temporary.descriptor)
    except OSError as exc:
        raise PersistenceIOError(f"Could not inspect temporary persistence output: {exc}") from exc
    expected_links = 0 if parent.parent_fd is not None else 1
    if (
        _is_link_or_reparse(info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != expected_links
        or _entry_identity(info) != temporary.identity
    ):
        raise PersistenceIOError("Temporary persistence output changed before publication")
    if temporary.name is not None:
        _verify_owned_entry(parent, temporary.name, temporary.identity)
    parent.assert_current()


def _linux_link_descriptor_no_replace(
    source_descriptor: int,
    destination_descriptor: int,
    destination_name: str,
) -> None:
    if not sys.platform.startswith("linux"):
        raise PersistenceIOError(
            "Identity-coupled persistence publication is unavailable on this platform",
            reason_code="persistence_atomic_replace_unavailable",
        )
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        linkat = libc.linkat
    except (AttributeError, OSError) as exc:
        raise PersistenceIOError(
            "Identity-coupled persistence publication is unavailable",
            reason_code="persistence_atomic_replace_unavailable",
        ) from exc
    linkat.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = linkat(
        source_descriptor,
        b"",
        destination_descriptor,
        os.fsencode(destination_name),
        _AT_EMPTY_PATH,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, "destination already exists", destination_name)
    if error in {
        errno.EINVAL,
        errno.ENOENT,
        errno.ENOSYS,
        errno.EPERM,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }:
        raise PersistenceIOError(
            "Identity-coupled persistence publication is unavailable",
            reason_code="persistence_atomic_replace_unavailable",
        )
    raise PersistenceIOError(f"Could not publish persistence output: {os.strerror(error)}")


def _linux_rename_name_noreplace(
    parent_descriptor: int,
    source_name: str,
    destination_name: str,
) -> None:
    if not sys.platform.startswith("linux"):
        raise PersistenceIOError(
            "Identity-coupled retained cleanup is unavailable on this platform",
            reason_code="persistence_atomic_replace_unavailable",
        )
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise PersistenceIOError(
            "Identity-coupled retained cleanup is unavailable",
            reason_code="persistence_atomic_replace_unavailable",
        ) from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = renameat2(
        parent_descriptor,
        os.fsencode(source_name),
        parent_descriptor,
        os.fsencode(destination_name),
        1,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, "destination already exists", destination_name)
    if error in {
        errno.EINVAL,
        errno.ENOSYS,
        errno.EPERM,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }:
        raise PersistenceIOError(
            "Identity-coupled retained cleanup is unavailable",
            reason_code="persistence_atomic_replace_unavailable",
        )
    raise PersistenceIOError(
        f"Could not claim retained publication entry: {os.strerror(error)}",
        reason_code="persistence_target_unsafe",
    )


def _linux_claim_and_remove_owned_entry(
    parent_descriptor: int,
    name: str,
    descriptor: int,
    expected_identity: tuple[int, int],
    *,
    expected_links: int,
) -> None:
    claim_name = f".game-package-delete-{secrets.token_hex(16)}"
    claim_error: BaseException | None = None
    claimed = False
    try:
        _linux_rename_name_noreplace(
            parent_descriptor,
            name,
            claim_name,
        )
        claimed = True
    except BaseException as original:
        try:
            retained = descriptor_file_stat(descriptor)
            named = os.stat(
                claim_name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            raise
        except BaseException as reconciliation_error:
            original.add_note(
                f"Retained cleanup claim reconciliation failed: {reconciliation_error}"
            )
            raise
        if (
            _is_link_or_reparse(retained)
            or _is_link_or_reparse(named)
            or not stat.S_ISREG(retained.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or retained.st_nlink != expected_links
            or named.st_nlink != expected_links
            or _entry_identity(retained) != expected_identity
            or _entry_identity(named) != expected_identity
        ):
            original.add_note(
                "Retained cleanup claim outcome is ambiguous; the claimed entry was preserved"
            )
            raise
        claimed = True
        claim_error = original
        original.add_note(
            "Retained cleanup claim completed before reporting failure; "
            "the exact owned entry was removed"
        )
    try:
        retained = descriptor_file_stat(descriptor)
        named = os.stat(
            claim_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if (
            _is_link_or_reparse(retained)
            or _is_link_or_reparse(named)
            or not stat.S_ISREG(retained.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or retained.st_nlink != expected_links
            or named.st_nlink != expected_links
            or _entry_identity(retained) != expected_identity
            or _entry_identity(named) != expected_identity
        ):
            raise PersistenceIOError(
                "Claimed retained publication entry changed identity",
                reason_code="persistence_target_unsafe",
            )
        os.unlink(claim_name, dir_fd=parent_descriptor)
        claimed = False
    except BaseException as original:
        if claimed:
            try:
                _linux_rename_name_noreplace(
                    parent_descriptor,
                    claim_name,
                    name,
                )
            except BaseException as rollback_error:
                raise PersistenceIOError(
                    "Retained cleanup claim could not be restored",
                    reason_code="persistence_target_unsafe",
                ) from rollback_error
        raise original
    try:
        remaining_links = descriptor_file_stat(descriptor).st_nlink
    except OSError as exc:
        raise PersistenceIOError(
            "Could not verify retained publication deletion",
            reason_code="persistence_target_unsafe",
        ) from exc
    if remaining_links != expected_links - 1:
        raise PersistenceIOError(
            "Retained publication entry remained linked after deletion",
            reason_code="persistence_target_unsafe",
        )
    if claim_error is not None:
        raise claim_error


def _read_exact_temporary_bytes(
    parent: _PinnedOutputParent,
    temporary: _TemporaryEntry,
    *,
    limit: int,
) -> bytes:
    _verify_temporary_descriptor(parent, temporary)
    before = descriptor_file_stat(temporary.descriptor)
    if before.st_size > limit:
        raise PersistenceIOError(
            "Temporary publication exceeds its byte limit",
            reason_code="persistence_bytes_exceeded",
        )
    descriptor = os.dup(temporary.descriptor)
    try:
        payloads: list[bytes] = []
        for _ in range(2):
            os.lseek(descriptor, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise PersistenceIOError(
                        "Temporary publication was truncated during verification",
                        reason_code="persistence_target_unsafe",
                    )
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise PersistenceIOError(
                    "Temporary publication grew during verification",
                    reason_code="persistence_target_unsafe",
                )
            payloads.append(b"".join(chunks))
        after = descriptor_file_stat(descriptor)
        if (
            payloads[0] != payloads[1]
            or _is_link_or_reparse(after)
            or not stat.S_ISREG(after.st_mode)
            or _entry_identity(after) != temporary.identity
            or after.st_nlink != before.st_nlink
            or after.st_size != before.st_size
        ):
            raise PersistenceIOError(
                "Temporary publication changed during verification",
                reason_code="persistence_target_unsafe",
            )
        _verify_temporary_descriptor(parent, temporary)
        return payloads[0]
    finally:
        os.close(descriptor)


def _published_temporary_state(
    parent: _PinnedOutputParent,
    temporary: _TemporaryEntry,
    destination_name: str,
) -> str:
    retained = descriptor_file_stat(temporary.descriptor)
    named = _entry_info(parent, destination_name)
    if named is None:
        return "absent"
    if (
        _is_link_or_reparse(retained)
        or _is_link_or_reparse(named)
        or not stat.S_ISREG(retained.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or _entry_identity(retained) != temporary.identity
        or _entry_identity(named) != temporary.identity
        or retained.st_nlink != 1
        or named.st_nlink != 1
        or retained.st_size != named.st_size
    ):
        return "foreign"
    return "owned"


def _remove_published_temporary(
    parent: _PinnedOutputParent,
    temporary: _TemporaryEntry,
    destination_name: str,
) -> None:
    if parent.parent_fd is not None:
        _linux_claim_and_remove_owned_entry(
            parent.parent_fd,
            destination_name,
            temporary.descriptor,
            temporary.identity,
            expected_links=1,
        )
        temporary.published = False
        return
    if (
        parent.windows_api is None
        or temporary.windows_handle is None
        or parent.windows_parent_handle is None
    ):
        raise PersistenceIOError(
            "Secure byte-publication rollback is unavailable",
            reason_code="persistence_atomic_replace_unavailable",
        )
    parent.windows_api.mark_delete_on_close(temporary.windows_handle)
    temporary.discard = True
    temporary.published = False


def _retain_failed_temporary(
    staging: _PinnedOutputParent,
    temporary: _TemporaryEntry,
) -> None:
    """Retain an exact failed stage without deleting a foreign replacement."""

    _verify_temporary_descriptor(staging, temporary)
    os.fsync(temporary.descriptor)
    if staging.parent_fd is not None:
        for _ in range(100):
            name = f"{temporary.stage_prefix}{secrets.token_hex(16)}"
            try:
                _linux_link_descriptor_no_replace(
                    temporary.descriptor,
                    staging.parent_fd,
                    name,
                )
            except FileExistsError:
                continue
            temporary.name = name
            temporary.retained = True
            _verify_owned_entry(staging, name, temporary.identity)
            _fsync_retained_ancestry(staging)
            _verify_owned_entry(staging, name, temporary.identity)
            return
        raise PersistenceIOError(f"Could not retain failed persistence output in {staging.path}")
    if (
        staging.windows_api is None
        or staging.windows_parent_handle is None
        or temporary.windows_handle is None
        or temporary.name is None
    ):
        raise PersistenceIOError(
            "Secure failed-stage retention is unavailable",
            reason_code="persistence_atomic_replace_unavailable",
        )
    temporary.retained = True
    _verify_owned_entry(staging, temporary.name, temporary.identity)
    _fsync_retained_ancestry(staging)
    _verify_owned_entry(staging, temporary.name, temporary.identity)


def _close_temporary_entry(
    staging: _PinnedOutputParent,
    temporary: _TemporaryEntry,
) -> None:
    errors: list[BaseException] = []
    if (
        temporary.discard
        and not temporary.published
        and temporary.windows_handle is not None
        and staging.windows_api is not None
    ):
        try:
            staging.windows_api.mark_delete_on_close(temporary.windows_handle)
        except BaseException as exc:
            errors.append(exc)
    try:
        os.close(temporary.descriptor)
    except OSError as exc:
        try:
            descriptor_file_stat(temporary.descriptor)
        except OSError as inspection_error:
            exc.add_note(f"Temporary descriptor close-state inspection failed: {inspection_error}")
        else:
            try:
                os.fdopen(temporary.descriptor, "rb", closefd=True).close()
            except OSError as fallback_error:
                exc.add_note(f"Temporary descriptor fallback cleanup failed: {fallback_error}")
        errors.append(exc)
    if temporary.windows_handle is not None and staging.windows_api is not None:
        try:
            staging.windows_api.close(temporary.windows_handle)
        except BaseException as exc:
            errors.append(exc)
    if errors:
        primary = errors[0]
        for secondary in errors[1:]:
            primary.add_note(f"Additional temporary persistence cleanup failure: {secondary}")
        raise primary


def _fsync_parent(parent: _PinnedOutputParent) -> None:
    parent.assert_current()
    if parent.parent_fd is not None:
        os.fsync(parent.parent_fd)
    elif parent.windows_api is not None and len(parent.windows_handles) >= 2:
        parent.windows_api.flush_relative_directory(
            parent.windows_handles[-2],
            parent.path.name,
            parent.identities[-1],
            context=f"persistence directory {parent.path}",
        )
    else:
        raise PersistenceIOError(
            "Durable persistence directory flush is unavailable",
            reason_code="persistence_durability_unavailable",
        )
    parent.assert_current()


def _fsync_owned_entry(
    parent: _PinnedOutputParent,
    name: str,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Flush one retained regular file and prove its visible identity stayed exact."""

    descriptor: int | None = None
    windows_handle: int | None = None
    source = parent.path / name
    try:
        parent.assert_current()
        before = _entry_info(parent, name)
        identity = _validated_target_identity(before, source)
        if identity is None:
            raise PersistenceIOError(
                f"Persistence file disappeared before durable flush: {source}",
                reason_code="persistence_target_unsafe",
            )
        if expected_identity is not None and identity != expected_identity:
            raise PersistenceIOError(
                f"Persistence file identity changed before durable flush: {source}",
                reason_code="persistence_target_unsafe",
            )
        if parent.parent_fd is not None:
            descriptor = os.open(
                name,
                os.O_RDWR
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent.parent_fd,
            )
            opened = descriptor_file_stat(descriptor)
        elif parent.windows_api is not None and parent.windows_parent_handle is not None:
            windows_handle = parent.windows_api.open_existing_file(
                parent.windows_parent_handle,
                name,
                writable=True,
            )
            opened = parent.windows_api._state(
                windows_handle,
                directory=False,
                context=f"persistence file {source}",
            )
            descriptor = parent.windows_api.duplicate_to_descriptor(
                windows_handle,
                writable=True,
            )
        else:
            raise PersistenceIOError("secure persistence primitives are unavailable")
        if (
            _is_link_or_reparse(opened)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _entry_identity(opened) != identity
        ):
            raise PersistenceIOError(
                f"Persistence file changed before durable flush: {source}",
                reason_code="persistence_target_unsafe",
            )
        os.fsync(descriptor)
        after = descriptor_file_stat(descriptor)
        visible = _validated_target_identity(_entry_info(parent, name), source)
        if (
            _is_link_or_reparse(after)
            or not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or _entry_identity(after) != identity
            or visible != identity
        ):
            raise PersistenceIOError(
                f"Persistence file changed during durable flush: {source}",
                reason_code="persistence_target_unsafe",
            )
        parent.assert_current()
        return identity
    except PersistenceIOError:
        raise
    except OSError as exc:
        raise PersistenceIOError(
            f"Could not durably flush persistence file {source}: {exc}",
            reason_code="persistence_durability_unavailable",
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if windows_handle is not None and parent.windows_api is not None:
            parent.windows_api.close(windows_handle)


def _read_exact_published_payload(
    destination: _PinnedOutputParent,
    destination_name: str,
    payload: bytes,
) -> None:
    if (
        _read_pinned_file(
            destination,
            destination_name,
            limit=max(len(payload), _MAX_IMMUTABLE_COLLISION_BYTES),
        )
        != payload
    ):
        raise PersistenceIOError(
            "Immutable persistence generation name collided with different bytes",
            reason_code="persistence_generation_collision",
        )


def _complete_publication_durability(
    staging: _PinnedOutputParent,
    destination: _PinnedOutputParent,
    destination_name: str,
    payload: bytes,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> None:
    _read_exact_published_payload(destination, destination_name, payload)
    identity = _fsync_owned_entry(
        destination,
        destination_name,
        expected_identity=expected_identity,
    )
    _fsync_retained_ancestry(destination)
    _fsync_retained_ancestry(staging)
    _verify_owned_entry(destination, destination_name, identity)
    _read_exact_published_payload(destination, destination_name, payload)


def _open_lock_entry(
    parent: _PinnedOutputParent,
    name: str,
) -> tuple[int, int | None, tuple[int, int]]:
    descriptor: int | None = None
    windows_handle: int | None = None
    try:
        parent.assert_current()
        if parent.parent_fd is not None:
            descriptor = os.open(
                name,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent.parent_fd,
            )
            info = descriptor_file_stat(descriptor)
        elif parent.windows_api is not None and parent.windows_parent_handle is not None:
            windows_handle = parent.windows_api.open_lock(
                parent.windows_parent_handle,
                name,
            )
            info = parent.windows_api._state(
                windows_handle,
                directory=False,
                context=f"persistence lock {parent.path / name}",
            )
            descriptor = parent.windows_api.duplicate_to_descriptor(
                windows_handle,
                writable=True,
            )
        else:
            raise PersistenceIOError("secure persistence lock primitives are unavailable")
        identity = _validated_target_identity(info, parent.path / name)
        visible = _validated_target_identity(
            _entry_info(parent, name),
            parent.path / name,
        )
        if identity is None or visible != identity:
            raise PersistenceIOError(
                f"Persistence lock identity is unsafe: {parent.path / name}",
                reason_code="persistence_parent_unsafe",
            )
        parent.assert_current()
        return descriptor, windows_handle, identity
    except BaseException:
        _release_unreturned_entry(descriptor, None, None)
        if windows_handle is not None and parent.windows_api is not None:
            parent.windows_api.close(windows_handle)
        raise


def _acquire_os_lock(descriptor: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            return
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except (ImportError, OSError) as exc:
        raise PersistenceIOError(
            f"Could not acquire persistence slot lock: {exc}",
            reason_code="persistence_locked",
        ) from exc


def _release_os_lock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def held_persistence_lock(path: str | Path) -> Iterator[None]:
    """Serialize cooperating writers through one retained, durable slot lock."""

    lock = Path(os.path.abspath(Path(path)))
    descriptor: int | None = None
    windows_handle: int | None = None
    locked = False
    with _open_verified_output_parent(lock.parent, create=True) as parent:
        try:
            descriptor, windows_handle, identity = _open_lock_entry(parent, lock.name)
            _acquire_os_lock(descriptor)
            locked = True
            current = descriptor_file_stat(descriptor)
            visible = _validated_target_identity(_entry_info(parent, lock.name), lock)
            if (
                _is_link_or_reparse(current)
                or not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or _entry_identity(current) != identity
                or visible != identity
            ):
                raise PersistenceIOError(
                    f"Persistence lock changed after acquisition: {lock}",
                    reason_code="persistence_parent_unsafe",
                )
            if current.st_size == 0:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.write(descriptor, b"\0") != 1:
                    raise OSError("short write while initializing persistence lock")
            elif current.st_size != 1:
                raise PersistenceIOError(
                    f"Persistence lock has invalid size: {lock}",
                    reason_code="persistence_parent_unsafe",
                )
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.read(descriptor, 1) != b"\0":
                raise PersistenceIOError(
                    f"Persistence lock has invalid contents: {lock}",
                    reason_code="persistence_parent_unsafe",
                )
            os.fsync(descriptor)
            _fsync_retained_ancestry(parent)
            parent.assert_current()
            if _validated_target_identity(_entry_info(parent, lock.name), lock) != identity:
                raise PersistenceIOError(
                    f"Persistence lock changed before slot mutation: {lock}",
                    reason_code="persistence_parent_unsafe",
                )
            yield
            parent.assert_current()
            if _validated_target_identity(_entry_info(parent, lock.name), lock) != identity:
                raise PersistenceIOError(
                    f"Persistence lock changed during slot mutation: {lock}",
                    reason_code="persistence_parent_unsafe",
                )
        except PersistenceIOError:
            raise
        except OSError as exc:
            raise PersistenceIOError(f"Persistence slot lock failed: {lock}: {exc}") from exc
        finally:
            primary = sys.exception()
            cleanup_errors: list[tuple[str, BaseException]] = []
            if descriptor is not None:
                if locked:
                    try:
                        _release_os_lock(descriptor)
                    except BaseException as exc:
                        cleanup_errors.append(("release", exc))
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    cleanup_errors.append(("descriptor", exc))
            if windows_handle is not None and parent.windows_api is not None:
                try:
                    parent.windows_api.close(windows_handle)
                except BaseException as exc:
                    cleanup_errors.append(("handle", exc))
            cleanup_notes = {
                "release": "Persistence lock release failed",
                "descriptor": "Persistence lock descriptor cleanup failed",
                "handle": "Persistence lock handle cleanup failed",
            }
            if primary is not None:
                for cleanup_kind, cleanup_error in cleanup_errors:
                    primary.add_note(f"{cleanup_notes[cleanup_kind]}: {cleanup_error}")
            elif cleanup_errors:
                first_kind, first_error = cleanup_errors[0]
                primary_details = {
                    "release": f"Could not release persistence slot lock {lock}",
                    "descriptor": f"Could not close persistence slot lock {lock}",
                    "handle": f"Could not close persistence slot lock handle {lock}",
                }
                cleanup_failure = PersistenceIOError(
                    f"{primary_details[first_kind]}: {first_error}"
                )
                for cleanup_kind, cleanup_error in cleanup_errors[1:]:
                    cleanup_failure.add_note(f"{cleanup_notes[cleanup_kind]}: {cleanup_error}")
                raise cleanup_failure from first_error


def publish_bytes_noreplace(
    destination_directory: str | Path,
    destination_name: str,
    payload: bytes,
    *,
    expected_parent_identity: tuple[int, int] | None = None,
    limit: int,
    validate: Callable[[bytes], None] | None = None,
    publication_hook: Callable[[str, Path | None], None] | None = None,
    mode: int = 0o644,
) -> tuple[int, int]:
    """Publish exact verified bytes without replacing an existing destination.

    Linux publication writes an anonymous ``O_TMPFILE`` and links that retained
    descriptor with ``linkat(AT_EMPTY_PATH)``. Windows keeps the exclusively
    created stage handle open through rename and durability verification.
    Failure rollback is coupled to the owned descriptor/handle rather than to a
    pathname lookup.
    """

    destination_name = _publication_component(destination_name)
    if type(payload) is not bytes:
        raise PersistenceIOError(
            "Published payload must be exact bytes",
            reason_code="persistence_bytes_invalid",
        )
    if type(limit) is not int or type(limit) is bool or limit < 1:
        raise PersistenceIOError(
            "Publication byte limit must be a positive exact integer",
            reason_code="persistence_limit_invalid",
        )
    if len(payload) > limit:
        raise PersistenceIOError(
            "Published payload exceeds its byte limit",
            reason_code="persistence_bytes_exceeded",
        )
    if validate is not None and not callable(validate):
        raise PersistenceIOError(
            "Publication validator must be callable",
            reason_code="persistence_target_unsafe",
        )
    if publication_hook is not None and not callable(publication_hook):
        raise PersistenceIOError(
            "Publication hook must be callable",
            reason_code="persistence_target_unsafe",
        )
    if type(mode) is not int or type(mode) is bool or mode < 0 or mode > 0o777:
        raise PersistenceIOError(
            "Publication mode must be a portable exact permission integer",
            reason_code="persistence_target_unsafe",
        )

    committed = False
    original_temporary_name: str | None = None
    with _retained_publication_parent(
        destination_directory,
        expected_identity=expected_parent_identity,
    ) as retained:
        parent = retained._parent
        if _entry_info(parent, destination_name) is not None:
            raise FileExistsError(
                errno.EEXIST,
                "destination already exists",
                destination_name,
            )
        temporary = _create_temporary_entry(
            parent,
            f".{destination_name}.stage.",
        )
        original_temporary_name = temporary.name
        try:
            with os.fdopen(os.dup(temporary.descriptor), "wb", buffering=0) as target:
                _write_all(target, payload)
            if parent.parent_fd is not None:
                os.fchmod(temporary.descriptor, mode)
            os.fsync(temporary.descriptor)
            written = _read_exact_temporary_bytes(
                parent,
                temporary,
                limit=limit,
            )
            if written != payload:
                raise PersistenceIOError(
                    "Temporary publication differs from its exact source bytes",
                    reason_code="persistence_target_unsafe",
                )
            if validate is not None:
                validate(written)
            if publication_hook is not None:
                publication_hook(
                    "after_temporary_fsync",
                    (parent.path / temporary.name if temporary.name is not None else None),
                )
            parent.assert_current()
            if parent.parent_fd is not None:
                try:
                    _linux_link_descriptor_no_replace(
                        temporary.descriptor,
                        parent.parent_fd,
                        destination_name,
                    )
                except FileExistsError:
                    temporary.published = False
                    raise
                except BaseException as exc:
                    try:
                        state = _published_temporary_state(
                            parent,
                            temporary,
                            destination_name,
                        )
                    except BaseException as reconciliation_error:
                        temporary.published = False
                        exc.add_note(
                            "Byte-publication outcome reconciliation failed; "
                            "no destination rollback was attempted without an exact "
                            "owned binding: "
                            f"{reconciliation_error}"
                        )
                    else:
                        temporary.published = state == "owned"
                        if state == "owned":
                            exc.add_note(
                                "Byte publication completed before reporting failure; "
                                "the exact destination was scheduled for rollback"
                            )
                        elif state == "foreign":
                            exc.add_note(
                                "Byte-publication destination is foreign or ambiguous; "
                                "it was preserved without a rollback claim"
                            )
                    raise
            elif (
                parent.windows_api is not None
                and parent.windows_parent_handle is not None
                and temporary.windows_handle is not None
            ):
                temporary.published = True
                parent.windows_api.rename(
                    temporary.windows_handle,
                    parent.windows_parent_handle,
                    destination_name,
                    replace=False,
                )
                temporary.name = destination_name
            else:
                raise PersistenceIOError(
                    "Secure byte publication is unavailable",
                    reason_code="persistence_atomic_replace_unavailable",
                )
            temporary.published = True
            if publication_hook is not None:
                publication_hook(
                    "after_destination_link",
                    parent.path / destination_name,
                )
            if (
                _published_temporary_state(
                    parent,
                    temporary,
                    destination_name,
                )
                != "owned"
            ):
                raise PersistenceIOError(
                    "Published destination does not bind the exact owned bytes",
                    reason_code="persistence_target_unsafe",
                )
            visible = _read_pinned_file(
                parent,
                destination_name,
                limit=limit,
            )
            if visible != payload:
                raise PersistenceIOError(
                    "Published destination differs from its exact source bytes",
                    reason_code="persistence_target_unsafe",
                )
            _fsync_retained_ancestry(parent)
            if (
                _published_temporary_state(
                    parent,
                    temporary,
                    destination_name,
                )
                != "owned"
                or _read_pinned_file(
                    parent,
                    destination_name,
                    limit=limit,
                )
                != payload
            ):
                raise PersistenceIOError(
                    "Published destination changed during durable verification",
                    reason_code="persistence_target_unsafe",
                )
            committed = True
            return temporary.identity
        finally:
            primary = sys.exception()
            cleanup_errors: list[BaseException] = []
            if not committed:
                if temporary.published:
                    try:
                        _remove_published_temporary(
                            parent,
                            temporary,
                            destination_name,
                        )
                    except BaseException as exc:
                        cleanup_errors.append(exc)
                temporary.discard = True
            try:
                _close_temporary_entry(parent, temporary)
            except BaseException as exc:
                cleanup_errors.append(exc)
            if not committed:
                for cleanup_name in (destination_name, original_temporary_name):
                    if cleanup_name is None:
                        continue
                    try:
                        remaining = _entry_info(parent, cleanup_name)
                    except BaseException as exc:
                        cleanup_errors.append(exc)
                        continue
                    if (
                        remaining is not None
                        and not _is_link_or_reparse(remaining)
                        and stat.S_ISREG(remaining.st_mode)
                        and _entry_identity(remaining) == temporary.identity
                    ):
                        cleanup_errors.append(
                            PersistenceIOError(
                                "Owned byte-publication entry survived rollback",
                                reason_code="persistence_target_unsafe",
                            )
                        )
                try:
                    retained.flush()
                except BaseException as exc:
                    cleanup_errors.append(exc)
            if cleanup_errors:
                if primary is not None:
                    for cleanup_error in cleanup_errors:
                        primary.add_note(f"Byte-publication cleanup failed: {cleanup_error}")
                else:
                    cleanup_error = cleanup_errors[0]
                    for secondary in cleanup_errors[1:]:
                        cleanup_error.add_note(
                            f"Additional byte-publication cleanup failure: {secondary}"
                        )
                    raise cleanup_error


def publish_json_noreplace(
    staging_directory: str | Path,
    destination_directory: str | Path,
    destination_name: str,
    value: object,
) -> Path:
    """Publish one immutable JSON file by retained-handle no-replace rename.

    A failed or ambiguous staging entry is deliberately retained outside the
    immutable destination inventory. A destination collision is idempotent only
    when the already-published bytes are exactly equal.
    """

    if (
        type(destination_name) is not str
        or not destination_name
        or destination_name in {".", ".."}
        or "/" in destination_name
        or "\\" in destination_name
        or "\x00" in destination_name
    ):
        raise PersistenceIOError(
            "Immutable persistence destination name is invalid",
            reason_code="persistence_path_identity_invalid",
        )
    payload = _encode_json(value)
    staging_path = Path(os.path.abspath(Path(staging_directory)))
    destination_path = Path(os.path.abspath(Path(destination_directory)))
    temporary: _TemporaryEntry | None = None
    try:
        with (
            _open_verified_output_parent(staging_path, create=True) as staging,
            _open_verified_output_parent(destination_path, create=True) as destination,
        ):
            staging.assert_current()
            destination.assert_current()
            existing = _entry_info(destination, destination_name)
            if existing is not None:
                _validated_target_identity(
                    existing,
                    destination_path / destination_name,
                )
                _complete_publication_durability(
                    staging,
                    destination,
                    destination_name,
                    payload,
                )
                return destination_path / destination_name

            temporary = _create_temporary_entry(
                staging,
                f".{destination_name}.stage.",
            )
            try:
                with os.fdopen(os.dup(temporary.descriptor), "wb", buffering=0) as target:
                    _write_all(target, payload)
                staging.assert_current()
                destination.assert_current()
                _verify_temporary_descriptor(staging, temporary)
                try:
                    if staging.parent_fd is not None and destination.parent_fd is not None:
                        _linux_link_descriptor_no_replace(
                            temporary.descriptor,
                            destination.parent_fd,
                            destination_name,
                        )
                    elif (
                        staging.windows_api is not None
                        and temporary.windows_handle is not None
                        and destination.windows_parent_handle is not None
                    ):
                        staging.windows_api.rename(
                            temporary.windows_handle,
                            destination.windows_parent_handle,
                            destination_name,
                            replace=False,
                        )
                    else:
                        raise PersistenceIOError(
                            "Secure immutable persistence publication is unavailable",
                            reason_code="persistence_atomic_replace_unavailable",
                        )
                except FileExistsError:
                    _complete_publication_durability(
                        staging,
                        destination,
                        destination_name,
                        payload,
                    )
                    temporary.discard = True
                    return destination_path / destination_name

                temporary.published = True
                _complete_publication_durability(
                    staging,
                    destination,
                    destination_name,
                    payload,
                    expected_identity=temporary.identity,
                )
                return destination_path / destination_name
            finally:
                primary = sys.exception()
                if not temporary.published and not temporary.discard and not temporary.retained:
                    try:
                        _retain_failed_temporary(staging, temporary)
                    except PersistenceIOError as exc:
                        if primary is not None:
                            primary.add_note(f"Failed-stage retention failed: {exc}")
                        else:
                            raise
                try:
                    _close_temporary_entry(staging, temporary)
                except PersistenceIOError as exc:
                    if primary is not None:
                        primary.add_note(f"Temporary persistence cleanup failed: {exc}")
                    else:
                        raise
    except PersistenceIOError:
        raise
    except OSError as exc:
        raise PersistenceIOError(
            f"Could not publish immutable persistence generation: {exc}"
        ) from exc


__all__ = [
    "PersistenceIOError",
    "decode_json_object",
    "held_persistence_lock",
    "inspect_safe_entry",
    "publish_bytes_noreplace",
    "publish_json_noreplace",
    "read_directory_entries",
    "read_directory_files",
    "read_immutable_file_bytes",
    "read_json_object",
]
