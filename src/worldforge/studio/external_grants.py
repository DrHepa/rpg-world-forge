from __future__ import annotations

import os
import sqlite3
import stat
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from isoworld.content.file_stat import FileStat, path_file_stat
from worldforge.studio.contracts import (
    ENTITY_ID_PATTERN,
    EXTERNAL_GRANT_FORMAT,
    EXTERNAL_GRANT_STATES,
    EXTERNAL_JOB_OPERATIONS,
    EXTERNAL_OPERATION_KINDS,
    SHA256_PATTERN,
    WORKSPACE_ID_PATTERN,
    validate_studio_external_grant,
)
from worldforge.studio.errors import (
    StudioContractError,
    StudioError,
    conflict,
    invalid_request,
    invalid_state,
    not_found,
)
from worldforge.studio.storage import StudioStore, decode_object, encode_json, utc_now
from worldforge.studio.workspaces import (
    WorkspaceManager,
    _pinned_ancestor_identities,
)

_SOURCE_DIRECTORY_KINDS = frozenset({"game_materialization_bundle", "standalone_game"})
_SOURCE_FILE_KINDS = frozenset({"game_package"})
_MAX_DISPLAY_NAME = 128


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _identity(info: FileStat) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _normalized_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise invalid_request("External artifact path is invalid")
    if unicodedata.normalize("NFC", value) != value:
        raise invalid_request("External artifact path must be NFC normalized")
    path = Path(value)
    absolute = Path(os.path.abspath(path))
    if not path.is_absolute() or str(path) != str(absolute):
        raise invalid_request("External artifact path must be absolute and normalized")
    return absolute


def _display_name(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_DISPLAY_NAME:
        raise invalid_request("External grant display name is invalid")
    if (
        unicodedata.normalize("NFC", value) != value
        or any(character in value for character in "/\\\x00\r\n")
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise invalid_request("External grant display name is invalid")
    return value


def _identifier(value: object, *, field: str, pattern: object) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:  # type: ignore[attr-defined]
        raise invalid_request(f"{field} is not a valid identifier")
    return value


def _expected_hash(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise invalid_request("External grant expected_content_hash is invalid")
    return value


def _path_key(path: Path) -> tuple[str, ...]:
    return tuple(
        unicodedata.normalize("NFC", os.path.normcase(part)).casefold() for part in path.parts
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    left_key = _path_key(left)
    right_key = _path_key(right)
    common = min(len(left_key), len(right_key))
    return left_key[:common] == right_key[:common]


def _safe_leaf(path: Path) -> str:
    leaf = path.name
    if (
        not leaf
        or leaf in {".", ".."}
        or unicodedata.normalize("NFC", leaf) != leaf
        or leaf[-1:] in {" ", "."}
        or any(character in leaf for character in "/\\\x00\r\n")
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in leaf)
    ):
        raise invalid_request("External target name is not portable")
    return leaf


def _pin_directory(path: Path, *, context: str) -> tuple[int, int]:
    try:
        with _pinned_ancestor_identities(path, context=context) as identities:
            return identities[-1]
    except StudioError:
        raise invalid_request(f"{context} is not a safe directory") from None


def _plain_source(path: Path, artifact_kind: str) -> tuple[int, int]:
    parent_identity = _pin_directory(path.parent, context="External source parent")
    del parent_identity
    try:
        info = path_file_stat(path)
    except (OSError, ValueError):
        raise invalid_request("External source is unavailable") from None
    if _is_link_or_reparse(info):
        raise invalid_request("External source cannot be a link or reparse point")
    if artifact_kind in _SOURCE_DIRECTORY_KINDS:
        if not stat.S_ISDIR(info.st_mode):
            raise invalid_request("External source must be a plain directory")
        pinned = _pin_directory(path, context="External source")
        if pinned != _identity(info):
            raise conflict("External source identity changed during registration")
    elif artifact_kind in _SOURCE_FILE_KINDS:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise invalid_request("External source must be a standalone regular file")
    else:  # pragma: no cover - contract validation closes kinds
        raise invalid_request("External source kind is unsupported")
    try:
        after = path_file_stat(path)
    except OSError:
        raise conflict("External source identity changed during registration") from None
    if _identity(after) != _identity(info) or after.st_mode != info.st_mode:
        raise conflict("External source identity changed during registration")
    return _identity(info)


def _absent_target(path: Path) -> tuple[tuple[int, int], str]:
    leaf = _safe_leaf(path)
    parent_identity = _pin_directory(path.parent, context="External target parent")
    try:
        with os.scandir(path.parent) as entries:
            matches = [
                entry.name
                for entry in entries
                if unicodedata.normalize("NFC", entry.name).casefold()
                == unicodedata.normalize("NFC", leaf).casefold()
            ]
    except OSError:
        raise invalid_request("External target parent is unavailable") from None
    if matches:
        raise invalid_request("External target already exists or has an NFC/casefold collision")
    if path.exists() or path.is_symlink():
        raise invalid_request("External target already exists")
    if _pin_directory(path.parent, context="External target parent") != parent_identity:
        raise conflict("External target parent identity changed during registration")
    return parent_identity, leaf


class ExternalGrantManager:
    """Private native path authority with pathless public grant records."""

    def __init__(self, store: StudioStore) -> None:
        self.store = store

    def create(self, params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("external_grant.create params must be an object")
        allowed = {
            "grant_id",
            "workspace_id",
            "operation",
            "role",
            "artifact_kind",
            "display_name",
            "path",
            "expected_content_hash",
        }
        required = allowed - {"grant_id"}
        missing = required - set(params)
        unknown = set(params) - allowed
        if missing or unknown:
            fields = missing or unknown
            raise invalid_request(
                "external_grant.create has invalid fields: " + ", ".join(sorted(fields))
            )
        workspace_id = _identifier(
            params["workspace_id"],
            field="workspace_id",
            pattern=WORKSPACE_ID_PATTERN,
        )
        operation = params["operation"]
        if not isinstance(operation, str) or operation not in EXTERNAL_JOB_OPERATIONS:
            raise invalid_request("External grant operation is unknown")
        role = params["role"]
        if role not in {"source", "target"}:
            raise invalid_request("External grant role is unknown")
        artifact_kind = params["artifact_kind"]
        if artifact_kind != EXTERNAL_OPERATION_KINDS[operation][role]:
            raise invalid_request("External grant artifact kind is invalid")
        path = _normalized_path(params["path"])
        timestamp = utc_now()
        grant_id = params.get("grant_id") or f"grant_{uuid.uuid4().hex}"
        grant_id = _identifier(
            grant_id,
            field="grant_id",
            pattern=ENTITY_ID_PATTERN,
        )
        record = {
            "format": EXTERNAL_GRANT_FORMAT,
            "format_version": 1,
            "grant_id": grant_id,
            "workspace_id": workspace_id,
            "operation": operation,
            "role": role,
            "artifact_kind": artifact_kind,
            "display_name": _display_name(params["display_name"]),
            "state": "ready",
            "expected_content_hash": _expected_hash(params["expected_content_hash"]),
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        try:
            validate_studio_external_grant(record)
            self.store.connection.execute("BEGIN IMMEDIATE")
            workspace = WorkspaceManager(self.store).get(workspace_id)
            self._reject_unsafe_overlap(path, role=role, workspace=workspace)
            self._reject_active_grant_overlap(path)
            source_identity: tuple[int, int] | None = None
            parent_identity: tuple[int, int] | None = None
            normalized_leaf: str | None = None
            if role == "source":
                source_identity = _plain_source(path, artifact_kind)
            else:
                parent_identity, normalized_leaf = _absent_target(path)
            self.store.connection.execute(
                "INSERT INTO external_grants "
                "(grant_id, workspace_id, operation, role, artifact_kind, state, "
                "record_json, absolute_path, source_dev, source_ino, parent_dev, "
                "parent_ino, normalized_leaf, reserved_job_id, generation) "
                "VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, NULL, 0)",
                (
                    grant_id,
                    workspace_id,
                    operation,
                    role,
                    artifact_kind,
                    encode_json(record),
                    str(path),
                    None if source_identity is None else str(source_identity[0]),
                    None if source_identity is None else str(source_identity[1]),
                    None if parent_identity is None else str(parent_identity[0]),
                    None if parent_identity is None else str(parent_identity[1]),
                    normalized_leaf,
                ),
            )
            self.store.record_event(
                workspace_id=workspace_id,
                topic="external_grant.created",
                entity_type="external_grant",
                entity_id=grant_id,
                payload={
                    "operation": operation,
                    "role": role,
                    "artifact_kind": artifact_kind,
                    "state": "ready",
                },
                created_at=timestamp,
            )
            self.store.connection.commit()
        except sqlite3.IntegrityError as exc:
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            raise invalid_request(f"External grant {grant_id} already exists") from exc
        except Exception:
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            raise
        return record

    def get(self, grant_id: object) -> dict[str, Any]:
        if not isinstance(grant_id, str):
            raise invalid_request("grant_id must be a string")
        row = self.store.connection.execute(
            "SELECT record_json FROM external_grants WHERE grant_id = ?",
            (grant_id,),
        ).fetchone()
        if row is None:
            raise not_found(f"External grant {grant_id} was not found")
        record = decode_object(row["record_json"], context="external grant")
        try:
            return validate_studio_external_grant(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored external grant is invalid") from exc

    def revoke(self, grant_id: object) -> dict[str, Any]:
        record = self.get(grant_id)
        if record["state"] == "revoked":
            return record
        if record["state"] != "ready":
            raise invalid_state("Only a ready external grant may be revoked")
        return self._set_public_state(record["grant_id"], "revoked")

    def reserve_for_job(
        self,
        *,
        job_id: str,
        workspace_id: str,
        operation: str,
        job_input: dict[str, Any],
    ) -> None:
        source_id = job_input["source_grant_id"]
        target_id = job_input["target_grant_id"]
        rows = {
            row["grant_id"]: row
            for row in self.store.connection.execute(
                "SELECT * FROM external_grants WHERE grant_id IN (?, ?)",
                (source_id, target_id),
            )
        }
        if set(rows) != {source_id, target_id}:
            raise invalid_request("External job grants are unavailable")
        source = self._validated_row(rows[source_id])
        target = self._validated_row(rows[target_id])
        if (
            source["workspace_id"] != workspace_id
            or target["workspace_id"] != workspace_id
            or source["operation"] != operation
            or target["operation"] != operation
            or source["role"] != "source"
            or target["role"] != "target"
        ):
            raise invalid_request("External job grants do not match the requested operation")
        hash_field = {
            "game.materialize": "expected_materialization_hash",
            "game.package": "expected_game_hash",
            "game.package.extract": "expected_package_hash",
        }[operation]
        if source["expected_content_hash"] != job_input[hash_field]:
            raise invalid_request("External source grant hash does not match the job")
        if source["state"] != "ready":
            raise invalid_state("External source grant is not ready")
        if target["state"] != "ready" or rows[target_id]["reserved_job_id"] is not None:
            raise invalid_state("External target grant is already reserved")
        timestamp = utc_now()
        target["state"] = "reserved"
        target["updated_at"] = timestamp
        validate_studio_external_grant(target)
        cursor = self.store.connection.execute(
            "UPDATE external_grants SET state = 'reserved', record_json = ?, "
            "reserved_job_id = ?, generation = generation + 1 "
            "WHERE grant_id = ? AND state = 'ready' AND reserved_job_id IS NULL",
            (encode_json(target), job_id, target_id),
        )
        if cursor.rowcount != 1:
            raise invalid_state("External target grant is already reserved")

    def binding_for_job(self, job: dict[str, Any]) -> dict[str, Any]:
        source_id = job["input"]["source_grant_id"]
        target_id = job["input"]["target_grant_id"]
        rows = {
            row["grant_id"]: row
            for row in self.store.connection.execute(
                "SELECT * FROM external_grants WHERE grant_id IN (?, ?)",
                (source_id, target_id),
            )
        }
        if set(rows) != {source_id, target_id}:
            raise conflict("External job grants are unavailable")
        source = self._validated_row(rows[source_id])
        target = self._validated_row(rows[target_id])
        if (
            source["workspace_id"] != job["workspace_id"]
            or target["workspace_id"] != job["workspace_id"]
            or source["operation"] != job["operation"]
            or target["operation"] != job["operation"]
            or rows[target_id]["reserved_job_id"] != job["job_id"]
            or target["state"] not in {"reserved", "recovery_required"}
        ):
            raise conflict("External job grant reservation changed")
        source_path = Path(rows[source_id]["absolute_path"])
        target_path = Path(rows[target_id]["absolute_path"])
        expected_source = (
            int(rows[source_id]["source_dev"]),
            int(rows[source_id]["source_ino"]),
        )
        try:
            current_source = _plain_source(source_path, source["artifact_kind"])
        except StudioError:
            raise conflict("External source identity changed") from None
        if current_source != expected_source:
            raise conflict("External source identity changed")
        expected_parent = (
            int(rows[target_id]["parent_dev"]),
            int(rows[target_id]["parent_ino"]),
        )
        try:
            current_parent = _pin_directory(
                target_path.parent,
                context="External target parent",
            )
        except StudioError:
            raise conflict("External target parent identity changed") from None
        if (
            current_parent != expected_parent
            or target_path.name != rows[target_id]["normalized_leaf"]
        ):
            raise conflict("External target parent identity changed")
        return {
            "source_path": source_path,
            "target_path": target_path,
            "source_identity": expected_source,
            "parent_identity": expected_parent,
            "generation": int(rows[target_id]["generation"]),
            "source_grant": source,
            "target_grant": target,
        }

    def set_target_state_for_job(self, job_id: str, state: str) -> dict[str, Any]:
        if state not in EXTERNAL_GRANT_STATES:
            raise ValueError(state)
        row = self.store.connection.execute(
            "SELECT grant_id FROM external_grants WHERE reserved_job_id = ? AND role = 'target'",
            (job_id,),
        ).fetchone()
        if row is None:
            raise StudioError("internal_error", "External target reservation is unavailable")
        return self._set_public_state(row["grant_id"], state)

    def release_target_for_job(self, job_id: str) -> dict[str, Any]:
        row = self.store.connection.execute(
            "SELECT grant_id, record_json FROM external_grants "
            "WHERE reserved_job_id = ? AND role = 'target'",
            (job_id,),
        ).fetchone()
        if row is None:
            raise StudioError("internal_error", "External target reservation is unavailable")
        record = decode_object(row["record_json"], context="external grant")
        try:
            validate_studio_external_grant(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored external grant is invalid") from exc
        record["state"] = "ready"
        record["updated_at"] = utc_now()
        validate_studio_external_grant(record)
        self.store.connection.execute(
            "UPDATE external_grants SET state = 'ready', record_json = ?, "
            "reserved_job_id = NULL WHERE grant_id = ? AND reserved_job_id = ?",
            (encode_json(record), row["grant_id"], job_id),
        )
        return record

    def consume_source_for_job(self, job: dict[str, Any]) -> dict[str, Any]:
        return self._set_public_state(job["input"]["source_grant_id"], "consumed")

    def _set_public_state(self, grant_id: str, state: str) -> dict[str, Any]:
        record = self.get(grant_id)
        record["state"] = state
        record["updated_at"] = utc_now()
        validate_studio_external_grant(record)
        self.store.connection.execute(
            "UPDATE external_grants SET state = ?, record_json = ? WHERE grant_id = ?",
            (state, encode_json(record), grant_id),
        )
        return record

    def _reject_unsafe_overlap(
        self,
        path: Path,
        *,
        role: str,
        workspace: dict[str, Any],
    ) -> None:
        candidate = path if role == "source" else path
        unsafe = [self.store.data_dir]
        for field in ("forge_root", "world_root", "game_root", "bundle_root"):
            value = workspace[field]
            if value is not None:
                unsafe.append(Path(value))
        rows = self.store.connection.execute("SELECT record_json FROM workspaces").fetchall()
        for row in rows:
            existing = decode_object(row["record_json"], context="workspace")
            for field in ("forge_root", "world_root", "game_root", "bundle_root"):
                value = existing.get(field)
                if isinstance(value, str):
                    unsafe.append(Path(value))
        if any(_paths_overlap(candidate, root) for root in unsafe):
            raise invalid_request("External artifact path overlaps an unsafe root")

    def _reject_active_grant_overlap(self, path: Path) -> None:
        rows = self.store.connection.execute(
            "SELECT absolute_path FROM external_grants "
            "WHERE state IN ('ready', 'reserved', 'recovery_required') "
            "ORDER BY grant_id"
        )
        for row in rows:
            if _paths_overlap(path, Path(row["absolute_path"])):
                raise invalid_request("External artifact path overlaps active external authority")

    @staticmethod
    def _validated_row(row: sqlite3.Row) -> dict[str, Any]:
        record = decode_object(row["record_json"], context="external grant")
        try:
            return validate_studio_external_grant(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored external grant is invalid") from exc
