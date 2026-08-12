from __future__ import annotations

import os
import sqlite3
import stat
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from isoworld.content.file_stat import FileStat, path_file_stat
from isoworld.content.portability import portable_relative_path
from worldforge.repository_boundary import FORGE_ROOT
from worldforge.studio.contracts import (
    CREATION_OUTPUT_GRANT_FORMAT,
    ENTITY_ID_PATTERN,
    SHA256_PATTERN,
    WORKSPACE_ID_PATTERN,
    validate_studio_creation_output_grant_v6,
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
from worldforge.studio.workspaces import _pinned_ancestor_identities

_MAX_DISPLAY_NAME = 128
MAX_CREATION_OUTPUT_GRANT_PAGE = 8


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _path_key(path: Path) -> tuple[str, ...]:
    return tuple(
        unicodedata.normalize("NFC", os.path.normcase(part)).casefold() for part in path.parts
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    left_key = _path_key(left)
    right_key = _path_key(right)
    common = min(len(left_key), len(right_key))
    return left_key[:common] == right_key[:common]


def _normalized_absolute_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise invalid_request("Creation output path is invalid")
    if unicodedata.normalize("NFC", value) != value:
        raise invalid_request("Creation output path must be NFC normalized")
    supplied = Path(value)
    absolute = Path(os.path.abspath(supplied))
    if not supplied.is_absolute() or str(supplied) != str(absolute):
        raise invalid_request("Creation output path must be absolute and normalized")
    return absolute


def _display_name(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_DISPLAY_NAME:
        raise invalid_request("Creation output grant display name is invalid")
    if (
        unicodedata.normalize("NFC", value) != value
        or any(character in value for character in "/\\\x00\r\n")
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise invalid_request("Creation output grant display name is invalid")
    return value


def _identifier(value: object, *, field: str, workspace: bool = False) -> str:
    pattern = WORKSPACE_ID_PATTERN if workspace else ENTITY_ID_PATTERN
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise invalid_request(f"{field} is not a valid identifier")
    return value


def _generation(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise invalid_request(f"{field} must be a non-negative integer")
    return value


def _digest(value: object, *, field: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise invalid_request(f"{field} must be a lowercase SHA-256 digest")
    return value


def _native_identity(value: object, *, field: str) -> tuple[int, int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value)
    ):
        raise StudioError("internal_error", f"Stored creation output {field} is invalid")
    return int(value[0]), int(value[1])


def _operation_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 32
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise StudioError("internal_error", "Stored creation output operation ID is invalid")
    return value


def _journal_payload_state(value: object, *, field: str) -> str:
    if value not in {"intent", "copying", "ready"}:
        raise StudioError("internal_error", f"Stored creation output {field} is invalid")
    return str(value)


def _validated_recovery(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise StudioError("internal_error", "Stored creation output recovery is invalid")
    phase = value.get("phase")
    if phase in {
        "file_publication_reserved",
        "file_publication_started",
        "file_publication_verified",
    }:
        fields = {
            "phase",
            "expected_manifest_hash",
            "expected_archive_sha256",
            "expected_size_bytes",
        }
        if phase == "file_publication_verified":
            fields.add("published_identity")
        if set(value) != fields:
            raise StudioError("internal_error", "Stored creation output recovery is invalid")
        _digest(value["expected_manifest_hash"], field="recovery expected_manifest_hash")
        _digest(value["expected_archive_sha256"], field="recovery expected_archive_sha256")
        _generation(value["expected_size_bytes"], field="recovery expected_size_bytes")
        if int(value["expected_size_bytes"]) < 1:
            raise StudioError("internal_error", "Stored creation output recovery is invalid")
        if phase == "file_publication_verified":
            _native_identity(value["published_identity"], field="published identity")
        return dict(value)
    base = {"phase", "expected_manifest_hash", "expected_tree_hash"}
    bound = {
        "journal_identity",
        "operation_id",
        "journal_payload_sha256",
        "journal_payload_state",
    }
    if phase not in {
        "publication_reserved",
        "publication_started",
        "publication_stage_allocated",
        "publication_staged",
        "publication_resetting",
        "publication_verified",
    }:
        raise StudioError("internal_error", "Stored creation output recovery is invalid")
    allowed = {frozenset(base)}
    if phase == "publication_started":
        allowed.add(frozenset(base | bound))
    elif phase == "publication_resetting":
        allowed = {
            frozenset(base | bound),
            frozenset(base | bound | {"stage_identity"}),
        }
    elif phase in {"publication_stage_allocated", "publication_staged"}:
        allowed = {frozenset(base | bound | {"stage_identity"})}
    elif phase == "publication_verified":
        allowed = {
            frozenset(base | {"published_identity"}),
            frozenset(base | bound | {"stage_identity", "published_identity"}),
        }
    if frozenset(value) not in allowed:
        raise StudioError("internal_error", "Stored creation output recovery is invalid")
    _digest(value["expected_manifest_hash"], field="recovery expected_manifest_hash")
    _digest(value["expected_tree_hash"], field="recovery expected_tree_hash")
    if "journal_identity" in value:
        _native_identity(value["journal_identity"], field="journal identity")
        _operation_id(value["operation_id"])
        _digest(value["journal_payload_sha256"], field="recovery journal_payload_sha256")
        payload_state = _journal_payload_state(
            value["journal_payload_state"],
            field="journal payload state",
        )
        if phase in {"publication_started", "publication_stage_allocated"}:
            expected_states = {"intent"}
        elif phase == "publication_staged":
            expected_states = {"copying"}
        elif phase == "publication_verified":
            expected_states = {"ready"}
        else:
            expected_states = {"intent", "copying"}
        if payload_state not in expected_states:
            raise StudioError(
                "internal_error",
                "Stored creation output journal payload phase is invalid",
            )
    if "stage_identity" in value:
        _native_identity(value["stage_identity"], field="stage identity")
    if phase == "publication_verified":
        _native_identity(value["published_identity"], field="published identity")
    return dict(value)


def _pin_directory(path: Path, *, context: str) -> tuple[int, int]:
    try:
        with _pinned_ancestor_identities(path, context=context) as identities:
            return identities[-1]
    except StudioError:
        raise invalid_request(f"{context} is not a safe directory") from None


def _safe_absent_target(path: Path) -> tuple[tuple[int, int], str]:
    leaf = path.name
    if portable_relative_path(leaf) is None or leaf.startswith("."):
        raise invalid_request("Creation output target name is not portable")
    parent_identity = _pin_directory(path.parent, context="Creation output parent")
    try:
        with os.scandir(path.parent) as entries:
            matches = [
                entry.name
                for entry in entries
                if unicodedata.normalize("NFC", entry.name).casefold()
                == unicodedata.normalize("NFC", leaf).casefold()
            ]
    except OSError:
        raise invalid_request("Creation output parent is unavailable") from None
    if matches or path.exists() or path.is_symlink():
        raise invalid_request(
            "Creation output target is not exactly absent or has an NFC/casefold collision"
        )
    if _pin_directory(path.parent, context="Creation output parent") != parent_identity:
        raise conflict("Creation output parent identity changed during registration")
    return parent_identity, leaf


def _require_reset_stage_absent(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    operation_id: str,
) -> None:
    destination = Path(row["absolute_path"])
    job_id = row["reserved_job_id"]
    if type(job_id) is not str:
        raise conflict("Creation output publication job authority is unavailable")
    job = connection.execute(
        "SELECT operation FROM creation_jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if job is None:
        raise conflict("Creation output publication job authority is unavailable")
    stage_family = {
        "game.materialize": "standalone-stage",
        "game.package.extract": "game-package-stage",
    }.get(job["operation"])
    if stage_family is None:
        raise conflict("Creation output publication stage family is unsupported")
    stage = destination.parent / (f".{destination.name}.{stage_family}-{operation_id}")
    try:
        path_file_stat(stage)
    except FileNotFoundError:
        return
    except OSError:
        raise conflict("Creation output publication reset stage cannot be inspected") from None
    raise conflict("Creation output publication reset stage reappeared")


class CreationOutputGrantManager:
    """Retain native output authority while exposing only pathless public grants."""

    def __init__(self, store: StudioStore) -> None:
        self.store = store

    def create(self, params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("creation_output_grant.create params must be an object")
        allowed = {"grant_id", "workspace_id", "kind", "display_name", "path"}
        required = allowed - {"grant_id"}
        invalid = (required - set(params)) | (set(params) - allowed)
        if invalid:
            raise invalid_request(
                "creation_output_grant.create has invalid fields: " + ", ".join(sorted(invalid))
            )
        grant_id = params.get("grant_id") or f"grant_{uuid.uuid4().hex}"
        grant_id = _identifier(grant_id, field="grant_id")
        workspace_id = _identifier(params["workspace_id"], field="workspace_id", workspace=True)
        if params["kind"] not in {
            "generic_assetpack_directory",
            "game_runtime_bundle_directory",
            "game_materialization_bundle_directory",
            "standalone_game_directory",
            "game_package_file",
            "headless_evidence_directory",
        }:
            raise invalid_request("Creation output grant kind is unknown")
        kind = str(params["kind"])
        format_version = {
            "generic_assetpack_directory": 1,
            "game_runtime_bundle_directory": 2,
            "game_materialization_bundle_directory": 3,
            "standalone_game_directory": 4,
            "game_package_file": 5,
            "headless_evidence_directory": 6,
        }[kind]
        path = _normalized_absolute_path(params["path"])
        timestamp = utc_now()
        record = {
            "format": CREATION_OUTPUT_GRANT_FORMAT,
            "format_version": format_version,
            "grant_id": grant_id,
            "workspace_id": workspace_id,
            "kind": kind,
            "display_name": _display_name(params["display_name"]),
            "state": "ready",
            "generation": 0,
            "publication": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        try:
            validate_studio_creation_output_grant_v6(record)
            self.store.connection.execute("BEGIN IMMEDIATE")
            workspace = self.store.connection.execute(
                "SELECT absolute_root FROM creation_workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            if workspace is None:
                raise invalid_request("Creation output workspace is unavailable")
            unsafe = (self.store.data_dir, Path(FORGE_ROOT), Path(workspace["absolute_root"]))
            if any(_paths_overlap(path, root) for root in unsafe):
                raise invalid_request("Creation output target overlaps Forge or source roots")
            for row in self.store.connection.execute(
                "SELECT absolute_path FROM creation_output_grants "
                "WHERE state IN ('ready', 'reserved', 'published', 'recovery_required')"
            ):
                if _paths_overlap(path, Path(row["absolute_path"])):
                    raise invalid_request(
                        "Creation output target has an active NFC/casefold authority collision"
                    )
            parent_identity, leaf = _safe_absent_target(path)
            self.store.connection.execute(
                "INSERT INTO creation_output_grants "
                "(grant_id, workspace_id, kind, state, record_json, absolute_path, "
                "parent_dev, parent_ino, normalized_leaf, reserved_job_id, generation, "
                "expected_manifest_hash, expected_tree_hash, published_dev, published_ino, "
                "recovery_json) VALUES (?, ?, ?, 'ready', ?, ?, "
                "?, ?, ?, NULL, 0, NULL, NULL, NULL, NULL, NULL)",
                (
                    grant_id,
                    workspace_id,
                    kind,
                    encode_json(record),
                    str(path),
                    str(parent_identity[0]),
                    str(parent_identity[1]),
                    leaf,
                ),
            )
            self.store.record_creation_event(
                workspace_id=workspace_id,
                topic="creation_output_grant.created",
                entity_type="creation_output_grant",
                entity_id=grant_id,
                payload={"kind": kind, "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation output grant {grant_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    def get(self, grant_id: object) -> dict[str, Any]:
        row = self._row(grant_id)
        return self._validated_row(row)

    def list(
        self,
        *,
        workspace_id: object,
        cursor: object = None,
        limit: object = MAX_CREATION_OUTPUT_GRANT_PAGE,
    ) -> tuple[list[dict[str, Any]], str | None]:
        identifier = _identifier(workspace_id, field="workspace_id", workspace=True)
        after = None if cursor is None else _identifier(cursor, field="cursor")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_CREATION_OUTPUT_GRANT_PAGE
        ):
            raise invalid_request("creation output grant list limit is invalid")
        if after is not None:
            retained = self.store.connection.execute(
                "SELECT 1 FROM creation_output_grants WHERE workspace_id = ? AND grant_id = ?",
                (identifier, after),
            ).fetchone()
            if retained is None:
                raise conflict("Creation output grant cursor is unavailable")
        rows = self.store.connection.execute(
            "SELECT * FROM creation_output_grants WHERE workspace_id = ? "
            "AND (? IS NULL OR grant_id > ?) ORDER BY grant_id LIMIT ?",
            (identifier, after, after, limit + 1),
        ).fetchall()
        page = rows[:limit]
        next_cursor = str(page[-1]["grant_id"]) if len(rows) > limit and page else None
        return [self._validated_row(row) for row in page], next_cursor

    def revoke(self, grant_id: object, *, expected_generation: object) -> dict[str, Any]:
        generation = _generation(expected_generation, field="expected_generation")
        with self.store.connection:
            record = self.get(grant_id)
            if record["generation"] != generation:
                raise conflict("Creation output grant generation changed")
            if record["state"] != "ready":
                raise invalid_state("Only a ready creation output grant may be revoked")
            return self._transition(record, state="revoked", clear_reservation=True)

    def reserve_for_job(
        self,
        *,
        grant_id: str,
        job_id: str,
        workspace_id: str,
        expected_generation: int,
        expected_manifest_hash: str,
        expected_tree_hash: str | None = None,
        expected_archive_sha256: str | None = None,
        expected_size_bytes: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        row = self._row(grant_id)
        record = self._validated_row(row)
        if record["workspace_id"] != workspace_id:
            raise invalid_request("Creation output grant belongs to another workspace")
        if record["state"] != "ready" or row["reserved_job_id"] is not None:
            raise invalid_state("Creation output grant is not ready")
        if record["generation"] != expected_generation:
            raise conflict("Creation output grant generation changed")
        is_file = record["kind"] == "game_package_file"
        if is_file:
            checked_tree_hash = None
            checked_archive_hash = _digest(
                expected_archive_sha256,
                field="expected_archive_sha256",
            )
            checked_size = _generation(expected_size_bytes, field="expected_size_bytes")
            if checked_size < 1:
                raise invalid_request("expected_size_bytes must be positive")
        else:
            checked_tree_hash = _digest(expected_tree_hash, field="expected_tree_hash")
            if expected_archive_sha256 is not None or expected_size_bytes is not None:
                raise invalid_request("Directory output grant received file archive authority")
            checked_archive_hash = None
            checked_size = None
        self._recensus(row, allow_visible=False)
        updated = dict(record)
        updated["state"] = "reserved"
        updated["generation"] += 1
        updated["updated_at"] = utc_now()
        validate_studio_creation_output_grant_v6(updated)
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET state = 'reserved', record_json = ?, "
            "reserved_job_id = ?, generation = ?, expected_manifest_hash = ?, "
            "expected_tree_hash = ?, expected_archive_sha256 = ?, expected_size_bytes = ?, "
            "recovery_json = NULL WHERE grant_id = ? "
            "AND state = 'ready' AND generation = ? AND reserved_job_id IS NULL",
            (
                encode_json(updated),
                job_id,
                updated["generation"],
                _digest(expected_manifest_hash, field="expected_manifest_hash"),
                checked_tree_hash,
                checked_archive_hash,
                checked_size,
                grant_id,
                expected_generation,
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation output grant changed concurrently")
        return updated, self.binding_for_job(job_id, allow_visible=False)

    def binding_for_job(self, job_id: str, *, allow_visible: bool | None) -> dict[str, Any]:
        row = self.store.connection.execute(
            "SELECT * FROM creation_output_grants WHERE reserved_job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise conflict("Creation output grant reservation is unavailable")
        record = self._validated_row(row)
        if record["state"] not in {"reserved", "recovery_required", "published"}:
            raise conflict("Creation output grant reservation state changed")
        self._recensus(row, allow_visible=allow_visible)
        return self._private_binding(row)

    def published_binding(
        self,
        *,
        grant_id: str,
        workspace_id: str,
        expected_generation: int,
    ) -> dict[str, Any]:
        """Retain one exact published directory for an internal consumer.

        Public Studio contracts remain pathless.  This binding is deliberately
        private to the service boundary and pins both the grant CAS generation
        and the native directory identity before any published bytes are read.
        """

        row = self._row(grant_id)
        record = self._validated_row(row)
        if record["workspace_id"] != workspace_id:
            raise invalid_request("Creation output grant belongs to another workspace")
        if record["state"] != "published":
            raise invalid_state("Creation output grant is not published")
        if record["generation"] != expected_generation:
            raise conflict("Creation output grant generation changed")
        self._recensus(row, allow_visible=True)
        binding = self._private_binding(row)
        published_identity = binding["published_identity"]
        if published_identity is None:
            raise invalid_state("Published creation output identity is unavailable")
        try:
            info = path_file_stat(binding["path"])
        except OSError as exc:
            raise conflict("Published creation output cannot be inspected") from exc
        expected_mode = (
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            if record["kind"] == "game_package_file"
            else stat.S_ISDIR(info.st_mode)
        )
        if (
            _is_link_or_reparse(info)
            or not expected_mode
            or (int(info.st_dev), int(info.st_ino)) != published_identity
        ):
            raise conflict("Published creation output identity changed")
        return binding

    def mark_recovery_required(
        self,
        job_id: str,
        *,
        recovery: Mapping[str, object],
    ) -> dict[str, Any]:
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] == "published":
            return record
        if record["state"] not in {"reserved", "recovery_required"}:
            raise invalid_state("Creation output grant cannot require recovery")
        updated = self._transition(record, state="recovery_required", clear_reservation=False)
        self.store.connection.execute(
            "UPDATE creation_output_grants SET recovery_json = ? WHERE grant_id = ?",
            (encode_json(dict(recovery)), record["grant_id"]),
        )
        return updated

    def begin_publication(self, job_id: str) -> dict[str, Any]:
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] != "reserved":
            raise invalid_state("Creation output grant is not reserved for publication")
        binding = self._private_binding(row)
        if binding["recovery"] is not None:
            self._recensus(row, allow_visible=None)
            recovery = binding["recovery"]
            hashes_changed = recovery["expected_manifest_hash"] != binding["expected_manifest_hash"]
            if record["kind"] == "game_package_file":
                hashes_changed = (
                    hashes_changed
                    or recovery["expected_archive_sha256"] != binding["expected_archive_sha256"]
                    or recovery["expected_size_bytes"] != binding["expected_size_bytes"]
                )
            else:
                hashes_changed = (
                    hashes_changed
                    or recovery["expected_tree_hash"] != binding["expected_tree_hash"]
                )
            if hashes_changed:
                raise conflict("Creation output publication recovery hashes changed")
            return binding
        self._recensus(row, allow_visible=False)
        recovery = (
            {
                "phase": "file_publication_reserved",
                "expected_manifest_hash": row["expected_manifest_hash"],
                "expected_archive_sha256": row["expected_archive_sha256"],
                "expected_size_bytes": int(row["expected_size_bytes"]),
            }
            if row["kind"] == "game_package_file"
            else {
                "phase": (
                    "publication_reserved"
                    if row["kind"] == "standalone_game_directory"
                    else "publication_started"
                ),
                "expected_manifest_hash": row["expected_manifest_hash"],
                "expected_tree_hash": row["expected_tree_hash"],
            }
        )
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET recovery_json = ? WHERE grant_id = ? "
            "AND reserved_job_id = ? AND state = 'reserved' AND generation = ? "
            "AND recovery_json IS NULL",
            (encode_json(recovery), record["grant_id"], job_id, record["generation"]),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation output publication intent lost its CAS")
        return self._private_binding(self._reserved_row(job_id))

    def note_publication_started(
        self,
        job_id: str,
        *,
        journal_identity: tuple[int, int],
        operation_id: str,
        journal_payload_sha256: str,
        journal_payload_state: str,
    ) -> dict[str, Any]:
        checked_identity = _native_identity(journal_identity, field="journal identity")
        checked_operation = _operation_id(operation_id)
        checked_payload = _digest(
            journal_payload_sha256,
            field="journal_payload_sha256",
        )
        checked_payload_state = _journal_payload_state(
            journal_payload_state,
            field="journal payload state",
        )
        if checked_payload_state != "intent":
            raise conflict("Creation output publication start is not an intent journal")
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] not in {"reserved", "recovery_required"}:
            raise invalid_state("Creation output grant cannot record publication start")
        binding = self._private_binding(row)
        retained = binding["recovery"]
        if retained is None:
            raise conflict("Creation output publication reservation is unavailable")
        expected = {
            "phase": "publication_started",
            "expected_manifest_hash": binding["expected_manifest_hash"],
            "expected_tree_hash": binding["expected_tree_hash"],
            "journal_identity": list(checked_identity),
            "operation_id": checked_operation,
            "journal_payload_sha256": checked_payload,
            "journal_payload_state": checked_payload_state,
        }
        if retained["phase"] == "publication_started":
            if retained != expected:
                raise conflict("Creation output publication journal authority changed")
            return binding
        if retained["phase"] != "publication_reserved":
            raise conflict("Creation output publication cannot bind a new journal")
        self._recensus(row, allow_visible=False)
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET recovery_json = ? WHERE grant_id = ? "
            "AND reserved_job_id = ? AND state IN ('reserved', 'recovery_required') "
            "AND generation = ? AND recovery_json = ?",
            (
                encode_json(expected),
                record["grant_id"],
                job_id,
                record["generation"],
                row["recovery_json"],
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation output publication journal binding lost its CAS")
        return self._private_binding(self._reserved_row(job_id))

    def note_file_publication_started(self, job_id: str) -> dict[str, Any]:
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["kind"] != "game_package_file" or record["state"] not in {
            "reserved",
            "recovery_required",
        }:
            raise invalid_state("Game package output grant cannot start publication")
        binding = self._private_binding(row)
        retained = binding["recovery"]
        if retained is None:
            raise conflict("Game package publication reservation is unavailable")
        expected = {
            **retained,
            "phase": "file_publication_started",
        }
        if retained == expected:
            return binding
        if retained["phase"] != "file_publication_reserved":
            raise conflict("Game package publication phase changed")
        self._recensus(row, allow_visible=False)
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET recovery_json = ? WHERE grant_id = ? "
            "AND reserved_job_id = ? AND state IN ('reserved', 'recovery_required') "
            "AND generation = ? AND recovery_json = ?",
            (
                encode_json(expected),
                record["grant_id"],
                job_id,
                record["generation"],
                row["recovery_json"],
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Game package publication start lost its CAS")
        return self._private_binding(self._reserved_row(job_id))

    def note_file_publication_verified(
        self,
        job_id: str,
        *,
        published_identity: tuple[int, int],
    ) -> dict[str, Any]:
        checked_identity = _native_identity(published_identity, field="published identity")
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["kind"] != "game_package_file" or record["state"] not in {
            "reserved",
            "recovery_required",
        }:
            raise invalid_state("Game package output grant cannot record publication")
        binding = self._private_binding(row)
        retained = binding["recovery"]
        if retained is None or retained["phase"] not in {
            "file_publication_started",
            "file_publication_verified",
        }:
            raise conflict("Game package publication was not durably started")
        if (
            retained["phase"] == "file_publication_verified"
            and tuple(retained["published_identity"]) != checked_identity
        ):
            raise conflict("Game package publication identity changed")
        self._recensus(row, allow_visible=True)
        info = path_file_stat(Path(row["absolute_path"]))
        if (
            _is_link_or_reparse(info)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (int(info.st_dev), int(info.st_ino)) != checked_identity
            or int(info.st_size) != int(row["expected_size_bytes"])
        ):
            raise conflict("Game package publication file identity changed")
        recovery = {
            "phase": "file_publication_verified",
            "expected_manifest_hash": row["expected_manifest_hash"],
            "expected_archive_sha256": row["expected_archive_sha256"],
            "expected_size_bytes": int(row["expected_size_bytes"]),
            "published_identity": [checked_identity[0], checked_identity[1]],
        }
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET published_dev = ?, published_ino = ?, "
            "recovery_json = ? WHERE grant_id = ? AND reserved_job_id = ? "
            "AND state IN ('reserved', 'recovery_required') AND generation = ? "
            "AND recovery_json = ? AND ((published_dev IS NULL AND published_ino IS NULL) "
            "OR (published_dev = ? AND published_ino = ?))",
            (
                str(checked_identity[0]),
                str(checked_identity[1]),
                encode_json(recovery),
                record["grant_id"],
                job_id,
                record["generation"],
                row["recovery_json"],
                str(checked_identity[0]),
                str(checked_identity[1]),
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Game package publication evidence lost its CAS")
        return self._private_binding(self._reserved_row(job_id))

    def note_publication_stage_allocated(
        self,
        job_id: str,
        *,
        journal_identity: tuple[int, int],
        operation_id: str,
        stage_identity: tuple[int, int],
        journal_payload_sha256: str,
        journal_payload_state: str,
    ) -> dict[str, Any]:
        checked_journal = _native_identity(journal_identity, field="journal identity")
        checked_operation = _operation_id(operation_id)
        checked_stage = _native_identity(stage_identity, field="stage identity")
        checked_payload = _digest(
            journal_payload_sha256,
            field="journal_payload_sha256",
        )
        checked_payload_state = _journal_payload_state(
            journal_payload_state,
            field="journal payload state",
        )
        if checked_payload_state != "intent":
            raise conflict("Creation output allocated stage is not bound to intent")
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] not in {"reserved", "recovery_required"}:
            raise invalid_state("Creation output grant cannot record its allocated stage")
        binding = self._private_binding(row)
        retained = binding["recovery"]
        expected = {
            "phase": "publication_stage_allocated",
            "expected_manifest_hash": binding["expected_manifest_hash"],
            "expected_tree_hash": binding["expected_tree_hash"],
            "journal_identity": list(checked_journal),
            "operation_id": checked_operation,
            "stage_identity": list(checked_stage),
            "journal_payload_sha256": checked_payload,
            "journal_payload_state": checked_payload_state,
        }
        if retained == expected:
            return binding
        if (
            retained is None
            or retained["phase"] != "publication_started"
            or _native_identity(retained.get("journal_identity"), field="journal identity")
            != checked_journal
            or _operation_id(retained.get("operation_id")) != checked_operation
            or retained.get("journal_payload_sha256") != checked_payload
            or retained.get("journal_payload_state") != checked_payload_state
            or binding["published_identity"] is not None
        ):
            raise conflict("Creation output allocated stage authority changed")
        self._recensus(row, allow_visible=False)
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET recovery_json = ? WHERE grant_id = ? "
            "AND reserved_job_id = ? AND state IN ('reserved', 'recovery_required') "
            "AND generation = ? AND recovery_json = ? "
            "AND published_dev IS NULL AND published_ino IS NULL",
            (
                encode_json(expected),
                record["grant_id"],
                job_id,
                record["generation"],
                row["recovery_json"],
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation output allocated stage binding lost its CAS")
        return self._private_binding(self._reserved_row(job_id))

    def note_publication_staged(
        self,
        job_id: str,
        *,
        journal_identity: tuple[int, int],
        operation_id: str,
        stage_identity: tuple[int, int],
        journal_payload_sha256: str,
        journal_payload_state: str,
    ) -> dict[str, Any]:
        checked_journal = _native_identity(journal_identity, field="journal identity")
        checked_operation = _operation_id(operation_id)
        checked_stage = _native_identity(stage_identity, field="stage identity")
        checked_payload = _digest(
            journal_payload_sha256,
            field="journal_payload_sha256",
        )
        checked_payload_state = _journal_payload_state(
            journal_payload_state,
            field="journal payload state",
        )
        if checked_payload_state != "copying":
            raise conflict("Creation output publication stage is not a copying journal")
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] not in {"reserved", "recovery_required"}:
            raise invalid_state("Creation output grant cannot record its publication stage")
        binding = self._private_binding(row)
        retained = binding["recovery"]
        expected = {
            "phase": "publication_staged",
            "expected_manifest_hash": binding["expected_manifest_hash"],
            "expected_tree_hash": binding["expected_tree_hash"],
            "journal_identity": list(checked_journal),
            "operation_id": checked_operation,
            "stage_identity": list(checked_stage),
            "journal_payload_sha256": checked_payload,
            "journal_payload_state": checked_payload_state,
        }
        if retained == expected:
            return binding
        if (
            retained is None
            or retained["phase"] != "publication_stage_allocated"
            or _native_identity(retained.get("journal_identity"), field="journal identity")
            != checked_journal
            or _operation_id(retained.get("operation_id")) != checked_operation
            or _native_identity(retained.get("stage_identity"), field="stage identity")
            != checked_stage
            or retained.get("journal_payload_state") != "intent"
            or binding["published_identity"] is not None
        ):
            raise conflict("Creation output publication stage authority changed")
        self._recensus(row, allow_visible=False)
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET recovery_json = ? WHERE grant_id = ? "
            "AND reserved_job_id = ? AND state IN ('reserved', 'recovery_required') "
            "AND generation = ? AND recovery_json = ? "
            "AND published_dev IS NULL AND published_ino IS NULL",
            (
                encode_json(expected),
                record["grant_id"],
                job_id,
                record["generation"],
                row["recovery_json"],
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation output publication stage binding lost its CAS")
        return self._private_binding(self._reserved_row(job_id))

    def note_publication_resetting(
        self,
        job_id: str,
        *,
        journal_identity: tuple[int, int],
        operation_id: str,
    ) -> dict[str, Any]:
        checked_identity = _native_identity(journal_identity, field="journal identity")
        checked_operation = _operation_id(operation_id)
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] not in {"reserved", "recovery_required"}:
            raise invalid_state("Creation output grant cannot prepare publication reset")
        binding = self._private_binding(row)
        retained = binding["recovery"]
        if retained is None or retained["phase"] not in {
            "publication_started",
            "publication_stage_allocated",
            "publication_staged",
            "publication_resetting",
        }:
            raise conflict("Creation output publication reset authority is unavailable")
        if (
            _native_identity(retained.get("journal_identity"), field="journal identity")
            != checked_identity
            or _operation_id(retained.get("operation_id")) != checked_operation
            or binding["published_identity"] is not None
        ):
            raise conflict("Creation output publication reset authority changed")
        resetting = {**retained, "phase": "publication_resetting"}
        if retained == resetting:
            return binding
        self._recensus(row, allow_visible=False)
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET recovery_json = ? WHERE grant_id = ? "
            "AND reserved_job_id = ? AND state IN ('reserved', 'recovery_required') "
            "AND generation = ? AND recovery_json = ? "
            "AND published_dev IS NULL AND published_ino IS NULL",
            (
                encode_json(resetting),
                record["grant_id"],
                job_id,
                record["generation"],
                row["recovery_json"],
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation output publication reset binding lost its CAS")
        return self._private_binding(self._reserved_row(job_id))

    def reset_publication_started(
        self,
        job_id: str,
        *,
        journal_identity: tuple[int, int],
        operation_id: str,
    ) -> dict[str, Any]:
        checked_identity = _native_identity(journal_identity, field="journal identity")
        checked_operation = _operation_id(operation_id)
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] not in {"reserved", "recovery_required"}:
            raise invalid_state("Creation output grant cannot reset publication")
        binding = self._private_binding(row)
        retained = binding["recovery"]
        if (
            retained is None
            or retained["phase"] != "publication_resetting"
            or _native_identity(retained.get("journal_identity"), field="journal identity")
            != checked_identity
            or _operation_id(retained.get("operation_id")) != checked_operation
            or binding["published_identity"] is not None
        ):
            raise conflict("Creation output publication reset authority changed")
        self._recensus(row, allow_visible=False)
        _require_reset_stage_absent(
            self.store.connection,
            row,
            checked_operation,
        )
        reset = {
            "phase": "publication_reserved",
            "expected_manifest_hash": binding["expected_manifest_hash"],
            "expected_tree_hash": binding["expected_tree_hash"],
        }
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET recovery_json = ? WHERE grant_id = ? "
            "AND reserved_job_id = ? AND state IN ('reserved', 'recovery_required') "
            "AND generation = ? AND recovery_json = ? "
            "AND published_dev IS NULL AND published_ino IS NULL",
            (
                encode_json(reset),
                record["grant_id"],
                job_id,
                record["generation"],
                row["recovery_json"],
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation output publication reset lost its CAS")
        self._recensus(row, allow_visible=False)
        _require_reset_stage_absent(
            self.store.connection,
            row,
            checked_operation,
        )
        return self._private_binding(self._reserved_row(job_id))

    def resume_for_job(self, job_id: str) -> dict[str, Any]:
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] not in {"reserved", "recovery_required"}:
            raise invalid_state("Creation output grant cannot resume publication")
        binding = self._private_binding(row)
        self._recensus(row, allow_visible=None)
        if binding["recovery"] is None:
            if record["state"] != "reserved":
                raise conflict("Creation output recovery evidence is unavailable")
            return record
        updated = dict(record)
        updated["state"] = "reserved"
        updated["generation"] += 1
        updated["updated_at"] = utc_now()
        validate_studio_creation_output_grant_v6(updated)
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET state = 'reserved', record_json = ?, "
            "generation = ? WHERE grant_id = ? AND reserved_job_id = ? "
            "AND state IN ('reserved', 'recovery_required') AND generation = ?",
            (
                encode_json(updated),
                updated["generation"],
                record["grant_id"],
                job_id,
                record["generation"],
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation output recovery resume lost its CAS")
        return updated

    def rollback_for_job(self, job_id: str) -> dict[str, Any]:
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] not in {"reserved", "recovery_required"}:
            raise invalid_state("Creation output grant cannot be rolled back")
        self._recensus(row, allow_visible=False)
        return self._transition(
            record,
            state="ready",
            clear_reservation=True,
            clear_hashes=True,
        )

    def note_publication_verified(
        self,
        job_id: str,
        *,
        published_identity: tuple[int, int],
        journal_identity: tuple[int, int] | None = None,
        operation_id: str | None = None,
        stage_identity: tuple[int, int] | None = None,
        journal_payload_sha256: str | None = None,
        journal_payload_state: str | None = None,
    ) -> dict[str, Any]:
        checked_published = _native_identity(published_identity, field="published identity")
        journal_authority = (
            journal_identity,
            operation_id,
            stage_identity,
            journal_payload_sha256,
            journal_payload_state,
        )
        if any(item is not None for item in journal_authority) and not all(
            item is not None for item in journal_authority
        ):
            raise conflict("Creation output publication journal authority is incomplete")
        checked_journal = (
            None
            if journal_identity is None
            else _native_identity(journal_identity, field="journal identity")
        )
        checked_operation = None if operation_id is None else _operation_id(operation_id)
        checked_stage = (
            None
            if stage_identity is None
            else _native_identity(stage_identity, field="stage identity")
        )
        checked_payload = (
            None
            if journal_payload_sha256 is None
            else _digest(journal_payload_sha256, field="journal_payload_sha256")
        )
        checked_payload_state = (
            None
            if journal_payload_state is None
            else _journal_payload_state(
                journal_payload_state,
                field="journal payload state",
            )
        )
        if checked_payload_state is not None and checked_payload_state != "ready":
            raise conflict("Published creation output is not bound to a ready journal")
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] not in {"reserved", "recovery_required"}:
            raise invalid_state("Creation output grant cannot record publication")
        binding = self._private_binding(row)
        retained_identity = binding["published_identity"]
        if retained_identity is not None and retained_identity != checked_published:
            raise conflict("Published creation output identity changed")
        retained_recovery = binding["recovery"]
        if retained_recovery is None or retained_recovery["phase"] in {
            "publication_reserved",
            "publication_stage_allocated",
            "publication_resetting",
        }:
            raise conflict("Creation output publication journal was not bound")
        retained_journal = (
            None
            if "journal_identity" not in retained_recovery
            else _native_identity(
                retained_recovery["journal_identity"],
                field="journal identity",
            )
        )
        retained_operation = (
            None
            if "operation_id" not in retained_recovery
            else _operation_id(retained_recovery["operation_id"])
        )
        if retained_journal is not None and (
            checked_journal is not None
            and checked_journal != retained_journal
            or checked_operation is not None
            and checked_operation != retained_operation
        ):
            raise conflict("Published creation output journal authority changed")
        if retained_recovery["phase"] in {"publication_started", "publication_staged"} and (
            retained_journal is not None and (checked_journal is None or checked_operation is None)
        ):
            raise conflict("Published creation output journal authority is required")
        if retained_journal is not None and retained_recovery["phase"] not in {
            "publication_staged",
            "publication_verified",
        }:
            raise conflict("Published creation output journal has not reached its stage")
        retained_stage = (
            None
            if "stage_identity" not in retained_recovery
            else _native_identity(retained_recovery["stage_identity"], field="stage identity")
        )
        if retained_stage is not None and (
            retained_stage != checked_published or checked_stage != retained_stage
        ):
            raise conflict("Published creation output stage authority changed")
        if retained_journal is not None and (
            checked_payload is None
            or checked_payload_state != "ready"
            or retained_recovery.get("journal_payload_state") not in {"copying", "ready"}
        ):
            raise conflict("Published creation output journal payload authority changed")
        if retained_recovery["phase"] == "publication_verified" and (
            tuple(retained_recovery["published_identity"]) != checked_published
            or retained_recovery.get("journal_payload_sha256") != checked_payload
            or retained_recovery.get("journal_payload_state") != checked_payload_state
        ):
            raise conflict("Published creation output recovery identity changed")
        path = Path(row["absolute_path"])
        info = path_file_stat(path)
        if (
            _is_link_or_reparse(info)
            or not stat.S_ISDIR(info.st_mode)
            or (int(info.st_dev), int(info.st_ino)) != checked_published
        ):
            raise conflict("Published creation output identity changed")
        recovery = {
            "phase": "publication_verified",
            "expected_manifest_hash": row["expected_manifest_hash"],
            "expected_tree_hash": row["expected_tree_hash"],
            "published_identity": [checked_published[0], checked_published[1]],
        }
        if retained_journal is not None:
            recovery["journal_identity"] = list(retained_journal)
            recovery["operation_id"] = retained_operation
            recovery["journal_payload_sha256"] = checked_payload
            recovery["journal_payload_state"] = checked_payload_state
        if retained_stage is not None:
            recovery["stage_identity"] = list(retained_stage)
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET published_dev = ?, published_ino = ?, "
            "recovery_json = ? WHERE grant_id = ? AND reserved_job_id = ? "
            "AND state IN ('reserved', 'recovery_required') AND generation = ? "
            "AND recovery_json = ? "
            "AND ((published_dev IS NULL AND published_ino IS NULL) "
            "OR (published_dev = ? AND published_ino = ?))",
            (
                str(checked_published[0]),
                str(checked_published[1]),
                encode_json(recovery),
                record["grant_id"],
                job_id,
                record["generation"],
                row["recovery_json"],
                str(checked_published[0]),
                str(checked_published[1]),
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation output publication evidence lost its CAS")
        return self._private_binding(self._reserved_row(job_id))

    def release_for_job(self, job_id: str) -> dict[str, Any]:
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] != "reserved":
            raise invalid_state("Only an unmutated reserved output grant may be released")
        self._recensus(row, allow_visible=False)
        return self._transition(record, state="ready", clear_reservation=True, clear_hashes=True)

    def mark_published(
        self,
        job_id: str,
        *,
        publication: Mapping[str, object],
        published_identity: tuple[int, int],
        _verify_transition: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        row = self._reserved_row(job_id)
        record = self._validated_row(row)
        if record["state"] not in {"reserved", "recovery_required"}:
            raise invalid_state("Creation output grant is not reserved for publication")
        binding = self._private_binding(row)
        info = path_file_stat(binding["path"])
        expected_mode = (
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            if record["kind"] == "game_package_file"
            else stat.S_ISDIR(info.st_mode)
        )
        if (
            _is_link_or_reparse(info)
            or not expected_mode
            or (int(info.st_dev), int(info.st_ino)) != published_identity
        ):
            raise conflict("Published creation output identity changed")
        if _verify_transition is not None:
            _verify_transition(binding)
        updated = dict(record)
        updated["state"] = "published"
        updated["publication"] = dict(publication)
        updated["generation"] += 1
        updated["updated_at"] = utc_now()
        validate_studio_creation_output_grant_v6(updated)
        cursor = self.store.connection.execute(
            "UPDATE creation_output_grants SET state = 'published', record_json = ?, "
            "generation = ?, published_dev = ?, published_ino = ?, recovery_json = NULL "
            "WHERE grant_id = ? AND reserved_job_id = ? AND state IN "
            "('reserved', 'recovery_required') AND generation = ?",
            (
                encode_json(updated),
                updated["generation"],
                str(published_identity[0]),
                str(published_identity[1]),
                record["grant_id"],
                job_id,
                record["generation"],
            ),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation output grant publication lost its CAS")
        if _verify_transition is not None:
            _verify_transition(binding)
        return updated

    def _transition(
        self,
        record: Mapping[str, Any],
        *,
        state: str,
        clear_reservation: bool,
        clear_hashes: bool = False,
    ) -> dict[str, Any]:
        updated = dict(record)
        updated["state"] = state
        updated["generation"] += 1
        updated["updated_at"] = utc_now()
        if state != "published":
            updated["publication"] = None
        validate_studio_creation_output_grant_v6(updated)
        fields = ["state = ?", "record_json = ?", "generation = ?"]
        values: list[object] = [state, encode_json(updated), updated["generation"]]
        if clear_reservation:
            fields.append("reserved_job_id = NULL")
        if clear_hashes:
            fields.extend(
                [
                    "expected_manifest_hash = NULL",
                    "expected_tree_hash = NULL",
                    "expected_archive_sha256 = NULL",
                    "expected_size_bytes = NULL",
                    "published_dev = NULL",
                    "published_ino = NULL",
                    "recovery_json = NULL",
                ]
            )
        values.extend([record["grant_id"], record["generation"]])
        cursor = self.store.connection.execute(
            f"UPDATE creation_output_grants SET {', '.join(fields)} "
            "WHERE grant_id = ? AND generation = ?",
            tuple(values),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation output grant transition lost its CAS")
        return updated

    def _recensus(self, row: sqlite3.Row, *, allow_visible: bool | None) -> None:
        path = Path(row["absolute_path"])
        parent = _pin_directory(path.parent, context="Creation output parent")
        expected_parent = (int(row["parent_dev"]), int(row["parent_ino"]))
        if parent != expected_parent or path.name != row["normalized_leaf"]:
            raise conflict("Creation output parent identity changed")
        try:
            info = path_file_stat(path)
        except FileNotFoundError:
            if allow_visible is True:
                raise conflict("Creation output disappeared") from None
            return
        except OSError:
            raise conflict("Creation output cannot be inspected") from None
        if allow_visible is False:
            raise conflict("Creation output target is no longer absent")
        expected_mode = (
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            if row["kind"] == "game_package_file"
            else stat.S_ISDIR(info.st_mode)
        )
        if _is_link_or_reparse(info) or not expected_mode:
            raise conflict("Creation output is not a plain directory")

    def _private_binding(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "path": Path(row["absolute_path"]),
            "parent_identity": (int(row["parent_dev"]), int(row["parent_ino"])),
            "leaf": row["normalized_leaf"],
            "generation": int(row["generation"]),
            "expected_manifest_hash": row["expected_manifest_hash"],
            "expected_tree_hash": row["expected_tree_hash"],
            "expected_archive_sha256": row["expected_archive_sha256"],
            "expected_size_bytes": (
                None if row["expected_size_bytes"] is None else int(row["expected_size_bytes"])
            ),
            "published_identity": (
                None
                if row["published_dev"] is None or row["published_ino"] is None
                else (int(row["published_dev"]), int(row["published_ino"]))
            ),
            "recovery": (
                None
                if row["recovery_json"] is None
                else _validated_recovery(
                    decode_object(row["recovery_json"], context="creation output recovery")
                )
            ),
        }

    def _reserved_row(self, job_id: str) -> sqlite3.Row:
        row = self.store.connection.execute(
            "SELECT * FROM creation_output_grants WHERE reserved_job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise conflict("Creation output grant reservation is unavailable")
        return row

    def _row(self, grant_id: object) -> sqlite3.Row:
        identifier = _identifier(grant_id, field="grant_id")
        row = self.store.connection.execute(
            "SELECT * FROM creation_output_grants WHERE grant_id = ?",
            (identifier,),
        ).fetchone()
        if row is None:
            raise not_found(f"Creation output grant {identifier} was not found")
        return row

    @staticmethod
    def _validated_row(row: sqlite3.Row) -> dict[str, Any]:
        record = decode_object(row["record_json"], context="creation output grant")
        try:
            checked = validate_studio_creation_output_grant_v6(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored creation output grant is invalid") from exc
        if (
            checked["grant_id"] != row["grant_id"]
            or checked["state"] != row["state"]
            or checked["generation"] != int(row["generation"])
            or checked["workspace_id"] != row["workspace_id"]
            or checked["kind"] != row["kind"]
        ):
            raise StudioError("internal_error", "Stored creation output grant projection changed")
        return checked
