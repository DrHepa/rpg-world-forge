from __future__ import annotations

import hmac
import os
import sqlite3
import stat
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from isoworld.content.file_stat import FileStat, path_file_stat
from isoworld.content.portability import portable_relative_path
from worldforge.creation_contracts import CreationContractError, load_creation_project
from worldforge.creation_route import CreationRouteError, route_creation_project
from worldforge.phase_report_v3 import document_identity
from worldforge.repository_boundary import FORGE_ROOT
from worldforge.studio.contracts import (
    CREATION_ROOT_GRANT_FORMAT,
    ENTITY_ID_PATTERN,
    SHA256_PATTERN,
    validate_studio_creation_root_grant,
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
from worldforge.studio.workspaces import _overlaps, _pinned_ancestor_identities

_MAX_DISPLAY_NAME = 128
_ACTIVE_STATES = frozenset({"ready", "reserved", "recovery_required"})


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _identity(info: FileStat) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _normalized_absolute_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise invalid_request("Creation root path is invalid")
    if unicodedata.normalize("NFC", value) != value:
        raise invalid_request("Creation root path must be NFC normalized")
    supplied = Path(value)
    absolute = Path(os.path.abspath(supplied))
    if not supplied.is_absolute() or str(supplied) != str(absolute):
        raise invalid_request("Creation root path must be absolute and normalized")
    if supplied.is_symlink():
        raise invalid_request(
            "Creation project root contains a symbolic link or reparse point",
            reason_code="creation_project_root_linked",
        )
    return absolute


def _display_name(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_DISPLAY_NAME:
        raise invalid_request("Creation root grant display name is invalid")
    if (
        unicodedata.normalize("NFC", value) != value
        or any(character in value for character in "/\\\x00\r\n")
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise invalid_request("Creation root grant display name is invalid")
    return value


def _expected_hash(value: object, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise invalid_request("expected_project_hash must be a lowercase SHA-256 digest")
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


def _pinned_directory(path: Path, *, context: str) -> tuple[int, int]:
    try:
        with _pinned_ancestor_identities(path, context=context) as identities:
            return identities[-1]
    except StudioError:
        raise invalid_request(f"{context} is not a safe directory") from None


def _safe_target_leaf(path: Path) -> tuple[tuple[int, int], str]:
    leaf = path.name
    if portable_relative_path(leaf) is None:
        raise invalid_request("Creation target name is not portable")
    parent_identity = _pinned_directory(path.parent, context="Creation target parent")
    try:
        with os.scandir(path.parent) as entries:
            matches = [
                entry.name
                for entry in entries
                if unicodedata.normalize("NFC", entry.name).casefold()
                == unicodedata.normalize("NFC", leaf).casefold()
            ]
    except OSError:
        raise invalid_request("Creation target parent is unavailable") from None
    if matches or path.exists() or path.is_symlink():
        raise invalid_request("Creation target is not exactly absent")
    if _pinned_directory(path.parent, context="Creation target parent") != parent_identity:
        raise conflict("Creation target parent identity changed during registration")
    return parent_identity, leaf


def _creation_contract_error_cause(
    error: BaseException,
) -> CreationContractError | None:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, CreationContractError):
            return current
        current = current.__cause__
    return None


def _load_existing_project(
    root: Path,
    *,
    expected_hash: str,
) -> tuple[dict[str, Any], tuple[int, int]]:
    try:
        root_info = path_file_stat(root)
    except OSError as exc:
        raise invalid_request(
            "Creation project root could not be inspected safely",
            reason_code="creation_project_inspection_failed",
        ) from exc
    if _is_link_or_reparse(root_info):
        raise invalid_request(
            "Creation project root contains a symbolic link or reparse point",
            reason_code="creation_project_root_linked",
        )
    if not stat.S_ISDIR(root_info.st_mode):
        raise invalid_request(
            "Creation project root must be a real directory",
            reason_code="creation_project_root_non_directory",
        )
    try:
        if route_creation_project(root) != "generic":
            raise CreationRouteError("expected a generic project")
        loaded = load_creation_project(root / "project.json")
        identity = document_identity(loaded.project)
    except CreationContractError as exc:
        message = (
            "Creation root is not an integral generic project"
            if exc.reason_code == "creation_contract_invalid"
            else exc.detail
        )
        raise invalid_request(message, reason_code=exc.reason_code) from exc
    except CreationRouteError as exc:
        contract_error = _creation_contract_error_cause(exc)
        if contract_error is not None:
            message = (
                "Creation root is not an integral generic project"
                if contract_error.reason_code == "creation_contract_invalid"
                else contract_error.detail
            )
            raise invalid_request(
                message,
                reason_code=contract_error.reason_code,
            ) from exc
        raise invalid_request("Creation root is not an integral generic project") from exc
    except ValueError as exc:
        raise invalid_request("Creation root is not an integral generic project") from exc
    if not hmac.compare_digest(identity["content_hash"], expected_hash):
        raise conflict("Creation project hash does not match expected_project_hash")
    root_identity = _pinned_directory(root, context="Creation project root")
    try:
        marker = path_file_stat(root / "project.json")
    except OSError as exc:
        raise invalid_request("Creation project marker is unavailable") from exc
    if _is_link_or_reparse(marker) or not stat.S_ISREG(marker.st_mode) or marker.st_nlink != 1:
        raise invalid_request("Creation project marker must be a standalone regular file")
    if _pinned_directory(root, context="Creation project root") != root_identity:
        raise conflict("Creation root identity changed during registration")
    return identity, root_identity


class CreationRootGrantManager:
    """Retained native authority for generic creation roots with pathless public records."""

    def __init__(self, store: StudioStore) -> None:
        self.store = store

    def create(self, params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("creation_root_grant.create params must be an object")
        allowed = {
            "grant_id",
            "role",
            "display_name",
            "path",
            "expected_project_hash",
        }
        required = allowed - {"grant_id"}
        missing = required - set(params)
        unknown = set(params) - allowed
        if missing or unknown:
            fields = missing or unknown
            raise invalid_request(
                "creation_root_grant.create has invalid fields: " + ", ".join(sorted(fields))
            )
        grant_id = params.get("grant_id") or f"grant_{uuid.uuid4().hex}"
        if not isinstance(grant_id, str) or ENTITY_ID_PATTERN.fullmatch(grant_id) is None:
            raise invalid_request("grant_id is not a valid identifier")
        role = params["role"]
        if role not in {"existing_root", "new_target"}:
            raise invalid_request("Creation root grant role is unknown")
        path = _normalized_absolute_path(params["path"])
        expected_hash = _expected_hash(
            params["expected_project_hash"],
            required=role == "existing_root",
        )
        if role == "new_target" and expected_hash is not None:
            raise invalid_request("new_target grants require a null expected_project_hash")
        timestamp = utc_now()
        expected_project: dict[str, Any] | None = None
        root_identity: tuple[int, int] | None = None
        parent_identity: tuple[int, int] | None = None
        normalized_leaf: str | None = None
        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            self._reject_unsafe_overlap(path)
            self._reject_active_grant_overlap(path)
            if role == "existing_root":
                assert expected_hash is not None
                expected_project, root_identity = _load_existing_project(
                    path,
                    expected_hash=expected_hash,
                )
            else:
                parent_identity, normalized_leaf = _safe_target_leaf(path)
            record = {
                "format": CREATION_ROOT_GRANT_FORMAT,
                "format_version": 1,
                "grant_id": grant_id,
                "role": role,
                "display_name": _display_name(params["display_name"]),
                "state": "ready",
                "expected_target_state": (
                    "existing_project" if role == "existing_root" else "absent"
                ),
                "expected_project": expected_project,
                "generation": 0,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            validate_studio_creation_root_grant(record)
            self.store.connection.execute(
                "INSERT INTO creation_root_grants "
                "(grant_id, role, state, record_json, absolute_path, root_dev, root_ino, "
                "parent_dev, parent_ino, normalized_leaf, reserved_workspace_id, generation, "
                "creation_spec_json) VALUES (?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL)",
                (
                    grant_id,
                    role,
                    encode_json(record),
                    str(path),
                    None if root_identity is None else str(root_identity[0]),
                    None if root_identity is None else str(root_identity[1]),
                    None if parent_identity is None else str(parent_identity[0]),
                    None if parent_identity is None else str(parent_identity[1]),
                    normalized_leaf,
                ),
            )
            self.store.record_creation_event(
                workspace_id=None,
                topic="creation_root_grant.created",
                entity_type="creation_root_grant",
                entity_id=grant_id,
                payload={"role": role, "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation root grant {grant_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    def get(self, grant_id: object) -> dict[str, Any]:
        if not isinstance(grant_id, str) or ENTITY_ID_PATTERN.fullmatch(grant_id) is None:
            raise invalid_request("grant_id is not a valid identifier")
        row = self.store.connection.execute(
            "SELECT * FROM creation_root_grants WHERE grant_id = ?",
            (grant_id,),
        ).fetchone()
        if row is None:
            raise not_found(f"Creation root grant {grant_id} was not found")
        return self._validated_row(row)

    def revoke(self, grant_id: object, *, expected_generation: object) -> dict[str, Any]:
        if (
            isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 0
        ):
            raise invalid_request("expected_generation must be a non-negative integer")
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            record = self.get(grant_id)
            if record["generation"] != expected_generation:
                raise conflict("Creation root grant generation changed")
            if record["state"] != "ready":
                raise invalid_state("Only a ready creation root grant can be revoked")
            revoked = self._transition(
                record,
                state="revoked",
                expected_generation=expected_generation,
                clear_reservation=True,
            )
            self.store.record_creation_event(
                workspace_id=None,
                topic="creation_root_grant.revoked",
                entity_type="creation_root_grant",
                entity_id=revoked["grant_id"],
                payload={"generation": revoked["generation"]},
                created_at=revoked["updated_at"],
            )
            self.store.connection.commit()
            return revoked
        except BaseException:
            self.store.connection.rollback()
            raise

    def reserve(
        self,
        grant_id: object,
        *,
        workspace_id: str,
        expected_generation: object,
        role: str,
        creation_spec: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if (
            isinstance(expected_generation, bool)
            or not isinstance(expected_generation, int)
            or expected_generation < 0
        ):
            raise invalid_request("expected_grant_generation must be a non-negative integer")
        row = self._row(grant_id)
        record = self._validated_row(row)
        if record["role"] != role:
            raise invalid_request(f"Creation root grant must have role {role}")
        if record["generation"] != expected_generation:
            raise conflict("Creation root grant generation changed")
        if record["state"] != "ready":
            raise invalid_state("Creation root grant is not ready")
        self._recensus_row(
            row,
            workspace_id=workspace_id,
            allow_visible_target=False,
        )
        updated = dict(record)
        updated["state"] = "reserved"
        updated["generation"] += 1
        updated["updated_at"] = utc_now()
        validate_studio_creation_root_grant(updated)
        cursor = self.store.connection.execute(
            "UPDATE creation_root_grants SET state = 'reserved', record_json = ?, "
            "reserved_workspace_id = ?, generation = ?, creation_spec_json = ? "
            "WHERE grant_id = ? AND state = 'ready' AND generation = ?",
            (
                encode_json(updated),
                workspace_id,
                updated["generation"],
                None if creation_spec is None else encode_json(creation_spec),
                updated["grant_id"],
                expected_generation,
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation root grant changed concurrently")
        binding = self._private_binding(self._row(updated["grant_id"]))
        return updated, binding

    def release(self, grant_id: str, *, expected_generation: int) -> dict[str, Any]:
        record = self.get(grant_id)
        if record["state"] != "reserved":
            raise invalid_state("Only a reserved creation root grant can be released")
        return self._transition(
            record,
            state="ready",
            expected_generation=expected_generation,
            clear_reservation=True,
        )

    def mark_recovery_required(
        self,
        grant_id: str,
        *,
        expected_generation: int,
    ) -> dict[str, Any]:
        record = self.get(grant_id)
        if record["state"] != "reserved":
            raise invalid_state("Only a reserved creation root grant can require recovery")
        return self._transition(
            record,
            state="recovery_required",
            expected_generation=expected_generation,
            clear_reservation=False,
        )

    def consume(
        self,
        grant_id: str,
        *,
        expected_generation: int,
        created_root_identity: tuple[int, int] | None = None,
        retain_reservation: bool = False,
    ) -> dict[str, Any]:
        record = self.get(grant_id)
        if record["state"] not in {"reserved", "recovery_required"}:
            raise invalid_state("Creation root grant is not reserved for consumption")
        row = self._row(grant_id)
        self._recensus_row(
            row,
            workspace_id=row["reserved_workspace_id"],
            allow_visible_target=record["role"] == "new_target",
            expected_visible_identity=created_root_identity,
        )
        updated = self._transition(
            record,
            state="consumed",
            expected_generation=expected_generation,
            clear_reservation=not retain_reservation,
            created_root_identity=created_root_identity,
        )
        return updated

    def consumed_binding(self, grant_id: str, *, workspace_id: str) -> dict[str, Any]:
        row = self._row(grant_id)
        record = self._validated_row(row)
        if record["state"] != "consumed" or row["reserved_workspace_id"] != workspace_id:
            raise invalid_state("Creation root grant has no matching committed creation")
        self._recensus_row(
            row,
            workspace_id=workspace_id,
            allow_visible_target=True,
        )
        return self._private_binding(row)

    def finish_consumed_reservation(
        self,
        grant_id: str,
        *,
        workspace_id: str,
        expected_generation: int,
    ) -> None:
        cursor = self.store.connection.execute(
            "UPDATE creation_root_grants SET reserved_workspace_id = NULL, "
            "creation_spec_json = NULL WHERE grant_id = ? AND state = 'consumed' "
            "AND generation = ? AND reserved_workspace_id = ?",
            (grant_id, expected_generation, workspace_id),
        )
        if cursor.rowcount != 1:
            row = self._row(grant_id)
            record = self._validated_row(row)
            if (
                record["state"] == "consumed"
                and record["generation"] == expected_generation
                and row["reserved_workspace_id"] is None
                and row["creation_spec_json"] is None
            ):
                return
            raise conflict("Creation root grant cleanup state changed concurrently")

    def recovery_binding(self, grant_id: str, *, workspace_id: str) -> dict[str, Any]:
        row = self._row(grant_id)
        record = self._validated_row(row)
        if record["state"] != "recovery_required" or row["reserved_workspace_id"] != workspace_id:
            raise invalid_state("Creation root grant has no matching recovery")
        self._recensus_row(
            row,
            workspace_id=workspace_id,
            allow_visible_target=True,
        )
        return self._private_binding(row)

    def reserved_binding(self, grant_id: str, *, workspace_id: str) -> dict[str, Any]:
        """Reacquire one exact new-target reservation after a service restart."""

        row = self._row(grant_id)
        record = self._validated_row(row)
        if (
            record["state"] != "reserved"
            or record["role"] != "new_target"
            or row["reserved_workspace_id"] != workspace_id
        ):
            raise invalid_state("Creation root grant has no matching reservation")
        self._recensus_row(
            row,
            workspace_id=workspace_id,
            allow_visible_target=Path(row["absolute_path"]).exists(),
        )
        return self._private_binding(row)

    def recensus(
        self,
        grant_id: str,
        *,
        workspace_id: str,
        allow_visible_target: bool,
        expected_generation: int | None = None,
        expected_visible_identity: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        """Revalidate the exact binding and every competing authority."""

        row = self._row(grant_id)
        record = self._validated_row(row)
        if expected_generation is not None and record["generation"] != expected_generation:
            raise conflict("Creation root grant generation changed")
        reserved_workspace = row["reserved_workspace_id"]
        if record["state"] != "ready" and reserved_workspace != workspace_id:
            raise invalid_state("Creation root grant reservation changed")
        self._recensus_row(
            row,
            workspace_id=workspace_id,
            allow_visible_target=allow_visible_target,
            expected_visible_identity=expected_visible_identity,
        )
        return self._private_binding(row)

    def _recensus_row(
        self,
        row: sqlite3.Row,
        *,
        workspace_id: str | None,
        allow_visible_target: bool,
        expected_visible_identity: tuple[int, int] | None = None,
    ) -> None:
        path = Path(row["absolute_path"])
        self._revalidate_row(
            row,
            allow_visible_target=allow_visible_target,
            expected_visible_identity=expected_visible_identity,
        )
        self._reject_unsafe_overlap(
            path,
            exclude_creation_workspace_id=workspace_id,
        )
        self._reject_active_grant_overlap(path, exclude_grant_id=row["grant_id"])

    def _transition(
        self,
        record: dict[str, Any],
        *,
        state: str,
        expected_generation: int,
        clear_reservation: bool,
        created_root_identity: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        if record["generation"] != expected_generation:
            raise conflict("Creation root grant generation changed")
        updated = dict(record)
        updated["state"] = state
        updated["generation"] += 1
        updated["updated_at"] = utc_now()
        validate_studio_creation_root_grant(updated)
        fields = [
            "state = ?",
            "record_json = ?",
            "generation = ?",
        ]
        values: list[object] = [state, encode_json(updated), updated["generation"]]
        if clear_reservation:
            fields.extend(["reserved_workspace_id = NULL", "creation_spec_json = NULL"])
        if created_root_identity is not None:
            fields.extend(["root_dev = ?", "root_ino = ?"])
            values.extend([str(created_root_identity[0]), str(created_root_identity[1])])
        values.extend([record["grant_id"], expected_generation, record["state"]])
        cursor = self.store.connection.execute(
            f"UPDATE creation_root_grants SET {', '.join(fields)} "  # noqa: S608
            "WHERE grant_id = ? AND generation = ? AND state = ?",
            tuple(values),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation root grant changed concurrently")
        return updated

    def _revalidate_row(
        self,
        row: sqlite3.Row,
        *,
        allow_visible_target: bool,
        expected_visible_identity: tuple[int, int] | None = None,
    ) -> None:
        path = Path(row["absolute_path"])
        if row["role"] == "existing_root":
            expected = (int(row["root_dev"]), int(row["root_ino"]))
            identity = _pinned_directory(path, context="Creation project root")
            if identity != expected:
                raise conflict("Creation root identity changed")
            record = self._validated_row(row)
            expected_project = record["expected_project"]
            assert expected_project is not None
            _load_existing_project(path, expected_hash=expected_project["content_hash"])
            return
        parent = (int(row["parent_dev"]), int(row["parent_ino"]))
        if _pinned_directory(path.parent, context="Creation target parent") != parent:
            raise conflict("Creation target parent identity changed")
        if allow_visible_target:
            observed = _pinned_directory(path, context="Creation recovery target")
            expected = expected_visible_identity
            if expected is None and row["root_dev"] is not None:
                if row["root_ino"] is None:
                    raise StudioError(
                        "internal_error",
                        "Stored creation root identity is incomplete",
                    )
                expected = int(row["root_dev"]), int(row["root_ino"])
            if expected is not None and observed != expected:
                raise conflict("Created project root identity changed")
            return
        observed, leaf = _safe_target_leaf(path)
        if observed != parent or leaf != row["normalized_leaf"]:
            raise conflict("Creation target binding changed")

    def _reject_unsafe_overlap(
        self,
        candidate: Path,
        *,
        exclude_creation_workspace_id: str | None = None,
    ) -> None:
        unsafe = [self.store.data_dir, FORGE_ROOT]
        for row in self.store.connection.execute("SELECT record_json FROM workspaces"):
            record = decode_object(row["record_json"], context="workspace")
            for field in ("forge_root", "world_root", "game_root", "bundle_root"):
                value = record.get(field)
                if isinstance(value, str):
                    unsafe.append(Path(value))
        unsafe.extend(
            Path(row["absolute_root"])
            for row in self.store.connection.execute(
                "SELECT workspace_id, absolute_root FROM creation_workspaces"
            )
            if row["workspace_id"] != exclude_creation_workspace_id
        )
        unsafe.extend(
            Path(row["absolute_path"])
            for row in self.store.connection.execute(
                "SELECT absolute_path FROM external_grants "
                "WHERE state IN ('ready', 'reserved', 'recovery_required')"
            )
        )
        for root in unsafe:
            if _paths_overlap(candidate, root):
                raise invalid_request("Creation root overlaps an unsafe root")
            if candidate.exists() and root.exists() and candidate.is_dir() and root.is_dir():
                try:
                    if _overlaps(candidate, root):
                        raise invalid_request("Creation root overlaps an unsafe root alias")
                except StudioError:
                    raise invalid_request("Creation root overlap could not be verified") from None

    def _reject_active_grant_overlap(
        self,
        path: Path,
        *,
        exclude_grant_id: str | None = None,
    ) -> None:
        rows = self.store.connection.execute(
            "SELECT grant_id, absolute_path FROM creation_root_grants "
            "WHERE state IN ('ready', 'reserved', 'recovery_required') ORDER BY grant_id"
        )
        for row in rows:
            if row["grant_id"] == exclude_grant_id:
                continue
            existing = Path(row["absolute_path"])
            if _paths_overlap(path, existing):
                raise invalid_request("Creation root overlaps active creation authority")
            if path.exists() and existing.exists() and path.is_dir() and existing.is_dir():
                try:
                    if _overlaps(path, existing):
                        raise invalid_request("Creation root overlaps active creation authority")
                except StudioError:
                    raise invalid_request("Creation root overlap could not be verified") from None

    def _row(self, grant_id: object) -> sqlite3.Row:
        if not isinstance(grant_id, str) or ENTITY_ID_PATTERN.fullmatch(grant_id) is None:
            raise invalid_request("grant_id is not a valid identifier")
        row = self.store.connection.execute(
            "SELECT * FROM creation_root_grants WHERE grant_id = ?",
            (grant_id,),
        ).fetchone()
        if row is None:
            raise not_found(f"Creation root grant {grant_id} was not found")
        return row

    @staticmethod
    def _private_binding(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "path": Path(row["absolute_path"]),
            "root_identity": (
                None if row["root_dev"] is None else (int(row["root_dev"]), int(row["root_ino"]))
            ),
            "parent_identity": (
                None
                if row["parent_dev"] is None
                else (int(row["parent_dev"]), int(row["parent_ino"]))
            ),
            "normalized_leaf": row["normalized_leaf"],
            "reserved_workspace_id": row["reserved_workspace_id"],
            "creation_spec": (
                None
                if row["creation_spec_json"] is None
                else decode_object(row["creation_spec_json"], context="creation specification")
            ),
        }

    @staticmethod
    def _validated_row(row: sqlite3.Row) -> dict[str, Any]:
        record = decode_object(row["record_json"], context="creation root grant")
        try:
            checked = validate_studio_creation_root_grant(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored creation root grant is invalid") from exc
        if checked["state"] != row["state"] or checked["generation"] != row["generation"]:
            raise StudioError("internal_error", "Stored creation root grant state diverged")
        return checked
