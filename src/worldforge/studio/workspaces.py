from __future__ import annotations

import ctypes
import os
import sqlite3
import stat
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from isoworld.content.file_stat import (
    FileStat,
    _windows_handle_stat,
    descriptor_file_stat,
    path_file_stat,
)
from worldforge.repository_boundary import (
    repository_kind,
    require_standalone_bundle_root,
    require_standalone_game_root,
)
from worldforge.studio.contracts import WORKSPACE_ID_PATTERN, validate_forge_workspace
from worldforge.studio.errors import (
    StudioContractError,
    StudioError,
    conflict,
    invalid_request,
    not_found,
)
from worldforge.studio.storage import StudioStore, decode_object, encode_json, utc_now
from worldforge.world_lifecycle import inspect_world_project

_PARAM_FIELDS = frozenset({"workspace_id", "forge_root", "world_root", "game_root", "bundle_root"})
_ROOT_FIELDS = ("forge_root", "world_root", "game_root", "bundle_root")
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_POSIX_PINNED_ANCESTRY = os.name == "posix" and os.open in os.supports_dir_fd


def _plain_directory_identity(info: FileStat) -> tuple[int, int]:
    is_link = stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )
    if is_link or not stat.S_ISDIR(info.st_mode):
        raise OSError("entry is not a plain directory")
    return info.st_dev, info.st_ino


def _file_identity(info: FileStat, *, context: str) -> tuple[int, int]:
    try:
        return _plain_directory_identity(info)
    except OSError as exc:
        raise invalid_request(f"{context} must be a real directory") from exc


def _identity(path: Path, *, context: str) -> tuple[int, int]:
    try:
        info = path_file_stat(path)
    except (OSError, ValueError) as exc:
        raise invalid_request(f"Could not inspect {context}: {exc}") from exc
    return _file_identity(info, context=context)


def _resolved_root(value: object, *, context: str, required: bool) -> Path | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        raise invalid_request(f"{context} must be a non-empty path")
    if unicodedata.normalize("NFC", value) != value:
        raise invalid_request(f"{context} must be NFC normalized")
    supplied = Path(value)
    if supplied.is_symlink():
        raise invalid_request(f"{context} cannot be a symbolic link")
    resolved = supplied.resolve()
    if unicodedata.normalize("NFC", str(resolved)) != str(resolved):
        raise invalid_request(f"{context} resolves to a non-NFC path")
    _identity(resolved, context=context)
    return resolved


class _WindowsRelativeDirectoryApi:
    _FILE_LIST_DIRECTORY = 0x0001
    _FILE_READ_ATTRIBUTES = 0x0080
    _SYNCHRONIZE = 0x00100000
    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_OPEN = 0x00000001
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _OPEN_EXISTING = 3
    _OBJ_CASE_INSENSITIVE = 0x00000040

    def __init__(self) -> None:
        try:
            from ctypes import wintypes

            self.wintypes = wintypes
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.ntdll = ctypes.WinDLL("ntdll")
        except (AttributeError, ImportError, OSError) as exc:
            raise OSError("Windows relative directory API is unavailable") from exc

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", wintypes.LPWSTR),
            ]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [
                ("Length", wintypes.ULONG),
                ("RootDirectory", wintypes.HANDLE),
                ("ObjectName", ctypes.POINTER(UnicodeString)),
                ("Attributes", wintypes.ULONG),
                ("SecurityDescriptor", wintypes.LPVOID),
                ("SecurityQualityOfService", wintypes.LPVOID),
            ]

        class IoStatusBlock(ctypes.Structure):
            _fields_ = [
                ("Status", ctypes.c_void_p),
                ("Information", ctypes.c_size_t),
            ]

        self.UnicodeString = UnicodeString
        self.ObjectAttributes = ObjectAttributes
        self.IoStatusBlock = IoStatusBlock
        self.create_file = self.kernel32.CreateFileW
        self.create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.create_file.restype = wintypes.HANDLE
        self.close_handle = self.kernel32.CloseHandle
        self.close_handle.argtypes = [wintypes.HANDLE]
        self.close_handle.restype = wintypes.BOOL
        self.nt_create_file = self.ntdll.NtCreateFile
        self.nt_create_file.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.ULONG,
            ctypes.POINTER(ObjectAttributes),
            ctypes.POINTER(IoStatusBlock),
            wintypes.LPVOID,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.LPVOID,
            wintypes.ULONG,
        ]
        self.nt_create_file.restype = wintypes.LONG

    def state(self, handle: int, *, context: str, directory: bool) -> FileStat:
        info = _windows_handle_stat(handle)
        is_link = stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
        expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if is_link or not expected_type:
            kind = "directory" if directory else "regular file"
            raise OSError(f"{context} is not a plain {kind}")
        return info

    def open_anchor(self, anchor: Path, *, context: str) -> int:
        handle = self.create_file(
            str(anchor),
            self._FILE_LIST_DIRECTORY | self._FILE_READ_ATTRIBUTES | self._SYNCHRONIZE,
            self._FILE_SHARE_READ | self._FILE_SHARE_WRITE,
            None,
            self._OPEN_EXISTING,
            self._FILE_FLAG_BACKUP_SEMANTICS | self._FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        value = ctypes.cast(handle, ctypes.c_void_p).value
        if value in {None, ctypes.c_void_p(-1).value}:
            raise ctypes.WinError(ctypes.get_last_error())
        result = int(value)
        try:
            self.state(result, context=context, directory=True)
        except BaseException:
            self.close(result)
            raise
        return result

    def open_relative(
        self,
        parent: int,
        name: str,
        *,
        context: str,
        directory: bool,
    ) -> int:
        if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise OSError("relative entry name is invalid")
        try:
            encoded = name.encode("utf-16-le", errors="strict")
        except UnicodeError as exc:
            raise OSError("relative entry name is invalid") from exc
        if len(encoded) > 65_532:
            raise OSError("relative entry name is too long")
        buffer = ctypes.create_unicode_buffer(name)
        unicode_name = self.UnicodeString(
            len(encoded),
            len(encoded) + 2,
            ctypes.cast(buffer, self.wintypes.LPWSTR),
        )
        attributes = self.ObjectAttributes(
            ctypes.sizeof(self.ObjectAttributes),
            self.wintypes.HANDLE(parent),
            ctypes.pointer(unicode_name),
            self._OBJ_CASE_INSENSITIVE,
            None,
            None,
        )
        io_status = self.IoStatusBlock()
        output = self.wintypes.HANDLE()
        status_code = int(
            self.nt_create_file(
                ctypes.byref(output),
                (self._FILE_LIST_DIRECTORY if directory else self._GENERIC_READ)
                | self._FILE_READ_ATTRIBUTES
                | self._SYNCHRONIZE,
                ctypes.byref(attributes),
                ctypes.byref(io_status),
                None,
                0,
                self._FILE_SHARE_READ | self._FILE_SHARE_WRITE,
                self._FILE_OPEN,
                (self._FILE_DIRECTORY_FILE if directory else self._FILE_NON_DIRECTORY_FILE)
                | self._FILE_OPEN_REPARSE_POINT
                | self._FILE_SYNCHRONOUS_IO_NONALERT,
                None,
                0,
            )
        )
        if status_code < 0:
            raise OSError(f"Windows relative open failed: 0x{status_code & 0xFFFFFFFF:08x}")
        value = ctypes.cast(output, ctypes.c_void_p).value
        if value is None:
            raise OSError("Windows relative open returned no handle")
        result = int(value)
        try:
            self.state(result, context=context, directory=directory)
        except BaseException:
            self.close(result)
            raise
        return result

    def close(self, handle: int) -> None:
        if handle and not self.close_handle(self.wintypes.HANDLE(handle)):
            raise ctypes.WinError(ctypes.get_last_error())


def _close_descriptors(descriptors: list[int]) -> None:
    errors: list[OSError] = []
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError as exc:
            errors.append(exc)
    if errors:
        raise errors[0]


def _close_windows_handles(api: _WindowsRelativeDirectoryApi, handles: list[int]) -> None:
    errors: list[OSError] = []
    for handle in reversed(handles):
        try:
            api.close(handle)
        except OSError as exc:
            errors.append(exc)
    if errors:
        raise errors[0]


def _open_posix_ancestry(
    path: Path,
    *,
    context: str,
) -> tuple[list[int], tuple[tuple[int, int], ...]]:
    descriptors: list[int] = []
    identities: list[tuple[int, int]] = []
    try:
        descriptor = os.open(Path(path.anchor), _DIRECTORY_OPEN_FLAGS)
        descriptors.append(descriptor)
        identities.append(_plain_directory_identity(descriptor_file_stat(descriptor)))
        for part in path.parts[1:]:
            descriptor = os.open(
                part,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=descriptors[-1],
            )
            descriptors.append(descriptor)
            identities.append(_plain_directory_identity(descriptor_file_stat(descriptor)))
        return descriptors, tuple(identities)
    except BaseException:
        _close_descriptors(descriptors)
        raise


def _open_windows_ancestry(
    api: _WindowsRelativeDirectoryApi,
    path: Path,
    *,
    context: str,
) -> tuple[list[int], tuple[tuple[int, int], ...]]:
    handles: list[int] = []
    identities: list[tuple[int, int]] = []
    try:
        handle = api.open_anchor(Path(path.anchor), context=f"{context} component {path.anchor}")
        handles.append(handle)
        identities.append(
            _plain_directory_identity(
                api.state(handle, context=f"{context} component {path.anchor}", directory=True)
            )
        )
        for part in path.parts[1:]:
            handle = api.open_relative(
                handles[-1],
                part,
                context=f"{context} component {part}",
                directory=True,
            )
            handles.append(handle)
            identities.append(
                _plain_directory_identity(
                    api.state(handle, context=f"{context} component {part}", directory=True)
                )
            )
        return handles, tuple(identities)
    except BaseException:
        _close_windows_handles(api, handles)
        raise


@contextmanager
def _pinned_ancestor_identities(
    path: Path,
    *,
    context: str,
) -> Iterator[tuple[tuple[int, int], ...]]:
    """Pin one absolute directory chain root-to-leaf without following links."""

    if "\x00" in str(path) or not path.is_absolute():
        raise invalid_request(f"{context} must be an absolute safe directory")
    if os.name == "posix":
        if not _POSIX_PINNED_ANCESTRY:
            raise invalid_request("Secure workspace ancestry inspection is unavailable")
        descriptors: list[int] = []
        try:
            descriptors, identities = _open_posix_ancestry(path, context=context)
            yield identities
            verification, visible_identities = _open_posix_ancestry(path, context=context)
            try:
                if visible_identities != identities:
                    raise invalid_request(f"{context} identity changed while being pinned")
            finally:
                _close_descriptors(verification)
        except StudioError:
            raise
        except (OSError, ValueError) as exc:
            raise invalid_request(f"Could not inspect {context}: {exc}") from exc
        finally:
            _close_descriptors(descriptors)
        return
    if os.name != "nt":
        raise invalid_request("Secure workspace ancestry inspection is unsupported")

    handles: list[int] = []
    api: _WindowsRelativeDirectoryApi | None = None
    try:
        api = _WindowsRelativeDirectoryApi()
        handles, identities = _open_windows_ancestry(api, path, context=context)
        yield identities
        verification, visible_identities = _open_windows_ancestry(api, path, context=context)
        try:
            if visible_identities != identities:
                raise invalid_request(f"{context} identity changed while being pinned")
        finally:
            _close_windows_handles(api, verification)
    except StudioError:
        raise
    except (OSError, ValueError) as exc:
        raise invalid_request(f"Could not inspect {context}: {exc}") from exc
    finally:
        if api is not None:
            try:
                _close_windows_handles(api, handles)
            except OSError as exc:
                raise StudioError(
                    "internal_error", "Could not release pinned workspace ancestry"
                ) from exc


def _overlaps(left: Path, right: Path) -> bool:
    """Compare existing directory ancestry by filesystem identity, not path spelling."""

    with _pinned_ancestor_identities(left, context=f"workspace boundary {left}") as left_identities:
        with _pinned_ancestor_identities(
            right, context=f"workspace boundary {right}"
        ) as right_identities:
            return (
                left_identities[-1] in right_identities or right_identities[-1] in left_identities
            )


class WorkspaceManager:
    def __init__(self, store: StudioStore) -> None:
        self.store = store

    def register(self, params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("workspace.register params must be an object")
        unknown = set(params) - _PARAM_FIELDS
        missing = {"forge_root", "world_root"} - set(params)
        if unknown:
            raise invalid_request(
                f"workspace.register contains unknown fields: {', '.join(sorted(unknown))}"
            )
        if missing:
            raise invalid_request(
                f"workspace.register is missing fields: {', '.join(sorted(missing))}"
            )
        workspace_id = params.get("workspace_id") or f"workspace_{uuid.uuid4().hex}"
        if (
            not isinstance(workspace_id, str)
            or WORKSPACE_ID_PATTERN.fullmatch(workspace_id) is None
        ):
            raise invalid_request("workspace_id is not a valid identifier")

        roots: dict[str, Path | None] = {
            "forge_root": _resolved_root(
                params.get("forge_root"), context="Forge root", required=True
            ),
            "world_root": _resolved_root(
                params.get("world_root"), context="world root", required=True
            ),
            "game_root": _resolved_root(
                params.get("game_root"), context="game root", required=False
            ),
            "bundle_root": _resolved_root(
                params.get("bundle_root"), context="bundle root", required=False
            ),
        }
        present = [(field, path) for field, path in roots.items() if path is not None]
        for field, path in present:
            assert path is not None
            if _overlaps(self.store.data_dir, path):
                raise invalid_request(
                    f"Studio data directory must remain outside the {field.replace('_', ' ')}"
                )
        folded: dict[str, tuple[str, Path]] = {}
        identities: dict[str, tuple[int, int]] = {}
        for field, path in present:
            assert path is not None
            folded_path = unicodedata.normalize("NFC", os.path.normcase(str(path))).casefold()
            prior = folded.setdefault(folded_path, (field, path))
            if prior[0] != field:
                raise invalid_request(
                    f"Workspace roots have a casefold collision: {prior[0]}, {field}"
                )
            identities[field] = _identity(path, context=field.replace("_", " "))
        for index, (left_field, left) in enumerate(present):
            for right_field, right in present[index + 1 :]:
                assert left is not None and right is not None
                if identities[left_field] == identities[right_field] or _overlaps(left, right):
                    raise invalid_request(
                        f"Workspace roots overlap or share an identity: {left_field}, {right_field}"
                    )

        forge_root = roots["forge_root"]
        world_root = roots["world_root"]
        assert forge_root is not None and world_root is not None
        if repository_kind(forge_root) != "forge":
            raise invalid_request("Forge root is not the RPG World Forge repository")
        try:
            inspect_world_project(world_root)
        except ValueError as exc:
            raise invalid_request(f"World root is not a canonical world repository: {exc}") from exc
        if roots["game_root"] is not None:
            try:
                roots["game_root"] = require_standalone_game_root(roots["game_root"])
            except ValueError as exc:
                raise invalid_request(str(exc)) from exc
        if roots["bundle_root"] is not None:
            try:
                roots["bundle_root"] = require_standalone_bundle_root(roots["bundle_root"])
            except ValueError as exc:
                raise invalid_request(str(exc)) from exc

        self._reject_registered_collisions(workspace_id, roots, identities)
        record = {
            "format": "rpg-world-forge.forge_workspace",
            "format_version": 1,
            "workspace_id": workspace_id,
            **{
                field: None if roots[field] is None else str(roots[field]) for field in _ROOT_FIELDS
            },
            "created_at": utc_now(),
        }
        try:
            validate_forge_workspace(record)
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        game_identity = identities.get("game_root")
        bundle_identity = identities.get("bundle_root")
        try:
            with self.store.connection:
                self.store.connection.execute(
                    "INSERT INTO workspaces "
                    "(workspace_id, record_json, forge_dev, forge_ino, world_dev, world_ino, "
                    "game_dev, game_ino, bundle_dev, bundle_ino) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        workspace_id,
                        encode_json(record),
                        *(str(value) for value in identities["forge_root"]),
                        *(str(value) for value in identities["world_root"]),
                        *(
                            (None, None)
                            if game_identity is None
                            else tuple(str(value) for value in game_identity)
                        ),
                        *(
                            (None, None)
                            if bundle_identity is None
                            else tuple(str(value) for value in bundle_identity)
                        ),
                    ),
                )
                self.store.record_event(
                    workspace_id=workspace_id,
                    topic="workspace.registered",
                    entity_type="workspace",
                    entity_id=workspace_id,
                    payload={},
                )
        except sqlite3.IntegrityError as exc:
            raise conflict(f"Workspace {workspace_id} is already registered") from exc
        return record

    def _reject_registered_collisions(
        self,
        workspace_id: str,
        roots: dict[str, Path | None],
        identities: dict[str, tuple[int, int]],
    ) -> None:
        rows = self.store.connection.execute("SELECT * FROM workspaces").fetchall()
        for row in rows:
            if row["workspace_id"] == workspace_id:
                raise conflict(f"Workspace {workspace_id} is already registered")
            existing = decode_object(row["record_json"], context="workspace")
            for field in ("world_root", "game_root", "bundle_root"):
                candidate = roots[field]
                if candidate is None:
                    continue
                candidate_identity = identities[field]
                for existing_field in ("world_root", "game_root", "bundle_root"):
                    existing_value = existing.get(existing_field)
                    if existing_value is None:
                        continue
                    existing_path = Path(existing_value)
                    existing_identity = (
                        int(row[f"{existing_field.removesuffix('_root')}_dev"]),
                        int(row[f"{existing_field.removesuffix('_root')}_ino"]),
                    )
                    same_fold = str(candidate).casefold() == str(existing_path).casefold()
                    if candidate_identity == existing_identity or same_fold:
                        raise conflict(
                            "Repository root is already registered by workspace "
                            f"{row['workspace_id']}"
                        )
                    if _overlaps(candidate, existing_path):
                        raise conflict(f"Repository root overlaps workspace {row['workspace_id']}")

    def get(self, workspace_id: object) -> dict[str, Any]:
        if not isinstance(workspace_id, str):
            raise invalid_request("workspace_id must be a string")
        row = self.store.connection.execute(
            "SELECT record_json FROM workspaces WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        if row is None:
            raise not_found(f"Workspace {workspace_id} was not found")
        record = decode_object(row["record_json"], context="workspace")
        try:
            return validate_forge_workspace(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored workspace is invalid") from exc

    def list(self) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT record_json FROM workspaces ORDER BY workspace_id"
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            record = decode_object(row["record_json"], context="workspace")
            try:
                result.append(validate_forge_workspace(record))
            except StudioContractError as exc:
                raise StudioError("internal_error", "Stored workspace is invalid") from exc
        return result

    def root_identity(self, workspace_id: str, field: str) -> tuple[int, int] | None:
        if field not in _ROOT_FIELDS:
            raise ValueError(field)
        column = field.removesuffix("_root")
        row = self.store.connection.execute(
            f"SELECT {column}_dev, {column}_ino FROM workspaces WHERE workspace_id = ?",  # noqa: S608
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise not_found(f"Workspace {workspace_id} was not found")
        dev, ino = row[0], row[1]
        return None if dev is None or ino is None else (int(dev), int(ino))

    def verified_root(
        self, workspace_id: object, field: str
    ) -> tuple[Path, tuple[int, int]] | None:
        """Return a registered root only while its non-followed identity remains intact."""

        if field not in _ROOT_FIELDS:
            raise ValueError(field)
        workspace = self.get(workspace_id)
        value = workspace[field]
        expected = self.root_identity(workspace["workspace_id"], field)
        if value is None:
            if expected is not None:
                raise StudioError("internal_error", "Stored workspace root identity is invalid")
            return None
        if expected is None:
            raise StudioError("internal_error", "Stored workspace root identity is missing")
        try:
            info = path_file_stat(Path(value))
        except OSError as exc:
            raise conflict(f"Registered {field.replace('_', ' ')} is unavailable") from exc
        is_link = stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        )
        if is_link or not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != expected:
            raise conflict(f"Registered {field.replace('_', ' ')} identity changed")
        return Path(value), expected
