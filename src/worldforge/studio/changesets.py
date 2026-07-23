from __future__ import annotations

import ctypes
import hashlib
import hmac
import os
import sqlite3
import stat
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.file_stat import FileStat, descriptor_file_stat, path_file_stat
from isoworld.content.portability import portable_relative_path
from worldforge.asset_io import AssetContractError, encoded_json, read_json_object
from worldforge.studio.changeset_review import (
    ReviewDiffError,
    build_changeset_diff,
    compute_review_sha256,
    unavailable_v1_diff,
)
from worldforge.studio.contracts import studio_source_path, validate_studio_changeset
from worldforge.studio.errors import (
    StudioContractError,
    StudioError,
    conflict,
    invalid_request,
    invalid_state,
    not_found,
)
from worldforge.studio.storage import StudioStore, decode_object, encode_json, utc_now
from worldforge.studio.workspaces import WorkspaceManager
from worldforge.world_lifecycle import inspect_world_project
from worldforge.world_lock import exclusive_world_lifecycle

MAX_CHANGE_FILE_BYTES = 16 * 1024 * 1024
MAX_CHANGESET_BYTES = 64 * 1024 * 1024
MAX_CHANGESET_OPERATIONS = 256
JOURNAL_FORMAT = "rpg-world-forge.studio_apply_journal"
JOURNAL_VERSION = 2
LEGACY_JOURNAL_VERSION = 1
_POSIX_PINNED_DIRECTORY_IO = os.name == "posix" and all(
    function in os.supports_dir_fd for function in (os.link, os.open, os.stat, os.unlink)
)
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


def _platform_name() -> str:
    return os.name


def _identity(info: FileStat) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _same_file_state(left: FileStat, right: FileStat) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_nlink,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_nlink,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return
    if os.name != "nt":
        raise StudioError(
            "internal_error", "Durable directory metadata flush is unsupported on this platform"
        )
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise StudioError("internal_error", "Windows directory durability API is unavailable")
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
        0x00000001 | 0x00000002 | 0x00000004,  # share read/write/delete
        None,
        3,  # OPEN_EXISTING
        0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in {None, invalid}:
        error = ctypes.get_last_error()
        raise StudioError(
            "internal_error", f"Could not open Windows directory for durable flush: {error}"
        )
    flush = kernel32.FlushFileBuffers
    flush.argtypes = [ctypes.c_void_p]
    flush.restype = ctypes.c_int
    close = kernel32.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    close.restype = ctypes.c_int
    try:
        if not flush(ctypes.c_void_p(handle)):
            error = ctypes.get_last_error()
            raise StudioError(
                "internal_error",
                f"Windows filesystem cannot durably flush directory metadata: {error}",
            )
    finally:
        close(ctypes.c_void_p(handle))


def _replace_durable(source: Path, destination: Path) -> None:
    if os.name != "nt":
        os.replace(source, destination)
        return
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise StudioError("internal_error", "Windows durable replacement API is unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)
    move_file = kernel32.MoveFileExW
    move_file.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move_file.restype = ctypes.c_int
    if not move_file(str(source), str(destination), 0x00000001 | 0x00000008):
        error = ctypes.get_last_error()
        raise StudioError(
            "internal_error", f"Could not durably replace Windows apply journal: {error}"
        )


def _safe_file_snapshot(
    path: Path,
    *,
    context: str,
    require_standalone: bool,
    require_utf8: bool = True,
    limit: int = MAX_CHANGE_FILE_BYTES,
) -> tuple[bytes, tuple[int, int]]:
    descriptor: int | None = None
    try:
        path_before = path_file_stat(path)
        if _is_link_or_reparse(path_before) or not stat.S_ISREG(path_before.st_mode):
            raise OSError("not a regular file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = descriptor_file_stat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("not a regular file")
        if require_standalone and before.st_nlink != 1:
            raise OSError("is a hard link")
        if before.st_size > limit:
            raise OSError(f"exceeds {limit} bytes")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            payload = source.read(limit + 1)
            source.seek(0)
            repeated = source.read(limit + 1)
            after = descriptor_file_stat(source.fileno())
        if len(payload) > limit:
            raise OSError(f"exceeds {limit} bytes")
        if payload != repeated or not _same_file_state(before, after):
            raise OSError("file changed while reading")
        path_after = path_file_stat(path)
        if (
            _is_link_or_reparse(path_after)
            or not _same_file_state(path_before, before)
            or not _same_file_state(path_after, before)
        ):
            raise OSError("path identity changed while reading")
        if require_utf8:
            payload.decode("utf-8")
        return payload, _identity(before)
    except UnicodeDecodeError as exc:
        raise invalid_request(f"{context} is not UTF-8") from exc
    except OSError as exc:
        message = str(exc)
        if "hard link" in message:
            raise invalid_request(f"{context} cannot be a hard link") from exc
        raise invalid_request(f"Could not read {context}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _path_info(path: Path) -> FileStat | None:
    try:
        return path_file_stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise conflict(f"Could not inspect changeset target: {exc}") from exc


def _normalize_path(value: object) -> PurePosixPath:
    relative = studio_source_path(value)
    if relative is None:
        raise invalid_request("Changeset paths are limited to portable files under source/")
    return relative


def _safe_directory_info(path: Path, *, context: str) -> FileStat:
    try:
        info = path_file_stat(path)
    except OSError as exc:
        raise conflict(f"Could not inspect {context}: {exc}") from exc
    if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise conflict(f"{context} is not a plain directory")
    return info


def _windows_lock_directory(path: Path) -> int:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise StudioError("internal_error", "Windows directory locking API is unavailable")
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
        0x00000080 | 0x00020000,  # FILE_READ_ATTRIBUTES | READ_CONTROL
        0x00000001 | 0x00000002,  # share reads/writes, never deletion
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in {None, invalid}:
        error = ctypes.get_last_error()
        raise conflict(f"Could not pin Windows changeset directory: {error}")
    return int(handle)


def _windows_close_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel32.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    close.restype = ctypes.c_int
    if not close(ctypes.c_void_p(handle)):
        error = ctypes.get_last_error()
        raise StudioError("internal_error", f"Could not release Windows directory lock: {error}")


@dataclass(slots=True)
class _PinnedParent:
    path: Path
    identity: tuple[int, int]
    descriptor: int | None
    descriptors: tuple[int, ...]
    windows_handles: tuple[int, ...]
    chain: tuple[tuple[Path, tuple[int, int]], ...]

    def verify_visible(self) -> None:
        for path, expected in self.chain:
            info = _safe_directory_info(path, context=f"changeset directory {path}")
            if _identity(info) != expected:
                raise conflict(f"Changeset directory identity changed: {path}")
        if self.descriptor is not None:
            opened = os.fstat(self.descriptor)
            if not stat.S_ISDIR(opened.st_mode) or _identity(opened) != self.identity:
                raise conflict(f"Pinned changeset directory identity changed: {self.path}")

    def entry_info(self, name: str) -> FileStat | None:
        try:
            if self.descriptor is not None:
                return os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
            self.verify_visible()
            return path_file_stat(self.path / name)
        except FileNotFoundError:
            return None

    def open_entry(self, name: str, flags: int, mode: int = 0o600) -> int:
        if self.descriptor is not None:
            return os.open(name, flags, mode, dir_fd=self.descriptor)
        self.verify_visible()
        descriptor = os.open(self.path / name, flags, mode)
        try:
            self.verify_visible()
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def link(self, source_name: str, destination_name: str) -> None:
        if self.descriptor is not None:
            os.link(
                source_name,
                destination_name,
                src_dir_fd=self.descriptor,
                dst_dir_fd=self.descriptor,
                follow_symlinks=False,
            )
            return
        self.verify_visible()
        os.link(
            self.path / source_name,
            self.path / destination_name,
            follow_symlinks=False,
        )
        self.verify_visible()

    def unlink(self, name: str) -> None:
        if self.descriptor is not None:
            os.unlink(name, dir_fd=self.descriptor)
            return
        self.verify_visible()
        (self.path / name).unlink()
        self.verify_visible()

    def flush(self) -> None:
        if self.descriptor is not None:
            os.fsync(self.descriptor)
            return
        self.verify_visible()
        _fsync_directory(self.path)
        self.verify_visible()


def _reject_pinned_collision(parent: _PinnedParent, requested: str, *, context: str) -> None:
    requested_key = unicodedata.normalize("NFC", requested).casefold()
    if parent.descriptor is not None:
        try:
            names = os.listdir(parent.descriptor)
        except OSError as exc:
            raise conflict(f"Could not enumerate {context}: {exc}") from exc
    else:
        parent.verify_visible()
        names = [entry.name for entry in parent.path.iterdir()]
    matches = [
        name for name in names if unicodedata.normalize("NFC", name).casefold() == requested_key
    ]
    if len(matches) > 1 or (matches and matches[0] != requested):
        spellings = ", ".join(sorted(repr(name) for name in matches))
        raise invalid_request(f"{context} has an NFC/casefold collision: {spellings}")


@contextmanager
def _open_pinned_parent(
    world_root: Path,
    relative: PurePosixPath,
    *,
    world_identity: tuple[int, int],
    parent_identity: tuple[int, int],
) -> Iterator[_PinnedParent]:
    platform = _platform_name()
    if platform == "posix":
        if not _POSIX_PINNED_DIRECTORY_IO:
            raise StudioError(
                "internal_error", "Secure POSIX changeset directory I/O is unavailable"
            )
        descriptors: list[int] = []
        chain: list[tuple[Path, tuple[int, int]]] = []
        try:
            root_descriptor = os.open(world_root, _DIRECTORY_OPEN_FLAGS)
            descriptors.append(root_descriptor)
            root_info = os.fstat(root_descriptor)
            if not stat.S_ISDIR(root_info.st_mode) or _identity(root_info) != world_identity:
                raise conflict("World root identity changed before changeset I/O")
            visible_root = _safe_directory_info(world_root, context="changeset world root")
            if _identity(visible_root) != world_identity:
                raise conflict("World root identity changed before changeset I/O")
            chain.append((world_root, world_identity))
            current_descriptor = root_descriptor
            current_path = world_root
            for part in relative.parts[:-1]:
                probe = _PinnedParent(
                    current_path,
                    _identity(os.fstat(current_descriptor)),
                    current_descriptor,
                    tuple(descriptors),
                    (),
                    tuple(chain),
                )
                _reject_pinned_collision(probe, part, context=f"changeset path component {part}")
                child_descriptor = os.open(part, _DIRECTORY_OPEN_FLAGS, dir_fd=current_descriptor)
                descriptors.append(child_descriptor)
                child_info = os.fstat(child_descriptor)
                if not stat.S_ISDIR(child_info.st_mode):
                    raise conflict(f"Changeset parent is not a directory: {relative}")
                current_path /= part
                visible = _safe_directory_info(
                    current_path, context=f"changeset directory {current_path}"
                )
                child_identity = _identity(child_info)
                if _identity(visible) != child_identity:
                    raise conflict(
                        f"Changeset directory changed while being pinned: {current_path}"
                    )
                chain.append((current_path, child_identity))
                current_descriptor = child_descriptor
            if _identity(os.fstat(current_descriptor)) != parent_identity:
                raise conflict(f"Changeset parent identity changed: {current_path}")
            pinned = _PinnedParent(
                current_path,
                parent_identity,
                current_descriptor,
                tuple(descriptors),
                (),
                tuple(chain),
            )
            _reject_pinned_collision(pinned, relative.name, context=f"changeset target {relative}")
            pinned.verify_visible()
            yield pinned
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        return
    if platform != "nt":
        raise StudioError("internal_error", "Secure changeset directory I/O is unsupported")

    handles: list[int] = []
    chain = []
    try:
        current_path = world_root
        root_before = _safe_directory_info(current_path, context="changeset world root")
        if _identity(root_before) != world_identity:
            raise conflict("World root identity changed before changeset I/O")
        root_handle = _windows_lock_directory(current_path)
        handles.append(root_handle)
        root_after = _safe_directory_info(current_path, context="changeset world root")
        if _identity(root_after) != world_identity:
            raise conflict("World root identity changed while being pinned")
        chain.append((current_path, world_identity))
        for part in relative.parts[:-1]:
            probe = _PinnedParent(
                current_path,
                chain[-1][1],
                None,
                (),
                tuple(handles),
                tuple(chain),
            )
            _reject_pinned_collision(probe, part, context=f"changeset path component {part}")
            child_path = current_path / part
            before = _safe_directory_info(child_path, context=f"changeset directory {child_path}")
            child_handle = _windows_lock_directory(child_path)
            handles.append(child_handle)
            after = _safe_directory_info(child_path, context=f"changeset directory {child_path}")
            if _identity(before) != _identity(after):
                raise conflict(f"Changeset directory changed while being pinned: {child_path}")
            current_path = child_path
            chain.append((current_path, _identity(after)))
        if chain[-1][1] != parent_identity:
            raise conflict(f"Changeset parent identity changed: {current_path}")
        pinned = _PinnedParent(
            current_path,
            parent_identity,
            None,
            (),
            tuple(handles),
            tuple(chain),
        )
        _reject_pinned_collision(pinned, relative.name, context=f"changeset target {relative}")
        pinned.verify_visible()
        yield pinned
    finally:
        errors: list[BaseException] = []
        for handle in reversed(handles):
            try:
                _windows_close_handle(handle)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise StudioError(
                "internal_error", "Could not release pinned changeset directories"
            ) from errors[0]


def _safe_entry_snapshot(
    parent: _PinnedParent,
    name: str,
    *,
    context: str,
    require_standalone: bool,
    require_utf8: bool = True,
    limit: int = MAX_CHANGE_FILE_BYTES,
) -> tuple[bytes, tuple[int, int]]:
    descriptor: int | None = None
    try:
        before_path = parent.entry_info(name)
        if (
            before_path is None
            or _is_link_or_reparse(before_path)
            or not stat.S_ISREG(before_path.st_mode)
        ):
            raise OSError("not a regular file")
        descriptor = parent.open_entry(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        before = descriptor_file_stat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError("not a regular file")
        if require_standalone and before.st_nlink != 1:
            raise OSError("is a hard link")
        if before.st_size > limit:
            raise OSError(f"exceeds {limit} bytes")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            payload = source.read(limit + 1)
            source.seek(0)
            repeated = source.read(limit + 1)
            after = descriptor_file_stat(source.fileno())
        if len(payload) > limit:
            raise OSError(f"exceeds {limit} bytes")
        if payload != repeated or not _same_file_state(before, after):
            raise OSError("file changed while reading")
        after_path = parent.entry_info(name)
        if (
            after_path is None
            or _is_link_or_reparse(after_path)
            or not _same_file_state(before_path, before)
            or not _same_file_state(after_path, before)
        ):
            raise OSError("entry identity changed while reading")
        if require_utf8:
            payload.decode("utf-8")
        return payload, _identity(before)
    except UnicodeDecodeError as exc:
        raise invalid_request(f"{context} is not UTF-8") from exc
    except OSError as exc:
        message = str(exc)
        if "hard link" in message:
            raise invalid_request(f"{context} cannot be a hard link") from exc
        raise invalid_request(f"Could not read {context}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_source_file_snapshot(
    world_root: Path,
    relative: PurePosixPath,
    *,
    world_identity: tuple[int, int],
    context: str,
    limit: int,
) -> bytes:
    """Read one source file through the same pinned boundary used by changesets."""

    if studio_source_path(relative.as_posix()) != relative:
        raise invalid_request("Source paths are limited to portable files under source/")
    return read_workspace_file_snapshot(
        world_root,
        relative,
        world_identity=world_identity,
        context=context,
        limit=limit,
    )


def read_workspace_file_snapshot(
    world_root: Path,
    relative: PurePosixPath,
    *,
    world_identity: tuple[int, int],
    context: str,
    limit: int,
) -> bytes:
    """Read one portable workspace file through a pinned standalone-file boundary."""

    if portable_relative_path(relative.as_posix()) != relative:
        raise invalid_request("Workspace snapshot paths must be portable relative paths")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_CHANGE_FILE_BYTES
    ):
        raise ValueError("limit must be a positive Studio workspace-file bound")
    parent_path = world_root.joinpath(*relative.parts[:-1])
    parent_info = _safe_directory_info(parent_path, context=f"workspace parent {relative.parent}")
    with _open_pinned_parent(
        world_root,
        relative,
        world_identity=world_identity,
        parent_identity=_identity(parent_info),
    ) as parent:
        payload, _ = _safe_entry_snapshot(
            parent,
            relative.name,
            context=context,
            require_standalone=True,
            limit=limit,
        )
    return payload


def _journal_identity(value: object, *, context: str) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value)
    ):
        raise StudioError("internal_error", f"{context} identity is invalid")
    return value[0], value[1]


@contextmanager
def _pinned_operation_parents(
    journal: dict[str, Any], world_root: Path
) -> Iterator[list[_PinnedParent]]:
    world_identity = _journal_identity(journal.get("world_identity"), context="world root")
    cache: dict[Path, tuple[tuple[int, int], _PinnedParent]] = {}
    parents: list[_PinnedParent] = []
    with ExitStack() as stack:
        for operation in journal["operations"]:
            relative = _normalize_path(operation.get("path"))
            parent_path = world_root.joinpath(*relative.parts[:-1])
            expected = _journal_identity(
                operation.get("parent_identity"), context=f"changeset parent {relative.parent}"
            )
            cached = cache.get(parent_path)
            if cached is not None:
                if cached[0] != expected:
                    raise StudioError(
                        "internal_error", "One changeset parent has inconsistent identities"
                    )
                parent = cached[1]
            else:
                parent = stack.enter_context(
                    _open_pinned_parent(
                        world_root,
                        relative,
                        world_identity=world_identity,
                        parent_identity=expected,
                    )
                )
                cache[parent_path] = (expected, parent)
            _reject_pinned_collision(parent, relative.name, context=f"changeset target {relative}")
            parents.append(parent)
        _verify_pinned_parents(parents)
        yield parents


def _verify_pinned_parents(parents: list[_PinnedParent]) -> None:
    verified: set[int] = set()
    for parent in parents:
        marker = id(parent)
        if marker in verified:
            continue
        parent.verify_visible()
        verified.add(marker)


def _safe_target(world_root: Path, relative: PurePosixPath) -> Path:
    current = world_root
    for index, part in enumerate(relative.parts[:-1]):
        _reject_sibling_collisions(current, part, context=f"changeset path component {part}")
        current /= part
        try:
            info = path_file_stat(current)
        except OSError as exc:
            raise invalid_request(f"Changeset parent is missing: {relative}") from exc
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise invalid_request(f"Changeset parent is unsafe: {relative}")
        if index == 0 and current != world_root / "source":
            raise invalid_request("Changeset escaped the world source directory")
    target_name = relative.parts[-1]
    _reject_sibling_collisions(current, target_name, context=f"changeset target {relative}")
    return current / target_name


def _reject_sibling_collisions(directory: Path, requested: str, *, context: str) -> None:
    requested_key = unicodedata.normalize("NFC", requested).casefold()
    matches = [
        entry.name
        for entry in directory.iterdir()
        if unicodedata.normalize("NFC", entry.name).casefold() == requested_key
    ]
    if len(matches) > 1 or (matches and matches[0] != requested):
        spellings = ", ".join(sorted(repr(name) for name in matches))
        raise invalid_request(f"{context} has an NFC/casefold collision: {spellings}")


def _verified_world(
    store: StudioStore, workspace_id: object
) -> tuple[dict[str, Any], Path, tuple[int, int]]:
    manager = WorkspaceManager(store)
    workspace = manager.get(workspace_id)
    verified = manager.verified_root(workspace["workspace_id"], "world_root")
    assert verified is not None
    world_root, expected = verified
    try:
        inspect_world_project(world_root)
    except ValueError as exc:
        raise conflict(f"World repository is no longer canonical: {exc}") from exc
    return workspace, world_root, expected


class ChangesetManager:
    def __init__(self, store: StudioStore, *, recover: bool = True) -> None:
        self.store = store
        if recover:
            self.recover_journals()

    def create(self, params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("changeset.create params must be an object")
        allowed = {"changeset_id", "workspace_id", "operations"}
        unknown = set(params) - allowed
        missing = {"workspace_id", "operations"} - set(params)
        if unknown or missing:
            fields = unknown or missing
            raise invalid_request(
                f"changeset.create has invalid fields: {', '.join(sorted(fields))}"
            )
        operations = params["operations"]
        if (
            not isinstance(operations, list)
            or not operations
            or len(operations) > MAX_CHANGESET_OPERATIONS
        ):
            raise invalid_request(
                f"changeset operations must contain 1 to {MAX_CHANGESET_OPERATIONS} entries"
            )
        workspace, world_root, expected_identity = _verified_world(
            self.store, params["workspace_id"]
        )
        public_operations: list[dict[str, Any]] = []
        snapshots: list[tuple[bytes | None, bytes | None]] = []
        seen: set[str] = set()
        total = 0
        try:
            with exclusive_world_lifecycle(world_root, error_type=ValueError):
                if _identity(path_file_stat(world_root)) != expected_identity:
                    raise conflict("World root identity changed while staging changeset")
                for index, raw in enumerate(operations):
                    public, snapshot = self._capture_operation(world_root, raw, index=index)
                    folded = public["path"].casefold()
                    if folded in seen:
                        raise invalid_request(
                            "Changeset operations contain an NFC/casefold collision"
                        )
                    seen.add(folded)
                    total += public["base_size"] + public["size"]
                    if total > MAX_CHANGESET_BYTES:
                        raise invalid_request(
                            f"Changeset retained content exceeds {MAX_CHANGESET_BYTES} bytes"
                        )
                    public_operations.append(public)
                    snapshots.append(snapshot)
        except StudioError:
            raise
        except ValueError as exc:
            raise conflict(str(exc)) from exc
        timestamp = utc_now()
        record = {
            "format": "rpg-world-forge.studio_changeset",
            "format_version": 2,
            "changeset_id": params.get("changeset_id") or uuid.uuid4().hex,
            "workspace_id": workspace["workspace_id"],
            "status": "staged",
            "operations": public_operations,
            "review_sha256": compute_review_sha256(public_operations),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        try:
            validate_studio_changeset(record)
            build_changeset_diff(record, snapshots)
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        except ReviewDiffError as exc:
            raise invalid_request(str(exc)) from exc
        for public, (base_payload, proposed_payload) in zip(
            public_operations, snapshots, strict=True
        ):
            if base_payload is not None:
                self._store_blob(base_payload, public["base_sha256"])
            if proposed_payload is not None:
                self._store_blob(proposed_payload, public["proposed_sha256"])
        try:
            with self.store.connection:
                self.store.connection.execute(
                    "INSERT INTO changesets "
                    "(changeset_id, workspace_id, status, record_json) VALUES (?, ?, ?, ?)",
                    (
                        record["changeset_id"],
                        record["workspace_id"],
                        record["status"],
                        encode_json(record),
                    ),
                )
                self.store.record_event(
                    workspace_id=record["workspace_id"],
                    topic="changeset.created",
                    entity_type="changeset",
                    entity_id=record["changeset_id"],
                    payload={"operations": len(public_operations)},
                    created_at=timestamp,
                )
        except sqlite3.IntegrityError as exc:
            raise conflict(f"Changeset {record['changeset_id']} already exists") from exc
        return record

    def _capture_operation(
        self, world_root: Path, raw: object, *, index: int
    ) -> tuple[dict[str, Any], tuple[bytes | None, bytes | None]]:
        if not isinstance(raw, dict):
            raise invalid_request(f"changeset operation {index} must be an object")
        kind = raw.get("operation")
        if not isinstance(kind, str) or kind not in {"create", "replace", "delete"}:
            raise invalid_request(f"changeset operation {index} has an unknown operation")
        allowed = {"path", "operation", "content"} if kind != "delete" else {"path", "operation"}
        if kind != "create":
            allowed.add("expected_base_sha256")
        if set(raw) != allowed:
            required = allowed - {"expected_base_sha256"}
            if set(raw) != required:
                raise invalid_request(f"changeset operation {index} has invalid fields")
        relative = _normalize_path(raw.get("path"))
        target = _safe_target(world_root, relative)
        info = _path_info(target)
        if info is not None and (_is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode)):
            raise invalid_request(f"Changeset target is not a regular file: {relative}")
        base_payload: bytes | None = None
        base: str | None
        if kind == "create":
            if info is not None:
                raise conflict(f"Create target already exists: {relative}")
            base = None
        else:
            if info is None:
                raise conflict(f"Changeset base is absent: {relative}")
            base_payload, _base_identity = _safe_file_snapshot(
                target,
                context=f"changeset base {relative}",
                require_standalone=True,
            )
            base = _hash(base_payload)
            expected_base = raw.get("expected_base_sha256")
            if expected_base is not None:
                if (
                    not isinstance(expected_base, str)
                    or len(expected_base) != 64
                    or any(character not in "0123456789abcdef" for character in expected_base)
                ):
                    raise invalid_request(
                        f"changeset operation {index} expected_base_sha256 is invalid"
                    )
                if not hmac.compare_digest(expected_base, base):
                    raise conflict(f"Changeset base changed before staging: {relative}")
        payload: bytes | None = None
        proposed: str | None = None
        if kind != "delete":
            content = raw.get("content")
            if not isinstance(content, str):
                raise invalid_request(f"changeset operation {index} content must be UTF-8 text")
            try:
                payload = content.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise invalid_request(
                    f"changeset operation {index} content is not valid UTF-8"
                ) from exc
            if len(payload) > MAX_CHANGE_FILE_BYTES:
                raise invalid_request(
                    f"changeset operation {index} exceeds {MAX_CHANGE_FILE_BYTES} bytes"
                )
            proposed = _hash(payload)
        return (
            {
                "path": relative.as_posix(),
                "operation": kind,
                "base_sha256": base,
                "base_size": 0 if base_payload is None else len(base_payload),
                "proposed_sha256": proposed,
                "size": 0 if payload is None else len(payload),
            },
            (base_payload, payload),
        )

    def _store_blob(self, payload: bytes, digest: object) -> None:
        if not isinstance(digest, str) or _hash(payload) != digest:
            raise StudioError("internal_error", "Proposed blob digest is inconsistent")
        target = self.store.blob_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        for directory in (self.store.blobs_dir, target.parent):
            info = path_file_stat(directory)
            if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise StudioError("conflict", "Content-addressed blob parent is unsafe")
        if target.exists() or target.is_symlink():
            current, _ = _safe_file_snapshot(target, context="staged blob", require_standalone=True)
            if _hash(current) != digest or current != payload:
                raise StudioError("conflict", "Content-addressed blob path contains other bytes")
            return
        temporary = target.parent / f".{digest}.{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                pass
            _fsync_directory(target.parent)
            current, _ = _safe_file_snapshot(
                target, context="staged blob", require_standalone=False
            )
            if current != payload or _hash(current) != digest:
                raise StudioError("conflict", "Content-addressed blob publication was replaced")
        except OSError as exc:
            raise StudioError("internal_error", f"Could not persist changeset blob: {exc}") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            _fsync_directory(target.parent)
        final, _ = _safe_file_snapshot(target, context="staged blob", require_standalone=True)
        if final != payload:
            raise StudioError("conflict", "Content-addressed blob changed during publication")

    def get(self, changeset_id: object) -> dict[str, Any]:
        if not isinstance(changeset_id, str):
            raise invalid_request("changeset_id must be a string")
        row = self.store.connection.execute(
            "SELECT record_json FROM changesets WHERE changeset_id = ?", (changeset_id,)
        ).fetchone()
        if row is None:
            raise not_found(f"Changeset {changeset_id} was not found")
        return self._validated_row(row)

    def diff(self, changeset_id: object) -> dict[str, Any]:
        """Return immutable exact review evidence derived only from owned CAS bytes."""

        record = self.get(changeset_id)
        if record["format_version"] == 1:
            return unavailable_v1_diff(record)
        snapshots: list[tuple[bytes | None, bytes | None]] = []
        for operation in record["operations"]:
            base = (
                None
                if operation["base_sha256"] is None
                else self._read_blob(operation["base_sha256"], operation["base_size"])
            )
            proposed = (
                None
                if operation["proposed_sha256"] is None
                else self._read_blob(operation["proposed_sha256"], operation["size"])
            )
            snapshots.append((base, proposed))
        try:
            return build_changeset_diff(record, snapshots)
        except ReviewDiffError as exc:
            raise conflict(f"Changeset review evidence is invalid: {exc}") from exc

    def _read_blob(self, digest: str, size: int) -> bytes:
        try:
            payload, _identity_value = _safe_file_snapshot(
                self.store.blob_path(digest),
                context="changeset blob",
                require_standalone=True,
            )
        except StudioError as exc:
            raise conflict(f"Could not read retained changeset blob: {exc.message}") from exc
        if len(payload) != size or not hmac.compare_digest(_hash(payload), digest):
            raise conflict("Retained changeset blob does not match its digest and size")
        return payload

    def list(
        self, *, workspace_id: object = None, status: object = None, limit: object = 100
    ) -> list[dict[str, Any]]:
        if workspace_id is not None:
            WorkspaceManager(self.store).get(workspace_id)
        if status is not None and (
            not isinstance(status, str)
            or status not in {"staged", "approved", "applying", "rejected", "applied"}
        ):
            raise invalid_request("changeset status filter is unknown")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise invalid_request("changeset list limit must be an integer from 1 to 1000")
        clauses: list[str] = []
        values: list[object] = []
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            values.append(workspace_id)
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        rows = self.store.connection.execute(
            f"SELECT record_json FROM changesets{where} ORDER BY changeset_id LIMIT ?",  # noqa: S608
            (*values, limit),
        ).fetchall()
        return [self._validated_row(row) for row in rows]

    def approve(
        self, changeset_id: object, *, expected_review_sha256: object = None
    ) -> dict[str, Any]:
        return self._set_status(
            changeset_id,
            expected={"staged"},
            status="approved",
            expected_review_sha256=expected_review_sha256,
        )

    def reject(
        self, changeset_id: object, *, expected_review_sha256: object = None
    ) -> dict[str, Any]:
        return self._set_status(
            changeset_id,
            expected={"staged", "approved"},
            status="rejected",
            expected_review_sha256=expected_review_sha256,
        )

    def _set_status(
        self,
        changeset_id: object,
        *,
        expected: set[str],
        status: str,
        expected_review_sha256: object,
    ) -> dict[str, Any]:
        record = self.get(changeset_id)
        self._verify_expected_review(record, expected_review_sha256)
        if record["status"] not in expected:
            raise invalid_state(f"Changeset cannot transition from {record['status']} to {status}")
        updated = {**record, "status": status, "updated_at": utc_now()}
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE changesets SET status = ?, record_json = ? "
                "WHERE changeset_id = ? AND status = ?",
                (status, encode_json(updated), record["changeset_id"], record["status"]),
            )
            if cursor.rowcount != 1:
                raise conflict("Changeset state changed concurrently")
            self.store.record_event(
                workspace_id=record["workspace_id"],
                topic=f"changeset.{status}",
                entity_type="changeset",
                entity_id=record["changeset_id"],
                payload={"previous_status": record["status"]},
                created_at=updated["updated_at"],
            )
        return updated

    @staticmethod
    def _verify_expected_review(record: dict[str, Any], expected_review_sha256: object) -> None:
        if record["format_version"] == 1:
            if expected_review_sha256 is not None:
                raise invalid_request("Legacy changesets do not have expected_review_sha256")
            return
        if (
            not isinstance(expected_review_sha256, str)
            or len(expected_review_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_review_sha256)
        ):
            raise invalid_request(
                "expected_review_sha256 must be the reviewed lowercase SHA-256 digest"
            )
        if not hmac.compare_digest(expected_review_sha256, record["review_sha256"]):
            raise conflict("Changeset review hash changed before the requested action")

    def apply(
        self, changeset_id: object, *, expected_review_sha256: object = None
    ) -> dict[str, Any]:
        record = self.get(changeset_id)
        self._verify_expected_review(record, expected_review_sha256)
        if record["status"] != "approved":
            raise invalid_state("Only an approved changeset can be applied")
        _workspace, world_root, expected_identity = _verified_world(
            self.store, record["workspace_id"]
        )
        journal_path = self.store.journals_dir / f"{record['changeset_id']}.json"
        if journal_path.exists() or journal_path.is_symlink():
            raise conflict("Changeset already has a pending apply journal")
        claimed = self._claim_apply(record)
        try:
            with exclusive_world_lifecycle(world_root, error_type=ValueError):
                if _identity(path_file_stat(world_root)) != expected_identity:
                    raise conflict("World root identity changed while applying changeset")
                journal = self._prepare_journal(claimed, world_root, expected_identity)
                with _pinned_operation_parents(journal, world_root) as parents:
                    try:
                        self._write_journal(journal_path, journal)
                        self._prepare_stages(journal_path, journal, parents)
                        _verify_pinned_parents(parents)
                        journal["state"] = "prepared"
                        self._write_journal(journal_path, journal)
                        for operation, parent in zip(journal["operations"], parents, strict=True):
                            parent.verify_visible()
                            self._apply_operation(operation, parent)
                            parent.verify_visible()
                            operation["applied"] = True
                            journal["state"] = "applying"
                            self._write_journal(journal_path, journal)
                        _verify_pinned_parents(parents)
                        journal["state"] = "files_committed"
                        self._write_journal(journal_path, journal)
                        _verify_pinned_parents(parents)
                        return self._finalize_committed(journal_path, journal, world_root, parents)
                    except Exception as exc:
                        if journal["state"] == "files_committed":
                            raise StudioError(
                                "internal_error",
                                "Changeset files committed and durable recovery is required",
                            ) from exc
                        try:
                            self._rollback_journal(journal, parents)
                            self._remove_journal(journal_path)
                        except Exception as rollback_exc:
                            raise StudioError(
                                "internal_error",
                                "Changeset apply failed and durable recovery is required",
                            ) from rollback_exc
                        self._restore_apply_claim(
                            claimed,
                            topic="changeset.apply_rolled_back",
                            payload={"journal_state": journal["state"]},
                        )
                        if isinstance(exc, StudioError):
                            raise exc
                        raise StudioError(
                            "internal_error", "Changeset apply failed and was rolled back"
                        ) from exc
        except StudioError:
            if not journal_path.exists() and not journal_path.is_symlink():
                self._restore_apply_claim(
                    claimed,
                    topic="changeset.apply_claim_released",
                    payload={"reason": "journal_not_published"},
                )
            raise
        except ValueError as exc:
            if not journal_path.exists() and not journal_path.is_symlink():
                self._restore_apply_claim(
                    claimed,
                    topic="changeset.apply_claim_released",
                    payload={"reason": "journal_not_published"},
                )
            raise conflict(str(exc)) from exc
        except Exception as exc:
            if not journal_path.exists() and not journal_path.is_symlink():
                self._restore_apply_claim(
                    claimed,
                    topic="changeset.apply_claim_released",
                    payload={"reason": "journal_not_published"},
                )
            raise StudioError("internal_error", "Changeset apply failed") from exc

    def _claim_apply(self, record: dict[str, Any]) -> dict[str, Any]:
        updated = {**record, "status": "applying", "updated_at": utc_now()}
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE changesets SET status = 'applying', record_json = ? "
                "WHERE changeset_id = ? AND status = 'approved'",
                (encode_json(updated), record["changeset_id"]),
            )
            if cursor.rowcount != 1:
                raise conflict("Changeset state changed before the apply claim")
            self.store.record_event(
                workspace_id=record["workspace_id"],
                topic="changeset.applying",
                entity_type="changeset",
                entity_id=record["changeset_id"],
                payload={"previous_status": "approved"},
                created_at=updated["updated_at"],
            )
        return updated

    def _restore_apply_claim(
        self,
        claimed: dict[str, Any],
        *,
        topic: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        current = self.get(claimed["changeset_id"])
        if current["status"] == "approved":
            return current
        if current["status"] != "applying":
            raise conflict("Changeset apply claim changed before safe release")
        updated = {**current, "status": "approved", "updated_at": utc_now()}
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE changesets SET status = 'approved', record_json = ? "
                "WHERE changeset_id = ? AND status = 'applying'",
                (encode_json(updated), current["changeset_id"]),
            )
            if cursor.rowcount != 1:
                raise conflict("Changeset apply claim changed before safe release")
            self.store.record_event(
                workspace_id=current["workspace_id"],
                topic=topic,
                entity_type="changeset",
                entity_id=current["changeset_id"],
                payload=payload,
                created_at=updated["updated_at"],
            )
        return updated

    def _prepare_journal(
        self,
        record: dict[str, Any],
        world_root: Path,
        world_identity: tuple[int, int],
    ) -> dict[str, Any]:
        operations: list[dict[str, Any]] = []
        for index, public in enumerate(record["operations"]):
            relative = _normalize_path(public["path"])
            target = _safe_target(world_root, relative)
            parent_info = _safe_directory_info(
                target.parent, context=f"changeset parent {relative.parent}"
            )
            info = _path_info(target)
            base_identity: tuple[int, int] | None = None
            if public["operation"] == "create":
                if info is not None:
                    raise conflict(f"Changeset base is no longer absent: {relative}")
            else:
                if info is None:
                    raise conflict(f"Changeset base is now absent: {relative}")
                payload, base_identity = _safe_file_snapshot(
                    target,
                    context=f"changeset base {relative}",
                    require_standalone=True,
                )
                if _hash(payload) != public["base_sha256"] or (
                    record["format_version"] == 2 and len(payload) != public["base_size"]
                ):
                    raise conflict(f"Changeset base hash changed: {relative}")
            operations.append(
                {
                    **public,
                    "parent_identity": list(_identity(parent_info)),
                    "base_identity": None if base_identity is None else list(base_identity),
                    "stage_name": (
                        None
                        if public["operation"] == "delete"
                        else (
                            f".worldforge-studio-{record['changeset_id']}-{index}-"
                            f"{uuid.uuid4().hex}.stage"
                        )
                    ),
                    "stage_identity": None,
                    "rollback_name": (
                        None
                        if public["operation"] == "create"
                        else (
                            f".worldforge-studio-{record['changeset_id']}-{index}-"
                            f"{uuid.uuid4().hex}.rollback"
                        )
                    ),
                    "applied": False,
                }
            )
        return {
            "format": JOURNAL_FORMAT,
            "format_version": JOURNAL_VERSION,
            "changeset_format_version": record["format_version"],
            "review_sha256": record.get("review_sha256"),
            "changeset_id": record["changeset_id"],
            "workspace_id": record["workspace_id"],
            "world_root": str(world_root),
            "world_identity": list(world_identity),
            "state": "preparing",
            "operations": operations,
        }

    def _prepare_stages(
        self,
        journal_path: Path,
        journal: dict[str, Any],
        parents: list[_PinnedParent],
    ) -> None:
        for operation, parent in zip(journal["operations"], parents, strict=True):
            if operation["stage_name"] is None:
                continue
            blob = self.store.blob_path(operation["proposed_sha256"])
            payload, _blob_identity = _safe_file_snapshot(
                blob, context="staged blob", require_standalone=True
            )
            if _hash(payload) != operation["proposed_sha256"] or len(payload) != operation["size"]:
                raise conflict("Staged changeset blob no longer matches its record")
            descriptor: int | None = None
            try:
                descriptor = parent.open_entry(
                    operation["stage_name"],
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = None
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                parent.flush()
                stage_info = parent.entry_info(operation["stage_name"])
                if stage_info is None or not stat.S_ISREG(stage_info.st_mode):
                    raise conflict("Changeset stage disappeared after creation")
                operation["stage_identity"] = list(_identity(stage_info))
                parent.verify_visible()
                self._write_journal(journal_path, journal)
            except Exception:
                if descriptor is not None:
                    os.close(descriptor)
                    descriptor = None
                raise
            finally:
                if descriptor is not None:
                    os.close(descriptor)

    def _apply_operation(self, operation: dict[str, Any], parent: _PinnedParent) -> None:
        target_name = _normalize_path(operation["path"]).name
        if operation["operation"] != "create":
            payload, identity = _safe_entry_snapshot(
                parent,
                target_name,
                context="changeset base",
                require_standalone=True,
            )
            if (
                list(identity) != operation["base_identity"]
                or _hash(payload) != operation["base_sha256"]
            ):
                raise conflict(f"Changeset base changed during apply: {operation['path']}")
            self._move_noreplace(
                parent,
                target_name,
                operation["rollback_name"],
                identity,
                operation["base_sha256"],
            )
        if operation["operation"] != "delete":
            self._publish_stage(
                parent,
                operation["stage_name"],
                target_name,
                tuple(operation["stage_identity"]),
                operation["proposed_sha256"],
            )

    def _move_noreplace(
        self,
        parent: _PinnedParent,
        source_name: str,
        destination_name: str,
        identity: tuple[int, int],
        digest: str,
    ) -> None:
        if parent.entry_info(destination_name) is not None:
            raise conflict("Changeset rollback reservation already exists")
        parent.link(source_name, destination_name)
        linked, linked_identity = _safe_entry_snapshot(
            parent,
            destination_name,
            context="changeset rollback",
            require_standalone=False,
        )
        current, current_identity = _safe_entry_snapshot(
            parent,
            source_name,
            context="changeset source",
            require_standalone=False,
        )
        if linked_identity != identity or current_identity != identity or _hash(linked) != digest:
            parent.unlink(destination_name)
            raise conflict("Changeset source identity changed while reserving rollback")
        if current != linked:
            parent.unlink(destination_name)
            raise conflict("Changeset source bytes changed while reserving rollback")
        parent.unlink(source_name)
        parent.flush()
        final, final_identity = _safe_entry_snapshot(
            parent,
            destination_name,
            context="changeset rollback",
            require_standalone=True,
        )
        if final_identity != identity or _hash(final) != digest:
            raise conflict("Changeset rollback identity changed after move")

    def _publish_stage(
        self,
        parent: _PinnedParent,
        stage_name: str,
        target_name: str,
        identity: tuple[int, int],
        digest: str,
    ) -> None:
        if parent.entry_info(target_name) is not None:
            raise conflict("Changeset publication target is no longer absent")
        parent.link(stage_name, target_name)
        published, published_identity = _safe_entry_snapshot(
            parent,
            target_name,
            context="published changeset file",
            require_standalone=False,
        )
        staged, staged_identity = _safe_entry_snapshot(
            parent,
            stage_name,
            context="changeset stage",
            require_standalone=False,
        )
        if (
            published_identity != identity
            or staged_identity != identity
            or _hash(published) != digest
            or staged != published
        ):
            if published_identity == identity:
                parent.unlink(target_name)
            raise conflict("Changeset stage identity changed during publication")
        parent.unlink(stage_name)
        parent.flush()
        final, final_identity = _safe_entry_snapshot(
            parent,
            target_name,
            context="published changeset file",
            require_standalone=True,
        )
        if final_identity != identity or _hash(final) != digest:
            raise conflict("Published changeset file changed after publication")

    def _rollback_journal(self, journal: dict[str, Any], parents: list[_PinnedParent]) -> None:
        pairs = list(zip(journal["operations"], parents, strict=True))
        for operation, parent in reversed(pairs):
            target_name = _normalize_path(operation["path"]).name
            stage_identity = (
                None if operation["stage_identity"] is None else tuple(operation["stage_identity"])
            )
            info = parent.entry_info(target_name)
            if info is not None:
                payload, target_identity = _safe_entry_snapshot(
                    parent,
                    target_name,
                    context="rollback target",
                    require_standalone=False,
                )
                if (
                    stage_identity is not None
                    and target_identity == stage_identity
                    and _hash(payload) == operation["proposed_sha256"]
                ):
                    parent.unlink(target_name)
                    parent.flush()
                elif (
                    operation["base_identity"] is not None
                    and list(target_identity) == operation["base_identity"]
                    and _hash(payload) == operation["base_sha256"]
                ):
                    pass
                else:
                    raise conflict("Rollback target contains unowned bytes")
            rollback_name = operation["rollback_name"]
            if rollback_name is not None:
                rollback_info = parent.entry_info(rollback_name)
                if rollback_info is not None:
                    payload, rollback_identity = _safe_entry_snapshot(
                        parent,
                        rollback_name,
                        context="rollback source",
                        require_standalone=False,
                    )
                    if (
                        list(rollback_identity) != operation["base_identity"]
                        or _hash(payload) != operation["base_sha256"]
                    ):
                        raise conflict("Rollback source identity changed")
                    if parent.entry_info(target_name) is not None:
                        current, current_identity = _safe_entry_snapshot(
                            parent,
                            target_name,
                            context="rollback target",
                            require_standalone=False,
                        )
                        if current_identity != rollback_identity or _hash(current) != _hash(
                            payload
                        ):
                            raise conflict("Rollback refuses to replace an existing target")
                        parent.unlink(rollback_name)
                        parent.flush()
                        _safe_entry_snapshot(
                            parent,
                            target_name,
                            context="restored rollback target",
                            require_standalone=True,
                        )
                    else:
                        self._publish_stage(
                            parent,
                            rollback_name,
                            target_name,
                            rollback_identity,
                            operation["base_sha256"],
                        )
                elif parent.entry_info(target_name) is None:
                    raise StudioError("internal_error", "Rollback source is missing")
            stage_name = operation["stage_name"]
            if stage_name is not None:
                if parent.entry_info(stage_name) is not None:
                    payload, current_identity = _safe_entry_snapshot(
                        parent,
                        stage_name,
                        context="changeset stage",
                        require_standalone=True,
                        require_utf8=False,
                    )
                    if (
                        _hash(payload) != operation["proposed_sha256"]
                        or len(payload) != operation["size"]
                        or (stage_identity is not None and current_identity != stage_identity)
                    ):
                        raise conflict("Changeset stage changed before cleanup")
                    parent.unlink(stage_name)
                    parent.flush()

    def _finalize_committed(
        self,
        journal_path: Path,
        journal: dict[str, Any],
        world_root: Path,
        parents: list[_PinnedParent],
    ) -> dict[str, Any]:
        self._validate_committed(journal, parents)
        _verify_pinned_parents(parents)
        record = self.get(journal["changeset_id"])
        if record["status"] == "applying":
            updated = {**record, "status": "applied", "updated_at": utc_now()}
            with self.store.connection:
                _verify_pinned_parents(parents)
                cursor = self.store.connection.execute(
                    "UPDATE changesets SET status = 'applied', record_json = ? "
                    "WHERE changeset_id = ? AND status = 'applying'",
                    (encode_json(updated), record["changeset_id"]),
                )
                if cursor.rowcount != 1:
                    raise conflict("Changeset state changed before commit recording")
                self.store.record_event(
                    workspace_id=record["workspace_id"],
                    topic="changeset.applied",
                    entity_type="changeset",
                    entity_id=record["changeset_id"],
                    payload={"recovered": False},
                    created_at=updated["updated_at"],
                )
                _verify_pinned_parents(parents)
        elif record["status"] == "applied":
            updated = record
        else:
            raise StudioError(
                "internal_error", "Committed journal has incompatible changeset state"
            )
        for operation, parent in zip(journal["operations"], parents, strict=True):
            rollback_name = operation["rollback_name"]
            if rollback_name is not None and parent.entry_info(rollback_name) is not None:
                payload, identity = _safe_entry_snapshot(
                    parent,
                    rollback_name,
                    context="committed rollback",
                    require_standalone=True,
                )
                if (
                    list(identity) != operation["base_identity"]
                    or _hash(payload) != operation["base_sha256"]
                ):
                    raise StudioError("internal_error", "Committed rollback changed before cleanup")
                parent.unlink(rollback_name)
                parent.flush()
        _verify_pinned_parents(parents)
        self._remove_journal(journal_path)
        return updated

    def _validate_committed(self, journal: dict[str, Any], parents: list[_PinnedParent]) -> None:
        for operation, parent in zip(journal["operations"], parents, strict=True):
            target_name = _normalize_path(operation["path"]).name
            if operation["operation"] == "delete":
                if parent.entry_info(target_name) is not None:
                    raise StudioError("internal_error", "Committed deletion target reappeared")
                continue
            payload, identity = _safe_entry_snapshot(
                parent,
                target_name,
                context="committed changeset target",
                require_standalone=True,
            )
            if (
                list(identity) != operation["stage_identity"]
                or _hash(payload) != operation["proposed_sha256"]
            ):
                raise StudioError("internal_error", "Committed changeset target changed")

    def recover_journals(self) -> None:
        for journal_path in sorted(self.store.journals_dir.glob("*.json")):
            try:
                journal = read_json_object(journal_path, limit=MAX_CHANGESET_BYTES)
            except AssetContractError as exc:
                raise StudioError("internal_error", "Studio apply journal is invalid") from exc
            record = self._journal_record(journal_path, journal)
            self._validate_recovery_state(record, journal)
            if journal["format_version"] == LEGACY_JOURNAL_VERSION:
                if record["status"] == "approved":
                    record = self._claim_apply(record)
                elif record["status"] not in {"applying", "applied"}:
                    raise StudioError(
                        "internal_error", "Legacy apply journal has incompatible changeset state"
                    )
            elif record["status"] not in {"applying", "applied"}:
                raise StudioError(
                    "internal_error", "Apply journal has incompatible changeset state"
                )
            workspace, world_root, expected = _verified_world(self.store, journal["workspace_id"])
            if journal["world_root"] != str(world_root) or journal["world_identity"] != list(
                expected
            ):
                raise StudioError("internal_error", "Studio apply journal world identity changed")
            try:
                with exclusive_world_lifecycle(world_root, error_type=ValueError):
                    with _pinned_operation_parents(journal, world_root) as parents:
                        if journal["state"] == "files_committed":
                            updated = self._finalize_committed(
                                journal_path, journal, world_root, parents
                            )
                            if updated["status"] == "applied":
                                with self.store.connection:
                                    self.store.record_event(
                                        workspace_id=workspace["workspace_id"],
                                        topic="changeset.recovered_commit",
                                        entity_type="changeset",
                                        entity_id=journal["changeset_id"],
                                        payload={},
                                    )
                        else:
                            self._rollback_journal(journal, parents)
                            self._remove_journal(journal_path)
                            self._restore_apply_claim(
                                record,
                                topic="changeset.recovered_rollback",
                                payload={"journal_state": journal["state"]},
                            )
            except StudioError:
                raise
            except ValueError as exc:
                raise StudioError(
                    "conflict", f"Could not recover Studio apply journal: {exc}"
                ) from exc
        self._recover_orphaned_apply_claims()

    @staticmethod
    def _validate_recovery_state(record: dict[str, Any], journal: dict[str, Any]) -> None:
        if record["status"] == "applied" and journal["state"] != "files_committed":
            raise StudioError(
                "internal_error", "Applied changeset has an incompatible journal state"
            )

    def _journal_record(self, journal_path: Path, journal: dict[str, Any]) -> dict[str, Any]:
        common_fields = {
            "format",
            "format_version",
            "changeset_id",
            "workspace_id",
            "world_root",
            "world_identity",
            "state",
            "operations",
        }
        version = journal.get("format_version")
        expected_fields = (
            common_fields
            if version == LEGACY_JOURNAL_VERSION
            else common_fields | {"changeset_format_version", "review_sha256"}
        )
        if (
            journal.get("format") != JOURNAL_FORMAT
            or version not in {LEGACY_JOURNAL_VERSION, JOURNAL_VERSION}
            or set(journal) != expected_fields
            or not isinstance(journal.get("changeset_id"), str)
            or not isinstance(journal.get("workspace_id"), str)
            or not isinstance(journal.get("world_root"), str)
            or not self._journal_identity(journal.get("world_identity"), nullable=False)
            or not isinstance(journal.get("operations"), list)
            or journal.get("state") not in {"preparing", "prepared", "applying", "files_committed"}
        ):
            raise StudioError("internal_error", "Studio apply journal has an unsupported shape")
        if journal_path.name != f"{journal['changeset_id']}.json":
            raise StudioError("internal_error", "Studio apply journal path is inconsistent")
        record = self.get(journal["changeset_id"])
        if record["workspace_id"] != journal["workspace_id"]:
            raise StudioError("internal_error", "Studio apply journal workspace is inconsistent")
        if version == LEGACY_JOURNAL_VERSION:
            if record["format_version"] != 1:
                raise StudioError(
                    "internal_error", "Legacy apply journal cannot identify this changeset"
                )
        elif journal["changeset_format_version"] != record["format_version"] or journal[
            "review_sha256"
        ] != record.get("review_sha256"):
            raise StudioError("internal_error", "Studio apply journal review identity changed")
        operations = journal["operations"]
        if len(operations) != len(record["operations"]):
            raise StudioError("internal_error", "Studio apply journal operations changed")
        private_fields = {
            "parent_identity",
            "base_identity",
            "stage_name",
            "stage_identity",
            "rollback_name",
            "applied",
        }
        for index, (item, public) in enumerate(zip(operations, record["operations"], strict=True)):
            if not isinstance(item, dict) or set(item) != set(public) | private_fields:
                raise StudioError("internal_error", "Studio apply journal operation shape changed")
            if any(item[key] != value for key, value in public.items()):
                raise StudioError(
                    "internal_error", "Studio apply journal operation identity changed"
                )
            if (
                not self._journal_identity(item["parent_identity"], nullable=False)
                or not self._journal_identity(item["base_identity"], nullable=True)
                or not self._journal_identity(item["stage_identity"], nullable=True)
                or not isinstance(item["applied"], bool)
                or not self._journal_temporary_name(
                    item["stage_name"], journal["changeset_id"], index, "stage"
                )
                or not self._journal_temporary_name(
                    item["rollback_name"], journal["changeset_id"], index, "rollback"
                )
            ):
                raise StudioError(
                    "internal_error", "Studio apply journal operation state is invalid"
                )
            kind = public["operation"]
            if (
                (kind == "create" and item["base_identity"] is not None)
                or (kind != "create" and item["base_identity"] is None)
                or (kind == "delete" and item["stage_name"] is not None)
                or (kind != "delete" and item["stage_name"] is None)
                or (kind == "create" and item["rollback_name"] is not None)
                or (kind != "create" and item["rollback_name"] is None)
            ):
                raise StudioError(
                    "internal_error", "Studio apply journal operation lifecycle is invalid"
                )
        return record

    @staticmethod
    def _journal_identity(value: object, *, nullable: bool) -> bool:
        if nullable and value is None:
            return True
        return (
            isinstance(value, list)
            and len(value) == 2
            and all(
                isinstance(part, int) and not isinstance(part, bool) and part >= 0 for part in value
            )
        )

    @staticmethod
    def _journal_temporary_name(value: object, changeset_id: str, index: int, suffix: str) -> bool:
        if value is None:
            return True
        if not isinstance(value, str):
            return False
        prefix = f".worldforge-studio-{changeset_id}-{index}-"
        ending = f".{suffix}"
        if not value.startswith(prefix) or not value.endswith(ending):
            return False
        nonce = value[len(prefix) : -len(ending)]
        return len(nonce) == 32 and all(character in "0123456789abcdef" for character in nonce)

    def _recover_orphaned_apply_claims(self) -> None:
        rows = self.store.connection.execute(
            "SELECT record_json FROM changesets WHERE status = 'applying' ORDER BY changeset_id"
        ).fetchall()
        for row in rows:
            record = self._validated_row(row)
            journal_path = self.store.journals_dir / f"{record['changeset_id']}.json"
            if journal_path.exists() or journal_path.is_symlink():
                raise StudioError(
                    "internal_error", "Applying changeset retained an unprocessed journal"
                )
            self._restore_apply_claim(
                record,
                topic="changeset.recovered_orphan_claim",
                payload={"reason": "journal_not_published"},
            )

    @staticmethod
    def _write_journal(path: Path, journal: dict[str, Any]) -> None:
        payload = encoded_json(journal)
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        descriptor: int | None = None
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            _replace_durable(temporary, path)
            _fsync_directory(path.parent)
        except OSError as exc:
            raise StudioError(
                "internal_error", f"Could not persist changeset journal: {exc}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _remove_journal(path: Path) -> None:
        if path.exists() or path.is_symlink():
            info = path_file_stat(path)
            if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise StudioError("internal_error", "Changeset journal identity is unsafe")
            path.unlink()
            _fsync_directory(path.parent)

    @staticmethod
    def _validated_row(row: sqlite3.Row) -> dict[str, Any]:
        record = decode_object(row["record_json"], context="changeset")
        try:
            return validate_studio_changeset(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored changeset is invalid") from exc
