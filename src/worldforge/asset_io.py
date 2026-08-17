from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import math
import os
import secrets
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.portability import is_portable_path_component
from worldforge.file_stat import (
    FileStat,
    descriptor_file_stat,
    is_link_or_reparse,
    path_file_stat,
    windows_handle_file_stat,
    windows_handle_file_stat_strict,
)
from worldforge.integrity import canonical_payload_hash

MAX_CONTRACT_BYTES = 16 * 1024 * 1024
MAX_ASSET_BYTES = 512 * 1024 * 1024


class AssetContractError(ValueError):
    """Raised when an M5 authoring artifact violates its safe-file contract."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "asset_contract_invalid",
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class BoundFileBytes:
    """Exact bytes retained against one standalone regular-file identity."""

    payload: bytes
    identity: tuple[int, int]
    size_bytes: int | None = None
    change_time_ns: int | None = None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def read_json_object(path: str | Path, *, limit: int = MAX_CONTRACT_BYTES) -> dict[str, Any]:
    source = Path(path)
    descriptor: int | None = None
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("not a standalone regular file")
        if info.st_size > limit:
            raise OSError(f"exceeds the {limit}-byte limit")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            payload = stream.read(limit + 1)
        if len(payload) > limit:
            raise OSError(f"exceeds the {limit}-byte limit")
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AssetContractError(f"Could not read {source}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise AssetContractError(f"{source} must contain a JSON object")
    return value


def normalized_relative_path(value: object) -> PurePosixPath | None:
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


def resolve_artifact(
    root: str | Path,
    relative: object,
    *,
    required: bool = True,
    max_bytes: int = MAX_ASSET_BYTES,
) -> Path | None:
    """Resolve one portable, non-linked artifact beneath ``root``.

    Every existing parent and the file itself are checked without following a
    symbolic link. Hard-linked files are rejected so a later mutation outside
    the production tree cannot silently change a hash-bound artifact.
    """

    normalized = normalized_relative_path(relative)
    if normalized is None:
        if required:
            raise AssetContractError(f"Unsafe artifact path: {relative!r}")
        return None
    base = Path(root).resolve()
    current = base
    for part in normalized.parts[:-1]:
        current = current / part
        try:
            info = path_file_stat(current)
        except OSError:
            if required:
                raise AssetContractError(f"Artifact parent is missing: {relative}") from None
            return None
        if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise AssetContractError(f"Artifact parent is not a safe directory: {relative}")
    target = current / normalized.parts[-1]
    try:
        info = path_file_stat(target)
    except OSError:
        if required:
            raise AssetContractError(f"Artifact is missing: {relative}") from None
        return None
    if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise AssetContractError(f"Artifact is not a standalone regular file: {relative}")
    if info.st_size > max_bytes:
        raise AssetContractError(f"Artifact exceeds the {max_bytes}-byte limit: {relative}")
    return target


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_reference(root: str | Path, relative: str) -> dict[str, Any]:
    path = resolve_artifact(root, relative)
    assert path is not None
    return {"file": relative, "sha256": sha256_file(path)}


def verify_artifact_reference(
    root: str | Path,
    reference: object,
    *,
    context: str,
    allowed_extra: frozenset[str] = frozenset(),
) -> Path:
    if not isinstance(reference, dict):
        raise AssetContractError(f"{context} must be an artifact reference")
    unknown = set(reference) - {"file", "sha256", "size"} - allowed_extra
    if unknown:
        raise AssetContractError(f"{context} contains unknown fields: {', '.join(sorted(unknown))}")
    path = resolve_artifact(root, reference.get("file"))
    assert path is not None
    expected = reference.get("sha256")
    actual = sha256_file(path)
    if not isinstance(expected, str) or expected != actual:
        raise AssetContractError(f"{context} SHA-256 does not match {reference.get('file')}")
    size = reference.get("size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
        raise AssetContractError(f"{context} size must be a non-negative integer")
    if isinstance(size, int) and size != path.stat().st_size:
        raise AssetContractError(f"{context} size does not match {reference.get('file')}")
    return path


def encoded_json(value: object) -> bytes:
    try:
        document = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise AssetContractError(f"Could not encode strict JSON: {exc}") from exc
    return (document + "\n").encode("utf-8")


def prepare_output_path(path: str | Path) -> Path:
    """Create and verify output parents without accepting a symbolic-link hop."""

    absolute = Path(os.path.abspath(Path(path)))
    parent = absolute.parent
    current = Path(parent.anchor)
    for part in parent.parts[1:]:
        current /= part
        try:
            info = path_file_stat(current)
        except FileNotFoundError:
            try:
                current.mkdir()
            except FileExistsError:
                pass
            try:
                info = path_file_stat(current)
            except OSError as exc:
                raise AssetContractError(
                    f"Could not verify output parent {current}: {exc}"
                ) from exc
        except OSError as exc:
            raise AssetContractError(f"Could not verify output parent {current}: {exc}") from exc
        if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise AssetContractError(f"Output parent is not a safe directory: {current}")
    return absolute


_DIR_FD_PUBLICATION = (
    os.name == "posix"
    and sys.platform.startswith("linux")
    and hasattr(os, "O_TMPFILE")
    and all(
        function in os.supports_dir_fd
        for function in (os.open, os.mkdir, os.link, os.rename, os.stat)
    )
)
_AT_EMPTY_PATH = 0x1000


def _directory_info_identity(info: FileStat, *, path: Path) -> tuple[int, int]:
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise AssetContractError(f"Output parent is not a safe directory: {path}")
    return info.st_dev, info.st_ino


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _close_posix_descriptors(descriptors: list[int] | tuple[int, ...]) -> None:
    errors: list[OSError] = []
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError as exc:
            errors.append(exc)
    if errors:
        raise errors[0]


def _open_posix_ancestry(
    path: Path,
    *,
    create: bool,
) -> tuple[list[int], tuple[tuple[int, int], ...]]:
    descriptors: list[int] = []
    identities: list[tuple[int, int]] = []
    flags = _directory_open_flags()
    try:
        descriptor = os.open(Path(path.anchor), flags)
        descriptors.append(descriptor)
        identities.append(
            _directory_info_identity(
                descriptor_file_stat(descriptor),
                path=Path(path.anchor),
            )
        )
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            try:
                descriptor = os.open(part, flags, dir_fd=descriptors[-1])
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o755, dir_fd=descriptors[-1])
                except FileExistsError:
                    pass
                descriptor = os.open(part, flags, dir_fd=descriptors[-1])
            descriptors.append(descriptor)
            identities.append(
                _directory_info_identity(
                    descriptor_file_stat(descriptor),
                    path=current,
                )
            )
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


class _WindowsOverlapped(ctypes.Structure):
    _fields_ = [
        ("internal", ctypes.c_size_t),
        ("internal_high", ctypes.c_size_t),
        ("offset", ctypes.c_uint32),
        ("offset_high", ctypes.c_uint32),
        ("event", ctypes.c_void_p),
    ]


class _WindowsFileRenameInformationEx(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("root_directory", ctypes.c_void_p),
        ("filename_length", ctypes.c_uint32),
        ("filename", ctypes.c_uint16 * 1),
    ]


class _WindowsFileDispositionInformationEx(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint32)]


_WINDOWS_SERVER_2022_BUILD = 20_348


def _windows_migration_ex_contract_supported(version: object | None = None) -> bool:
    """Limit native migration Ex classes to the hosted Windows support contract."""

    if version is None:
        get_version = getattr(sys, "getwindowsversion", None)
        if get_version is None:
            return False
        version = get_version()
    major = getattr(version, "major", None)
    build = getattr(version, "build", None)
    return (
        isinstance(major, int)
        and not isinstance(major, bool)
        and isinstance(build, int)
        and not isinstance(build, bool)
        and major >= 10
        and build >= _WINDOWS_SERVER_2022_BUILD
    )


class _WindowsPublicationApi:
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
    _FILE_RENAME_INFORMATION = 10
    _WIN32_FILE_RENAME_INFO_EX = 22
    _NT_FILE_RENAME_INFORMATION_EX = 65
    _FILE_DISPOSITION_INFORMATION = 4
    _FILE_DISPOSITION_INFORMATION_EX = 21
    _FILE_RENAME_FLAG_REPLACE_IF_EXISTS = 0x00000001
    _FILE_RENAME_FLAG_POSIX_SEMANTICS = 0x00000002
    _FILE_DISPOSITION_FLAG_DELETE = 0x00000001
    _FILE_DISPOSITION_FLAG_POSIX_SEMANTICS = 0x00000002
    _LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
    _LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
    _FILE_SUPPORTS_POSIX_UNLINK_RENAME = 0x00000400
    _FILE_SUPPORTS_HARD_LINKS = 0x00400000
    _DRIVE_FIXED = 3
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    def __init__(self) -> None:
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise AssetContractError("secure publication primitives are unavailable")
        try:
            self.kernel32 = win_dll("kernel32", use_last_error=True)
            self.ntdll = win_dll("ntdll", use_last_error=True)
        except OSError as exc:
            raise AssetContractError("secure publication primitives are unavailable") from exc
        self._create_file_w = self.kernel32.CreateFileW
        self._create_file_w.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self._create_file_w.restype = ctypes.c_void_p
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
        try:
            self.set_information = self.kernel32.SetFileInformationByHandle
        except AttributeError as exc:
            raise AssetContractError(
                "secure Windows file-information primitives are unavailable"
            ) from exc
        self.set_information.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self.set_information.restype = ctypes.c_int
        self.flush_file_buffers = self.kernel32.FlushFileBuffers
        self.flush_file_buffers.argtypes = [ctypes.c_void_p]
        self.flush_file_buffers.restype = ctypes.c_int
        self.lock_file_ex = self.kernel32.LockFileEx
        self.lock_file_ex.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(_WindowsOverlapped),
        ]
        self.lock_file_ex.restype = ctypes.c_int
        self.unlock_file_ex = self.kernel32.UnlockFileEx
        self.unlock_file_ex.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(_WindowsOverlapped),
        ]
        self.unlock_file_ex.restype = ctypes.c_int
        self.get_volume_information = self.kernel32.GetVolumeInformationByHandleW
        self.get_volume_information.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_wchar_p,
            ctypes.c_uint32,
        ]
        self.get_volume_information.restype = ctypes.c_int
        self.get_drive_type = self.kernel32.GetDriveTypeW
        self.get_drive_type.argtypes = [ctypes.c_wchar_p]
        self.get_drive_type.restype = ctypes.c_uint32
        self.create_hard_link = self.kernel32.CreateHardLinkW
        self.create_hard_link.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
        ]
        self.create_hard_link.restype = ctypes.c_int
        try:
            self.nt_create_file = self.ntdll.NtCreateFile
            self.nt_set_information = self.ntdll.NtSetInformationFile
            self.nt_status_to_dos_error = self.ntdll.RtlNtStatusToDosError
        except AttributeError as exc:
            raise AssetContractError("secure publication primitives are unavailable") from exc
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

    @staticmethod
    def _handle_value(value: object) -> int:
        result = ctypes.cast(value, ctypes.c_void_p).value
        if result in {None, _WindowsPublicationApi._INVALID_HANDLE}:
            raise AssetContractError("secure publication handle creation failed")
        return int(result)

    def _state(self, handle: int, *, directory: bool, context: str) -> FileStat:
        try:
            info = windows_handle_file_stat(handle)
        except OSError as exc:
            raise AssetContractError(f"Could not inspect {context}: {exc}") from exc
        expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if is_link_or_reparse(info) or not expected_type:
            expected = "directory" if directory else "regular file"
            raise AssetContractError(f"{context} is not a safe {expected}")
        return info

    def _strict_state(self, handle: int, *, directory: bool, context: str) -> FileStat:
        try:
            info = windows_handle_file_stat_strict(handle)
        except OSError as exc:
            raise AssetContractError(f"Could not strictly inspect {context}: {exc}") from exc
        expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if is_link_or_reparse(info) or not expected_type:
            expected = "directory" if directory else "regular file"
            raise AssetContractError(f"{context} is not a safe strict {expected}")
        return info

    def open_anchor(self, path: Path) -> int:
        handle = self._create_file_w(
            str(path),
            self._FILE_LIST_DIRECTORY
            | self._FILE_TRAVERSE
            | self._FILE_READ_ATTRIBUTES
            | self._SYNCHRONIZE,
            self._SHARE_READ | self._SHARE_WRITE | self._SHARE_DELETE,
            None,
            self._OPEN_EXISTING,
            self._FILE_FLAG_BACKUP_SEMANTICS | self._FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        result = self._handle_value(handle)
        try:
            self._state(result, directory=True, context=f"output ancestor {path}")
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
            raise AssetContractError("Windows publication entry component is invalid")
        encoded = name.encode("utf-16-le", errors="strict")
        if len(encoded) > 65_532:
            raise AssetContractError("Windows publication entry component is too long")
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
            _WindowsPublicationApi._OBJ_CASE_INSENSITIVE,
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
        # Keep the native name buffers alive until NtCreateFile returns.
        del buffer, unicode_name
        if status_code < 0:
            error = int(self.nt_status_to_dos_error(ctypes.c_int32(status_code)))
            if disposition == self._FILE_CREATE and error in {80, 183}:
                raise FileExistsError(error, "entry already exists", name)
            if error in {2, 3}:
                raise FileNotFoundError(error, "entry is missing", name)
            raise AssetContractError(f"Could not {context}: error {error}")
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
            share=self._SHARE_READ | self._SHARE_WRITE,
            options=self._FILE_DIRECTORY_FILE,
            context=f"open or create output ancestor {name}",
        )
        try:
            self._state(result, directory=True, context=f"output ancestor {name}")
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
        if len(path.parts) <= 1:
            raise AssetContractError("Windows filesystem root output parent is unsupported")
        handles: list[int] = []
        identities: list[tuple[int, int]] = []
        try:
            handle = self.open_anchor(Path(path.anchor))
            handles.append(handle)
            info = self._state(
                handle,
                directory=True,
                context=f"output ancestor {path.anchor}",
            )
            identities.append((info.st_dev, info.st_ino))
            for part in path.parts[1:]:
                handle = self.open_relative_directory(
                    handles[-1],
                    part,
                    create=create,
                )
                handles.append(handle)
                info = self._state(
                    handle,
                    directory=True,
                    context=f"output ancestor {part}",
                )
                identities.append((info.st_dev, info.st_ino))
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
            context=f"create temporary output {name}",
        )

    def create_directory(
        self,
        parent: int,
        name: str,
        *,
        request_delete: bool = True,
    ) -> int:
        access = (
            self._GENERIC_READ
            | self._GENERIC_WRITE
            | self._FILE_LIST_DIRECTORY
            | self._FILE_TRAVERSE
            | self._FILE_READ_ATTRIBUTES
            | self._SYNCHRONIZE
        )
        if request_delete:
            access |= self._DELETE
        result = self._open_relative(
            parent,
            name,
            access=access,
            disposition=self._FILE_CREATE,
            share=self._SHARE_READ | self._SHARE_WRITE,
            options=self._FILE_DIRECTORY_FILE,
            context=f"create retained output directory {name}",
        )
        try:
            self._state(
                result,
                directory=True,
                context=f"retained output directory {name}",
            )
        except BaseException:
            self.close(result)
            raise
        return result

    def create_file(self, parent: int, name: str) -> int:
        result = self._open_relative(
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
            context=f"create retained output file {name}",
        )
        try:
            info = self._state(
                result,
                directory=False,
                context=f"retained output file {name}",
            )
            if info.st_nlink != 1 or info.st_size != 0:
                raise AssetContractError(f"Retained output file {name} is not new and standalone")
        except BaseException:
            self.close(result)
            raise
        return result

    def flush_handle(self, handle: int, *, context: str) -> None:
        if not self.flush_file_buffers(ctypes.c_void_p(handle)):
            error = ctypes.get_last_error()
            raise AssetContractError(f"Could not durably flush {context}: error {error}")

    def open_lock(self, parent: int, name: str) -> int:
        return self._open_relative(
            parent,
            name,
            access=self._GENERIC_READ
            | self._GENERIC_WRITE
            | self._FILE_READ_ATTRIBUTES
            | self._SYNCHRONIZE,
            disposition=self._FILE_OPEN_IF,
            share=self._SHARE_READ | self._SHARE_WRITE,
            options=self._FILE_NON_DIRECTORY_FILE,
            context=f"open output lock {name}",
        )

    def open_existing_entry(self, parent: int, name: str) -> int:
        return self._open_relative(
            parent,
            name,
            access=self._GENERIC_READ | self._FILE_READ_ATTRIBUTES | self._SYNCHRONIZE,
            disposition=self._FILE_OPEN,
            share=self._SHARE_READ | self._SHARE_WRITE,
            options=0,
            context=f"open publication entry {name}",
        )

    def open_existing_file(self, parent: int, name: str) -> int:
        return self._open_relative(
            parent,
            name,
            access=self._GENERIC_READ | self._FILE_READ_ATTRIBUTES | self._SYNCHRONIZE,
            disposition=self._FILE_OPEN,
            share=self._SHARE_READ | self._SHARE_WRITE,
            options=self._FILE_NON_DIRECTORY_FILE,
            context=f"open publication file {name}",
        )

    def open_existing_file_strict(
        self,
        parent: int,
        name: str,
        *,
        sealed: bool = False,
        delete: bool = False,
        share_delete: bool = False,
        write: bool = False,
    ) -> int:
        access = self._GENERIC_READ | self._FILE_READ_ATTRIBUTES | self._SYNCHRONIZE
        if delete:
            access |= self._DELETE
        if write:
            access |= self._GENERIC_WRITE
        share = (
            self._SHARE_READ if sealed or delete or write else self._SHARE_READ | self._SHARE_WRITE
        )
        if share_delete:
            share |= self._SHARE_DELETE
        result = self._open_relative(
            parent,
            name,
            access=access,
            disposition=self._FILE_OPEN,
            share=share,
            options=self._FILE_NON_DIRECTORY_FILE,
            context=f"open strict publication file {name}",
        )
        try:
            self._strict_state(
                result,
                directory=False,
                context=f"strict publication file {name}",
            )
        except BaseException:
            self.close(result)
            raise
        return result

    def open_existing_directory_strict(
        self,
        parent: int,
        name: str,
        *,
        delete: bool = False,
    ) -> int:
        access = (
            self._GENERIC_READ
            | self._FILE_LIST_DIRECTORY
            | self._FILE_TRAVERSE
            | self._FILE_READ_ATTRIBUTES
            | self._SYNCHRONIZE
        )
        if delete:
            access |= self._DELETE
        result = self._open_relative(
            parent,
            name,
            access=access,
            disposition=self._FILE_OPEN,
            share=self._SHARE_READ | self._SHARE_WRITE,
            options=self._FILE_DIRECTORY_FILE,
            context=f"open strict publication directory {name}",
        )
        try:
            self._strict_state(
                result,
                directory=True,
                context=f"strict publication directory {name}",
            )
        except BaseException:
            self.close(result)
            raise
        return result

    def entry_info(self, handle: int, *, context: str) -> FileStat:
        try:
            return windows_handle_file_stat(handle)
        except OSError as exc:
            raise AssetContractError(f"Could not inspect {context}: {exc}") from exc

    def strict_entry_info(self, handle: int, *, context: str) -> FileStat:
        return self._strict_state(handle, directory=False, context=context)

    def strict_directory_info(self, handle: int, *, context: str) -> FileStat:
        return self._strict_state(handle, directory=True, context=context)

    def read_strict_bound_bytes(
        self,
        handle: int,
        *,
        limit: int,
        context: str,
    ) -> tuple[BoundFileBytes, int]:
        """Read exact bytes while retaining mandatory 128-bit Windows identity."""

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise AssetContractError("strict Windows byte limit must be positive")
        before = self.strict_entry_info(handle, context=context)
        if before.st_size > limit:
            raise AssetContractError(f"{context} exceeds the {limit}-byte limit")
        descriptor = self.duplicate_to_descriptor(handle, writable=False)
        try:
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
        finally:
            os.close(descriptor)
        after = self.strict_entry_info(handle, context=context)
        before_state = (
            before.st_dev,
            before.st_ino,
            before.st_nlink,
            before.st_size,
            before.st_ctime_ns,
        )
        after_state = (
            after.st_dev,
            after.st_ino,
            after.st_nlink,
            after.st_size,
            after.st_ctime_ns,
        )
        if len(payload) > limit or before_state != after_state or after.st_size != len(payload):
            raise AssetContractError(f"{context} changed during strict retained read")
        return (
            BoundFileBytes(
                bytes(payload),
                (after.st_dev, after.st_ino),
                after.st_size,
                after.st_ctime_ns,
            ),
            after.st_nlink,
        )

    def write_strict_bytes(self, handle: int, payload: bytes, *, context: str) -> None:
        """Write and re-read one new strict Windows file through retained handles."""

        before = self.strict_entry_info(handle, context=context)
        if before.st_nlink != 1 or before.st_size != 0:
            raise AssetContractError(f"{context} is not a new standalone file")
        descriptor = self.duplicate_to_descriptor(handle, writable=True)
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            _write_all(descriptor, payload)
        finally:
            os.close(descriptor)
        self.flush_handle(handle, context=context)
        captured, links = self.read_strict_bound_bytes(
            handle,
            limit=max(1, len(payload)),
            context=context,
        )
        if links != 1 or captured.payload != payload:
            raise AssetContractError(f"{context} changed after durable write")

    def append_strict_journal_frame(
        self,
        handle: int,
        *,
        expected_size: int,
        truncate_to: int | None,
        frame: bytes,
        context: str,
    ) -> None:
        """Append one durable frame through a strict retained Windows handle."""

        before = self.strict_entry_info(handle, context=context)
        if (
            before.st_nlink != 1
            or before.st_size != expected_size
            or not frame
            or truncate_to is not None
            and (truncate_to < 0 or truncate_to > expected_size)
        ):
            raise AssetContractError(f"{context} changed before strict append")
        descriptor = self.duplicate_to_descriptor(handle, writable=True)
        try:
            if truncate_to is not None:
                os.ftruncate(descriptor, truncate_to)
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_END)
            _write_all(descriptor, frame)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        expected_after_size = (expected_size if truncate_to is None else truncate_to) + len(frame)
        after = self.strict_entry_info(handle, context=context)
        if (
            (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            or after.st_nlink != 1
            or after.st_size != expected_after_size
        ):
            raise AssetContractError(f"{context} changed during strict append")

    def acquire_lock(self, handle: int) -> _WindowsOverlapped:
        overlapped = _WindowsOverlapped()
        if not self.lock_file_ex(
            ctypes.c_void_p(handle),
            self._LOCKFILE_EXCLUSIVE_LOCK | self._LOCKFILE_FAIL_IMMEDIATELY,
            0,
            1,
            0,
            ctypes.byref(overlapped),
        ):
            error = ctypes.get_last_error()
            if error in {32, 33, 36, 158}:
                raise BlockingIOError(error, "Windows lifecycle lock is already held")
            raise AssetContractError(f"Could not acquire Windows lifecycle lock: error {error}")
        return overlapped

    def release_lock(self, handle: int, overlapped: _WindowsOverlapped) -> None:
        if not self.unlock_file_ex(
            ctypes.c_void_p(handle),
            0,
            1,
            0,
            ctypes.byref(overlapped),
        ):
            error = ctypes.get_last_error()
            raise AssetContractError(f"Could not release Windows lifecycle lock: error {error}")

    def normalize_lock_byte(self, handle: int) -> None:
        descriptor = self.duplicate_to_descriptor(handle, writable=True)
        try:
            os.ftruncate(descriptor, 1)
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, bytes((os.getpid() & 0xFF,)))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def migration_volume_capabilities(self, root_handle: int, root_path: Path):
        from worldforge.windows_project_migration import WindowsMigrationCapabilities

        self._strict_state(root_handle, directory=True, context="Windows migration root")
        filesystem = ctypes.create_unicode_buffer(32)
        serial = ctypes.c_uint32()
        maximum_component = ctypes.c_uint32()
        flags = ctypes.c_uint32()
        if not self.get_volume_information(
            ctypes.c_void_p(root_handle),
            None,
            0,
            ctypes.byref(serial),
            ctypes.byref(maximum_component),
            ctypes.byref(flags),
            filesystem,
            len(filesystem),
        ):
            error = ctypes.get_last_error()
            raise AssetContractError(f"Could not inspect Windows migration volume: error {error}")
        drive_type = int(self.get_drive_type(str(Path(root_path.anchor))))
        flushable = True
        try:
            self.flush_handle(root_handle, context="Windows migration root directory")
        except AssetContractError:
            flushable = False
        ex_information_classes = (
            callable(getattr(self, "set_information", None))
            and callable(getattr(self, "nt_set_information", None))
            and _windows_migration_ex_contract_supported()
        )
        return WindowsMigrationCapabilities(
            platform=os.name,
            filesystem=filesystem.value,
            local_fixed_volume=drive_type == self._DRIVE_FIXED,
            file_id_128=True,
            hard_links=bool(flags.value & self._FILE_SUPPORTS_HARD_LINKS),
            posix_unlink_rename=bool(flags.value & self._FILE_SUPPORTS_POSIX_UNLINK_RENAME),
            flushable_directories=flushable,
            rename_info_ex=ex_information_classes,
            disposition_info_ex=ex_information_classes,
        )

    def create_source_hard_link(self, destination: Path, source: Path) -> None:
        if not self.create_hard_link(str(destination), str(source), None):
            error = ctypes.get_last_error()
            if error in {80, 183}:
                raise FileExistsError(error, "retention link already exists", str(destination))
            raise AssetContractError(
                f"Could not create Windows source-retention hard link: error {error}"
            )

    def rename_ex(
        self,
        handle: int,
        parent_handle: int,
        destination_name: str,
    ) -> None:
        if (
            type(destination_name) is not str
            or not destination_name
            or destination_name in {".", ".."}
            or "/" in destination_name
            or "\\" in destination_name
            or "\x00" in destination_name
        ):
            raise AssetContractError("Windows migration target name is invalid")
        try:
            encoded = destination_name.encode("utf-16-le", errors="strict")
        except UnicodeError as exc:
            raise AssetContractError("Windows migration target name is invalid") from exc
        if len(encoded) > 65_532:
            raise AssetContractError("Windows migration target name is invalid")
        offset = _WindowsFileRenameInformationEx.filename.offset
        buffer = ctypes.create_string_buffer(
            max(ctypes.sizeof(_WindowsFileRenameInformationEx), offset + len(encoded))
        )
        information = _WindowsFileRenameInformationEx.from_buffer(buffer)
        information.flags = (
            self._FILE_RENAME_FLAG_REPLACE_IF_EXISTS | self._FILE_RENAME_FLAG_POSIX_SEMANTICS
        )
        # Bind the simple target name to the caller-retained parent handle.
        # This avoids cwd-relative rename behavior and keeps the migration
        # publication authority inside the sealed same-directory transaction.
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
                    self._NT_FILE_RENAME_INFORMATION_EX,
                )
            )
        ).value
        if status >= 0:
            return
        error = int(self.nt_status_to_dos_error(status))
        raise AssetContractError(f"Could not publish Windows migration target: error {error}")

    def dispose_ex(self, handle: int) -> None:
        information = _WindowsFileDispositionInformationEx(
            self._FILE_DISPOSITION_FLAG_DELETE | self._FILE_DISPOSITION_FLAG_POSIX_SEMANTICS
        )
        if not self.set_information(
            ctypes.c_void_p(handle),
            self._FILE_DISPOSITION_INFORMATION_EX,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = ctypes.get_last_error()
            raise AssetContractError(
                f"Could not delete Windows retained source link: error {error}"
            )

    def flush_relative_directory(
        self,
        parent: int,
        name: str,
        expected_identity: tuple[int, int],
        context: str,
    ) -> None:
        handle = self.open_relative_directory(
            parent,
            name,
            create=False,
            writable=True,
        )
        try:
            info = self._state(handle, directory=True, context=context)
            if (info.st_dev, info.st_ino) != expected_identity:
                raise AssetContractError(f"{context} identity changed before durable flush")
            if not self.flush_file_buffers(ctypes.c_void_p(handle)):
                error = ctypes.get_last_error()
                raise AssetContractError(f"Could not durably flush {context}: error {error}")
            info = self._state(handle, directory=True, context=context)
            if (info.st_dev, info.st_ino) != expected_identity:
                raise AssetContractError(f"{context} identity changed after durable flush")
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
            raise AssetContractError("Could not duplicate a Windows publication handle")
        try:
            import msvcrt

            flags = os.O_BINARY | (os.O_RDWR if writable else os.O_RDONLY)
            return msvcrt.open_osfhandle(int(duplicate.value), flags)
        except Exception as exc:
            self.close(int(duplicate.value))
            raise AssetContractError("Could not convert a Windows publication handle") from exc

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
            max(
                ctypes.sizeof(_WindowsFileRenameInformation),
                offset + len(encoded),
            )
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
        if status >= 0:
            return
        error = int(self.nt_status_to_dos_error(status))
        if error in {80, 183}:
            raise FileExistsError(error, "destination already exists", destination_name)
        raise AssetContractError(f"Could not publish Windows output: error {error}")

    def mark_delete_on_close(self, handle: int) -> None:
        disposition = _WindowsFileDispositionInformation(1)
        if not self.set_information(
            ctypes.c_void_p(handle),
            self._FILE_DISPOSITION_INFORMATION,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise AssetContractError("Could not retain exact Windows temporary cleanup")

    def close(self, handle: int) -> None:
        if handle and not self.close_handle(ctypes.c_void_p(handle)):
            raise AssetContractError("Could not close a Windows publication handle")

    def close_many(self, handles: list[int] | tuple[int, ...]) -> None:
        errors: list[AssetContractError] = []
        for handle in reversed(handles):
            try:
                self.close(handle)
            except AssetContractError as exc:
                errors.append(exc)
        if errors:
            raise errors[0]


@dataclass(slots=True)
class _PinnedOutputParent:
    path: Path
    identities: tuple[tuple[int, int], ...]
    posix_descriptors: tuple[int, ...] = ()
    windows_api: _WindowsPublicationApi | None = None
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
                        raise AssetContractError(
                            "Output ancestry changed during publication",
                            reason_code="output_ancestry_changed",
                        )
                finally:
                    _close_posix_descriptors(verification)
                return
            if self.windows_api is None or not self.windows_handles:
                raise AssetContractError("secure publication primitives are unavailable")
            verification, visible = self.windows_api.open_ancestry(
                self.path,
                create=False,
            )
            try:
                if visible != self.identities:
                    raise AssetContractError(
                        "Output ancestry changed during publication",
                        reason_code="output_ancestry_changed",
                    )
            finally:
                self.windows_api.close_many(verification)
        except AssetContractError as exc:
            if exc.reason_code == "output_ancestry_changed":
                raise
            raise AssetContractError(
                "Output ancestry changed during publication",
                reason_code="output_ancestry_changed",
            ) from exc
        except (OSError, ValueError) as exc:
            raise AssetContractError(
                "Output ancestry changed during publication",
                reason_code="output_ancestry_changed",
            ) from exc

    def flush_durable(self, *, context: str) -> None:
        """Durably flush this exact retained parent without trusting its pathname."""

        self.assert_current()
        if self.parent_fd is not None:
            os.fsync(self.parent_fd)
            self.assert_current()
            return
        if (
            self.windows_api is None
            or len(self.windows_handles) < 2
            or len(self.identities) != len(self.windows_handles)
        ):
            raise AssetContractError(
                "Secure Windows parent durability requires a retained ancestor handle"
            )
        self.windows_api.flush_relative_directory(
            self.windows_handles[-2],
            self.path.name,
            self.identities[-1],
            context,
        )
        self.assert_current()

    def close(self) -> None:
        if self.posix_descriptors:
            _close_posix_descriptors(self.posix_descriptors)
            return
        if self.windows_api is not None:
            self.windows_api.close_many(self.windows_handles)


@contextmanager
def _open_verified_output_parent(
    path: Path,
    *,
    create: bool = True,
) -> Iterator[_PinnedOutputParent]:
    """Retain the complete output ancestry and fail closed without safe primitives."""

    absolute = Path(os.path.abspath(path))
    pinned: _PinnedOutputParent | None = None
    yielded = False
    try:
        if os.name == "posix":
            if not _DIR_FD_PUBLICATION:
                raise AssetContractError("secure publication primitives are unavailable")
            descriptors, identities = _open_posix_ancestry(absolute, create=create)
            pinned = _PinnedOutputParent(
                absolute,
                identities,
                posix_descriptors=tuple(descriptors),
            )
        elif os.name == "nt":
            api = _WindowsPublicationApi()
            handles, identities = api.open_ancestry(absolute, create=create)
            pinned = _PinnedOutputParent(
                absolute,
                identities,
                windows_api=api,
                windows_handles=tuple(handles),
            )
        else:
            raise AssetContractError("secure publication primitives are unavailable")
        pinned.assert_current()
        yielded = True
        yield pinned
        pinned.assert_current()
    except AssetContractError:
        raise
    except (OSError, ValueError) as exc:
        if yielded:
            raise
        raise AssetContractError(
            f"Output parent is not a safe directory or could not be retained: {absolute}: {exc}"
        ) from exc
    finally:
        if pinned is not None:
            primary = sys.exception()
            try:
                pinned.close()
            except (OSError, AssetContractError) as exc:
                if primary is not None:
                    primary.add_note(f"Output ancestry cleanup failed: {exc}")
                else:
                    raise AssetContractError(
                        f"Could not release output ancestry {absolute}: {exc}"
                    ) from exc


PinnedOutputParent = _PinnedOutputParent
open_verified_output_parent = _open_verified_output_parent


def _entry_info(parent: _PinnedOutputParent, name: str) -> FileStat | None:
    handle: int | None = None
    try:
        if parent.parent_fd is not None:
            return os.stat(name, dir_fd=parent.parent_fd, follow_symlinks=False)
        if parent.windows_api is None or parent.windows_parent_handle is None:
            raise AssetContractError("secure publication primitives are unavailable")
        handle = parent.windows_api.open_existing_entry(
            parent.windows_parent_handle,
            name,
        )
        return parent.windows_api.entry_info(
            handle,
            context=f"output {parent.path / name}",
        )
    except FileNotFoundError:
        return None
    finally:
        if handle is not None and parent.windows_api is not None:
            parent.windows_api.close(handle)


def _published_file_info(parent: _PinnedOutputParent, name: str) -> FileStat | None:
    handle: int | None = None
    try:
        if parent.parent_fd is not None:
            return _entry_info(parent, name)
        if parent.windows_api is None or parent.windows_parent_handle is None:
            raise AssetContractError("secure publication primitives are unavailable")
        handle = parent.windows_api.open_existing_file_strict(
            parent.windows_parent_handle,
            name,
            share_delete=True,
        )
        return parent.windows_api.strict_entry_info(
            handle,
            context=f"published output {parent.path / name}",
        )
    except FileNotFoundError:
        return None
    finally:
        if handle is not None and parent.windows_api is not None:
            parent.windows_api.close(handle)


@dataclass(slots=True)
class _TemporaryEntry:
    descriptor: int
    identity: tuple[int, int]
    windows_handle: int | None = None
    staged_name: str | None = None
    published: bool = False


def _create_temporary_entry(
    parent: _PinnedOutputParent,
    prefix: str,
) -> _TemporaryEntry:
    if parent.parent_fd is not None:
        flags = (
            os.O_RDWR | os.O_TMPFILE | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(".", flags, 0o600, dir_fd=parent.parent_fd)
        except OSError as exc:
            raise AssetContractError(
                f"Could not allocate an anonymous temporary output in {parent.path}: {exc}"
            ) from exc
        info = descriptor_file_stat(descriptor)
        return _TemporaryEntry(
            descriptor=descriptor,
            identity=(info.st_dev, info.st_ino),
        )
    if parent.windows_api is None or parent.windows_parent_handle is None:
        raise AssetContractError("secure publication primitives are unavailable")
    for _ in range(100):
        name = f"{prefix}{secrets.token_hex(16)}"
        try:
            handle = parent.windows_api.create_temporary(
                parent.windows_parent_handle,
                name,
            )
        except FileExistsError:
            continue
        info = parent.windows_api._state(
            handle,
            directory=False,
            context=f"temporary output {parent.path / name}",
        )
        if info.st_nlink != 1:
            parent.windows_api.close(handle)
            raise AssetContractError(
                f"Temporary output is not a standalone regular file: {parent.path / name}"
            )
        try:
            descriptor = parent.windows_api.duplicate_to_descriptor(
                handle,
                writable=True,
            )
        except BaseException:
            parent.windows_api.mark_delete_on_close(handle)
            parent.windows_api.close(handle)
            raise
        return _TemporaryEntry(
            descriptor=descriptor,
            identity=(info.st_dev, info.st_ino),
            windows_handle=handle,
            staged_name=name,
        )
    raise AssetContractError(f"Could not allocate a temporary output in {parent.path}")


def _close_temporary_entry(
    parent: _PinnedOutputParent,
    temporary: _TemporaryEntry,
) -> None:
    errors: list[BaseException] = []
    try:
        os.close(temporary.descriptor)
    except OSError as exc:
        errors.append(exc)
    if temporary.windows_handle is not None and parent.windows_api is not None:
        if not temporary.published:
            try:
                parent.windows_api.mark_delete_on_close(temporary.windows_handle)
            except AssetContractError as exc:
                errors.append(exc)
        try:
            parent.windows_api.close(temporary.windows_handle)
        except AssetContractError as exc:
            errors.append(exc)
    if errors:
        raise AssetContractError(f"Could not release temporary output: {errors[0]}")


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short write while publishing JSON")
        remaining = remaining[written:]
    os.fsync(descriptor)


def _linux_link_descriptor_no_replace(
    descriptor: int,
    parent_descriptor: int,
    destination_name: str,
) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        linkat = libc.linkat
    except (AttributeError, OSError) as exc:
        raise AssetContractError("secure publication primitives are unavailable") from exc
    linkat.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    ctypes.set_errno(0)
    if (
        linkat(
            descriptor,
            b"",
            parent_descriptor,
            os.fsencode(destination_name),
            _AT_EMPTY_PATH,
        )
        == 0
    ):
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
        raise AssetContractError("secure publication primitives are unavailable")
    raise AssetContractError(f"Could not publish output {destination_name}: {os.strerror(error)}")


def _publish_temporary(
    parent: _PinnedOutputParent,
    temporary: _TemporaryEntry,
    destination_name: str,
    *,
    overwrite: bool,
) -> None:
    parent.assert_current()
    if parent.parent_fd is not None:
        if overwrite:
            staged_name = f".{destination_name}.publish.{secrets.token_hex(16)}"
            _linux_link_descriptor_no_replace(
                temporary.descriptor,
                parent.parent_fd,
                staged_name,
            )
            temporary.staged_name = staged_name
            staged = _entry_info(parent, staged_name)
            if (
                staged is None
                or is_link_or_reparse(staged)
                or not stat.S_ISREG(staged.st_mode)
                or staged.st_nlink != 1
                or (staged.st_dev, staged.st_ino) != temporary.identity
            ):
                raise AssetContractError(
                    f"Temporary output changed before publication: {parent.path / staged_name}"
                )
            parent.assert_current()
            os.rename(
                staged_name,
                destination_name,
                src_dir_fd=parent.parent_fd,
                dst_dir_fd=parent.parent_fd,
            )
        else:
            _linux_link_descriptor_no_replace(
                temporary.descriptor,
                parent.parent_fd,
                destination_name,
            )
    elif (
        parent.windows_api is not None
        and parent.windows_parent_handle is not None
        and temporary.windows_handle is not None
    ):
        parent.windows_api.rename(
            temporary.windows_handle,
            parent.windows_parent_handle,
            destination_name,
            replace=overwrite,
        )
    else:
        raise AssetContractError("secure publication primitives are unavailable")
    temporary.published = True
    parent.assert_current()


def _read_json_object_entry(
    parent: _PinnedOutputParent,
    name: str,
) -> dict[str, Any]:
    descriptor: int | None = None
    windows_handle: int | None = None
    source = parent.path / name
    try:
        if parent.parent_fd is not None:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent.parent_fd,
            )
            info = descriptor_file_stat(descriptor)
        elif parent.windows_api is not None:
            if parent.windows_parent_handle is None:
                raise OSError("secure publication primitives are unavailable")
            windows_handle = parent.windows_api.open_existing_file(
                parent.windows_parent_handle,
                name,
            )
            info = parent.windows_api._state(
                windows_handle,
                directory=False,
                context=f"output {source}",
            )
            descriptor = parent.windows_api.duplicate_to_descriptor(
                windows_handle,
                writable=False,
            )
        else:
            raise OSError("secure publication primitives are unavailable")
        if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("not a standalone regular file")
        if info.st_size > MAX_CONTRACT_BYTES:
            raise OSError(f"exceeds the {MAX_CONTRACT_BYTES}-byte limit")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            payload = stream.read(MAX_CONTRACT_BYTES + 1)
        if len(payload) > MAX_CONTRACT_BYTES:
            raise OSError(f"exceeds the {MAX_CONTRACT_BYTES}-byte limit")
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AssetContractError(f"Could not read {source}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if windows_handle is not None and parent.windows_api is not None:
            parent.windows_api.close(windows_handle)
    if not isinstance(value, dict):
        raise AssetContractError(f"{source} must contain a JSON object")
    return value


def _read_bytes_entry(
    parent: _PinnedOutputParent,
    name: str,
    *,
    limit: int,
) -> BoundFileBytes:
    descriptor: int | None = None
    windows_handle: int | None = None
    source = parent.path / name
    try:
        parent.assert_current()
        if parent.parent_fd is not None:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent.parent_fd,
            )
            retained = descriptor_file_stat(descriptor)
        elif parent.windows_api is not None and parent.windows_parent_handle is not None:
            windows_handle = parent.windows_api.open_existing_file(
                parent.windows_parent_handle,
                name,
            )
            retained = parent.windows_api._state(
                windows_handle,
                directory=False,
                context=f"bound file {source}",
            )
            descriptor = parent.windows_api.duplicate_to_descriptor(
                windows_handle,
                writable=False,
            )
        else:
            raise OSError("secure publication primitives are unavailable")
        identity = (retained.st_dev, retained.st_ino)
        named = _entry_info(parent, name)
        if (
            named is None
            or is_link_or_reparse(retained)
            or is_link_or_reparse(named)
            or not stat.S_ISREG(retained.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or retained.st_nlink != 1
            or named.st_nlink != 1
            or (named.st_dev, named.st_ino) != identity
        ):
            raise OSError("not a retained standalone regular file")
        if retained.st_size > limit:
            raise OSError(f"exceeds the {limit}-byte limit")
        payload = bytearray()
        while len(payload) <= limit:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > limit:
            raise OSError(f"exceeds the {limit}-byte limit")
        retained_after = descriptor_file_stat(descriptor)
        named_after = _entry_info(parent, name)
        if (
            named_after is None
            or is_link_or_reparse(retained_after)
            or is_link_or_reparse(named_after)
            or not stat.S_ISREG(retained_after.st_mode)
            or not stat.S_ISREG(named_after.st_mode)
            or retained_after.st_nlink != 1
            or named_after.st_nlink != 1
            or (retained_after.st_dev, retained_after.st_ino) != identity
            or (named_after.st_dev, named_after.st_ino) != identity
            or retained_after.st_size != len(payload)
        ):
            raise OSError("bound file changed while reading")
        parent.assert_current()
        return BoundFileBytes(
            bytes(payload),
            identity,
            retained_after.st_size,
            retained_after.st_ctime_ns,
        )
    except (AssetContractError, OSError) as exc:
        raise AssetContractError(f"Could not read bound file {source}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if windows_handle is not None and parent.windows_api is not None:
            parent.windows_api.close(windows_handle)


def read_bound_bytes(
    path: str | Path,
    *,
    limit: int = MAX_CONTRACT_BYTES,
) -> BoundFileBytes:
    """Read exact bytes while retaining the parent and file namespace identity."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise AssetContractError("bound file byte limit must be a positive integer")
    requested = Path(path)
    destination = Path(os.path.abspath(requested))
    with _open_verified_output_parent(destination.parent, create=False) as parent:
        try:
            return _read_bytes_entry(parent, destination.name, limit=limit)
        except AssetContractError as exc:
            raise AssetContractError(f"Could not read {requested}: {exc}") from exc


def _validated_entry_name(name: object, *, context: str) -> str:
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise AssetContractError(f"{context} must be one safe directory entry name")
    return name


def read_bound_bytes_at(
    directory_fd: int,
    name: str,
    *,
    limit: int = MAX_CONTRACT_BYTES,
) -> BoundFileBytes:
    """Read one exact standalone file relative to an already retained directory."""

    if os.name != "posix" or not isinstance(directory_fd, int) or directory_fd < 0:
        raise AssetContractError("retained directory reads are unavailable on this platform")
    entry = _validated_entry_name(name, context="bound file name")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise AssetContractError("bound file byte limit must be a positive integer")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            entry,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        retained = descriptor_file_stat(descriptor)
        named = os.stat(entry, dir_fd=directory_fd, follow_symlinks=False)
        identity = retained.st_dev, retained.st_ino
        if (
            is_link_or_reparse(retained)
            or is_link_or_reparse(named)
            or not stat.S_ISREG(retained.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or retained.st_nlink != 1
            or named.st_nlink != 1
            or (named.st_dev, named.st_ino) != identity
            or retained.st_size > limit
        ):
            raise OSError("not a retained standalone regular file")
        payload = bytearray()
        while len(payload) <= limit:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = descriptor_file_stat(descriptor)
        named_after = os.stat(entry, dir_fd=directory_fd, follow_symlinks=False)
        if (
            len(payload) > limit
            or is_link_or_reparse(after)
            or is_link_or_reparse(named_after)
            or not stat.S_ISREG(after.st_mode)
            or not stat.S_ISREG(named_after.st_mode)
            or after.st_nlink != 1
            or named_after.st_nlink != 1
            or (after.st_dev, after.st_ino) != identity
            or (named_after.st_dev, named_after.st_ino) != identity
            or after.st_size != len(payload)
        ):
            raise OSError("bound file changed while reading")
        return BoundFileBytes(
            bytes(payload),
            identity,
            after.st_size,
            after.st_ctime_ns,
        )
    except (OSError, ValueError) as exc:
        raise AssetContractError(f"Could not read retained file {entry}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_optional_bound_bytes_at(
    directory_fd: int,
    name: str,
    *,
    limit: int = MAX_CONTRACT_BYTES,
) -> BoundFileBytes | None:
    """Distinguish an absent retained-directory entry from an unsafe one."""

    entry = _validated_entry_name(name, context="optional bound file name")
    try:
        os.stat(entry, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AssetContractError(f"Could not inspect retained file {entry}: {exc}") from exc
    return read_bound_bytes_at(directory_fd, entry, limit=limit)


_RENAME_EXCHANGE = 2
_RENAME_NOREPLACE = 1


def _linux_rename_names_noreplace(
    directory_fd: int,
    source: str,
    destination: str,
) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise AssetContractError("identity-bound removal is unavailable") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    if (
        renameat2(
            directory_fd,
            os.fsencode(source),
            directory_fd,
            os.fsencode(destination),
            _RENAME_NOREPLACE,
        )
        == 0
    ):
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, "destination already exists", destination)
    if error in {
        errno.EINVAL,
        errno.ENOSYS,
        errno.EPERM,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }:
        raise AssetContractError("identity-bound removal is unavailable")
    raise AssetContractError(f"Could not claim retained file: {os.strerror(error)}")


def _linux_exchange_names(directory_fd: int, first: str, second: str) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise AssetContractError("identity-atomic replacement is unavailable") from exc
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    ctypes.set_errno(0)
    if (
        renameat2(
            directory_fd,
            os.fsencode(first),
            directory_fd,
            os.fsencode(second),
            _RENAME_EXCHANGE,
        )
        == 0
    ):
        return
    error = ctypes.get_errno()
    if error in {
        errno.EINVAL,
        errno.ENOSYS,
        errno.EPERM,
        getattr(errno, "ENOTSUP", errno.EINVAL),
        getattr(errno, "EOPNOTSUPP", errno.EINVAL),
    }:
        raise AssetContractError("identity-atomic replacement is unavailable")
    raise AssetContractError(f"Could not exchange retained files: {os.strerror(error)}")


def remove_retained_regular_file_at(
    directory_fd: int,
    name: str,
    descriptor: int,
    *,
    expected_identity: tuple[int, int],
) -> None:
    """Claim and remove only the name still bound to one retained descriptor."""

    if os.name != "posix":
        raise AssetContractError("identity-bound removal is unavailable")
    entry = _validated_entry_name(name, context="retained removal name")
    claim = f".worldforge-delete-{secrets.token_hex(32)}"
    _linux_rename_names_noreplace(directory_fd, entry, claim)
    claimed = True
    try:
        retained = descriptor_file_stat(descriptor)
        named = os.stat(claim, dir_fd=directory_fd, follow_symlinks=False)
        if (
            is_link_or_reparse(retained)
            or is_link_or_reparse(named)
            or not stat.S_ISREG(retained.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or retained.st_nlink != 1
            or named.st_nlink != 1
            or (retained.st_dev, retained.st_ino) != expected_identity
            or (named.st_dev, named.st_ino) != expected_identity
        ):
            raise AssetContractError("Claimed retained file identity diverged")
        os.unlink(claim, dir_fd=directory_fd)
        claimed = False
        if descriptor_file_stat(descriptor).st_nlink != 0:
            raise AssetContractError("Retained file remained linked after removal")
        os.fsync(directory_fd)
    except BaseException as original:
        if claimed:
            try:
                _linux_rename_names_noreplace(directory_fd, claim, entry)
            except BaseException as rollback_error:
                original.add_note(
                    f"Identity-bound removal rollback could not restore {entry}: {rollback_error}"
                )
        raise


def remove_bound_file_at(
    directory_fd: int,
    name: str,
    *,
    expected_identity: tuple[int, int],
    expected_sha256: str,
    limit: int = MAX_CONTRACT_BYTES,
) -> None:
    """Remove only one exact retained-directory file identity and byte hash."""

    entry = _validated_entry_name(name, context="bound removal name")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            entry,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        retained = descriptor_file_stat(descriptor)
        if (
            is_link_or_reparse(retained)
            or not stat.S_ISREG(retained.st_mode)
            or retained.st_nlink != 1
            or (retained.st_dev, retained.st_ino) != expected_identity
            or retained.st_size > limit
        ):
            raise AssetContractError(f"Retained file identity or bytes diverged: {entry}")
        payload = bytearray()
        while len(payload) <= limit:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = descriptor_file_stat(descriptor)
        if (
            len(payload) > limit
            or after.st_nlink != 1
            or (after.st_dev, after.st_ino) != expected_identity
            or after.st_size != len(payload)
            or hashlib.sha256(payload).hexdigest() != expected_sha256
        ):
            raise AssetContractError(f"Retained file identity or bytes diverged: {entry}")
        remove_retained_regular_file_at(
            directory_fd,
            entry,
            descriptor,
            expected_identity=expected_identity,
        )
    except AssetContractError:
        raise
    except OSError as exc:
        raise AssetContractError(f"Could not remove retained file {entry}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def write_bytes_identity_atomic_replace_at(
    directory_fd: int,
    destination_name: str,
    payload: bytes,
    *,
    expected_sha256: str,
    expected_identity: tuple[int, int],
    staging_name: str,
    before_exchange: Callable[[], None] | None = None,
    after_exchange: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Exchange an exact retained file with rollback-safe identity verification.

    Linux ``renameat2(RENAME_EXCHANGE)`` is required. Platforms without that
    primitive fail closed; there is deliberately no rename-over fallback.
    """

    if os.name != "posix" or not hasattr(os, "O_TMPFILE"):
        raise AssetContractError("identity-atomic replacement is unavailable")
    destination = _validated_entry_name(destination_name, context="destination name")
    staging = _validated_entry_name(staging_name, context="staging name")
    if destination == staging:
        raise AssetContractError("staging name must differ from the destination")
    if not isinstance(payload, bytes) or not payload:
        raise AssetContractError("replacement payload must be non-empty bytes")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise AssetContractError("expected_sha256 must be a lowercase SHA-256 digest")
    if (
        not isinstance(expected_identity, tuple)
        or len(expected_identity) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in expected_identity
        )
    ):
        raise AssetContractError("expected_identity must be a file identity pair")

    source = read_bound_bytes_at(directory_fd, destination, limit=MAX_CONTRACT_BYTES)
    if source.identity != expected_identity:
        raise AssetContractError("Source identity diverged before atomic exchange")
    if hashlib.sha256(source.payload).hexdigest() != expected_sha256:
        raise AssetContractError("Source bytes diverged before atomic exchange")

    flags = os.O_RDWR | os.O_TMPFILE | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    temporary_fd: int | None = None
    temporary_identity: tuple[int, int] | None = None
    linked = False
    exchanged = False
    try:
        try:
            temporary_fd = os.open(".", flags, 0o600, dir_fd=directory_fd)
        except OSError as exc:
            unavailable = {
                errno.EINVAL,
                errno.ENOSYS,
                errno.EPERM,
                errno.ENOTSUP,
                errno.EOPNOTSUPP,
            }
            if exc.errno in unavailable:
                raise AssetContractError("identity-atomic replacement is unavailable") from exc
            raise AssetContractError(
                f"Could not allocate identity-atomic temporary file: {exc}"
            ) from exc
        temporary_info = descriptor_file_stat(temporary_fd)
        temporary_identity = temporary_info.st_dev, temporary_info.st_ino
        if (
            is_link_or_reparse(temporary_info)
            or not stat.S_ISREG(temporary_info.st_mode)
            or temporary_info.st_nlink != 0
        ):
            raise AssetContractError("Identity-atomic temporary file is unsafe")
        _write_all(temporary_fd, payload)
        _linux_link_descriptor_no_replace(temporary_fd, directory_fd, staging)
        linked = True
        staged = read_bound_bytes_at(directory_fd, staging, limit=max(len(payload), 1))
        if staged.identity != temporary_identity or staged.payload != payload:
            raise AssetContractError("Identity-atomic staging file diverged")
        try:
            os.fsync(directory_fd)
        except OSError as exc:
            raise AssetContractError(
                f"Could not durably flush identity-atomic staging entry: {exc}"
            ) from exc
        if before_exchange is not None:
            before_exchange()
        _linux_exchange_names(directory_fd, staging, destination)
        exchanged = True
        if after_exchange is not None:
            after_exchange()

        published = read_bound_bytes_at(
            directory_fd,
            destination,
            limit=max(MAX_CONTRACT_BYTES, len(payload)),
        )
        displaced = read_bound_bytes_at(directory_fd, staging, limit=MAX_CONTRACT_BYTES)
        if published.identity != temporary_identity or published.payload != payload:
            raise AssetContractError("Published identity diverged after atomic exchange")
        if (
            displaced.identity != expected_identity
            or hashlib.sha256(displaced.payload).hexdigest() != expected_sha256
        ):
            _linux_exchange_names(directory_fd, staging, destination)
            exchanged = False
            restored = read_bound_bytes_at(directory_fd, destination, limit=MAX_CONTRACT_BYTES)
            staged_again = read_bound_bytes_at(
                directory_fd,
                staging,
                limit=max(MAX_CONTRACT_BYTES, len(payload)),
            )
            if (
                restored.identity != displaced.identity
                or staged_again.identity != temporary_identity
            ):
                raise AssetContractError("Identity-safe rollback diverged")
            remove_bound_file_at(
                directory_fd,
                staging,
                expected_identity=temporary_identity,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                limit=max(MAX_CONTRACT_BYTES, len(payload)),
            )
            linked = False
            os.fsync(directory_fd)
            raise AssetContractError("Source identity diverged during atomic exchange")

        # The displaced source deliberately remains under ``staging``.  The
        # transaction owner must first durably journal the returned target
        # identity and only then remove that exact displaced identity.  Removing
        # it here would leave an unrecoverable crash window between the exchange
        # and the caller's durable state transition.
        os.fsync(directory_fd)
        return published.identity
    except FileExistsError as exc:
        raise AssetContractError("Identity-atomic staging entry already exists") from exc
    finally:
        primary = sys.exception()
        if linked and not exchanged and temporary_identity is not None:
            try:
                remove_bound_file_at(
                    directory_fd,
                    staging,
                    expected_identity=temporary_identity,
                    expected_sha256=hashlib.sha256(payload).hexdigest(),
                    limit=max(MAX_CONTRACT_BYTES, len(payload)),
                )
            except AssetContractError as exc:
                if primary is not None:
                    primary.add_note(f"Identity-atomic staging cleanup failed: {exc}")
                else:
                    raise
        if temporary_fd is not None:
            os.close(temporary_fd)


@contextmanager
def _exclusive_write_lock(
    destination: Path,
    parent: _PinnedOutputParent,
) -> Iterator[None]:
    """Serialize cooperating replacements of one contract file."""

    if parent.parent_fd is not None:
        try:
            import fcntl

            fcntl.flock(parent.parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (ImportError, OSError) as exc:
            raise AssetContractError(f"Another writer is updating {destination}") from exc
        try:
            parent.assert_current()
            yield
            parent.assert_current()
        finally:
            fcntl.flock(parent.parent_fd, fcntl.LOCK_UN)
        return
    if parent.windows_api is None:
        raise AssetContractError("secure publication primitives are unavailable")
    lock = destination.with_name(f".{destination.name}.lock")
    handle: int | None = None
    descriptor: int | None = None
    locked = False
    try:
        if parent.windows_parent_handle is None:
            raise AssetContractError("secure publication primitives are unavailable")
        handle = parent.windows_api.open_lock(
            parent.windows_parent_handle,
            lock.name,
        )
        info = parent.windows_api._state(
            handle,
            directory=False,
            context=f"output lock {lock}",
        )
        if info.st_nlink != 1:
            raise AssetContractError(f"Output lock is not a standalone regular file: {lock}")
        descriptor = parent.windows_api.duplicate_to_descriptor(
            handle,
            writable=True,
        )
        if info.st_size == 0:
            if os.write(descriptor, b"\0") != 1:
                raise OSError("short write while initializing output lock")
            os.fsync(descriptor)
        elif info.st_size != 1:
            raise AssetContractError(f"Output lock has invalid contents: {lock}")
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise AssetContractError(f"Another writer is updating {destination}") from exc
        locked = True
        parent.assert_current()
        yield
        parent.assert_current()
    finally:
        primary = sys.exception()
        if descriptor is not None:
            if locked:
                try:
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                except (OSError, ImportError) as exc:
                    if primary is not None:
                        primary.add_note(f"Output lock release failed: {exc}")
                    else:
                        raise AssetContractError(
                            f"Could not release output lock {lock}: {exc}"
                        ) from exc
            os.close(descriptor)
        if handle is not None:
            parent.windows_api.close(handle)


def _validated_existing(
    info: FileStat | None,
    destination: Path,
) -> tuple[int, int] | None:
    if info is None:
        return None
    if is_link_or_reparse(info):
        raise AssetContractError(
            f"Refusing to replace symbolic link or reparse point {destination}"
        )
    if not stat.S_ISREG(info.st_mode):
        raise AssetContractError(f"Refusing to replace non-regular file {destination}")
    if info.st_nlink != 1:
        raise AssetContractError(f"Refusing to replace hard-linked file {destination}")
    return info.st_dev, info.st_ino


def _sync_output_parent(parent: _PinnedOutputParent) -> None:
    parent.flush_durable(
        context=f"published JSON parent {parent.path}",
    )


def _write_json_publication(
    path: str | Path,
    value: object,
    *,
    overwrite: bool,
    expected_cooperative_content_hash: str | None,
    durable_parent: bool = False,
) -> None:
    if expected_cooperative_content_hash is not None and not overwrite:
        raise AssertionError("cooperative content checks require replacement")
    if expected_cooperative_content_hash is not None and (
        not isinstance(expected_cooperative_content_hash, str)
        or len(expected_cooperative_content_hash) != 64
        or any(
            character not in "0123456789abcdef" for character in expected_cooperative_content_hash
        )
    ):
        raise AssetContractError(
            "expected_cooperative_content_hash must be a lowercase SHA-256 digest"
        )
    payload = encoded_json(value)
    requested_destination = Path(path)
    destination = Path(os.path.abspath(requested_destination))
    with _open_verified_output_parent(destination.parent) as parent:
        try:
            existing = _entry_info(parent, destination.name)
        except OSError as exc:
            raise AssetContractError(
                f"Could not inspect output {requested_destination}: {exc}"
            ) from exc
        _validated_existing(existing, requested_destination)
        if existing is not None and not overwrite:
            raise AssetContractError(f"Refusing to overwrite {requested_destination}")

        temporary = _create_temporary_entry(
            parent,
            f".{destination.name}.",
        )
        temporary_info = descriptor_file_stat(temporary.descriptor)
        expected_links = 0 if parent.parent_fd is not None else 1
        if (
            is_link_or_reparse(temporary_info)
            or not stat.S_ISREG(temporary_info.st_mode)
            or temporary_info.st_nlink != expected_links
            or (temporary_info.st_dev, temporary_info.st_ino) != temporary.identity
        ):
            _close_temporary_entry(parent, temporary)
            raise AssetContractError(
                f"Temporary output is not an exact regular file for {destination}"
            )
        try:
            _write_all(temporary.descriptor, payload)
            if not overwrite:
                try:
                    _publish_temporary(
                        parent,
                        temporary,
                        destination.name,
                        overwrite=False,
                    )
                except FileExistsError as exc:
                    raise AssetContractError(
                        f"Refusing to overwrite {requested_destination}"
                    ) from exc
            else:
                with _exclusive_write_lock(destination, parent):
                    current_info = _entry_info(parent, destination.name)
                    _validated_existing(current_info, requested_destination)
                    if expected_cooperative_content_hash is not None:
                        current = _read_json_object_entry(
                            parent,
                            destination.name,
                        )
                        if current.get("content_hash") != expected_cooperative_content_hash:
                            raise AssetContractError(
                                f"Content changed before publishing {requested_destination}"
                            )
                    _publish_temporary(
                        parent,
                        temporary,
                        destination.name,
                        overwrite=True,
                    )
            try:
                published = _published_file_info(parent, destination.name)
            except OSError as exc:
                raise AssetContractError(
                    f"Could not verify published output {requested_destination}: {exc}"
                ) from exc
            if (
                published is None
                or is_link_or_reparse(published)
                or not stat.S_ISREG(published.st_mode)
                or published.st_nlink != 1
                or (published.st_dev, published.st_ino) != temporary.identity
                or published.st_size != len(payload)
            ):
                raise AssetContractError(
                    f"Published output identity changed: {requested_destination}"
                )
            if durable_parent:
                try:
                    _sync_output_parent(parent)
                except (AssetContractError, OSError) as exc:
                    raise AssetContractError(
                        f"Published output durability is indeterminate: "
                        f"{requested_destination}: {exc}"
                    ) from exc
        finally:
            primary = sys.exception()
            try:
                _close_temporary_entry(parent, temporary)
            except AssetContractError as exc:
                if primary is not None:
                    primary.add_note(str(exc))
                else:
                    raise


def write_json_atomic(
    path: str | Path,
    value: object,
    *,
    overwrite: bool = False,
    expected_content_hash: str | None = None,
    durable_parent: bool = False,
) -> None:
    """Create one strict-JSON file without replacement.

    The destination name is claimed directly from an anonymous/retained
    temporary handle. Fixed-path replacement and content-hash compare-and-swap
    are deliberately rejected because the supported filesystems expose no
    cross-platform identity-conditional replacement primitive.
    """

    if overwrite or expected_content_hash is not None:
        raise AssetContractError(
            "Secure fixed-path JSON replacement is unavailable; "
            "use write_json_cooperative_replace only when every writer honors "
            "the cooperative lock"
        )
    _write_json_publication(
        path,
        value,
        overwrite=False,
        expected_cooperative_content_hash=None,
        durable_parent=durable_parent,
    )


def write_bytes_atomic(
    path: str | Path,
    payload: bytes,
    *,
    durable_parent: bool = False,
) -> None:
    """Create one exact binary file through the secure no-replace boundary."""

    if not isinstance(payload, bytes) or not payload:
        raise AssetContractError("binary publication payload must be non-empty bytes")
    requested_destination = Path(path)
    destination = Path(os.path.abspath(requested_destination))
    with _open_verified_output_parent(destination.parent) as parent:
        try:
            existing = _entry_info(parent, destination.name)
        except OSError as exc:
            raise AssetContractError(
                f"Could not inspect output {requested_destination}: {exc}"
            ) from exc
        _validated_existing(existing, requested_destination)
        if existing is not None:
            raise AssetContractError(f"Refusing to overwrite {requested_destination}")

        temporary = _create_temporary_entry(
            parent,
            f".{destination.name}.",
        )
        temporary_info = descriptor_file_stat(temporary.descriptor)
        expected_links = 0 if parent.parent_fd is not None else 1
        if (
            is_link_or_reparse(temporary_info)
            or not stat.S_ISREG(temporary_info.st_mode)
            or temporary_info.st_nlink != expected_links
            or (temporary_info.st_dev, temporary_info.st_ino) != temporary.identity
        ):
            _close_temporary_entry(parent, temporary)
            raise AssetContractError(
                f"Temporary output is not an exact regular file for {destination}"
            )
        try:
            _write_all(temporary.descriptor, payload)
            try:
                _publish_temporary(
                    parent,
                    temporary,
                    destination.name,
                    overwrite=False,
                )
            except FileExistsError as exc:
                raise AssetContractError(f"Refusing to overwrite {requested_destination}") from exc
            try:
                published = _published_file_info(parent, destination.name)
            except OSError as exc:
                raise AssetContractError(
                    f"Could not verify published output {requested_destination}: {exc}"
                ) from exc
            if (
                published is None
                or is_link_or_reparse(published)
                or not stat.S_ISREG(published.st_mode)
                or published.st_nlink != 1
                or (published.st_dev, published.st_ino) != temporary.identity
                or published.st_size != len(payload)
            ):
                raise AssetContractError(
                    f"Published output identity changed: {requested_destination}"
                )
            if durable_parent:
                try:
                    _sync_output_parent(parent)
                except (AssetContractError, OSError) as exc:
                    raise AssetContractError(
                        "Published output durability is indeterminate: "
                        f"{requested_destination}: {exc}"
                    ) from exc
        finally:
            primary = sys.exception()
            try:
                _close_temporary_entry(parent, temporary)
            except AssetContractError as exc:
                if primary is not None:
                    primary.add_note(str(exc))
                else:
                    raise


def write_bytes_cooperative_replace(
    path: str | Path,
    payload: bytes,
    *,
    expected_sha256: str,
    expected_identity: tuple[int, int],
    durable_parent: bool = False,
) -> tuple[int, int]:
    """Replace exact bytes after an identity and raw-byte SHA-256 precondition.

    The caller must own the higher-level cooperative lifecycle lock. This
    boundary retains the complete output ancestry, checks the source identity
    and exact bytes again while holding the local writer lock, publishes from a
    retained temporary, and verifies the resulting identity and bytes.
    """

    if not isinstance(payload, bytes) or not payload:
        raise AssetContractError("replacement payload must be non-empty bytes")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise AssetContractError("expected_sha256 must be a lowercase SHA-256 digest")
    if (
        not isinstance(expected_identity, tuple)
        or len(expected_identity) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in expected_identity
        )
    ):
        raise AssetContractError("expected_identity must be a file identity pair")
    requested_destination = Path(path)
    destination = Path(os.path.abspath(requested_destination))
    with _open_verified_output_parent(destination.parent) as parent:
        initial_info = _entry_info(parent, destination.name)
        initial_identity = _validated_existing(initial_info, requested_destination)
        if initial_identity != expected_identity:
            raise AssetContractError(
                f"Output identity changed before publication: {requested_destination}"
            )

        temporary = _create_temporary_entry(parent, f".{destination.name}.")
        temporary_info = descriptor_file_stat(temporary.descriptor)
        expected_links = 0 if parent.parent_fd is not None else 1
        if (
            is_link_or_reparse(temporary_info)
            or not stat.S_ISREG(temporary_info.st_mode)
            or temporary_info.st_nlink != expected_links
            or (temporary_info.st_dev, temporary_info.st_ino) != temporary.identity
        ):
            _close_temporary_entry(parent, temporary)
            raise AssetContractError(
                f"Temporary output is not an exact regular file for {destination}"
            )
        try:
            _write_all(temporary.descriptor, payload)
            with _exclusive_write_lock(destination, parent):
                current = _read_bytes_entry(
                    parent,
                    destination.name,
                    limit=MAX_CONTRACT_BYTES,
                )
                if current.identity != expected_identity:
                    raise AssetContractError(
                        f"Output identity changed before publication: {requested_destination}"
                    )
                if hashlib.sha256(current.payload).hexdigest() != expected_sha256:
                    raise AssetContractError(
                        f"Output bytes changed before publication: {requested_destination}"
                    )
                _publish_temporary(
                    parent,
                    temporary,
                    destination.name,
                    overwrite=True,
                )
            published = _read_bytes_entry(
                parent,
                destination.name,
                limit=max(MAX_CONTRACT_BYTES, len(payload)),
            )
            if published.identity != temporary.identity or published.payload != payload:
                raise AssetContractError(
                    f"Published output identity or bytes changed: {requested_destination}"
                )
            if durable_parent:
                try:
                    _sync_output_parent(parent)
                except (AssetContractError, OSError) as exc:
                    raise AssetContractError(
                        "Published output durability is indeterminate: "
                        f"{requested_destination}: {exc}"
                    ) from exc
                durable = _read_bytes_entry(
                    parent,
                    destination.name,
                    limit=max(MAX_CONTRACT_BYTES, len(payload)),
                )
                if durable != published:
                    raise AssetContractError(
                        f"Published output changed after durable flush: {requested_destination}"
                    )
            return published.identity
        finally:
            primary = sys.exception()
            try:
                _close_temporary_entry(parent, temporary)
            except AssetContractError as exc:
                if primary is not None:
                    primary.add_note(str(exc))
                else:
                    raise


def write_json_cooperative_replace(
    path: str | Path,
    value: object,
    *,
    expected_cooperative_content_hash: str | None = None,
    durable_parent: bool = False,
) -> None:
    """Replace strict JSON for a closed set of cooperating writers.

    This is atomic namespace replacement, not adversarial compare-and-swap.
    The optional content-hash precondition is valid only while every writer
    serializes through this function. An external process that can mutate the
    same directory is outside this API's stated concurrency guarantee.
    """

    _write_json_publication(
        path,
        value,
        overwrite=True,
        expected_cooperative_content_hash=expected_cooperative_content_hash,
        durable_parent=durable_parent,
    )


def bind_content_hash(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["content_hash"] = canonical_payload_hash(result)
    return result


def require_content_hash(payload: dict[str, Any], *, context: str) -> None:
    expected = payload.get("content_hash")
    if not isinstance(expected, str) or expected != canonical_payload_hash(payload):
        raise AssetContractError(f"{context} content hash does not match its contents")
