from __future__ import annotations

import ctypes
import hashlib
import os
import secrets
import stat
import sys
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

from isoworld.content.file_stat import (
    FileStat,
    descriptor_file_stat,
    file_identity,
    path_file_stat,
)
from isoworld.content.media import ValidatedMedia, read_validated_resource

_SNAPSHOT_PREFIX = "isoworld-renderpack-"
_DELETE_PREFIX = ".isoworld-delete-"
_RESOURCE_SNAPSHOT_CHUNK_BYTES = 64 * 1024
MAX_OWNED_RESOURCE_BYTES = 2 * 1024 * 1024 * 1024
_WINDOWS_LOCAL_SYSTEM_SID = "S-1-5-18"
_WINDOWS_BUILTIN_ADMINISTRATORS_SID = "S-1-5-32-544"
_Identity = tuple[int, int]
_POSIX_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_SAFE_POSIX_DIR_FDS = os.name == "posix" and all(
    function in os.supports_dir_fd
    for function in (os.mkdir, os.open, os.stat, os.unlink, os.rmdir, os.rename)
)


class ResourceSnapshotError(RuntimeError):
    """Raised when a private runtime resource snapshot loses safe ownership."""


def note_cleanup_failure(
    primary: BaseException | None,
    cleanup: BaseException,
    *,
    context: str,
) -> bool:
    """Attach bounded cleanup context without replacing an active exception."""

    if primary is None:
        return False
    safe_context = " ".join(context.split())[:160] or "cleanup"
    safe_detail = " ".join(str(cleanup).split())[:512]
    cleanup_type = type(cleanup).__name__
    note = f"{safe_context} failed ({cleanup_type})"
    if safe_detail:
        note += f": {safe_detail}"
    primary.add_note(note)
    return True


@dataclass(frozen=True, slots=True)
class ResourceSnapshotChunk:
    """One fixed-size sequential read from an owned resource snapshot."""

    sequence: int
    payload: bytes
    cumulative_bytes: int
    cumulative_sha256: str
    eof: bool


@dataclass(frozen=True, slots=True)
class MaterializedResource:
    """Metadata for one exact generic file captured into an owned snapshot."""

    path: Path
    size: int
    sha256: str


def _platform_name() -> str:
    """Return the host platform through a narrow, testable seam."""

    return os.name


@dataclass(frozen=True, slots=True)
class _FileRecord:
    identity: _Identity
    size: int
    mtime_ns: int
    ctime_ns: int
    mode: int
    sha256: str


def _identity(info: FileStat) -> _Identity:
    return file_identity(info)


def _file_record(info: FileStat, digest: str) -> _FileRecord:
    return _FileRecord(
        identity=_identity(info),
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
        mode=stat.S_IMODE(info.st_mode),
        sha256=digest,
    )


def _source_state(info: FileStat) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        stat.S_IFMT(info.st_mode),
        info.st_nlink,
    )


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _lexical_absolute(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _source_directory_snapshot(
    root: Path, relative: PurePosixPath
) -> tuple[
    Path,
    tuple[tuple[Path, _Identity], ...],
    FileStat,
]:
    """Capture every lexical parent and one regular, single-link source file."""

    absolute_root = _lexical_absolute(root)
    current = Path(absolute_root.anchor)
    directories: list[tuple[Path, _Identity]] = []
    offset = 0
    if absolute_root.anchor:
        info = path_file_stat(current)
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ResourceSnapshotError(f"Resource source parent is unsafe: {current}")
        directories.append((current, _identity(info)))
        offset = 1
    for part in absolute_root.parts[offset:]:
        current /= part
        info = path_file_stat(current)
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ResourceSnapshotError(f"Resource source parent is unsafe: {current}")
        directories.append((current, _identity(info)))
    if not directories:
        info = path_file_stat(absolute_root)
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ResourceSnapshotError(f"Resource source parent is unsafe: {absolute_root}")
        directories.append((absolute_root, _identity(info)))

    current = absolute_root
    for part in relative.parts[:-1]:
        current /= part
        info = path_file_stat(current)
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ResourceSnapshotError(f"Resource source parent is unsafe: {current}")
        directories.append((current, _identity(info)))
    target = absolute_root.joinpath(*relative.parts)
    info = path_file_stat(target)
    if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise ResourceSnapshotError(f"Resource source must be a regular file: {target}")
    if info.st_nlink != 1:
        raise ResourceSnapshotError(f"Resource source must not be hard-linked: {target}")
    return target, tuple(directories), info


def _verify_source_snapshot(
    root: Path,
    relative: PurePosixPath,
    directories: tuple[tuple[Path, _Identity], ...],
    expected: FileStat,
) -> None:
    target, current_directories, current = _source_directory_snapshot(root, relative)
    if current_directories != directories or _source_state(current) != _source_state(expected):
        raise ResourceSnapshotError(f"Resource source identity changed while reading: {target}")


def _reader_descriptor_matches(info: FileStat, record: _FileRecord) -> bool:
    return _stat_matches_record(info, record) and not (
        _platform_name() == "nt" and getattr(info, "st_file_attributes", 0) & 0x400
    )


def _require_reader_descriptor_state(
    descriptor: int,
    record: _FileRecord,
    *,
    phase: str,
) -> None:
    try:
        info = descriptor_file_stat(descriptor)
    except OSError as exc:
        raise ResourceSnapshotError(
            f"Snapshot reader descriptor became unreadable {phase}: {exc}"
        ) from exc
    if not _reader_descriptor_matches(info, record):
        raise ResourceSnapshotError(f"Snapshot reader descriptor changed {phase}")


def _close_reader_descriptor(descriptor: int) -> None:
    """Close a reader descriptor through a single-attempt test seam."""

    os.close(descriptor)


class ResourceSnapshotReader:
    """Read one sealed snapshot through an owned, forward-only descriptor."""

    __slots__ = (
        "_closed",
        "_cumulative_bytes",
        "_descriptor",
        "_digest",
        "_exhausted",
        "_record",
        "_sequence",
    )

    def __init__(self, descriptor: int, record: _FileRecord) -> None:
        self._descriptor: int | None = descriptor
        self._record = record
        self._closed = False
        self._exhausted = False
        self._sequence = 0
        self._cumulative_bytes = 0
        self._digest = hashlib.sha256()

    @property
    def size(self) -> int:
        return self._record.size

    @property
    def sha256(self) -> str:
        return self._record.sha256

    @property
    def closed(self) -> bool:
        return self._closed

    def _consume_descriptor(self) -> BaseException | None:
        if self._closed:
            return None
        descriptor = self._descriptor
        self._descriptor = None
        self._closed = True
        if descriptor is None:
            return None
        try:
            _close_reader_descriptor(descriptor)
        except BaseException as exc:
            return exc
        return None

    def _raise_read_failure(self, original: BaseException) -> NoReturn:
        close_error = self._consume_descriptor()
        if close_error is not None:
            raise ResourceSnapshotError(
                f"{original}; additionally could not close the consumed snapshot "
                f"reader descriptor: {close_error}"
            ) from original
        if isinstance(original, ResourceSnapshotError):
            raise original
        raise ResourceSnapshotError(f"Could not read snapshot resource: {original}") from original

    def read_next(self) -> ResourceSnapshotChunk:
        if self._closed:
            raise ResourceSnapshotError("Snapshot reader is closed")
        if self._exhausted:
            raise ResourceSnapshotError("Snapshot reader is exhausted")
        descriptor = self._descriptor
        if descriptor is None:
            raise ResourceSnapshotError("Snapshot reader descriptor is unavailable")

        try:
            _require_reader_descriptor_state(
                descriptor,
                self._record,
                phase="before reading",
            )
            remaining = self._record.size - self._cumulative_bytes
            if remaining < 0:
                raise ResourceSnapshotError(
                    "Snapshot reader integrity state exceeded the authorized size"
                )
            requested = min(_RESOURCE_SNAPSHOT_CHUNK_BYTES, remaining)
            pieces: list[bytes] = []
            received = 0
            while received < requested:
                piece = os.read(descriptor, requested - received)
                if not piece:
                    raise ResourceSnapshotError(
                        "Snapshot resource ended before its authorized size"
                    )
                pieces.append(piece)
                received += len(piece)
            payload = b"".join(pieces)
            cumulative_bytes = self._cumulative_bytes + len(payload)
            cumulative_digest = self._digest.copy()
            cumulative_digest.update(payload)

            _require_reader_descriptor_state(
                descriptor,
                self._record,
                phase="after reading",
            )
            eof = cumulative_bytes == self._record.size
            cumulative_sha256 = cumulative_digest.hexdigest()
            if eof and cumulative_sha256 != self._record.sha256:
                raise ResourceSnapshotError(
                    "Snapshot reader integrity did not match the authorized SHA-256"
                )

            sequence = self._sequence
            self._sequence += 1
            self._cumulative_bytes = cumulative_bytes
            self._digest = cumulative_digest
            self._exhausted = eof
            return ResourceSnapshotChunk(
                sequence=sequence,
                payload=payload,
                cumulative_bytes=cumulative_bytes,
                cumulative_sha256=cumulative_sha256,
                eof=eof,
            )
        except BaseException as exc:
            self._raise_read_failure(exc)

    def close(self) -> None:
        close_error = self._consume_descriptor()
        if close_error is not None:
            raise ResourceSnapshotError(
                f"Could not close snapshot reader descriptor: {close_error}"
            ) from close_error


def _windows_attributes(path: Path) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = [ctypes.c_wchar_p]
    get_attributes.restype = ctypes.c_uint32
    attributes = int(get_attributes(str(path)))
    if attributes == 0xFFFFFFFF:
        error = ctypes.get_last_error()
        raise ResourceSnapshotError(
            f"Could not inspect Windows snapshot attributes for {path}: {ctypes.FormatError(error)}"
        )
    return attributes


def _windows_private_sid_strings() -> tuple[str, ...]:
    """Return the exact principals granted access to a private snapshot."""

    return (
        _windows_current_user_sid_string(),
        _WINDOWS_LOCAL_SYSTEM_SID,
        _WINDOWS_BUILTIN_ADMINISTRATORS_SID,
    )


def _windows_acl_sid_inventory(path: Path) -> tuple[str, tuple[str, ...]]:
    """Read the owner and every ordinary allow ACE from a Windows DACL."""

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    get_security = advapi32.GetNamedSecurityInfoW
    get_security.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security.restype = ctypes.c_uint32
    result = int(
        get_security(
            str(path),
            1,  # SE_FILE_OBJECT
            0x00000001 | 0x00000004,  # OWNER_SECURITY_INFORMATION | DACL_...
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
    )
    if result != 0:
        raise ResourceSnapshotError(
            f"Could not validate Windows snapshot ACL for {path}: {ctypes.FormatError(result)}"
        )
    try:
        if not owner.value or not dacl.value:
            raise ResourceSnapshotError(f"Windows snapshot ACL is not private: {path}")

        class _Acl(ctypes.Structure):
            _fields_ = [
                ("revision", ctypes.c_ubyte),
                ("reserved", ctypes.c_ubyte),
                ("size", ctypes.c_ushort),
                ("ace_count", ctypes.c_ushort),
                ("reserved_two", ctypes.c_ushort),
            ]

        class _AceHeader(ctypes.Structure):
            _fields_ = [
                ("ace_type", ctypes.c_ubyte),
                ("ace_flags", ctypes.c_ubyte),
                ("ace_size", ctypes.c_ushort),
            ]

        convert_sid = advapi32.ConvertSidToStringSidW
        convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        convert_sid.restype = ctypes.c_int

        def sid_string(sid: ctypes.c_void_p) -> str:
            converted = ctypes.c_wchar_p()
            if not convert_sid(sid, ctypes.byref(converted)):
                error = ctypes.get_last_error()
                raise ResourceSnapshotError(
                    f"Could not inspect a Windows snapshot SID: {ctypes.FormatError(error)}"
                )
            try:
                value = converted.value
                if value is None:
                    raise ResourceSnapshotError("Windows returned an empty snapshot SID")
                return value
            finally:
                local_free(ctypes.cast(converted, ctypes.c_void_p))

        get_ace = advapi32.GetAce
        get_ace.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        get_ace.restype = ctypes.c_int
        acl = ctypes.cast(dacl, ctypes.POINTER(_Acl)).contents
        allowed_aces: list[str] = []
        for index in range(acl.ace_count):
            ace = ctypes.c_void_p()
            if not get_ace(dacl, index, ctypes.byref(ace)):
                error = ctypes.get_last_error()
                raise ResourceSnapshotError(
                    f"Could not inspect Windows snapshot ACL for {path}: "
                    f"{ctypes.FormatError(error)}"
                )
            header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
            if header.ace_type != 0:  # ACCESS_ALLOWED_ACE_TYPE
                if header.ace_type in {5, 9, 11}:  # object/callback allow ACEs
                    raise ResourceSnapshotError(f"Windows snapshot ACL is not private: {path}")
                continue
            sid = ctypes.c_void_p(ace.value + ctypes.sizeof(_AceHeader) + 4)
            allowed_aces.append(sid_string(sid))
        return sid_string(owner), tuple(allowed_aces)
    finally:
        if descriptor.value:
            local_free(descriptor)


def _windows_acl_is_private(path: Path) -> None:
    """Require exactly the private principals used when the DACL is created."""

    expected = frozenset(_windows_private_sid_strings())
    owner, allowed_aces = _windows_acl_sid_inventory(path)
    if owner not in expected or frozenset(allowed_aces) != expected:
        raise ResourceSnapshotError(f"Windows snapshot ACL is not private: {path}")


def _windows_current_user_sid_string() -> str:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = ctypes.c_void_p
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    open_process_token.restype = ctypes.c_int
    get_token_information = advapi32.GetTokenInformation
    get_token_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    get_token_information.restype = ctypes.c_int
    convert_sid = advapi32.ConvertSidToStringSidW
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert_sid.restype = ctypes.c_int
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    token = ctypes.c_void_p()
    if not open_process_token(get_current_process(), 0x0008, ctypes.byref(token)):
        error = ctypes.get_last_error()
        raise ResourceSnapshotError(
            f"Could not inspect the Windows process token: {ctypes.FormatError(error)}"
        )
    try:
        required = ctypes.c_uint32()
        get_token_information(token, 1, None, 0, ctypes.byref(required))
        if not required.value:
            error = ctypes.get_last_error()
            raise ResourceSnapshotError(
                f"Could not size the Windows process token: {ctypes.FormatError(error)}"
            )
        buffer = ctypes.create_string_buffer(required.value)
        if not get_token_information(token, 1, buffer, required, ctypes.byref(required)):
            error = ctypes.get_last_error()
            raise ResourceSnapshotError(
                f"Could not read the Windows process token: {ctypes.FormatError(error)}"
            )

        class _SidAndAttributes(ctypes.Structure):
            _fields_ = [("sid", ctypes.c_void_p), ("attributes", ctypes.c_uint32)]

        sid = ctypes.cast(buffer, ctypes.POINTER(_SidAndAttributes)).contents.sid
        converted = ctypes.c_wchar_p()
        if not convert_sid(sid, ctypes.byref(converted)):
            error = ctypes.get_last_error()
            raise ResourceSnapshotError(
                f"Could not format the Windows user SID: {ctypes.FormatError(error)}"
            )
        try:
            value = converted.value
            if value is None:
                raise ResourceSnapshotError("Windows returned an empty current-user SID")
            return value
        finally:
            local_free(ctypes.cast(converted, ctypes.c_void_p))
    finally:
        close_handle(token)


def _windows_create_private_directory(path: Path) -> None:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sddl = "D:P" + "".join(f"(A;OICI;FA;;;{sid})" for sid in _windows_private_sid_strings())
    descriptor = ctypes.c_void_p()
    convert_descriptor = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert_descriptor.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    convert_descriptor.restype = ctypes.c_int
    if not convert_descriptor(sddl, 1, ctypes.byref(descriptor), None):
        error = ctypes.get_last_error()
        raise ResourceSnapshotError(
            f"Could not build a private Windows snapshot ACL: {ctypes.FormatError(error)}"
        )

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint32),
            ("security_descriptor", ctypes.c_void_p),
            ("inherit_handle", ctypes.c_int),
        ]

    attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
    create_directory = kernel32.CreateDirectoryW
    create_directory.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(_SecurityAttributes)]
    create_directory.restype = ctypes.c_int
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    try:
        if not create_directory(str(path), ctypes.byref(attributes)):
            error = ctypes.get_last_error()
            if error in {80, 183}:  # ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS
                raise FileExistsError(error, ctypes.FormatError(error), str(path))
            raise OSError(error, ctypes.FormatError(error), str(path))
    finally:
        local_free(descriptor)


def _windows_lock_directory(path: Path) -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
        0x00000080 | 0x00020000,  # FILE_READ_ATTRIBUTES | READ_CONTROL
        0x00000001 | 0x00000002,  # share reads/writes, but never deletion
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    if handle in {None, ctypes.c_void_p(-1).value}:
        error = ctypes.get_last_error()
        raise ResourceSnapshotError(
            f"Could not lock Windows snapshot directory {path}: {ctypes.FormatError(error)}"
        )
    guarded_handle = int(handle)
    try:
        attributes = _windows_attributes(path)
        if not attributes & 0x10 or attributes & 0x400:  # DIRECTORY, REPARSE_POINT
            raise ResourceSnapshotError(f"Windows snapshot path is not a plain directory: {path}")
        return guarded_handle
    except BaseException as validation_error:
        try:
            _windows_close_handle(guarded_handle)
        except BaseException as close_error:
            raise ResourceSnapshotError(
                f"{validation_error}; additionally could not close the acquired "
                f"Windows directory handle: {close_error}"
            ) from validation_error
        raise


def _windows_close_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    if not close_handle(ctypes.c_void_p(handle)):
        error = ctypes.get_last_error()
        raise ResourceSnapshotError(
            f"Could not close a Windows snapshot directory handle: {ctypes.FormatError(error)}"
        )


def _private_directory(path: Path, expected: _Identity) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ResourceSnapshotError(
            f"Snapshot directory is missing or unreadable: {path}: {exc}"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ResourceSnapshotError(f"Snapshot directory is no longer a directory: {path}")
    if _identity(info) != expected:
        raise ResourceSnapshotError(f"Snapshot directory identity changed: {path}")
    if os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o700:
        raise ResourceSnapshotError(f"Snapshot directory is not private: {path}")
    if os.name == "nt":
        if _windows_attributes(path) & 0x400:
            raise ResourceSnapshotError(f"Snapshot directory became a reparse point: {path}")
        _windows_acl_is_private(path)


def _stat_matches_record(info: FileStat, record: _FileRecord) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and _identity(info) == record.identity
        and info.st_size == record.size
        and info.st_mtime_ns == record.mtime_ns
        and info.st_ctime_ns == record.ctime_ns
        and stat.S_IMODE(info.st_mode) == record.mode
    )


def _stat_matches_claimed_record(info: FileStat, record: _FileRecord) -> bool:
    """Match state after an owner rename, which may legitimately advance ctime."""

    return (
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and _identity(info) == record.identity
        and info.st_size == record.size
        and info.st_mtime_ns == record.mtime_ns
        and stat.S_IMODE(info.st_mode) == record.mode
    )


def _validate_file_privacy(path: Path, info: FileStat) -> None:
    if os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o400:
        raise ResourceSnapshotError(f"Snapshot file is not sealed and private: {path}")
    if os.name == "nt":
        attributes = _windows_attributes(path)
        if attributes & 0x400:
            raise ResourceSnapshotError(f"Snapshot file became a reparse point: {path}")
        if not attributes & 0x1 or stat.S_IMODE(info.st_mode) & stat.S_IWRITE:
            raise ResourceSnapshotError(f"Snapshot file is not read-only: {path}")
        _windows_acl_is_private(path)


def _rename_noreplace(
    parent_descriptor: int | None,
    parent_path: Path,
    source_name: str,
    destination_name: str,
) -> None:
    if os.name == "posix":
        if not _SAFE_POSIX_DIR_FDS:
            raise ResourceSnapshotError(
                "Secure snapshot publication requires directory-relative filesystem primitives"
            )
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise ResourceSnapshotError(
                "Secure snapshot cleanup requires atomic renameat2 without replacement"
            )
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        assert parent_descriptor is not None
        if (
            renameat2(
                parent_descriptor,
                os.fsencode(source_name),
                parent_descriptor,
                os.fsencode(destination_name),
                1,  # RENAME_NOREPLACE
            )
            != 0
        ):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), source_name, destination_name)
        return
    if os.name == "nt":
        os.rename(parent_path / source_name, parent_path / destination_name)
        return
    raise ResourceSnapshotError("Secure snapshot cleanup is unsupported on this platform")


def _entry_stat(
    parent_descriptor: int | None,
    parent_path: Path,
    name: str,
) -> os.stat_result:
    if os.name == "posix":
        assert parent_descriptor is not None
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    return (parent_path / name).lstat()


def _file_entry_stat(
    parent_descriptor: int | None,
    parent_path: Path,
    name: str,
) -> FileStat:
    if _platform_name() == "nt":
        return path_file_stat(parent_path / name)
    if _platform_name() == "posix":
        assert parent_descriptor is not None
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    raise ResourceSnapshotError("Secure snapshot file inspection is unsupported on this platform")


def _open_new_file(
    *,
    parent_descriptor: int | None,
    parent_path: Path,
    name: str,
    flags: int,
    mode: int,
) -> int:
    if os.name == "posix":
        assert parent_descriptor is not None
        return os.open(name, flags, mode, dir_fd=parent_descriptor)
    return os.open(parent_path / name, flags, mode)


def _open_existing_file(
    *,
    parent_descriptor: int | None,
    parent_path: Path,
    name: str,
    flags: int,
) -> int:
    platform_name = _platform_name()
    if platform_name == "posix":
        if parent_descriptor is None:
            raise ResourceSnapshotError(
                "Snapshot reader requires a stable parent directory descriptor"
            )
        return os.open(name, flags, dir_fd=parent_descriptor)
    if platform_name == "nt":
        return os.open(parent_path / name, flags)
    raise ResourceSnapshotError("Secure snapshot readers are unsupported on this platform")


def _claim_entry(
    *,
    parent_descriptor: int | None,
    parent_path: Path,
    source_name: str,
    claim_name: str,
    expected: _Identity,
    directory: bool,
) -> None:
    """Atomically claim a name, verify it, and safely roll back a foreign claim."""

    try:
        _rename_noreplace(parent_descriptor, parent_path, source_name, claim_name)
    except OSError as exc:
        raise ResourceSnapshotError(
            f"Could not atomically claim snapshot entry {parent_path / source_name}: {exc}"
        ) from exc
    try:
        info = (
            _entry_stat(parent_descriptor, parent_path, claim_name)
            if directory
            else _file_entry_stat(parent_descriptor, parent_path, claim_name)
        )
        expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if stat.S_ISLNK(info.st_mode) or not expected_type or _identity(info) != expected:
            raise ResourceSnapshotError(
                f"Snapshot entry identity changed before cleanup: {parent_path / source_name}"
            )
    except Exception as original:
        try:
            _rename_noreplace(parent_descriptor, parent_path, claim_name, source_name)
        except Exception as rollback_error:
            raise ResourceSnapshotError(
                f"Foreign snapshot claim could not be rolled back at "
                f"{parent_path / source_name}: {rollback_error}"
            ) from original
        raise


class ResourceSnapshotOwner:
    """Own one sealed renderpack snapshot and delete only identity-bound entries.

    Linux uses stable directory descriptors and no-replace rename claims. Windows
    holds no-delete directory handles, rejects reparse points, applies and audits a
    private ACL, and treats a missing OS safety primitive as a hard failure.
    """

    __slots__ = (
        "root",
        "_active_root_name",
        "_closed",
        "_directories",
        "_directory_descriptors",
        "_directory_handles",
        "_files",
        "_parent",
        "_parent_descriptor",
        "_parent_handle",
        "_reader",
        "_reader_issued",
        "_root_claimed",
        "_root_removed",
    )

    def __init__(self) -> None:
        platform_name = _platform_name()
        if platform_name == "posix" and not sys.platform.startswith("linux"):
            raise ResourceSnapshotError(
                "Runtime resource snapshots support Linux and Windows; "
                "this POSIX platform has no configured atomic no-replace claim"
            )
        if platform_name == "posix" and not _SAFE_POSIX_DIR_FDS:
            raise ResourceSnapshotError(
                "Secure runtime snapshots require directory-relative filesystem primitives"
            )
        if platform_name not in {"posix", "nt"}:
            raise ResourceSnapshotError("Runtime resource snapshots are unsupported here")

        # Construction remains closed until the root identity and stable handle
        # have both been acquired.  This prevents a partially initialized object
        # from trying to finalize fields that were never established.
        self._closed = True
        self._root_claimed = False
        self._root_removed = False
        self._directories: dict[PurePosixPath, _Identity] = {}
        self._directory_descriptors: dict[PurePosixPath, int] = {}
        self._directory_handles: dict[PurePosixPath, int] = {}
        self._files: dict[PurePosixPath, _FileRecord] = {}
        self._parent_descriptor: int | None = None
        self._parent_handle: int | None = None
        self._reader: ResourceSnapshotReader | None = None
        self._reader_issued = False

        self._parent = Path(tempfile.gettempdir())
        root_name = ""
        root_path: Path | None = None
        root_created = False
        try:
            parent = self._parent.resolve(strict=True)
            self._parent = parent
            if platform_name == "posix":
                self._parent_descriptor = self._open_absolute_directory(parent)
            else:
                self._parent_handle = _windows_lock_directory(parent)

            for _ in range(128):
                root_name = f"{_SNAPSHOT_PREFIX}{secrets.token_hex(16)}"
                root_path = parent / root_name
                try:
                    if platform_name == "posix":
                        assert self._parent_descriptor is not None
                        os.mkdir(root_name, 0o700, dir_fd=self._parent_descriptor)
                    else:
                        _windows_create_private_directory(root_path)
                    root_created = True
                    break
                except FileExistsError:
                    continue
            else:
                raise ResourceSnapshotError("Could not allocate a unique snapshot root")

            assert root_path is not None
            self.root = root_path
            self._active_root_name = root_name
            if platform_name == "posix":
                assert self._parent_descriptor is not None
                created = os.stat(
                    root_name,
                    dir_fd=self._parent_descriptor,
                    follow_symlinks=False,
                )
            else:
                created = root_path.lstat()
            if not stat.S_ISDIR(created.st_mode) or stat.S_ISLNK(created.st_mode):
                raise ResourceSnapshotError(f"Snapshot root is not a directory: {root_path}")
            created_identity = _identity(created)
            self._directories[PurePosixPath(".")] = created_identity

            if platform_name == "posix":
                descriptor = os.open(
                    root_name,
                    _POSIX_DIRECTORY_FLAGS,
                    dir_fd=self._parent_descriptor,
                )
                self._directory_descriptors[PurePosixPath(".")] = descriptor
                os.fchmod(descriptor, 0o700)
                opened = os.fstat(descriptor)
            else:
                handle = _windows_lock_directory(root_path)
                self._directory_handles[PurePosixPath(".")] = handle
                opened = root_path.lstat()
            if _identity(opened) != created_identity:
                raise ResourceSnapshotError(
                    f"Snapshot root changed while acquiring its stable handle: {root_path}"
                )
            _private_directory(root_path, created_identity)
        except Exception as original:
            try:
                self._cleanup_failed_initialization(
                    root_name,
                    root_path,
                    original,
                    root_created=root_created,
                )
            except Exception:
                self._closed = (
                    PurePosixPath(".") not in self._directories
                    and self._parent_descriptor is None
                    and self._parent_handle is None
                    and not self._directory_descriptors
                    and not self._directory_handles
                )
                raise
            raise
        self._closed = False

    @staticmethod
    def _open_absolute_directory(path: Path) -> int:
        if not path.is_absolute():
            raise ResourceSnapshotError(f"Snapshot parent must be absolute: {path}")
        descriptor = os.open(path.anchor, _POSIX_DIRECTORY_FLAGS)
        try:
            for part in path.parts[1:]:
                child = os.open(part, _POSIX_DIRECTORY_FLAGS, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                raise ResourceSnapshotError(f"Snapshot parent is not a directory: {path}")
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _cleanup_failed_initialization(
        self,
        root_name: str,
        root_path: Path | None,
        original: BaseException,
        *,
        root_created: bool,
    ) -> None:
        del root_path
        for relative in sorted(
            tuple(self._directory_descriptors),
            key=lambda value: (len(value.parts), value.as_posix()),
            reverse=True,
        ):
            descriptor = self._directory_descriptors[relative]
            try:
                os.close(descriptor)
            except OSError as exc:
                raise ResourceSnapshotError(
                    f"Could not release failed snapshot directory handle: {exc}"
                ) from original
            self._directory_descriptors.pop(relative)
        for relative in sorted(
            tuple(self._directory_handles),
            key=lambda value: (len(value.parts), value.as_posix()),
            reverse=True,
        ):
            handle = self._directory_handles[relative]
            try:
                _windows_close_handle(handle)
            except ResourceSnapshotError as exc:
                raise ResourceSnapshotError(
                    f"Could not release failed Windows snapshot handle: {exc}"
                ) from original
            self._directory_handles.pop(relative)

        expected_root = self._directories.get(PurePosixPath("."))
        missing_identity = root_created and expected_root is None
        if not root_created:
            self._root_removed = True
        if root_name and expected_root is not None:
            claim_name = f"{_DELETE_PREFIX}{secrets.token_hex(16)}"
            try:
                _claim_entry(
                    parent_descriptor=self._parent_descriptor,
                    parent_path=self._parent,
                    source_name=root_name,
                    claim_name=claim_name,
                    expected=expected_root,
                    directory=True,
                )
                if os.name == "posix" and self._parent_descriptor is not None:
                    os.rmdir(claim_name, dir_fd=self._parent_descriptor)
                else:
                    (self._parent / claim_name).rmdir()
            except OSError as exc:
                try:
                    _rename_noreplace(
                        self._parent_descriptor,
                        self._parent,
                        claim_name,
                        root_name,
                    )
                    self._active_root_name = root_name
                    self._root_claimed = False
                    self._acquire_directory_handle(PurePosixPath("."))
                except Exception as rollback_error:
                    self._active_root_name = claim_name
                    self._root_claimed = True
                    try:
                        self._acquire_directory_handle(PurePosixPath("."))
                    except Exception:
                        pass
                    raise ResourceSnapshotError(
                        f"Failed snapshot root cleanup rollback failed: {rollback_error}"
                    ) from exc
                raise ResourceSnapshotError(
                    f"Could not remove failed snapshot root; ownership was restored: {exc}"
                ) from original
            except ResourceSnapshotError:
                self._acquire_directory_handle(PurePosixPath("."))
                raise
            self._root_removed = True

        if self._parent_descriptor is not None:
            try:
                os.close(self._parent_descriptor)
            except OSError as exc:
                raise ResourceSnapshotError(
                    f"Could not close failed snapshot parent handle: {exc}"
                ) from original
            self._parent_descriptor = None
        if self._parent_handle is not None:
            try:
                _windows_close_handle(self._parent_handle)
            except ResourceSnapshotError as exc:
                raise ResourceSnapshotError(
                    f"Could not close failed Windows snapshot parent handle: {exc}"
                ) from original
            self._parent_handle = None
        if self._root_removed:
            self._directories.clear()
        if missing_identity:
            raise ResourceSnapshotError(
                "Refused to remove a snapshot root whose identity was not acquired"
            ) from original

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> ResourceSnapshotOwner:
        if self._closed:
            raise ResourceSnapshotError("Snapshot owner is already closed")
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        del exc_type, traceback
        try:
            self.close()
        except ResourceSnapshotError as cleanup_error:
            if not note_cleanup_failure(
                exc,
                cleanup_error,
                context="runtime resource snapshot cleanup",
            ):
                raise
        return False

    def __del__(self) -> None:
        if getattr(self, "_closed", True):
            return
        try:
            self.close()
        except Exception as exc:
            warnings.warn(
                f"Could not finalize runtime resource snapshot {getattr(self, 'root', '?')}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )

    @property
    def _active_root(self) -> Path:
        return self._parent / self._active_root_name

    def _check_open(self) -> None:
        if self._closed:
            raise ResourceSnapshotError("Snapshot owner is closed")
        self._validate_directory(PurePosixPath("."))

    @staticmethod
    def _validate_relative(relative: PurePosixPath) -> None:
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ResourceSnapshotError(
                f"Snapshot path is not a canonical relative path: {relative}"
            )

    def _directory_path(self, relative: PurePosixPath) -> Path:
        if relative == PurePosixPath("."):
            return self._active_root
        return self._active_root.joinpath(*relative.parts)

    def _directory_descriptor(self, relative: PurePosixPath) -> int | None:
        return self._directory_descriptors.get(relative)

    def _validate_directory(self, relative: PurePosixPath) -> None:
        expected = self._directories.get(relative)
        if expected is None:
            raise ResourceSnapshotError(f"Snapshot does not own directory {relative}")
        path = self._directory_path(relative)
        _private_directory(path, expected)
        descriptor = self._directory_descriptors.get(relative)
        if descriptor is not None:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or _identity(info) != expected
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise ResourceSnapshotError(f"Snapshot directory handle changed: {path}")

    def _acquire_directory_handle(self, relative: PurePosixPath) -> None:
        expected = self._directories[relative]
        path = self._directory_path(relative)
        if os.name == "posix":
            parent_relative = relative.parent
            if relative == PurePosixPath("."):
                assert self._parent_descriptor is not None
                descriptor = os.open(
                    self._active_root_name,
                    _POSIX_DIRECTORY_FLAGS,
                    dir_fd=self._parent_descriptor,
                )
            else:
                if parent_relative == PurePosixPath("."):
                    parent_relative = PurePosixPath(".")
                descriptor = os.open(
                    relative.name,
                    _POSIX_DIRECTORY_FLAGS,
                    dir_fd=self._directory_descriptors[parent_relative],
                )
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISDIR(opened.st_mode) or _identity(opened) != expected:
                    raise ResourceSnapshotError(
                        f"Snapshot directory changed while acquiring its handle: {path}"
                    )
            except BaseException as validation_error:
                try:
                    os.close(descriptor)
                except OSError as close_error:
                    raise ResourceSnapshotError(
                        f"{validation_error}; additionally could not close the acquired "
                        f"snapshot directory descriptor: {close_error}"
                    ) from validation_error
                raise
            self._directory_descriptors[relative] = descriptor
            return
        handle = _windows_lock_directory(path)
        try:
            opened = path.lstat()
            if _identity(opened) != expected:
                raise ResourceSnapshotError(
                    f"Windows snapshot directory changed while acquiring its handle: {path}"
                )
        except BaseException as validation_error:
            try:
                _windows_close_handle(handle)
            except BaseException as close_error:
                raise ResourceSnapshotError(
                    f"{validation_error}; additionally could not close the acquired "
                    f"Windows snapshot directory handle: {close_error}"
                ) from validation_error
            raise
        self._directory_handles[relative] = handle

    def _rollback_new_directory(
        self,
        parent_relative: PurePosixPath,
        relative: PurePosixPath,
    ) -> None:
        identity = self._directories[relative]
        descriptor = self._directory_descriptors.get(relative)
        if descriptor is not None:
            os.close(descriptor)
            self._directory_descriptors.pop(relative)
        handle = self._directory_handles.get(relative)
        if handle is not None:
            _windows_close_handle(handle)
            self._directory_handles.pop(relative)

        parent_descriptor = self._directory_descriptor(parent_relative)
        parent_path = self._directory_path(parent_relative)
        claim_name = f"{_DELETE_PREFIX}{secrets.token_hex(16)}"
        _claim_entry(
            parent_descriptor=parent_descriptor,
            parent_path=parent_path,
            source_name=relative.name,
            claim_name=claim_name,
            expected=identity,
            directory=True,
        )
        try:
            if os.name == "posix":
                assert parent_descriptor is not None
                os.rmdir(claim_name, dir_fd=parent_descriptor)
            else:
                (parent_path / claim_name).rmdir()
        except OSError as exc:
            try:
                _rename_noreplace(
                    parent_descriptor,
                    parent_path,
                    claim_name,
                    relative.name,
                )
                self._acquire_directory_handle(relative)
            except Exception as rollback_error:
                raise ResourceSnapshotError(
                    f"New snapshot directory rollback failed: {rollback_error}"
                ) from exc
            raise ResourceSnapshotError(
                f"Could not remove failed snapshot directory {parent_path / relative.name}: {exc}"
            ) from exc
        self._directories.pop(relative)

    def _ensure_parent(self, relative: PurePosixPath) -> tuple[PurePosixPath, Path]:
        self._check_open()
        parent_relative = PurePosixPath(".")
        parent = self._active_root
        for part in relative.parts[:-1]:
            candidate_relative = (
                PurePosixPath(part)
                if parent_relative == PurePosixPath(".")
                else parent_relative / part
            )
            candidate = parent / part
            known = self._directories.get(candidate_relative)
            if known is None:
                self._validate_directory(parent_relative)
                created = False
                try:
                    if os.name == "posix":
                        parent_descriptor = self._directory_descriptors[parent_relative]
                        os.mkdir(part, 0o700, dir_fd=parent_descriptor)
                        created = True
                        info = os.stat(
                            part,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                    else:
                        _windows_create_private_directory(candidate)
                        created = True
                        info = candidate.lstat()
                    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                        raise ResourceSnapshotError(
                            f"New snapshot path is not a directory: {candidate}"
                        )
                    known = _identity(info)
                    self._directories[candidate_relative] = known

                    if os.name == "posix":
                        descriptor = os.open(
                            part,
                            _POSIX_DIRECTORY_FLAGS,
                            dir_fd=parent_descriptor,
                        )
                        self._directory_descriptors[candidate_relative] = descriptor
                        os.fchmod(descriptor, 0o700)
                        opened = os.fstat(descriptor)
                    else:
                        handle = _windows_lock_directory(candidate)
                        self._directory_handles[candidate_relative] = handle
                        opened = candidate.lstat()
                    if _identity(opened) != known:
                        raise ResourceSnapshotError(
                            "New snapshot directory changed while acquiring its handle: "
                            f"{candidate}"
                        )
                except Exception as exc:
                    if candidate_relative in self._directories:
                        try:
                            self._rollback_new_directory(
                                parent_relative,
                                candidate_relative,
                            )
                        except Exception as cleanup_error:
                            raise ResourceSnapshotError(
                                "Could not acquire or roll back private snapshot directory "
                                f"{candidate}: {cleanup_error}"
                            ) from exc
                    elif created:
                        raise ResourceSnapshotError(
                            "Could not prove ownership of newly created snapshot directory "
                            f"{candidate}; refusing path-based cleanup"
                        ) from exc
                    raise ResourceSnapshotError(
                        f"Could not create private snapshot directory {candidate}: {exc}"
                    ) from exc
            self._validate_directory(candidate_relative)
            parent_relative = candidate_relative
            parent = candidate
        return parent_relative, parent

    def _open_target(self, relative: PurePosixPath) -> tuple[Path, PurePosixPath, int, _Identity]:
        self._validate_relative(relative)
        parent_relative, parent = self._ensure_parent(relative)
        target = parent / relative.name
        self._validate_directory(parent_relative)
        flags = (
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor: int | None = None
        identity: _Identity | None = None
        try:
            descriptor = _open_new_file(
                parent_descriptor=self._directory_descriptor(parent_relative),
                parent_path=parent,
                name=relative.name,
                flags=flags,
                mode=0o600,
            )
            opened = descriptor_file_stat(descriptor)
            identity = _identity(opened)
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size != 0:
                raise ResourceSnapshotError(f"Snapshot target is not a new regular file: {target}")
            current = self._entry_stat(parent_relative, relative.name)
            if _identity(current) != identity or not stat.S_ISREG(current.st_mode):
                raise ResourceSnapshotError(f"Snapshot target identity changed: {target}")
            self._validate_directory(parent_relative)
            return target, parent_relative, descriptor, identity
        except Exception as original:
            close_error: OSError | None = None
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    close_error = exc
            if identity is not None:
                if close_error is not None:
                    try:
                        self._record_restored_partial(
                            parent_relative,
                            relative.name,
                            identity,
                        )
                    except Exception as inventory_error:
                        if relative in self._files:
                            raise ResourceSnapshotError(
                                f"Could not close failed snapshot target {target}: "
                                f"{close_error}; partial ownership was recorded but its "
                                f"validation handle also failed to close: {inventory_error}"
                            ) from original
                        raise ResourceSnapshotError(
                            f"Could not close failed snapshot target {target}: "
                            f"{close_error}; partial ownership registration also failed: "
                            f"{inventory_error}"
                        ) from original
                    raise ResourceSnapshotError(
                        f"Could not close failed snapshot target {target}: {close_error}; "
                        "partial ownership was recorded for retry"
                    ) from original
                try:
                    self._remove_partial(parent_relative, relative.name, identity)
                except ResourceSnapshotError as cleanup_error:
                    raise cleanup_error from original
            if close_error is not None:
                raise ResourceSnapshotError(
                    f"Could not close failed snapshot target {target}: {close_error}"
                ) from original
            raise

    def _entry_stat(self, parent_relative: PurePosixPath, name: str) -> FileStat:
        return _file_entry_stat(
            self._directory_descriptor(parent_relative),
            self._directory_path(parent_relative),
            name,
        )

    def _remove_partial(
        self,
        parent_relative: PurePosixPath,
        name: str,
        identity: _Identity,
    ) -> None:
        claim_name = f"{_DELETE_PREFIX}{secrets.token_hex(16)}"
        parent_descriptor = self._directory_descriptor(parent_relative)
        parent_path = self._directory_path(parent_relative)
        try:
            _claim_entry(
                parent_descriptor=parent_descriptor,
                parent_path=parent_path,
                source_name=name,
                claim_name=claim_name,
                expected=identity,
                directory=False,
            )
        except FileNotFoundError:
            return
        except Exception as claim_error:
            try:
                self._record_restored_partial(parent_relative, name, identity)
            except FileNotFoundError:
                raise claim_error from None
            except Exception as inventory_error:
                raise ResourceSnapshotError(
                    "Partial snapshot target claim failed and restored ownership "
                    f"could not be recorded: {inventory_error}"
                ) from claim_error
            raise ResourceSnapshotError(
                f"Could not claim partial snapshot target {parent_path / name}; "
                "ownership was recorded for retry"
            ) from claim_error
        try:
            if os.name == "posix":
                assert parent_descriptor is not None
                os.unlink(claim_name, dir_fd=parent_descriptor)
            else:
                claimed = parent_path / claim_name
                os.chmod(claimed, stat.S_IWRITE)
                claimed.unlink()
        except OSError as exc:
            try:
                _rename_noreplace(parent_descriptor, parent_path, claim_name, name)
            except Exception as rollback_error:
                raise ResourceSnapshotError(
                    f"Partial snapshot target cleanup and rollback failed: {rollback_error}"
                ) from exc
            try:
                self._record_restored_partial(parent_relative, name, identity)
            except Exception as inventory_error:
                raise ResourceSnapshotError(
                    "Partial snapshot target was restored but its ownership could not be "
                    f"recorded: {inventory_error}"
                ) from exc
            raise ResourceSnapshotError(
                f"Could not remove partial snapshot target {parent_path / name}: {exc}"
            ) from exc

    def _record_restored_partial(
        self,
        parent_relative: PurePosixPath,
        name: str,
        identity: _Identity,
    ) -> None:
        parent_descriptor = self._directory_descriptor(parent_relative)
        parent_path = self._directory_path(parent_relative)
        target = parent_path / name
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        if os.name == "posix":
            assert parent_descriptor is not None
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        else:
            descriptor = os.open(target, flags)
        record: _FileRecord | None = None
        validation_error: BaseException | None = None
        try:
            opened = descriptor_file_stat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or _identity(opened) != identity
            ):
                raise ResourceSnapshotError(
                    f"Restored partial snapshot target changed identity: {target}"
                )
            if os.name == "posix":
                os.fchmod(descriptor, 0o400)
            else:
                os.chmod(target, stat.S_IREAD)
            sealed = descriptor_file_stat(descriptor)
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = descriptor_file_stat(descriptor)
            record = _file_record(sealed, digest.hexdigest())
            if not _stat_matches_record(after, record):
                raise ResourceSnapshotError(
                    f"Restored partial snapshot target changed while recording: {target}"
                )
        except BaseException as exc:
            validation_error = exc

        close_error: OSError | None = None
        try:
            os.close(descriptor)
        except OSError as exc:
            close_error = exc
        if validation_error is not None:
            if close_error is not None:
                raise ResourceSnapshotError(
                    f"{validation_error}; additionally could not close the partial "
                    f"validation descriptor: {close_error}"
                ) from validation_error
            raise validation_error
        assert record is not None

        try:
            current = self._entry_stat(parent_relative, name)
            if not _stat_matches_record(current, record):
                raise ResourceSnapshotError(
                    f"Restored partial snapshot target changed after recording: {target}"
                )
            _validate_file_privacy(target, current)
            self._validate_directory(parent_relative)
        except BaseException as current_error:
            if close_error is not None:
                raise ResourceSnapshotError(
                    f"{current_error}; additionally could not close the partial "
                    f"validation descriptor: {close_error}"
                ) from current_error
            raise
        relative = (
            PurePosixPath(name) if parent_relative == PurePosixPath(".") else parent_relative / name
        )
        self._files[relative] = record
        if close_error is not None:
            raise ResourceSnapshotError(
                f"Could not close partial validation descriptor for {target}: "
                f"{close_error}; ownership was recorded for retry"
            ) from close_error

    def materialize(
        self,
        source_root: Path,
        relative: PurePosixPath,
        media_type: str,
        *,
        limit: int,
    ) -> ValidatedMedia:
        target, parent_relative, descriptor, identity = self._open_target(relative)
        descriptor_open = True
        try:
            resource = read_validated_resource(
                source_root,
                relative,
                media_type,
                limit=limit,
                materialize_descriptor=descriptor,
            )
            opened = descriptor_file_stat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or _identity(opened) != identity
                or opened.st_size < 0
            ):
                raise ResourceSnapshotError(
                    f"Snapshot target identity changed while materializing: {target}"
                )
            if os.name == "posix":
                os.fchmod(descriptor, 0o400)
            else:
                os.chmod(target, stat.S_IREAD)
            os.fsync(descriptor)
            sealed = descriptor_file_stat(descriptor)
            record = _file_record(sealed, resource.sha256)
            _validate_file_privacy(target, sealed)
            current = self._entry_stat(parent_relative, relative.name)
            if not _stat_matches_record(current, record):
                raise ResourceSnapshotError(
                    f"Snapshot target changed while it was being sealed: {target}"
                )
            self._validate_directory(parent_relative)
            try:
                os.close(descriptor)
            except OSError as close_error:
                descriptor_open = False
                self._files[relative] = record
                raise ResourceSnapshotError(
                    f"Could not close sealed snapshot target {target}: {close_error}; "
                    "ownership was recorded for retry"
                ) from close_error
            descriptor_open = False
            self._files[relative] = record
            return resource
        except Exception as original:
            if relative in self._files:
                raise
            close_error: OSError | None = None
            if descriptor_open:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    close_error = exc
                else:
                    descriptor_open = False
            if close_error is not None:
                try:
                    self._record_restored_partial(
                        parent_relative,
                        relative.name,
                        identity,
                    )
                except Exception as inventory_error:
                    if relative in self._files:
                        raise ResourceSnapshotError(
                            f"{original}; additionally could not close partial snapshot "
                            f"target {target}: {close_error}; ownership was recorded but "
                            f"its validation handle also failed: {inventory_error}"
                        ) from original
                    raise ResourceSnapshotError(
                        f"{original}; additionally could not close partial snapshot "
                        f"target {target}: {close_error}; ownership reconciliation "
                        f"failed: {inventory_error}"
                    ) from original
                raise ResourceSnapshotError(
                    f"{original}; additionally could not close partial snapshot target "
                    f"{target}: {close_error}; ownership was recorded for retry"
                ) from original
            try:
                self._remove_partial(parent_relative, relative.name, identity)
            except ResourceSnapshotError as cleanup_error:
                raise cleanup_error from original
            raise

    def materialize_file(
        self,
        source_root: Path,
        source_relative: PurePosixPath,
        destination_relative: PurePosixPath | None = None,
        *,
        limit: int = MAX_OWNED_RESOURCE_BYTES,
    ) -> MaterializedResource:
        """Capture one stable generic file without retaining its bytes in memory.

        This is deliberately separate from :meth:`materialize`: it does not
        interpret media and therefore does not weaken any existing image,
        audio, font, GLSL, or JSON limit. It exists for immutable containers
        whose integral validators inspect the captured bytes later.
        """

        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_OWNED_RESOURCE_BYTES
        ):
            raise ValueError(f"limit must be in 1..{MAX_OWNED_RESOURCE_BYTES} bytes")
        self._validate_relative(source_relative)
        relative = source_relative if destination_relative is None else destination_relative
        self._validate_relative(relative)

        try:
            source, directories, source_info = _source_directory_snapshot(
                Path(source_root),
                source_relative,
            )
        except (OSError, ResourceSnapshotError) as exc:
            if isinstance(exc, ResourceSnapshotError):
                raise
            raise ResourceSnapshotError(
                f"Could not inspect generic resource source: {exc}"
            ) from exc
        if source_info.st_size > limit:
            raise ResourceSnapshotError(f"Resource source exceeds the {limit}-byte limit: {source}")

        target, parent_relative, destination, identity = self._open_target(relative)
        destination_open = True
        source_descriptor: int | None = None
        record: _FileRecord | None = None
        try:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOINHERIT", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            source_descriptor = os.open(source, flags)
            before = descriptor_file_stat(source_descriptor)
            if (
                _is_link_or_reparse(before)
                or not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or _source_state(before) != _source_state(source_info)
            ):
                raise ResourceSnapshotError(f"Resource source changed while opening: {source}")

            digest = hashlib.sha256()
            total = 0
            while total < before.st_size:
                chunk = os.read(
                    source_descriptor,
                    min(1024 * 1024, before.st_size - total),
                )
                if not chunk:
                    raise ResourceSnapshotError(
                        f"Resource source ended before its captured size: {source}"
                    )
                total += len(chunk)
                if total > limit:
                    raise ResourceSnapshotError(
                        f"Resource source exceeds the {limit}-byte limit: {source}"
                    )
                digest.update(chunk)
                offset = 0
                while offset < len(chunk):
                    written = os.write(destination, chunk[offset:])
                    if written <= 0:
                        raise OSError("Could not make progress while materializing resource")
                    offset += written
            if os.read(source_descriptor, 1):
                raise ResourceSnapshotError(
                    f"Resource source grew while it was being captured: {source}"
                )
            after = descriptor_file_stat(source_descriptor)
            if _source_state(after) != _source_state(before):
                raise ResourceSnapshotError(f"Resource source changed while reading: {source}")
            _verify_source_snapshot(
                Path(source_root),
                source_relative,
                directories,
                source_info,
            )
            os.close(source_descriptor)
            source_descriptor = None

            opened = descriptor_file_stat(destination)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or _identity(opened) != identity
                or opened.st_size != total
            ):
                raise ResourceSnapshotError(
                    f"Snapshot target identity changed while materializing: {target}"
                )
            if os.name == "posix":
                os.fchmod(destination, 0o400)
            else:
                os.chmod(target, stat.S_IREAD)
            os.fsync(destination)
            sealed = descriptor_file_stat(destination)
            record = _file_record(sealed, digest.hexdigest())
            _validate_file_privacy(target, sealed)
            current = self._entry_stat(parent_relative, relative.name)
            if not _stat_matches_record(current, record):
                raise ResourceSnapshotError(
                    f"Snapshot target changed while it was being sealed: {target}"
                )
            self._validate_directory(parent_relative)
            os.close(destination)
            destination_open = False
            self._files[relative] = record
            return MaterializedResource(
                path=target,
                size=record.size,
                sha256=record.sha256,
            )
        except BaseException as original:
            if relative in self._files:
                raise
            source_close_error: BaseException | None = None
            if source_descriptor is not None:
                try:
                    os.close(source_descriptor)
                except BaseException as exc:
                    source_close_error = exc
                source_descriptor = None
            close_error: BaseException | None = None
            if destination_open:
                try:
                    os.close(destination)
                except BaseException as exc:
                    close_error = exc
            if close_error is not None:
                try:
                    self._record_restored_partial(
                        parent_relative,
                        relative.name,
                        identity,
                    )
                except BaseException as inventory_error:
                    raise ResourceSnapshotError(
                        f"{original}; additionally could not close generic snapshot "
                        f"target: {close_error}; ownership reconciliation failed: "
                        f"{inventory_error}"
                    ) from original
                raise ResourceSnapshotError(
                    f"{original}; additionally could not close generic snapshot "
                    f"target: {close_error}; ownership was recorded for retry"
                    + (
                        f"; source close also failed: {source_close_error}"
                        if source_close_error is not None
                        else ""
                    )
                ) from original
            try:
                self._remove_partial(parent_relative, relative.name, identity)
            except ResourceSnapshotError as cleanup_error:
                if source_close_error is not None:
                    raise ResourceSnapshotError(
                        f"{cleanup_error}; additionally source close failed: {source_close_error}"
                    ) from original
                raise cleanup_error from original
            if source_close_error is not None:
                raise ResourceSnapshotError(
                    f"{original}; additionally could not close generic resource "
                    f"source: {source_close_error}"
                ) from original
            raise
        finally:
            if source_descriptor is not None:
                os.close(source_descriptor)

    def _hash_entry(
        self,
        parent_relative: PurePosixPath,
        name: str,
        expected: _FileRecord,
        *,
        claimed: bool = False,
    ) -> str:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        if os.name == "posix":
            descriptor = os.open(
                name,
                flags,
                dir_fd=self._directory_descriptors[parent_relative],
            )
        else:
            descriptor = os.open(self._directory_path(parent_relative) / name, flags)
        try:
            before = descriptor_file_stat(descriptor)
            state_matches = _stat_matches_claimed_record if claimed else _stat_matches_record
            if not state_matches(before, expected):
                raise ResourceSnapshotError("Snapshot file changed before content verification")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = descriptor_file_stat(descriptor)
            if not state_matches(after, expected):
                raise ResourceSnapshotError("Snapshot file changed during content verification")
            return digest.hexdigest()
        finally:
            os.close(descriptor)

    def _validate_file(
        self,
        relative: PurePosixPath,
        *,
        cleanup: bool = False,
    ) -> _FileRecord:
        record = self._files.get(relative)
        if record is None:
            raise ResourceSnapshotError(f"Snapshot does not own resource path: {relative}")
        parent_relative = relative.parent
        if parent_relative == PurePosixPath("."):
            parent_relative = PurePosixPath(".")
        self._validate_directory(parent_relative)
        target = self._directory_path(parent_relative) / relative.name
        try:
            info = self._entry_stat(parent_relative, relative.name)
        except OSError as exc:
            raise ResourceSnapshotError(
                f"Snapshot file is missing or unreadable: {target}: {exc}"
            ) from exc
        state_matches = _stat_matches_claimed_record if cleanup else _stat_matches_record
        if stat.S_ISLNK(info.st_mode) or not state_matches(info, record):
            raise ResourceSnapshotError(f"Snapshot file state changed: {target}")
        _validate_file_privacy(target, info)
        if (
            self._hash_entry(
                parent_relative,
                relative.name,
                record,
                claimed=cleanup,
            )
            != record.sha256
        ):
            raise ResourceSnapshotError(f"Snapshot file content changed: {target}")
        current = self._entry_stat(parent_relative, relative.name)
        if not state_matches(current, record):
            raise ResourceSnapshotError(
                f"Snapshot file changed after content verification: {target}"
            )
        self._validate_directory(parent_relative)
        return record

    def resolve_file(self, relative: PurePosixPath) -> Path:
        self._validate_relative(relative)
        self._check_open()
        self._validate_file(relative)
        return self._active_root.joinpath(*relative.parts)

    def open_reader(self, relative: PurePosixPath) -> ResourceSnapshotReader:
        self._validate_relative(relative)
        self._check_open()
        if self._reader_issued:
            raise ResourceSnapshotError("Snapshot owner already issued its only reader")
        self._reader_issued = True

        record = self._validate_file(relative)
        parent_relative = relative.parent
        if parent_relative == PurePosixPath("."):
            parent_relative = PurePosixPath(".")
        target = self._directory_path(parent_relative) / relative.name
        self._validate_directory(parent_relative)
        try:
            before = self._entry_stat(parent_relative, relative.name)
        except OSError as exc:
            raise ResourceSnapshotError(
                f"Snapshot file is missing or unreadable before reader open: {target}: {exc}"
            ) from exc
        if stat.S_ISLNK(before.st_mode) or not _stat_matches_record(before, record):
            raise ResourceSnapshotError(f"Snapshot file changed before reader open: {target}")
        _validate_file_privacy(target, before)

        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor: int | None = None
        try:
            descriptor = _open_existing_file(
                parent_descriptor=self._directory_descriptor(parent_relative),
                parent_path=self._directory_path(parent_relative),
                name=relative.name,
                flags=flags,
            )
            os.set_inheritable(descriptor, False)
            if os.get_inheritable(descriptor):
                raise ResourceSnapshotError("Snapshot reader descriptor is inheritable")
            _require_reader_descriptor_state(
                descriptor,
                record,
                phase="while opening",
            )

            current = self._entry_stat(parent_relative, relative.name)
            if stat.S_ISLNK(current.st_mode) or not _stat_matches_record(current, record):
                raise ResourceSnapshotError(
                    f"Snapshot file changed while opening its reader: {target}"
                )
            _validate_file_privacy(target, current)
            self._validate_directory(parent_relative)
            _require_reader_descriptor_state(
                descriptor,
                record,
                phase="after opening",
            )
        except BaseException as original:
            close_error: BaseException | None = None
            if descriptor is not None:
                try:
                    _close_reader_descriptor(descriptor)
                except BaseException as exc:
                    close_error = exc
            if close_error is not None:
                raise ResourceSnapshotError(
                    f"{original}; additionally could not close the consumed snapshot "
                    f"reader descriptor: {close_error}"
                ) from original
            if isinstance(original, ResourceSnapshotError):
                raise
            raise ResourceSnapshotError(
                f"Could not open snapshot reader for {target}: {original}"
            ) from original

        assert descriptor is not None
        reader = ResourceSnapshotReader(descriptor, record)
        self._reader = reader
        return reader

    def _directory_names(self, relative: PurePosixPath) -> set[str]:
        if os.name == "posix":
            return set(os.listdir(self._directory_descriptors[relative]))
        return {entry.name for entry in os.scandir(self._directory_path(relative))}

    def _scan_inventory(self, *, cleanup: bool = False) -> None:
        for relative in sorted(
            self._directories,
            key=lambda value: (len(value.parts), value.as_posix()),
        ):
            self._validate_directory(relative)
            expected_names = {
                child.name
                for child in self._directories
                if child != PurePosixPath(".") and child.parent == relative
            }
            expected_names.update(child.name for child in self._files if child.parent == relative)
            actual_names = self._directory_names(relative)
            if actual_names != expected_names:
                raise ResourceSnapshotError(
                    f"Snapshot directory inventory changed: {self._directory_path(relative)}"
                )
        for relative in self._files:
            self._validate_file(relative, cleanup=cleanup)

    def _claim_root(self) -> None:
        if self._root_claimed:
            if os.name == "nt" and set(self._directory_handles) != set(self._directories):
                self._reopen_and_require_windows_directory_handles()
            return
        root_identity = self._directories[PurePosixPath(".")]
        claim_name = f"{_DELETE_PREFIX}{secrets.token_hex(16)}"
        original_name = self._active_root_name
        if os.name == "nt":
            self._release_windows_directory_handles()
        _claim_entry(
            parent_descriptor=self._parent_descriptor,
            parent_path=self._parent,
            source_name=self._active_root_name,
            claim_name=claim_name,
            expected=root_identity,
            directory=True,
        )
        self._active_root_name = claim_name
        if os.name == "nt":
            try:
                self._reopen_and_require_windows_directory_handles()
            except Exception as reopen_error:
                try:
                    _rename_noreplace(
                        self._parent_descriptor,
                        self._parent,
                        claim_name,
                        original_name,
                    )
                except Exception as rollback_error:
                    self._active_root_name = claim_name
                    self._root_claimed = False
                    try:
                        self._reopen_and_require_windows_directory_handles()
                    except Exception:
                        pass
                    else:
                        self._root_claimed = True
                    raise ResourceSnapshotError(
                        "Windows snapshot root handle acquisition and claim rollback "
                        f"failed: {rollback_error}"
                    ) from reopen_error
                self._active_root_name = original_name
                self._root_claimed = False
                try:
                    self._reopen_and_require_windows_directory_handles()
                except Exception as restore_error:
                    raise ResourceSnapshotError(
                        "Windows snapshot root claim was rolled back without stable "
                        f"handles: {restore_error}"
                    ) from reopen_error
                raise
        self._root_claimed = True
        self._scan_inventory(cleanup=True)

    def _reopen_and_require_windows_directory_handles(self) -> None:
        self._reopen_windows_directory_handles()
        if set(self._directory_handles) != set(self._directories):
            raise ResourceSnapshotError(
                "Windows snapshot directory handles were not completely restored"
            )

    def _release_windows_directory_handles(self) -> None:
        errors: list[ResourceSnapshotError] = []
        for relative in sorted(
            tuple(self._directory_handles),
            key=lambda value: (len(value.parts), value.as_posix()),
            reverse=True,
        ):
            handle = self._directory_handles[relative]
            try:
                _windows_close_handle(handle)
            except ResourceSnapshotError as exc:
                errors.append(exc)
            else:
                self._directory_handles.pop(relative)
        if errors:
            raise ResourceSnapshotError("; ".join(str(error) for error in errors))

    def _reopen_windows_directory_handles(self) -> None:
        expected_relatives = set(self._directories)
        stale_close_errors: list[str] = []
        for relative in sorted(
            set(self._directory_handles) - expected_relatives,
            key=lambda value: (len(value.parts), value.as_posix()),
            reverse=True,
        ):
            handle = self._directory_handles[relative]
            try:
                _windows_close_handle(handle)
            except BaseException as close_error:
                stale_close_errors.append(str(close_error))
            else:
                self._directory_handles.pop(relative)
        if stale_close_errors:
            raise ResourceSnapshotError(
                "Could not close stale Windows snapshot directory handles: "
                + "; ".join(stale_close_errors)
            )

        opened = dict(self._directory_handles)
        newly_opened: dict[PurePosixPath, int] = {}
        pending_handle: int | None = None
        pending_relative: PurePosixPath | None = None
        try:
            for relative in sorted(
                self._directories,
                key=lambda value: (len(value.parts), value.as_posix()),
            ):
                path = self._directory_path(relative)
                if relative in opened:
                    if _identity(path.lstat()) != self._directories[relative]:
                        handle = opened[relative]
                        try:
                            _windows_close_handle(handle)
                        except BaseException as close_error:
                            raise ResourceSnapshotError(
                                "Windows snapshot directory identity changed and its "
                                f"surviving handle could not be closed: {path}: {close_error}"
                            ) from close_error
                        self._directory_handles.pop(relative)
                        opened.pop(relative)
                        raise ResourceSnapshotError(
                            f"Windows snapshot directory changed while reopening: {path}"
                        )
                    continue
                pending_relative = relative
                pending_handle = _windows_lock_directory(path)
                if _identity(path.lstat()) != self._directories[relative]:
                    raise ResourceSnapshotError(
                        f"Windows snapshot directory changed while claiming: {path}"
                    )
                opened[relative] = pending_handle
                newly_opened[relative] = pending_handle
                pending_handle = None
                pending_relative = None
        except BaseException as validation_error:
            close_errors: list[str] = []
            if pending_handle is not None:
                try:
                    _windows_close_handle(pending_handle)
                except BaseException as close_error:
                    close_errors.append(str(close_error))
                    assert pending_relative is not None
                    self._directory_handles[pending_relative] = pending_handle
            for relative, handle in newly_opened.items():
                try:
                    _windows_close_handle(handle)
                except BaseException as close_error:
                    close_errors.append(str(close_error))
                    self._directory_handles[relative] = handle
            if close_errors:
                raise ResourceSnapshotError(
                    f"{validation_error}; additionally could not close failed Windows "
                    f"directory handles: {'; '.join(close_errors)}"
                ) from validation_error
            raise
        self._directory_handles = opened

    def _remove_owned_file(self, relative: PurePosixPath, record: _FileRecord) -> None:
        self._validate_file(relative, cleanup=True)
        parent_relative = relative.parent
        if parent_relative == PurePosixPath("."):
            parent_relative = PurePosixPath(".")
        parent_descriptor = self._directory_descriptor(parent_relative)
        parent_path = self._directory_path(parent_relative)
        claim_name = f"{_DELETE_PREFIX}{secrets.token_hex(16)}"
        _claim_entry(
            parent_descriptor=parent_descriptor,
            parent_path=parent_path,
            source_name=relative.name,
            claim_name=claim_name,
            expected=record.identity,
            directory=False,
        )
        try:
            claimed_info = _file_entry_stat(parent_descriptor, parent_path, claim_name)
            if not _stat_matches_claimed_record(claimed_info, record):
                raise ResourceSnapshotError(
                    f"Snapshot file changed after cleanup claim: {parent_path / relative.name}"
                )
            if self._hash_entry(parent_relative, claim_name, record, claimed=True) != record.sha256:
                raise ResourceSnapshotError(
                    "Snapshot file content changed after cleanup claim: "
                    f"{parent_path / relative.name}"
                )
            if os.name == "posix":
                assert parent_descriptor is not None
                os.unlink(claim_name, dir_fd=parent_descriptor)
            else:
                claimed = parent_path / claim_name
                os.chmod(claimed, stat.S_IWRITE)
                claimed.unlink()
        except Exception as original:
            try:
                if os.name == "nt":
                    claimed = parent_path / claim_name
                    os.chmod(claimed, stat.S_IREAD)
                    restored = _file_entry_stat(parent_descriptor, parent_path, claim_name)
                    if not _stat_matches_claimed_record(restored, record):
                        raise ResourceSnapshotError(
                            f"Windows snapshot file seal was not restored: {claimed}"
                        )
                    _validate_file_privacy(claimed, restored)
                    if (
                        self._hash_entry(
                            parent_relative,
                            claim_name,
                            record,
                            claimed=True,
                        )
                        != record.sha256
                    ):
                        raise ResourceSnapshotError(
                            f"Windows snapshot file content changed during rollback: {claimed}"
                        )
                _rename_noreplace(
                    parent_descriptor,
                    parent_path,
                    claim_name,
                    relative.name,
                )
            except Exception as rollback_error:
                raise ResourceSnapshotError(
                    f"Snapshot file cleanup rollback failed: {rollback_error}"
                ) from original
            raise ResourceSnapshotError(
                f"Could not safely remove snapshot file {parent_path / relative.name}: {original}"
            ) from original
        self._files.pop(relative)

    def _remove_owned_directory(self, relative: PurePosixPath, identity: _Identity) -> None:
        self._validate_directory(relative)
        parent_relative = relative.parent
        if parent_relative == PurePosixPath("."):
            parent_relative = PurePosixPath(".")
        parent_descriptor = self._directory_descriptor(parent_relative)
        parent_path = self._directory_path(parent_relative)
        if os.name == "posix":
            descriptor = self._directory_descriptors[relative]
            try:
                os.close(descriptor)
            except OSError as close_error:
                raise ResourceSnapshotError(
                    f"Could not close snapshot directory handle "
                    f"{parent_path / relative.name}: {close_error}"
                ) from close_error
            self._directory_descriptors.pop(relative)
        else:
            handle = self._directory_handles[relative]
            try:
                _windows_close_handle(handle)
            except ResourceSnapshotError as close_error:
                raise ResourceSnapshotError(
                    f"Could not close snapshot directory handle "
                    f"{parent_path / relative.name}: {close_error}"
                ) from close_error
            self._directory_handles.pop(relative)
        claim_name = f"{_DELETE_PREFIX}{secrets.token_hex(16)}"
        try:
            _claim_entry(
                parent_descriptor=parent_descriptor,
                parent_path=parent_path,
                source_name=relative.name,
                claim_name=claim_name,
                expected=identity,
                directory=True,
            )
        except Exception as claim_error:
            try:
                self._acquire_directory_handle(relative)
            except Exception as restore_error:
                raise ResourceSnapshotError(
                    f"Snapshot directory claim and handle restore failed: {restore_error}"
                ) from claim_error
            raise
        try:
            if os.name == "posix":
                assert parent_descriptor is not None
                os.rmdir(claim_name, dir_fd=parent_descriptor)
            else:
                (parent_path / claim_name).rmdir()
        except OSError as exc:
            try:
                _rename_noreplace(
                    parent_descriptor,
                    parent_path,
                    claim_name,
                    relative.name,
                )
                self._acquire_directory_handle(relative)
            except Exception as rollback_error:
                raise ResourceSnapshotError(
                    f"Snapshot directory cleanup rollback failed: {rollback_error}"
                ) from exc
            raise ResourceSnapshotError(
                f"Could not remove snapshot directory {parent_path / relative.name}: {exc}"
            ) from exc
        self._directories.pop(relative)

    def close(self) -> None:
        if self._closed:
            return
        reader = getattr(self, "_reader", None)
        if reader is not None and not reader.closed:
            raise ResourceSnapshotError(
                "Snapshot owner cannot close while its reader is still open"
            )
        root_relative = PurePosixPath(".")
        if not self._root_removed:
            self._check_open()
            self._scan_inventory(cleanup=True)
            self._claim_root()
            for relative, record in sorted(
                tuple(self._files.items()),
                key=lambda item: (len(item[0].parts), item[0].as_posix()),
                reverse=True,
            ):
                self._remove_owned_file(relative, record)
            for relative, identity in sorted(
                (
                    (relative, identity)
                    for relative, identity in self._directories.items()
                    if relative != root_relative
                ),
                key=lambda item: (len(item[0].parts), item[0].as_posix()),
                reverse=True,
            ):
                self._remove_owned_directory(relative, identity)

            self._validate_directory(root_relative)
            if os.name == "posix":
                root_descriptor = self._directory_descriptors[root_relative]
                try:
                    os.close(root_descriptor)
                except OSError as close_error:
                    raise ResourceSnapshotError(
                        f"Could not close snapshot root handle {self._active_root}: {close_error}"
                    ) from close_error
                self._directory_descriptors.pop(root_relative)
            else:
                root_handle = self._directory_handles[root_relative]
                try:
                    _windows_close_handle(root_handle)
                except ResourceSnapshotError as close_error:
                    raise ResourceSnapshotError(
                        f"Could not close snapshot root handle {self._active_root}: {close_error}"
                    ) from close_error
                self._directory_handles.pop(root_relative)
            try:
                if os.name == "posix":
                    assert self._parent_descriptor is not None
                    os.rmdir(self._active_root_name, dir_fd=self._parent_descriptor)
                else:
                    self._active_root.rmdir()
            except OSError as exc:
                try:
                    self._acquire_directory_handle(root_relative)
                except Exception as restore_error:
                    raise ResourceSnapshotError(
                        f"Could not remove snapshot root {self._active_root}: {exc}; "
                        f"stable handle restoration failed: {restore_error}"
                    ) from exc
                raise ResourceSnapshotError(
                    f"Could not remove snapshot root {self._active_root}: {exc}"
                ) from exc
            self._root_removed = True

        if self._parent_descriptor is not None:
            try:
                os.close(self._parent_descriptor)
            except OSError as close_error:
                raise ResourceSnapshotError(
                    f"Could not close snapshot parent handle: {close_error}"
                ) from close_error
            self._parent_descriptor = None
        if self._parent_handle is not None:
            try:
                _windows_close_handle(self._parent_handle)
            except ResourceSnapshotError as close_error:
                raise ResourceSnapshotError(
                    f"Could not close Windows snapshot parent handle: {close_error}"
                ) from close_error
            self._parent_handle = None
        self._directories.pop(root_relative, None)
        self._closed = True
