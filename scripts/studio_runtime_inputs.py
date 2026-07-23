#!/usr/bin/env python3
"""Acquire and verify the exact Studio runtime inputs pinned by the repository."""

from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import ssl
import stat
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any, BinaryIO, NoReturn, Protocol
from urllib.parse import urlsplit

try:
    from scripts.studio_runtime_sources import (
        ALLOWED_HTTPS_HOSTS,
        DEFAULT_SOURCE,
        REQUIRED_BLOCKERS,
        RuntimeSourcesError,
        load_strict_json,
        validate_document,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from studio_runtime_sources import (  # type: ignore[no-redef]
        ALLOWED_HTTPS_HOSTS,
        DEFAULT_SOURCE,
        REQUIRED_BLOCKERS,
        RuntimeSourcesError,
        load_strict_json,
        validate_document,
    )


FORMAT = "rpg-world-forge.studio_runtime_input_inventory"
FORMAT_VERSION = 1
TARGET_IDS = ("linux-x64", "win32-x64")
USER_AGENT = "rpg-world-forge-studio-runtime-inputs/1"
NETWORK_TIMEOUT_SECONDS = 30.0
DOWNLOAD_DEADLINE_SECONDS = 900.0
MAX_REDIRECTS = 1
CHUNK_BYTES = 1024 * 1024
MAX_CACHE_PATH_CHARS = 4096
MAX_REDIRECT_URL_CHARS = 8192
MAX_SIGNED_QUERY_CHARS = 4096

GITHUB_RELEASE_ASSET_HOST = "release-assets.githubusercontent.com"
GITHUB_RELEASE_REPOSITORY_IDS = {
    ("astral-sh", "python-build-standalone"): "162334160",
    ("openai", "codex"): "965415649",
}
_GITHUB_RELEASE_PATH = re.compile(
    r"^/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/releases/download/[^/]+/[^/]+$"
)
_GITHUB_ASSET_PATH = re.compile(
    r"^/github-production-release-asset/([1-9][0-9]{0,19})/"
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)

_PORTABLE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_WINDOWS_RESERVED = {
    "aux",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_HAS_SECURE_DIR_FD = os.name == "posix" and all(
    function in os.supports_dir_fd for function in (os.open, os.mkdir, os.stat, os.unlink, os.link)
)


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTimeLow", ctypes.c_uint32),
        ("ftCreationTimeHigh", ctypes.c_uint32),
        ("ftLastAccessTimeLow", ctypes.c_uint32),
        ("ftLastAccessTimeHigh", ctypes.c_uint32),
        ("ftLastWriteTimeLow", ctypes.c_uint32),
        ("ftLastWriteTimeHigh", ctypes.c_uint32),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


class _FileDispositionInformation(ctypes.Structure):
    _fields_ = [("delete_file", ctypes.c_ubyte)]


class _FileRenameInformation(ctypes.Structure):
    _fields_ = [
        ("replace_if_exists", ctypes.c_int),
        ("root_directory", ctypes.c_void_p),
        ("filename_length", ctypes.c_uint32),
        ("filename", ctypes.c_wchar * 1),
    ]


@dataclass(frozen=True)
class _WindowsHandleInfo:
    identity: tuple[int, int]
    attributes: int
    link_count: int
    size: int

    @property
    def directory(self) -> bool:
        return bool(self.attributes & 0x10)

    @property
    def reparse(self) -> bool:
        return bool(self.attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


class _WindowsApi:
    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _DELETE = 0x00010000
    _FILE_READ_ATTRIBUTES = 0x00000080
    _FILE_LIST_DIRECTORY = 0x00000001
    _SYNCHRONIZE = 0x00100000
    _SHARE_READ = 0x00000001
    _SHARE_WRITE = 0x00000002
    _CREATE_NEW = 1
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
    _INVALID_HANDLE = ctypes.c_void_p(-1).value
    _DUPLICATE_SAME_ACCESS = 0x00000002
    _FILE_RENAME_INFO = 3
    _FILE_DISPOSITION_INFO = 4

    def __init__(self) -> None:
        try:
            self.kernel32 = ctypes.WinDLL(  # type: ignore[attr-defined]
                "kernel32",
                use_last_error=True,
            )
        except (AttributeError, OSError):
            raise RuntimeInputsError("secure_primitive_unavailable", "cache") from None
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
        self.get_information = self.kernel32.GetFileInformationByHandle
        self.get_information.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_ByHandleFileInformation),
        ]
        self.get_information.restype = ctypes.c_int
        self.flush_file = self.kernel32.FlushFileBuffers
        self.flush_file.argtypes = [ctypes.c_void_p]
        self.flush_file.restype = ctypes.c_int
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
        self.set_information = self.kernel32.SetFileInformationByHandle
        self.set_information.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self.set_information.restype = ctypes.c_int

    def _open(
        self,
        path: Path,
        *,
        access: int,
        disposition: int,
        flags: int,
    ) -> int:
        handle = self.create_file(
            str(path),
            access,
            self._SHARE_READ | self._SHARE_WRITE,
            None,
            disposition,
            flags,
            None,
        )
        if handle == self._INVALID_HANDLE:
            error = ctypes.get_last_error()
            if error in {2, 3}:
                raise FileNotFoundError
            if error in {80, 183}:
                raise FileExistsError
            raise RuntimeInputsError("cache_root_unsafe", "cache")
        return int(handle)

    def open_directory(self, path: Path) -> int:
        handle = self._open(
            path,
            access=self._FILE_LIST_DIRECTORY | self._FILE_READ_ATTRIBUTES | self._SYNCHRONIZE,
            disposition=self._OPEN_EXISTING,
            flags=self._FILE_FLAG_BACKUP_SEMANTICS | self._FILE_FLAG_OPEN_REPARSE_POINT,
        )
        info = self.info(handle)
        if not info.directory or info.reparse:
            self.close(handle)
            raise RuntimeInputsError("cache_root_unsafe", "cache")
        return handle

    def open_entry(self, path: Path) -> int:
        return self._open(
            path,
            access=self._GENERIC_READ | self._FILE_READ_ATTRIBUTES | self._SYNCHRONIZE,
            disposition=self._OPEN_EXISTING,
            flags=self._FILE_FLAG_BACKUP_SEMANTICS
            | self._FILE_FLAG_OPEN_REPARSE_POINT
            | self._FILE_FLAG_SEQUENTIAL_SCAN,
        )

    def create_temporary(self, path: Path) -> int:
        return self._open(
            path,
            access=self._GENERIC_READ
            | self._GENERIC_WRITE
            | self._DELETE
            | self._FILE_READ_ATTRIBUTES
            | self._SYNCHRONIZE,
            disposition=self._CREATE_NEW,
            flags=self._FILE_ATTRIBUTE_NORMAL | self._FILE_FLAG_OPEN_REPARSE_POINT,
        )

    def info(self, handle: int) -> _WindowsHandleInfo:
        raw = _ByHandleFileInformation()
        if not self.get_information(ctypes.c_void_p(handle), ctypes.byref(raw)):
            raise RuntimeInputsError("cache_entry_unsafe", "cache")
        return _WindowsHandleInfo(
            identity=(
                int(raw.dwVolumeSerialNumber),
                (int(raw.nFileIndexHigh) << 32) | int(raw.nFileIndexLow),
            ),
            attributes=int(raw.dwFileAttributes),
            link_count=int(raw.nNumberOfLinks),
            size=(int(raw.nFileSizeHigh) << 32) | int(raw.nFileSizeLow),
        )

    def duplicate_to_fd(self, handle: int, *, writable: bool) -> int:
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
            raise RuntimeInputsError("secure_primitive_unavailable", "cache")
        try:
            import msvcrt

            flags = os.O_BINARY | (os.O_RDWR if writable else os.O_RDONLY)
            return msvcrt.open_osfhandle(int(duplicate.value), flags)
        except Exception:
            self.close(int(duplicate.value))
            raise RuntimeInputsError("secure_primitive_unavailable", "cache") from None

    def rename_no_replace(
        self,
        handle: int,
        directory_handle: int,
        destination_name: str,
    ) -> None:
        encoded = destination_name.encode("utf-16-le")
        offset = _FileRenameInformation.filename.offset
        buffer = ctypes.create_string_buffer(
            max(
                ctypes.sizeof(_FileRenameInformation),
                offset + len(encoded),
            )
        )
        information = _FileRenameInformation.from_buffer(buffer)
        information.replace_if_exists = False
        information.root_directory = ctypes.c_void_p(directory_handle)
        information.filename_length = len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + offset, encoded, len(encoded))
        if self.set_information(
            ctypes.c_void_p(handle),
            self._FILE_RENAME_INFO,
            buffer,
            len(buffer),
        ):
            return
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError
        raise RuntimeInputsError("secure_primitive_unavailable", "cache")

    def delete_on_close(self, handle: int) -> None:
        information = _FileDispositionInformation(delete_file=1)
        if not self.set_information(
            ctypes.c_void_p(handle),
            self._FILE_DISPOSITION_INFO,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            raise RuntimeInputsError("secure_primitive_unavailable", "cache")

    def flush(self, handle: int) -> None:
        if not self.flush_file(ctypes.c_void_p(handle)):
            raise RuntimeInputsError("sync_failed", "cache")

    def close(self, handle: int) -> None:
        if handle:
            self.close_handle(ctypes.c_void_p(handle))


_WINDOWS_API: _WindowsApi | None = None


class RuntimeInputsError(RuntimeError):
    """A public, redacted runtime-input failure."""

    _MESSAGES = {
        "cache_conflict": "an existing cache entry does not match the pinned input",
        "cache_entry_unsafe": "a cache entry is not a standalone regular file",
        "cache_parent_changed": "the cache parent changed during the operation",
        "cache_root_unsafe": "the cache directory boundary is unsafe",
        "download_digest_mismatch": "the downloaded input digest does not match the pin",
        "download_interrupted": "the input download did not complete",
        "download_size_mismatch": "the downloaded input size does not match the pin",
        "internal_error": "runtime input processing failed",
        "invalid_argument": "runtime input arguments are invalid",
        "manifest_invalid": "the pinned runtime input manifest is invalid",
        "network_failed": "the pinned input could not be retrieved",
        "redirect_rejected": "the input response redirect was rejected",
        "response_invalid": "the input response metadata is invalid",
        "secure_primitive_unavailable": "a required secure filesystem primitive is unavailable",
        "sync_failed": "the runtime input could not be durably synchronized",
    }

    def __init__(self, code: str, context: str, *, exit_code: int = 1) -> None:
        if code not in self._MESSAGES:
            code = "internal_error"
        if not re.fullmatch(r"(?:cli|manifest|target|cache|artifact\.[a-z0-9_-]+)", context):
            context = "cache"
        self.code = code
        self.context = context
        self.exit_code = exit_code
        self.safe_message = self._MESSAGES[code]
        super().__init__(f"{code} at {context}: {self.safe_message}")

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "context": self.context,
            "message": self.safe_message,
        }


if os.name == "nt":
    try:
        _WINDOWS_API = _WindowsApi()
    except RuntimeInputsError:
        _WINDOWS_API = None


class _CacheDirectoryMissing(FileNotFoundError):
    pass


@dataclass(frozen=True)
class _Deadline:
    expires_at: float
    clock: Callable[[], float]
    context: str

    @classmethod
    def start(
        cls,
        *,
        clock: Callable[[], float],
        context: str,
    ) -> _Deadline:
        started = clock()
        if isinstance(started, bool) or not isinstance(started, (int, float)):
            raise RuntimeInputsError("internal_error", "cache")
        return cls(
            expires_at=float(started) + DOWNLOAD_DEADLINE_SECONDS,
            clock=clock,
            context=context,
        )

    def remaining(self) -> float:
        current = self.clock()
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise RuntimeInputsError("internal_error", "cache")
        remaining = self.expires_at - float(current)
        if remaining <= 0:
            raise RuntimeInputsError("download_interrupted", self.context)
        return remaining

    def checkpoint(self) -> None:
        self.remaining()


@dataclass(frozen=True)
class InputArtifact:
    component: str
    filename: str
    url: str
    size: int
    sha256: str
    sha512: str | None = None

    def relative_path(self, target_id: str) -> str:
        return PurePosixPath(target_id, self.component, self.filename).as_posix()


@dataclass(frozen=True)
class InventoryItem:
    artifact: InputArtifact
    target_id: str
    status: str

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "component": self.artifact.component,
            "filename": self.artifact.filename,
            "relative_path": self.artifact.relative_path(self.target_id),
            "sha256": self.artifact.sha256,
            "size": self.artifact.size,
            "status": self.status,
        }
        if self.artifact.sha512 is not None:
            result["sha512"] = self.artifact.sha512
        return result


@dataclass(frozen=True)
class InventoryReport:
    action: str
    target_id: str
    offline: bool
    valid: bool
    items: tuple[InventoryItem, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "artifacts": [item.as_dict() for item in self.items],
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "offline": self.offline,
            "open_blocker_codes": list(REQUIRED_BLOCKERS),
            "redistribution_status": "blocked",
            "release_ready": False,
            "target_id": self.target_id,
            "valid": self.valid,
        }


@dataclass
class _PinnedCacheFile:
    descriptor: int
    state: os.stat_result
    identity: tuple[int, int]

    def close(self) -> None:
        if self.descriptor < 0:
            return
        try:
            os.close(self.descriptor)
        except OSError:
            pass
        self.descriptor = -1


@dataclass(frozen=True)
class _Inspection:
    status: str
    identity: tuple[int, int] | None = None
    pinned: _PinnedCacheFile | None = None

    @property
    def valid(self) -> bool:
        return self.status == "verified"


class _HTTPResponse(Protocol):
    status: int
    headers: Any

    def close(self) -> object: ...

    def geturl(self) -> str: ...

    def read(self, amount: int = -1) -> bytes: ...


class _HTTPOpener(Protocol):
    def open(self, request: urllib.request.Request, *, timeout: float) -> _HTTPResponse: ...


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _identity(info: os.stat_result) -> tuple[int, int]:
    identity = (info.st_dev, info.st_ino)
    if identity == (0, 0):
        raise RuntimeInputsError("secure_primitive_unavailable", "cache")
    return identity


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        _identity(left) == _identity(right)
        and stat.S_IFMT(left.st_mode) == stat.S_IFMT(right.st_mode)
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _portable_component(value: str) -> bool:
    if (
        not isinstance(value, str)
        or unicodedata.normalize("NFC", value) != value
        or not _PORTABLE_COMPONENT.fullmatch(value)
        or value.endswith((" ", "."))
    ):
        return False
    return value.split(".", 1)[0].casefold() not in _WINDOWS_RESERVED


def _normal_form(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _validate_target(target_id: object) -> str:
    if type(target_id) is not str or target_id not in TARGET_IDS:
        raise RuntimeInputsError("invalid_argument", "target", exit_code=2)
    return target_id


def _validate_cache_dir(cache_dir: object) -> Path:
    if isinstance(cache_dir, bool):
        raise RuntimeInputsError("invalid_argument", "cache", exit_code=2)
    try:
        raw = os.fspath(cache_dir)  # type: ignore[arg-type]
    except TypeError:
        raise RuntimeInputsError("invalid_argument", "cache", exit_code=2) from None
    if (
        type(raw) is not str
        or not raw
        or "\x00" in raw
        or len(raw) > MAX_CACHE_PATH_CHARS
        or unicodedata.normalize("NFC", raw) != raw
        or not os.path.isabs(raw)
    ):
        raise RuntimeInputsError("invalid_argument", "cache", exit_code=2)
    normalized = os.path.normpath(raw)
    if normalized != raw.rstrip(os.sep) and not (
        normalized == os.path.abspath(os.sep) and raw == normalized
    ):
        raise RuntimeInputsError("invalid_argument", "cache", exit_code=2)
    return Path(normalized)


def _manifest_document() -> dict[str, Any]:
    try:
        document = load_strict_json(DEFAULT_SOURCE)
        validate_document(document)
    except RuntimeSourcesError:
        raise RuntimeInputsError("manifest_invalid", "manifest") from None
    return document


def _artifact_from_record(component: str, record: dict[str, Any]) -> InputArtifact:
    return InputArtifact(
        component=component,
        filename=str(record["filename"]),
        url=str(record["url"]),
        size=int(record["size"]),
        sha256=str(record["sha256"]),
    )


def _resolve_target_inputs(
    document: dict[str, Any],
    target_id: str,
) -> tuple[InputArtifact, ...]:
    """Resolve the fixed cache inventory from one already-validated pinned document."""

    target_id = _validate_target(target_id)
    try:
        codex_target = next(
            target for target in document["codex"]["targets"] if target["target_id"] == target_id
        )
        python_target = next(
            target for target in document["python"]["targets"] if target["target_id"] == target_id
        )
        sri = str(codex_target["sri"])
        sri_bytes = base64.b64decode(sri.removeprefix("sha512-"), validate=True)
        codex_package = _artifact_from_record("codex-package", codex_target["archive"])
        artifacts = (
            InputArtifact(
                component=codex_package.component,
                filename=codex_package.filename,
                url=codex_package.url,
                size=codex_package.size,
                sha256=codex_package.sha256,
                sha512=sri_bytes.hex(),
            ),
            _artifact_from_record("codex-release", codex_target["release_archive"]),
            _artifact_from_record(
                "codex-checksums",
                document["codex"]["release_sha256sums"],
            ),
            _artifact_from_record("python-runtime", python_target["runtime_archive"]),
            _artifact_from_record("python-metadata", python_target["metadata_archive"]),
            _artifact_from_record("python-source", document["python"]["cpython_source"]),
            _artifact_from_record(
                "python-checksums",
                document["python"]["release_sha256sums"],
            ),
        )
    except (KeyError, StopIteration, TypeError, ValueError):
        raise RuntimeInputsError("manifest_invalid", "manifest") from None
    _validate_resolved_layout(target_id, artifacts)
    return artifacts


def _validate_resolved_layout(
    target_id: str,
    artifacts: tuple[InputArtifact, ...],
) -> None:
    if len(artifacts) != 7 or not _portable_component(target_id):
        raise RuntimeInputsError("manifest_invalid", "manifest")
    normalized_paths: list[tuple[str, ...]] = []
    hosts: set[str] = set()
    for artifact in artifacts:
        if not _portable_component(artifact.component) or not _portable_component(
            artifact.filename
        ):
            raise RuntimeInputsError("manifest_invalid", "manifest")
        path = (target_id, artifact.component, artifact.filename)
        normalized_paths.append(tuple(_normal_form(component) for component in path))
        try:
            parsed = urlsplit(artifact.url)
            port = parsed.port
        except ValueError:
            raise RuntimeInputsError("manifest_invalid", "manifest") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_HTTPS_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeInputsError("manifest_invalid", "manifest")
        hosts.add(parsed.hostname)
        if (
            artifact.size <= 0
            or not re.fullmatch(r"[0-9a-f]{64}", artifact.sha256)
            or (artifact.sha512 is not None and not re.fullmatch(r"[0-9a-f]{128}", artifact.sha512))
        ):
            raise RuntimeInputsError("manifest_invalid", "manifest")
    if hosts != set(ALLOWED_HTTPS_HOSTS):
        raise RuntimeInputsError("manifest_invalid", "manifest")
    for index, path in enumerate(normalized_paths):
        for other in normalized_paths[index + 1 :]:
            if path == other or path[: len(other)] == other or other[: len(path)] == path:
                raise RuntimeInputsError("manifest_invalid", "manifest")


class _Directory:
    def __init__(
        self,
        path: Path,
        descriptor: int | None,
        identity: tuple[int, int],
        *,
        windows_handles: tuple[int, ...] = (),
        windows_identity: tuple[int, int] | None = None,
    ) -> None:
        self.path = path
        self.descriptor = descriptor
        self.identity = identity
        self.windows_handles = windows_handles
        self.windows_identity = windows_identity
        self._posix_owned: dict[tuple[int, int], tuple[str, int]] = {}
        self._windows_owned: dict[tuple[int, int], tuple[str, int]] = {}

    def __enter__(self) -> _Directory:
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        for _name, descriptor in tuple(self._posix_owned.values()):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._posix_owned.clear()
        if _WINDOWS_API is not None:
            for _name, handle in tuple(self._windows_owned.values()):
                _WINDOWS_API.close(handle)
            self._windows_owned.clear()
            for handle in reversed(self.windows_handles):
                _WINDOWS_API.close(handle)
            self.windows_handles = ()
        if self.descriptor is not None:
            try:
                os.close(self.descriptor)
            except OSError:
                pass
            self.descriptor = None

    def assert_current(self) -> None:
        if self.windows_handles:
            if _WINDOWS_API is None or self.windows_identity is None:
                raise RuntimeInputsError("secure_primitive_unavailable", "cache")
            try:
                current_handle = _WINDOWS_API.open_directory(self.path)
                current_identity = _WINDOWS_API.info(current_handle).identity
            except (FileNotFoundError, RuntimeInputsError):
                raise RuntimeInputsError("cache_parent_changed", "cache") from None
            finally:
                if "current_handle" in locals():
                    _WINDOWS_API.close(current_handle)
            if current_identity != self.windows_identity:
                raise RuntimeInputsError("cache_parent_changed", "cache")
            return
        try:
            current = self.path.lstat()
        except OSError:
            raise RuntimeInputsError("cache_parent_changed", "cache") from None
        if (
            not stat.S_ISDIR(current.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or _is_reparse(current)
            or _identity(current) != self.identity
        ):
            raise RuntimeInputsError("cache_parent_changed", "cache")

    def names(self) -> list[str]:
        self.assert_current()
        try:
            if self.descriptor is not None:
                names = os.listdir(self.descriptor)
            else:
                names = os.listdir(self.path)
        except OSError:
            raise RuntimeInputsError("cache_root_unsafe", "cache") from None
        self.assert_current()
        return names

    def reject_alias(self, name: str) -> None:
        normalized = _normal_form(name)
        if any(
            candidate != name and _normal_form(candidate) == normalized
            for candidate in self.names()
        ):
            raise RuntimeInputsError("cache_root_unsafe", "cache")

    def entry_stat(self, name: str) -> os.stat_result | None:
        self.assert_current()
        if self.windows_handles:
            if _WINDOWS_API is None:
                raise RuntimeInputsError("secure_primitive_unavailable", "cache")
            try:
                handle = _WINDOWS_API.open_entry(self.path / name)
            except FileNotFoundError:
                return None
            except RuntimeInputsError:
                raise RuntimeInputsError("cache_entry_unsafe", "cache") from None
            try:
                handle_info = _WINDOWS_API.info(handle)
                if handle_info.reparse:
                    raise RuntimeInputsError("cache_entry_unsafe", "cache")
                result = (self.path / name).lstat()
            except OSError:
                raise RuntimeInputsError("cache_entry_unsafe", "cache") from None
            finally:
                _WINDOWS_API.close(handle)
            self.assert_current()
            return result
        try:
            if self.descriptor is not None:
                result = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
            else:
                result = (self.path / name).lstat()
        except FileNotFoundError:
            return None
        except OSError:
            raise RuntimeInputsError("cache_entry_unsafe", "cache") from None
        self.assert_current()
        return result

    def open_entry(self, name: str) -> int:
        self.assert_current()
        if self.windows_handles:
            if _WINDOWS_API is None:
                raise RuntimeInputsError("secure_primitive_unavailable", "cache")
            try:
                handle = _WINDOWS_API.open_entry(self.path / name)
                handle_info = _WINDOWS_API.info(handle)
                if handle_info.directory or handle_info.reparse:
                    raise RuntimeInputsError("cache_entry_unsafe", "cache")
                descriptor = _WINDOWS_API.duplicate_to_fd(handle, writable=False)
            except Exception:
                if "handle" in locals():
                    _WINDOWS_API.close(handle)
                raise
            _WINDOWS_API.close(handle)
            self.assert_current()
            return descriptor
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            if self.descriptor is not None:
                descriptor = os.open(name, flags, dir_fd=self.descriptor)
            else:
                descriptor = os.open(self.path / name, flags)
        except OSError:
            raise RuntimeInputsError("cache_entry_unsafe", "cache") from None
        try:
            self.assert_current()
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        return descriptor

    def create_temporary(self) -> tuple[int, str, tuple[int, int]]:
        self.assert_current()
        if self.windows_handles:
            if _WINDOWS_API is None:
                raise RuntimeInputsError("secure_primitive_unavailable", "cache")
            for _ in range(100):
                name = f".rwf-input-{secrets.token_hex(16)}.part"
                try:
                    guard = _WINDOWS_API.create_temporary(self.path / name)
                except FileExistsError:
                    continue
                descriptor = -1
                try:
                    guard_info = _WINDOWS_API.info(guard)
                    if (
                        guard_info.directory
                        or guard_info.reparse
                        or guard_info.link_count != 1
                        or guard_info.size != 0
                    ):
                        raise RuntimeInputsError("cache_entry_unsafe", "cache")
                    descriptor = _WINDOWS_API.duplicate_to_fd(guard, writable=True)
                    opened = os.fstat(descriptor)
                    identity = _identity(opened)
                    self.assert_current()
                    self._windows_owned[identity] = (name, guard)
                    return descriptor, name, identity
                except Exception:
                    if descriptor >= 0:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                    _WINDOWS_API.close(guard)
                    raise
            raise RuntimeInputsError("cache_root_unsafe", "cache")
        if (
            self.descriptor is None
            or not sys.platform.startswith("linux")
            or not getattr(os, "O_TMPFILE", 0)
        ):
            raise RuntimeInputsError("secure_primitive_unavailable", "cache")
        flags = os.O_RDWR | os.O_TMPFILE | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(
                ".",
                flags,
                0o600,
                dir_fd=self.descriptor,
            )
        except OSError as exc:
            if exc.errno in {
                errno.EINVAL,
                errno.EISDIR,
                errno.ENOSYS,
                errno.ENOTSUP,
                errno.EOPNOTSUPP,
                errno.EPERM,
            }:
                raise RuntimeInputsError(
                    "secure_primitive_unavailable",
                    "cache",
                ) from None
            raise RuntimeInputsError("cache_root_unsafe", "cache") from None
        guard: int | None = None
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 0 or opened.st_size != 0:
                raise RuntimeInputsError("cache_entry_unsafe", "cache")
            identity = _identity(opened)
            guard = os.dup(descriptor)
            os.set_inheritable(guard, False)
            self.assert_current()
            token = f".rwf-owned-{secrets.token_hex(16)}"
            self._posix_owned[identity] = (token, guard)
            return descriptor, token, identity
        except Exception:
            if guard is not None:
                try:
                    os.close(guard)
                except OSError:
                    pass
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def cleanup_owned(self, name: str, identity: tuple[int, int]) -> str:
        if self.windows_handles:
            if _WINDOWS_API is None:
                return "preserved"
            owned = self._windows_owned.get(identity)
            if owned is None or owned[0] != name:
                return "preserved"
            self._windows_owned.pop(identity)
            guard = owned[1]
            try:
                guard_info = _WINDOWS_API.info(guard)
                current = self.entry_stat(name)
                if (
                    guard_info.directory
                    or guard_info.reparse
                    or guard_info.link_count != 1
                    or current is None
                    or _identity(current) != identity
                ):
                    return "preserved"
                _WINDOWS_API.delete_on_close(guard)
                return "deleted"
            except RuntimeInputsError:
                return "preserved"
            finally:
                _WINDOWS_API.close(guard)
        if self.descriptor is not None:
            owned = self._posix_owned.get(identity)
            if owned is None or owned[0] != name:
                return "preserved"
            self._posix_owned.pop(identity)
            guard = owned[1]
            try:
                try:
                    guard_info = os.fstat(guard)
                except OSError:
                    return "preserved"
                if (
                    not stat.S_ISREG(guard_info.st_mode)
                    or guard_info.st_nlink not in {0, 1}
                    or _identity(guard_info) != identity
                ):
                    return "preserved"
                return "closed"
            finally:
                try:
                    os.close(guard)
                except OSError:
                    pass

        # The Windows implementation overrides this path with a deletion bound
        # to the still-open file handle. Other path-only fallbacks preserve.
        return "preserved"

    def posix_owned_descriptor(
        self,
        identity: tuple[int, int],
        name: str,
    ) -> int:
        owned = self._posix_owned.get(identity)
        if owned is None or owned[0] != name:
            raise RuntimeInputsError("cache_entry_unsafe", "cache")
        return owned[1]

    def release_posix_owned(
        self,
        identity: tuple[int, int],
        name: str,
    ) -> None:
        owned = self._posix_owned.get(identity)
        if owned is None or owned[0] != name:
            raise RuntimeInputsError("cache_entry_unsafe", "cache")
        self._posix_owned.pop(identity)
        guard = owned[1]
        try:
            try:
                info = os.fstat(guard)
            except OSError:
                raise RuntimeInputsError("cache_entry_unsafe", "cache") from None
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or _identity(info) != identity:
                raise RuntimeInputsError("cache_entry_unsafe", "cache")
        finally:
            try:
                os.close(guard)
            except OSError:
                pass

    def windows_owned_handle(
        self,
        identity: tuple[int, int],
        name: str,
    ) -> int:
        owned = self._windows_owned.get(identity)
        if owned is None or owned[0] != name:
            raise RuntimeInputsError("cache_entry_unsafe", "cache")
        return owned[1]

    def release_windows_owned(
        self,
        identity: tuple[int, int],
        name: str,
    ) -> None:
        if _WINDOWS_API is None:
            return
        owned = self._windows_owned.get(identity)
        if owned is None or owned[0] != name:
            raise RuntimeInputsError("cache_entry_unsafe", "cache")
        self._windows_owned.pop(identity)
        _WINDOWS_API.close(owned[1])


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_posix_directory(
    path: Path,
    fixed_components: tuple[str, ...],
    *,
    create: bool,
) -> _Directory:
    anchor = Path(path.anchor)
    try:
        descriptor = os.open(anchor, _directory_flags())
    except OSError:
        raise RuntimeInputsError("cache_root_unsafe", "cache") from None
    current = anchor
    try:
        components = (*path.parts[1:], *fixed_components)
        fixed_start = len(path.parts) - 1
        for index, component in enumerate(components):
            if index >= fixed_start:
                normalized = _normal_form(component)
                try:
                    names = os.listdir(descriptor)
                except OSError:
                    raise RuntimeInputsError("cache_root_unsafe", "cache") from None
                if any(
                    candidate != component and _normal_form(candidate) == normalized
                    for candidate in names
                ):
                    raise RuntimeInputsError("cache_root_unsafe", "cache")
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                except OSError:
                    raise RuntimeInputsError("cache_root_unsafe", "cache") from None
            try:
                child = os.open(component, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                raise _CacheDirectoryMissing from None
            except OSError:
                raise RuntimeInputsError("cache_root_unsafe", "cache") from None
            try:
                opened = os.fstat(child)
                linked = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or not stat.S_ISDIR(linked.st_mode)
                    or stat.S_ISLNK(linked.st_mode)
                    or _is_reparse(linked)
                    or _identity(opened) != _identity(linked)
                ):
                    raise RuntimeInputsError("cache_root_unsafe", "cache")
            except Exception:
                try:
                    os.close(child)
                except OSError:
                    pass
                raise
            try:
                os.close(descriptor)
            except OSError:
                pass
            descriptor = child
            current /= component
        identity = _identity(os.fstat(descriptor))
        directory = _Directory(current, descriptor, identity)
        descriptor = -1
        directory.assert_current()
        return directory
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_windows_directory(
    path: Path,
    fixed_components: tuple[str, ...],
    *,
    create: bool,
) -> _Directory:
    if _WINDOWS_API is None:
        raise RuntimeInputsError("secure_primitive_unavailable", "cache")
    current = Path(path.anchor)
    handles: list[int] = []
    try:
        handles.append(_WINDOWS_API.open_directory(current))
        components = (*path.parts[1:], *fixed_components)
        fixed_start = len(path.parts) - 1
        for index, component in enumerate(components):
            if index >= fixed_start:
                normalized = _normal_form(component)
                try:
                    names = os.listdir(current)
                except OSError:
                    raise RuntimeInputsError("cache_root_unsafe", "cache") from None
                if any(
                    candidate != component and _normal_form(candidate) == normalized
                    for candidate in names
                ):
                    raise RuntimeInputsError("cache_root_unsafe", "cache")
            candidate = current / component
            if create:
                try:
                    candidate.mkdir(mode=0o700)
                except FileExistsError:
                    pass
                except OSError:
                    raise RuntimeInputsError("cache_root_unsafe", "cache") from None
            try:
                handle = _WINDOWS_API.open_directory(candidate)
            except FileNotFoundError:
                raise _CacheDirectoryMissing from None
            handles.append(handle)
            current = candidate
        native_identity = _WINDOWS_API.info(handles[-1]).identity
        try:
            info = current.lstat()
        except OSError:
            raise RuntimeInputsError("cache_root_unsafe", "cache") from None
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise RuntimeInputsError("cache_root_unsafe", "cache")
        directory = _Directory(
            current,
            None,
            _identity(info),
            windows_handles=tuple(handles),
            windows_identity=native_identity,
        )
        handles = []
        directory.assert_current()
        return directory
    finally:
        for handle in reversed(handles):
            _WINDOWS_API.close(handle)


def _open_cache_directory(
    cache_dir: Path,
    *,
    create: bool,
) -> _Directory:
    if _HAS_SECURE_DIR_FD:
        return _open_posix_directory(cache_dir, (), create=create)
    if os.name == "nt":
        return _open_windows_directory(cache_dir, (), create=create)
    raise RuntimeInputsError("secure_primitive_unavailable", "cache")


def _open_component_directory(
    target_directory: _Directory,
    component: str,
    *,
    create: bool,
) -> _Directory:
    if not target_directory.windows_handles and target_directory.descriptor is None:
        raise RuntimeInputsError("secure_primitive_unavailable", "cache")
    target_directory.reject_alias(component)
    if target_directory.windows_handles:
        if _WINDOWS_API is None:
            raise RuntimeInputsError("secure_primitive_unavailable", "cache")
        candidate = target_directory.path / component
        if create:
            try:
                candidate.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError:
                raise RuntimeInputsError("cache_root_unsafe", "cache") from None
        try:
            handle = _WINDOWS_API.open_directory(candidate)
        except FileNotFoundError:
            raise _CacheDirectoryMissing from None
        try:
            native_identity = _WINDOWS_API.info(handle).identity
            info = candidate.lstat()
            target_directory.assert_current()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                raise RuntimeInputsError("cache_root_unsafe", "cache")
            child = _Directory(
                candidate,
                None,
                _identity(info),
                windows_handles=(handle,),
                windows_identity=native_identity,
            )
            handle = 0
            child.assert_current()
            return child
        finally:
            if handle:
                _WINDOWS_API.close(handle)
    if target_directory.descriptor is not None:
        if create:
            try:
                os.mkdir(component, 0o700, dir_fd=target_directory.descriptor)
            except FileExistsError:
                pass
            except OSError:
                raise RuntimeInputsError("cache_root_unsafe", "cache") from None
        try:
            descriptor = os.open(
                component,
                _directory_flags(),
                dir_fd=target_directory.descriptor,
            )
        except FileNotFoundError:
            raise _CacheDirectoryMissing from None
        except OSError:
            raise RuntimeInputsError("cache_root_unsafe", "cache") from None
        try:
            opened = os.fstat(descriptor)
            linked = os.stat(
                component,
                dir_fd=target_directory.descriptor,
                follow_symlinks=False,
            )
            target_directory.assert_current()
            if (
                not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(linked.st_mode)
                or stat.S_ISLNK(linked.st_mode)
                or _is_reparse(linked)
                or _identity(opened) != _identity(linked)
            ):
                raise RuntimeInputsError("cache_root_unsafe", "cache")
            child = _Directory(
                target_directory.path / component,
                descriptor,
                _identity(opened),
            )
            descriptor = -1
            child.assert_current()
            return child
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    raise RuntimeInputsError("secure_primitive_unavailable", "cache")


def _require_child_identity(
    parent: _Directory,
    name: str,
    expected_identity: tuple[int, int],
    *,
    directory: bool,
) -> None:
    parent.reject_alias(name)
    info = parent.entry_stat(name)
    if info is None or _identity(info) != expected_identity:
        raise RuntimeInputsError("cache_parent_changed", "cache")
    if directory:
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            raise RuntimeInputsError("cache_parent_changed", "cache")
    elif (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or _is_reparse(info)
        or info.st_nlink != 1
    ):
        raise RuntimeInputsError("cache_entry_unsafe", "cache")


def _read_and_hash(
    stream: BinaryIO,
    expected_size: int,
    *,
    need_sha512: bool,
) -> tuple[int, str, str | None]:
    sha256 = hashlib.sha256()
    sha512 = hashlib.sha512() if need_sha512 else None
    total = 0
    while True:
        block = stream.read(min(CHUNK_BYTES, expected_size + 1 - total))
        if not isinstance(block, bytes):
            raise RuntimeInputsError("cache_entry_unsafe", "cache")
        if not block:
            break
        total += len(block)
        if total > expected_size:
            break
        sha256.update(block)
        if sha512 is not None:
            sha512.update(block)
    return total, sha256.hexdigest(), sha512.hexdigest() if sha512 is not None else None


def _inspect_cached(
    directory: _Directory,
    artifact: InputArtifact,
    *,
    retain: bool = False,
) -> _Inspection:
    descriptor = -1
    try:
        directory.reject_alias(artifact.filename)
        before = directory.entry_stat(artifact.filename)
        if before is None:
            return _Inspection("missing")
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse(before)
            or before.st_nlink != 1
        ):
            return _Inspection("unsafe")
        descriptor = directory.open_entry(artifact.filename)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or opened.st_nlink != 1
            or _identity(opened) != _identity(before)
        ):
            return _Inspection("unsafe")
        if opened.st_size != artifact.size:
            return _Inspection("wrong_size")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            size, sha256, sha512 = _read_and_hash(
                stream,
                artifact.size,
                need_sha512=artifact.sha512 is not None,
            )
        after = os.fstat(descriptor)
        if size != artifact.size:
            return _Inspection("wrong_size")
        current = directory.entry_stat(artifact.filename)
        if (
            current is None
            or not _same_file_state(opened, after)
            or not _same_file_state(after, current)
            or current.st_nlink != 1
        ):
            return _Inspection("changed")
        if sha256 != artifact.sha256:
            return _Inspection("wrong_sha256")
        if artifact.sha512 is not None and sha512 != artifact.sha512:
            return _Inspection("wrong_sha512")
        identity = _identity(current)
        if retain:
            pinned = _PinnedCacheFile(
                descriptor=descriptor,
                state=after,
                identity=identity,
            )
            descriptor = -1
            return _Inspection("verified", identity, pinned)
        return _Inspection("verified", identity)
    except RuntimeInputsError:
        return _Inspection("unsafe")
    except OSError:
        return _Inspection("read_failed")
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _rehash_pinned(
    directory: _Directory,
    artifact: InputArtifact,
    pinned: _PinnedCacheFile,
) -> _Inspection:
    try:
        if pinned.descriptor < 0:
            return _Inspection("read_failed")
        before = os.fstat(pinned.descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or pinned.identity != _identity(before)
            or not _same_file_state(pinned.state, before)
        ):
            return _Inspection("changed")
        os.lseek(pinned.descriptor, 0, os.SEEK_SET)
        with os.fdopen(pinned.descriptor, "rb", closefd=False) as stream:
            size, sha256, sha512 = _read_and_hash(
                stream,
                artifact.size,
                need_sha512=artifact.sha512 is not None,
            )
        after = os.fstat(pinned.descriptor)
        if size != artifact.size or after.st_size != artifact.size:
            return _Inspection("wrong_size")
        if not _same_file_state(before, after):
            return _Inspection("changed")
        directory.reject_alias(artifact.filename)
        current = directory.entry_stat(artifact.filename)
        if (
            current is None
            or current.st_nlink != 1
            or not _same_file_state(after, current)
            or _identity(current) != pinned.identity
        ):
            return _Inspection("changed")
        if sha256 != artifact.sha256:
            return _Inspection("wrong_sha256")
        if artifact.sha512 is not None and sha512 != artifact.sha512:
            return _Inspection("wrong_sha512")
        return _Inspection("verified", pinned.identity)
    except RuntimeInputsError:
        return _Inspection("unsafe")
    except OSError:
        return _Inspection("read_failed")


def _split_https_url(
    url: object,
    *,
    max_chars: int,
) -> Any:
    if (
        type(url) is not str
        or not url
        or len(url) > max_chars
        or any(ord(character) <= 0x20 or ord(character) == 0x7F for character in url)
    ):
        raise RuntimeInputsError("redirect_rejected", "cache")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise RuntimeInputsError("redirect_rejected", "cache") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.netloc != parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not parsed.path.startswith("/")
        or "\\" in parsed.path
    ):
        raise RuntimeInputsError("redirect_rejected", "cache")
    return parsed


def _validate_source_url(url: object) -> Any:
    parsed = _split_https_url(url, max_chars=2048)
    if parsed.hostname not in ALLOWED_HTTPS_HOSTS or parsed.query or parsed.fragment:
        raise RuntimeInputsError("redirect_rejected", "cache")
    return parsed


def _github_release_repository_id(source_url: str) -> str | None:
    parsed = _validate_source_url(source_url)
    if parsed.hostname != "github.com":
        return None
    match = _GITHUB_RELEASE_PATH.fullmatch(parsed.path)
    if match is None or "%2f" in parsed.path.casefold() or "%5c" in parsed.path.casefold():
        return None
    return GITHUB_RELEASE_REPOSITORY_IDS.get((match.group(1).casefold(), match.group(2).casefold()))


def _validate_github_asset_url(
    source_url: str,
    candidate_url: object,
) -> str:
    repository_id = _github_release_repository_id(source_url)
    if repository_id is None:
        raise RuntimeInputsError("redirect_rejected", "cache")
    parsed = _split_https_url(
        candidate_url,
        max_chars=MAX_REDIRECT_URL_CHARS,
    )
    match = _GITHUB_ASSET_PATH.fullmatch(parsed.path)
    if (
        parsed.hostname != GITHUB_RELEASE_ASSET_HOST
        or parsed.fragment
        or not parsed.query
        or len(parsed.query) > MAX_SIGNED_QUERY_CHARS
        or match is None
        or match.group(1) != repository_id
    ):
        raise RuntimeInputsError("redirect_rejected", "cache")
    return candidate_url


def _validate_redirect_transition(
    source_url: str,
    current_url: str,
    candidate_url: object,
) -> str:
    _validate_source_url(source_url)
    if current_url != source_url:
        raise RuntimeInputsError("redirect_rejected", "cache")
    candidate = _validate_github_asset_url(source_url, candidate_url)
    if candidate == source_url:
        raise RuntimeInputsError("redirect_rejected", "cache")
    return candidate


class _StrictRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, original_url: str) -> None:
        super().__init__()
        self.original_url = original_url
        self.history: list[str] = []

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        if (
            code != 302
            or request.get_method() != "GET"
            or len(self.history) >= MAX_REDIRECTS
            or new_url in {self.original_url, *self.history}
        ):
            raise RuntimeInputsError("redirect_rejected", "cache")
        candidate = _validate_redirect_transition(
            self.original_url,
            request.full_url,
            new_url,
        )
        self.history.append(candidate)
        return urllib.request.Request(
            candidate,
            headers={
                "Accept-Encoding": "identity",
                "Cache-Control": "no-transform",
                "User-Agent": USER_AGENT,
            },
            method="GET",
        )


class _DefaultHTTPSOpener:
    def open(
        self,
        request: urllib.request.Request,
        *,
        timeout: float,
    ) -> _HTTPResponse:
        redirect_handler = _StrictRedirectHandler(request.full_url)
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            redirect_handler,
        )
        response = opener.open(request, timeout=timeout)
        response.rwf_redirect_chain = tuple(redirect_handler.history)  # type: ignore[attr-defined]
        return response  # type: ignore[return-value]


def _header_values(headers: object, name: str) -> list[str]:
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all(name)
        if values is None:
            return []
        if not isinstance(values, list) or any(type(value) is not str for value in values):
            raise RuntimeInputsError("response_invalid", "cache")
        return values
    get = getattr(headers, "get", None)
    if not callable(get):
        raise RuntimeInputsError("response_invalid", "cache")
    value = get(name)
    if value is None:
        return []
    if type(value) is not str:
        raise RuntimeInputsError("response_invalid", "cache")
    return [value]


def _validate_response(
    response: _HTTPResponse,
    artifact: InputArtifact,
) -> None:
    status = getattr(response, "status", None)
    if type(status) is not int or status != 200:
        raise RuntimeInputsError("response_invalid", f"artifact.{artifact.component}")
    chain = getattr(response, "rwf_redirect_chain", ())
    if not isinstance(chain, (tuple, list)) or len(chain) > MAX_REDIRECTS:
        raise RuntimeInputsError("redirect_rejected", f"artifact.{artifact.component}")
    _validate_source_url(artifact.url)
    current_url = artifact.url
    seen = {artifact.url}
    for candidate in chain:
        if type(candidate) is not str or candidate in seen:
            raise RuntimeInputsError(
                "redirect_rejected",
                f"artifact.{artifact.component}",
            )
        current_url = _validate_redirect_transition(
            artifact.url,
            current_url,
            candidate,
        )
        seen.add(current_url)
    try:
        final_url = response.geturl()
    except Exception:
        raise RuntimeInputsError(
            "response_invalid",
            f"artifact.{artifact.component}",
        ) from None
    if type(final_url) is not str or final_url != current_url:
        raise RuntimeInputsError(
            "redirect_rejected",
            f"artifact.{artifact.component}",
        )
    if chain:
        _validate_github_asset_url(artifact.url, final_url)
    elif final_url != artifact.url:
        raise RuntimeInputsError(
            "redirect_rejected",
            f"artifact.{artifact.component}",
        )

    lengths = _header_values(response.headers, "Content-Length")
    if (
        len(lengths) != 1
        or len(lengths[0]) > 10
        or not re.fullmatch(r"(?:0|[1-9][0-9]*)", lengths[0])
        or int(lengths[0]) != artifact.size
    ):
        raise RuntimeInputsError(
            "response_invalid",
            f"artifact.{artifact.component}",
        )
    encodings = _header_values(response.headers, "Content-Encoding")
    if encodings and (len(encodings) != 1 or encodings[0].casefold() != "identity"):
        raise RuntimeInputsError(
            "response_invalid",
            f"artifact.{artifact.component}",
        )
    if _header_values(response.headers, "Transfer-Encoding") or _header_values(
        response.headers, "Content-Range"
    ):
        raise RuntimeInputsError(
            "response_invalid",
            f"artifact.{artifact.component}",
        )


def _open_response(
    opener: _HTTPOpener,
    artifact: InputArtifact,
    deadline: _Deadline,
) -> _HTTPResponse:
    request = urllib.request.Request(
        artifact.url,
        headers={
            "Accept-Encoding": "identity",
            "Cache-Control": "no-transform",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        response = opener.open(
            request,
            timeout=min(NETWORK_TIMEOUT_SECONDS, deadline.remaining()),
        )
    except RuntimeInputsError:
        raise
    except Exception:
        raise RuntimeInputsError(
            "network_failed",
            f"artifact.{artifact.component}",
        ) from None
    try:
        deadline.checkpoint()
        _validate_response(response, artifact)
        deadline.checkpoint()
        return response
    except RuntimeInputsError:
        try:
            response.close()
        except Exception:
            pass
        raise
    except Exception:
        try:
            response.close()
        except Exception:
            pass
        raise RuntimeInputsError(
            "response_invalid",
            f"artifact.{artifact.component}",
        ) from None


def _sync_parent(directory: _Directory) -> None:
    directory.assert_current()
    if directory.windows_handles:
        if _WINDOWS_API is None:
            raise RuntimeInputsError("secure_primitive_unavailable", "cache")
        # Windows has no stdlib directory-fsync. Publication retains the
        # handle-bound file and flushes it again after the handle rename.
        return
    if directory.descriptor is None:
        raise RuntimeInputsError("secure_primitive_unavailable", "cache")
    try:
        os.fsync(directory.descriptor)
    except OSError:
        raise RuntimeInputsError("sync_failed", "cache") from None


def _linux_link_fd_no_replace(
    source_descriptor: int,
    directory_descriptor: int,
    destination_name: str,
) -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeInputsError("secure_primitive_unavailable", "cache")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        linkat = libc.linkat
    except (AttributeError, OSError):
        raise RuntimeInputsError("secure_primitive_unavailable", "cache") from None
    linkat.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    linkat.restype = ctypes.c_int
    result = linkat(
        source_descriptor,
        b"",
        directory_descriptor,
        os.fsencode(destination_name),
        0x1000,  # AT_EMPTY_PATH
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError
    if error in {
        errno.ENOENT,
        errno.ENOSYS,
        errno.EINVAL,
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
        errno.EPERM,
    }:
        raise RuntimeInputsError("secure_primitive_unavailable", "cache")
    raise RuntimeInputsError("cache_root_unsafe", "cache")


def _publish_no_replace(
    directory: _Directory,
    temporary_name: str,
    destination_name: str,
    temporary_identity: tuple[int, int],
) -> None:
    directory.assert_current()
    _sync_parent(directory)
    if directory.windows_handles:
        if _WINDOWS_API is None:
            raise RuntimeInputsError("secure_primitive_unavailable", "cache")
        temporary = directory.entry_stat(temporary_name)
        if (
            temporary is None
            or not stat.S_ISREG(temporary.st_mode)
            or temporary.st_nlink != 1
            or _identity(temporary) != temporary_identity
        ):
            raise RuntimeInputsError("cache_entry_unsafe", "cache")
        guard = directory.windows_owned_handle(
            temporary_identity,
            temporary_name,
        )
        _WINDOWS_API.rename_no_replace(
            guard,
            directory.windows_handles[-1],
            destination_name,
        )
    elif directory.descriptor is not None:
        guard = directory.posix_owned_descriptor(
            temporary_identity,
            temporary_name,
        )
        owned = os.fstat(guard)
        if (
            not stat.S_ISREG(owned.st_mode)
            or owned.st_nlink != 0
            or _identity(owned) != temporary_identity
        ):
            raise RuntimeInputsError("cache_entry_unsafe", "cache")
        _linux_link_fd_no_replace(
            guard,
            directory.descriptor,
            destination_name,
        )
    else:
        raise RuntimeInputsError("secure_primitive_unavailable", "cache")
    directory.assert_current()
    published = directory.entry_stat(destination_name)
    if (
        published is None
        or not stat.S_ISREG(published.st_mode)
        or published.st_nlink != 1
        or _identity(published) != temporary_identity
    ):
        raise RuntimeInputsError("cache_parent_changed", "cache")
    if directory.windows_handles:
        _WINDOWS_API.flush(guard)
    _sync_parent(directory)
    if directory.windows_handles:
        directory.release_windows_owned(
            temporary_identity,
            temporary_name,
        )
    elif directory.descriptor is not None:
        directory.release_posix_owned(
            temporary_identity,
            temporary_name,
        )


def _download_one(
    directory: _Directory,
    artifact: InputArtifact,
    opener: _HTTPOpener,
    *,
    clock: Callable[[], float],
) -> str:
    existing = _inspect_cached(directory, artifact)
    if existing.valid:
        return "reused"
    if existing.status != "missing":
        raise RuntimeInputsError(
            "cache_conflict",
            f"artifact.{artifact.component}",
        )

    deadline = _Deadline.start(
        clock=clock,
        context=f"artifact.{artifact.component}",
    )
    response = _open_response(opener, artifact, deadline)
    descriptor = -1
    temporary_name = ""
    temporary_identity: tuple[int, int] | None = None
    try:
        descriptor, temporary_name, temporary_identity = directory.create_temporary()
        sha256 = hashlib.sha256()
        sha512 = hashlib.sha512() if artifact.sha512 is not None else None
        total = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                while True:
                    deadline.checkpoint()
                    block = response.read(CHUNK_BYTES)
                    deadline.checkpoint()
                    if not isinstance(block, bytes):
                        raise RuntimeInputsError(
                            "download_interrupted",
                            f"artifact.{artifact.component}",
                        )
                    if not block:
                        break
                    total += len(block)
                    if total > artifact.size:
                        raise RuntimeInputsError(
                            "download_size_mismatch",
                            f"artifact.{artifact.component}",
                        )
                    output.write(block)
                    sha256.update(block)
                    if sha512 is not None:
                        sha512.update(block)
                output.flush()
                try:
                    os.fsync(output.fileno())
                except OSError:
                    raise RuntimeInputsError("sync_failed", "cache") from None
                sealed = os.fstat(output.fileno())
        except RuntimeInputsError:
            raise
        except Exception:
            raise RuntimeInputsError(
                "download_interrupted",
                f"artifact.{artifact.component}",
            ) from None
        if (
            total != artifact.size
            or sealed.st_size != artifact.size
            or sealed.st_nlink != (1 if directory.windows_handles else 0)
            or _identity(sealed) != temporary_identity
        ):
            raise RuntimeInputsError(
                "download_size_mismatch",
                f"artifact.{artifact.component}",
            )
        if sha256.hexdigest() != artifact.sha256 or (
            artifact.sha512 is not None
            and (sha512 is None or sha512.hexdigest() != artifact.sha512)
        ):
            raise RuntimeInputsError(
                "download_digest_mismatch",
                f"artifact.{artifact.component}",
            )
        directory.assert_current()
        if directory.windows_handles:
            current_temp = directory.entry_stat(temporary_name)
        elif directory.descriptor is not None:
            current_temp = os.fstat(
                directory.posix_owned_descriptor(
                    temporary_identity,
                    temporary_name,
                )
            )
        else:
            raise RuntimeInputsError("secure_primitive_unavailable", "cache")
        if (
            current_temp is None
            or current_temp.st_nlink != (1 if directory.windows_handles else 0)
            or _identity(current_temp) != temporary_identity
            or not _same_file_state(sealed, current_temp)
        ):
            raise RuntimeInputsError("cache_entry_unsafe", "cache")
        try:
            _publish_no_replace(
                directory,
                temporary_name,
                artifact.filename,
                temporary_identity,
            )
        except FileExistsError:
            raced = _inspect_cached(directory, artifact)
            if raced.valid:
                return "reused"
            raise RuntimeInputsError(
                "cache_conflict",
                f"artifact.{artifact.component}",
            ) from None
        verified = _inspect_cached(directory, artifact)
        if not verified.valid:
            raise RuntimeInputsError(
                "cache_entry_unsafe",
                f"artifact.{artifact.component}",
            )
        return "downloaded"
    finally:
        try:
            response.close()
        except Exception:
            pass
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_identity is not None and temporary_name:
            directory.cleanup_owned(temporary_name, temporary_identity)


def _verify_artifacts(
    target_id: str,
    cache_dir: Path,
    artifacts: tuple[InputArtifact, ...],
) -> InventoryReport:
    items: list[InventoryItem] = []
    try:
        cache_context = _open_cache_directory(cache_dir, create=False)
    except _CacheDirectoryMissing:
        cache_context = None
    if cache_context is None:
        items.extend(
            InventoryItem(artifact=artifact, target_id=target_id, status="missing")
            for artifact in artifacts
        )
    else:
        with cache_context as cache_directory:
            try:
                target_context = _open_component_directory(
                    cache_directory,
                    target_id,
                    create=False,
                )
            except _CacheDirectoryMissing:
                target_context = None
            if target_context is None:
                items.extend(
                    InventoryItem(
                        artifact=artifact,
                        target_id=target_id,
                        status="missing",
                    )
                    for artifact in artifacts
                )
            else:
                with target_context as target_directory:
                    pinned_files: list[_PinnedCacheFile] = []
                    try:
                        target_identity = target_directory.identity
                        verified: list[
                            tuple[
                                int,
                                InputArtifact,
                                tuple[int, int],
                                _PinnedCacheFile,
                            ]
                        ] = []
                        for artifact in artifacts:
                            _require_child_identity(
                                cache_directory,
                                target_id,
                                target_identity,
                                directory=True,
                            )
                            target_directory.assert_current()
                            component_identity: tuple[int, int] | None = None
                            try:
                                with _open_component_directory(
                                    target_directory,
                                    artifact.component,
                                    create=False,
                                ) as directory:
                                    component_identity = directory.identity
                                    inspection = _inspect_cached(
                                        directory,
                                        artifact,
                                        retain=True,
                                    )
                                    if inspection.pinned is not None:
                                        pinned_files.append(inspection.pinned)
                                    directory.reject_alias(artifact.filename)
                                    directory.assert_current()
                            except _CacheDirectoryMissing:
                                inspection = _Inspection("missing")
                            except RuntimeInputsError as exc:
                                if exc.code not in {
                                    "cache_entry_unsafe",
                                    "cache_root_unsafe",
                                }:
                                    raise
                                inspection = _Inspection("unsafe")
                            if component_identity is not None:
                                _require_child_identity(
                                    target_directory,
                                    artifact.component,
                                    component_identity,
                                    directory=True,
                                )
                            _require_child_identity(
                                cache_directory,
                                target_id,
                                target_identity,
                                directory=True,
                            )
                            if inspection.valid and (
                                inspection.identity is None
                                or inspection.pinned is None
                                or component_identity is None
                            ):
                                inspection = _Inspection("unsafe")
                            item_index = len(items)
                            items.append(
                                InventoryItem(
                                    artifact,
                                    target_id,
                                    inspection.status,
                                )
                            )
                            if (
                                inspection.valid
                                and inspection.identity is not None
                                and inspection.pinned is not None
                                and component_identity is not None
                            ):
                                verified.append(
                                    (
                                        item_index,
                                        artifact,
                                        component_identity,
                                        inspection.pinned,
                                    )
                                )
                        for (
                            item_index,
                            artifact,
                            component_identity,
                            pinned,
                        ) in verified:
                            _require_child_identity(
                                cache_directory,
                                target_id,
                                target_identity,
                                directory=True,
                            )
                            _require_child_identity(
                                target_directory,
                                artifact.component,
                                component_identity,
                                directory=True,
                            )
                            with _open_component_directory(
                                target_directory,
                                artifact.component,
                                create=False,
                            ) as directory:
                                final = _rehash_pinned(
                                    directory,
                                    artifact,
                                    pinned,
                                )
                            if not final.valid:
                                items[item_index] = InventoryItem(
                                    artifact,
                                    target_id,
                                    final.status,
                                )
                    finally:
                        for pinned in pinned_files:
                            pinned.close()
    return InventoryReport(
        action="verify",
        target_id=target_id,
        offline=True,
        valid=all(item.status == "verified" for item in items),
        items=tuple(items),
    )


def _fetch_artifacts(
    target_id: str,
    cache_dir: Path,
    artifacts: tuple[InputArtifact, ...],
    opener: _HTTPOpener,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> InventoryReport:
    items: list[InventoryItem] = []
    with _open_cache_directory(cache_dir, create=True) as cache_directory:
        with _open_component_directory(
            cache_directory,
            target_id,
            create=True,
        ) as target_directory:
            pinned_files: list[_PinnedCacheFile] = []
            try:
                target_identity = target_directory.identity
                completed: list[
                    tuple[
                        InputArtifact,
                        tuple[int, int],
                        _PinnedCacheFile,
                    ]
                ] = []
                for artifact in artifacts:
                    _require_child_identity(
                        cache_directory,
                        target_id,
                        target_identity,
                        directory=True,
                    )
                    target_directory.assert_current()
                    with _open_component_directory(
                        target_directory,
                        artifact.component,
                        create=True,
                    ) as directory:
                        component_identity = directory.identity
                        status = _download_one(
                            directory,
                            artifact,
                            opener,
                            clock=clock,
                        )
                        inspection = _inspect_cached(
                            directory,
                            artifact,
                            retain=True,
                        )
                        if inspection.pinned is not None:
                            pinned_files.append(inspection.pinned)
                        if (
                            not inspection.valid
                            or inspection.identity is None
                            or inspection.pinned is None
                        ):
                            raise RuntimeInputsError(
                                "cache_entry_unsafe",
                                f"artifact.{artifact.component}",
                            )
                        directory.reject_alias(artifact.filename)
                        _require_child_identity(
                            directory,
                            artifact.filename,
                            inspection.identity,
                            directory=False,
                        )
                        directory.assert_current()
                    _require_child_identity(
                        target_directory,
                        artifact.component,
                        component_identity,
                        directory=True,
                    )
                    _require_child_identity(
                        cache_directory,
                        target_id,
                        target_identity,
                        directory=True,
                    )
                    completed.append(
                        (
                            artifact,
                            component_identity,
                            inspection.pinned,
                        )
                    )
                    items.append(
                        InventoryItem(
                            artifact=artifact,
                            target_id=target_id,
                            status=status,
                        )
                    )
                for artifact, component_identity, pinned in completed:
                    _require_child_identity(
                        cache_directory,
                        target_id,
                        target_identity,
                        directory=True,
                    )
                    _require_child_identity(
                        target_directory,
                        artifact.component,
                        component_identity,
                        directory=True,
                    )
                    with _open_component_directory(
                        target_directory,
                        artifact.component,
                        create=False,
                    ) as directory:
                        final = _rehash_pinned(
                            directory,
                            artifact,
                            pinned,
                        )
                    if not final.valid:
                        raise RuntimeInputsError(
                            "cache_entry_unsafe",
                            f"artifact.{artifact.component}",
                        )
            finally:
                for pinned in pinned_files:
                    pinned.close()
    return InventoryReport(
        action="fetch",
        target_id=target_id,
        offline=False,
        valid=True,
        items=tuple(items),
    )


def verify_runtime_inputs(
    target_id: object,
    cache_dir: object,
    *,
    offline: object = True,
) -> InventoryReport:
    """Verify every required pinned input without constructing a network client."""

    try:
        target = _validate_target(target_id)
        cache = _validate_cache_dir(cache_dir)
        if offline is not True:
            raise RuntimeInputsError("invalid_argument", "cli", exit_code=2)
        document = _manifest_document()
        artifacts = _resolve_target_inputs(document, target)
        return _verify_artifacts(target, cache, artifacts)
    except RuntimeInputsError:
        raise
    except Exception:
        raise RuntimeInputsError("internal_error", "cache") from None


def fetch_runtime_inputs(
    target_id: object,
    cache_dir: object,
    *,
    opener: _HTTPOpener | None = None,
) -> InventoryReport:
    """Fetch every required pinned input into an exact no-replace cache."""

    try:
        target = _validate_target(target_id)
        cache = _validate_cache_dir(cache_dir)
        document = _manifest_document()
        artifacts = _resolve_target_inputs(document, target)
        network = opener if opener is not None else _DefaultHTTPSOpener()
        return _fetch_artifacts(target, cache, artifacts, network)
    except RuntimeInputsError:
        raise
    except Exception:
        raise RuntimeInputsError("internal_error", "cache") from None


class _ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def error(self, _message: str) -> NoReturn:
        raise RuntimeInputsError("invalid_argument", "cli", exit_code=2)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(description="Acquire or verify pinned Studio runtime inputs.")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="fetch every pinned target input")
    fetch.add_argument("--target", required=True)
    fetch.add_argument("--cache-dir", required=True)
    verify = commands.add_parser("verify", help="verify a complete local input cache")
    verify.add_argument("--offline", action="store_true")
    verify.add_argument("--target", required=True)
    verify.add_argument("--cache-dir", required=True)
    return parser


def _error_payload(error: RuntimeInputsError) -> dict[str, object]:
    return {
        "error": error.as_dict(),
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "redistribution_status": "blocked",
        "release_ready": False,
        "valid": False,
    }


def _print_json(value: object, *, stream: Any = sys.stdout) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        file=stream,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "fetch":
            report = fetch_runtime_inputs(args.target, args.cache_dir)
        elif args.command == "verify":
            if args.offline is not True:
                raise RuntimeInputsError("invalid_argument", "cli", exit_code=2)
            report = verify_runtime_inputs(
                args.target,
                args.cache_dir,
                offline=True,
            )
        else:
            raise RuntimeInputsError("invalid_argument", "cli", exit_code=2)
    except RuntimeInputsError as exc:
        _print_json(_error_payload(exc), stream=sys.stderr)
        return exc.exit_code
    except Exception:
        error = RuntimeInputsError("internal_error", "cache")
        _print_json(_error_payload(error), stream=sys.stderr)
        return 1
    _print_json(report.as_dict())
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
