from __future__ import annotations

import hashlib
import hmac
import sqlite3
import stat
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.file_stat import path_file_stat
from isoworld.content.portability import portable_relative_path
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.creation_contracts import (
    MAX_CREATION_CONTRACT_BYTES,
    CreationContractError,
    LoadedCreationProject,
    canonical_creation_hash,
    load_creation_project,
)
from worldforge.creation_readiness import CreationReadinessError, build_creation_readiness
from worldforge.creation_scaffold import (
    CREATION_SCAFFOLD_OPERATION_REASON_CODES,
    CreationScaffoldError,
    create_creation_project,
    normalize_creation_scaffold_facets,
)
from worldforge.creation_workflow import (
    PHASE_IDS,
    CreationWorkflowError,
    complete_creation_phase_inline_locked,
    reconcile_creation_workflow_locked,
    reopen_creation_phase_locked,
    validate_creation_phase_inline_locked,
    validate_creation_workflow_status,
    validate_recorded_creation_workflow_status,
)
from worldforge.directory_publish import (
    DirectoryPublishError,
    DirectoryPublishIndeterminateError,
    append_append_only_journal,
    create_append_only_journal,
    directory_identity,
    fsync_directory,
    read_append_only_journal_history_state,
    remove_append_only_journal,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.phase_report_v3 import (
    PhaseReportV3Error,
    document_identity,
    validate_artifact_identity,
)
from worldforge.studio.changesets import read_workspace_file_snapshot
from worldforge.studio.contracts import (
    CREATION_WORKSPACE_FORMAT,
    ENTITY_ID_PATTERN,
    SHA256_PATTERN,
    WORKSPACE_ID_PATTERN,
    validate_studio_creation_workspace,
)
from worldforge.studio.creation_grants import CreationRootGrantManager
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
from worldforge.world_lock import exclusive_world_lifecycle

_MAX_DOCUMENTS = 1024
_MAX_DOCUMENT_BYTES = min(MAX_CREATION_CONTRACT_BYTES, 16 * 1024 * 1024)
_MAX_JOURNAL_RECORD_BYTES = 64 * 1024
_MAX_JOURNAL_FILE_BYTES = 256 * 1024
_STATUS_PATH = PurePosixPath(".worldforge/status.json")
_CREATION_PHASES = (
    "before_publication",
    "target_published",
    "workspace_committed",
    "grant_consumed",
    "cleanup_authorized",
)
_CREATION_PHASE_INDEX = {phase: index for index, phase in enumerate(_CREATION_PHASES)}
_SAFE_SCAFFOLD_REASON_CODE_FALLBACK = "creation_scaffold_failed"
_SAFE_SCAFFOLD_REASON_CODE_MAX = 64
_SAFE_SCAFFOLD_REASON_CODES = frozenset(
    {
        _SAFE_SCAFFOLD_REASON_CODE_FALLBACK,
        "creation_scaffold_inputs_invalid",
        "creation_scaffold_recovery_required",
        *CREATION_SCAFFOLD_OPERATION_REASON_CODES,
    }
)


def _is_safe_reason_code(value: str) -> bool:
    return (
        0 < len(value) <= _SAFE_SCAFFOLD_REASON_CODE_MAX
        and value[0].islower()
        and value.isascii()
        and all(
            character.islower() or character.isdigit() or character == "_" for character in value
        )
    )


def _bounded_scaffold_failure_details(
    exc: CreationScaffoldError,
    *,
    phase: str,
) -> dict[str, str]:
    reason_code = exc.reason_code
    if (
        not isinstance(reason_code, str)
        or not _is_safe_reason_code(reason_code)
        or reason_code not in _SAFE_SCAFFOLD_REASON_CODES
    ):
        reason_code = _SAFE_SCAFFOLD_REASON_CODE_FALLBACK
    if phase not in _CREATION_PHASE_INDEX:
        phase = "before_publication"
    return {"reason_code": reason_code, "phase": phase}


_PHASE_REPORT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "project",
        "profile",
        "source_manifest",
        "phase",
        "status",
        "rationale",
        "evidence",
        "output_evidence",
        "reviewer",
        "invalidation_dependencies",
        "extensions",
        "content_hash",
    }
)
_NOT_APPLICABLE_RATIONALES = {
    "p03_geography": "world_absent",
    "p04_timeline": "chronology_absent",
    "p05_societies": "group_structures_absent",
    "p06_characters": "actors_absent",
    "p08_world_arcs": "narrative_absent",
    "p11_art_audio": "assets_not_applicable",
    "p12_asset_specs": "assets_not_applicable",
    "p13_asset_production": "runtime_not_applicable",
}


def _identifier(value: object, *, field: str, workspace: bool = False) -> str:
    pattern = WORKSPACE_ID_PATTERN if workspace else ENTITY_ID_PATTERN
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise invalid_request(f"{field} is not a valid identifier")
    return value


def _expected_hash(value: object, *, field: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise invalid_request(f"{field} must be a lowercase SHA-256 digest")
    return value


def _expected_nullable_hash(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _expected_hash(value, field=field)


def _expected_generation(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise invalid_request(f"{field} must be a non-negative integer")
    return value


def _document_id(document: dict[str, Any]) -> str:
    try:
        return str(document_identity(document)["id"])
    except PhaseReportV3Error as exc:
        raise StudioError(
            "internal_error", "Generic project document identity is unsupported"
        ) from exc


def _phase_identity(value: object, *, context: str) -> dict[str, Any]:
    try:
        return validate_artifact_identity(value, context=context)
    except PhaseReportV3Error:
        raise
    except (TypeError, ValueError) as exc:
        raise PhaseReportV3Error(f"{context} is invalid") from exc


def _phase_reviewer(value: object, *, context: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"id", "role"}:
        raise PhaseReportV3Error(f"{context} is invalid")
    reviewer_id = value.get("id")
    role = value.get("role")
    if (
        not isinstance(reviewer_id, str)
        or ENTITY_ID_PATTERN.fullmatch(reviewer_id) is None
        or not isinstance(role, str)
        or ENTITY_ID_PATTERN.fullmatch(role) is None
    ):
        raise PhaseReportV3Error(f"{context} is invalid")
    return {"id": reviewer_id, "role": role}


def _validate_recorded_phase_report(
    value: object,
    *,
    phase_id: str,
    reference: Mapping[str, Any],
    status: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _PHASE_REPORT_FIELDS:
        raise PhaseReportV3Error("recorded phase report fields are invalid")
    report = value
    if report.get("format") != "world-forge.phase_report" or report.get("format_version") != 3:
        raise PhaseReportV3Error("recorded phase report version is unsupported")
    if (
        report.get("phase") != phase_id
        or report.get("status") != reference.get("status")
        or report.get("content_hash") != reference.get("content_hash")
        or report.get("invalidation_dependencies") != reference.get("invalidation_dependencies")
    ):
        raise PhaseReportV3Error("recorded phase report does not match workflow history")
    for field in ("project", "profile", "source_manifest"):
        identity = _phase_identity(report.get(field), context=f"phase report {field}")
        if identity != status.get(field):
            raise PhaseReportV3Error("recorded phase report project binding changed")

    rationale = report.get("rationale")
    if not isinstance(rationale, dict) or set(rationale) != {"code", "message"}:
        raise PhaseReportV3Error("recorded phase report rationale is invalid")
    message = rationale.get("message")
    expected_rationale = (
        "phase_ready" if report["status"] == "ready" else _NOT_APPLICABLE_RATIONALES.get(phase_id)
    )
    if rationale.get("code") != expected_rationale or not isinstance(message, str) or not message:
        raise PhaseReportV3Error("recorded phase report rationale is invalid")

    reviewer = _phase_reviewer(report.get("reviewer"), context="phase report reviewer")
    evidence = report.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise PhaseReportV3Error("recorded phase report evidence is invalid")
    evidence_ids: list[str] = []
    direct_identities = [
        _phase_identity(report[field], context=f"phase report {field}")
        for field in ("project", "profile", "source_manifest")
    ]
    for index, item in enumerate(evidence):
        if not isinstance(item, dict) or set(item) != {"evidence_id", "claim", "subject"}:
            raise PhaseReportV3Error("recorded phase report evidence is invalid")
        evidence_id = item.get("evidence_id")
        claim = item.get("claim")
        if (
            not isinstance(evidence_id, str)
            or ENTITY_ID_PATTERN.fullmatch(evidence_id) is None
            or not isinstance(claim, str)
            or not claim
        ):
            raise PhaseReportV3Error("recorded phase report evidence is invalid")
        evidence_ids.append(evidence_id)
        direct_identities.append(
            _phase_identity(item.get("subject"), context=f"phase report evidence/{index}")
        )
    if evidence_ids != sorted(evidence_ids, key=lambda item: item.encode("utf-8")) or len(
        {item.casefold() for item in evidence_ids}
    ) != len(evidence_ids):
        raise PhaseReportV3Error("recorded phase report evidence order is invalid")

    output = report.get("output_evidence")
    if report["status"] == "not_applicable":
        if output is not None:
            raise PhaseReportV3Error("not-applicable phase report has output evidence")
    else:
        expected_output_fields = {
            "format",
            "format_version",
            "id",
            "phase",
            "role",
            "subject",
            "reviewer",
            "content_hash",
        }
        if not isinstance(output, dict) or set(output) != expected_output_fields:
            raise PhaseReportV3Error("recorded phase output evidence is invalid")
        if (
            output.get("format") != "world-forge.phase_output_evidence"
            or output.get("format_version") != 2
            or output.get("phase") != phase_id
            or _phase_reviewer(output.get("reviewer"), context="phase output reviewer") != reviewer
            or canonical_creation_hash(output) != output.get("content_hash")
        ):
            raise PhaseReportV3Error("recorded phase output evidence is invalid")
        direct_identities.append(
            _phase_identity(output.get("subject"), context="phase output subject")
        )

    dependencies = report.get("invalidation_dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise PhaseReportV3Error("recorded phase report dependencies are invalid")
    dependency_identities = [
        _phase_identity(item, context=f"phase report dependency/{index}")
        for index, item in enumerate(dependencies)
    ]
    dependency_keys = {
        (item["format"], item["format_version"], item["id"], item["content_hash"])
        for item in dependency_identities
    }
    if len(dependency_keys) != len(dependency_identities):
        raise PhaseReportV3Error("recorded phase report dependencies contain duplicates")
    if any(
        (item["format"], item["format_version"], item["id"], item["content_hash"])
        not in dependency_keys
        for item in direct_identities
    ):
        raise PhaseReportV3Error("recorded phase report dependencies are incomplete")
    extensions = report.get("extensions")
    if not isinstance(extensions, list):
        raise PhaseReportV3Error("recorded phase report extensions are invalid")
    if canonical_creation_hash(report) != report.get("content_hash"):
        raise PhaseReportV3Error("recorded phase report content hash does not match")
    return report.copy()


def _module_paths(project: LoadedCreationProject) -> list[tuple[PurePosixPath, dict[str, Any]]]:
    result = [
        (PurePosixPath("project.json"), project.project),
        (PurePosixPath(project.project["profile"]["path"]), project.profile),
        (PurePosixPath(project.project["source_manifest"]["path"]), project.manifest),
    ]
    manifest_root = PurePosixPath(project.project["source_manifest"]["path"]).parent
    collections = (
        ("world_modules", project.world_modules),
        ("activity_modules", project.activity_modules),
        ("narrative_modules", project.narrative_modules),
        ("system_modules", project.system_modules),
        ("logic_modules", project.logic_modules),
    )
    for collection_name, documents in collections:
        references = project.manifest["modules"][collection_name]
        if len(references) != len(documents):
            raise StudioError("internal_error", "Generic project module graph diverged")
        result.extend(
            (manifest_root / PurePosixPath(reference["path"]), document)
            for reference, document in zip(references, documents, strict=True)
        )
    if len(result) > _MAX_DOCUMENTS:
        raise invalid_request("Generic project exceeds Studio document limits")
    by_key: dict[tuple[str, ...], PurePosixPath] = {}
    for relative, _document in result:
        if portable_relative_path(relative.as_posix()) != relative:
            raise StudioError("internal_error", "Generic project contains a non-portable path")
        key = tuple(part.casefold() for part in relative.parts)
        if key in by_key:
            raise StudioError("internal_error", "Generic project document paths collide")
        by_key[key] = relative
    return sorted(result, key=lambda item: item[0].as_posix().encode("utf-8"))


def _source_snapshot(
    root: Path,
    expected_root_identity: tuple[int, int],
) -> tuple[LoadedCreationProject, list[dict[str, Any]], str]:
    try:
        with _pinned_ancestor_identities(root, context="Creation workspace root") as identities:
            if identities[-1] != expected_root_identity:
                raise conflict("Creation workspace root identity changed")
            project = load_creation_project(root / "project.json")
            summaries: list[dict[str, Any]] = []
            for relative, canonical in _module_paths(project):
                payload = read_workspace_file_snapshot(
                    root,
                    relative,
                    world_identity=expected_root_identity,
                    context=f"creation document {relative.as_posix()}",
                    limit=_MAX_DOCUMENT_BYTES,
                )
                try:
                    decoded = decode_json_object(payload, source=relative.as_posix())
                except RuntimeIOError as exc:
                    raise conflict("Creation workspace document is no longer strict JSON") from exc
                if decoded != canonical:
                    raise conflict("Creation workspace changed during its integral snapshot")
                summaries.append(
                    {
                        "path": relative.as_posix(),
                        "format": canonical["format"],
                        "format_version": canonical["format_version"],
                        "id": _document_id(canonical),
                        "content_hash": canonical["content_hash"],
                        "file_sha256": hashlib.sha256(payload).hexdigest(),
                    }
                )
            if (
                directory_identity(root, context="creation workspace root")
                != expected_root_identity
            ):
                raise conflict("Creation workspace root identity changed")
    except CreationContractError as exc:
        raise conflict(exc.detail, reason_code=exc.reason_code) from exc
    except StudioError:
        raise
    except (OSError, ValueError) as exc:
        raise conflict("Creation workspace is no longer an integral generic project") from exc
    revision_payload = encode_json({"documents": summaries}).encode("utf-8")
    return project, summaries, hashlib.sha256(revision_payload).hexdigest()


def _invalid_workflow_snapshot(
    root: Path,
    root_identity: tuple[int, int],
    *,
    status_hash: str | None = None,
) -> dict[str, Any]:
    if directory_identity(root, context="creation workspace root") != root_identity:
        raise conflict("Creation workspace root identity changed") from None
    return {
        "state": "invalid",
        "status_hash": status_hash,
        "current_phase": None,
        "revision": None,
        "status": None,
    }


def _workflow_snapshot(
    root: Path,
    project: LoadedCreationProject,
    root_identity: tuple[int, int],
) -> dict[str, Any]:
    try:
        info = path_file_stat(root / _STATUS_PATH)
    except FileNotFoundError:
        return {
            "state": "missing",
            "status_hash": None,
            "current_phase": None,
            "revision": None,
            "status": None,
        }
    except OSError:
        return _invalid_workflow_snapshot(root, root_identity)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return _invalid_workflow_snapshot(root, root_identity)
    try:
        payload = read_workspace_file_snapshot(
            root,
            _STATUS_PATH,
            world_identity=root_identity,
            context="creation workflow status",
            limit=_MAX_DOCUMENT_BYTES,
        )
    except (OSError, StudioError):
        return _invalid_workflow_snapshot(root, root_identity)
    file_hash = hashlib.sha256(payload).hexdigest()
    try:
        decoded = decode_json_object(payload, source=_STATUS_PATH.as_posix())
        recorded_status = validate_recorded_creation_workflow_status(decoded)
    except (CreationWorkflowError, RuntimeIOError, TypeError, ValueError):
        return _invalid_workflow_snapshot(root, root_identity, status_hash=file_hash)
    try:
        status = validate_creation_workflow_status(recorded_status, project)
    except CreationWorkflowError:
        return _invalid_workflow_snapshot(
            root,
            root_identity,
            status_hash=recorded_status["content_hash"],
        )
    if directory_identity(root, context="creation workspace root") != root_identity:
        raise conflict("Creation workspace root identity changed")
    complete = len(status["completed_phases"]) == len(PHASE_IDS)
    not_started = (
        status["revision"] == 0
        and not status["reports"]
        and not status["completed_phases"]
        and not status["invalidated_reports"]
    )
    return {
        "state": "complete" if complete else "not_started" if not_started else "active",
        "status_hash": status["content_hash"],
        "current_phase": status["current_phase"],
        "revision": status["revision"],
        "status": status,
    }


def _remove_journal(
    path: Path,
    history: tuple[bytes, ...],
    identity: tuple[int, int],
) -> None:
    if not history:
        raise StudioError("internal_error", "Creation journal history is empty")
    try:
        remove_append_only_journal(
            path,
            expected_identity=identity,
            expected_payload=history[-1],
            expected_history=history,
            max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
            max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
        )
    except DirectoryPublishIndeterminateError as exc:
        raise StudioError(
            "recovery_ambiguous", "Creation journal cleanup is indeterminate"
        ) from exc
    except DirectoryPublishError as exc:
        raise StudioError("recovery_failed", "Creation journal cleanup failed") from exc


def _creation_journal_base(
    *,
    workspace_id: str,
    grant_id: str,
    reservation_generation: int,
    root: Path,
    parent_identity: tuple[int, int],
    creation_spec: dict[str, Any],
) -> dict[str, Any]:
    return {
        "format": "world-forge.studio_creation_journal",
        "format_version": 1,
        "workspace_id": workspace_id,
        "grant_id": grant_id,
        "reservation_generation": reservation_generation,
        "target": str(root),
        "parent_identity": [parent_identity[0], parent_identity[1]],
        "creation_spec": creation_spec,
    }


def _journal_payload(
    base: dict[str, Any],
    *,
    phase: str,
    root_identity: tuple[int, int] | None,
) -> bytes:
    if phase not in _CREATION_PHASE_INDEX:
        raise StudioError("internal_error", "Creation journal phase is unknown")
    phase_index = _CREATION_PHASE_INDEX[phase]
    if phase_index >= _CREATION_PHASE_INDEX["target_published"] and root_identity is None:
        raise StudioError("internal_error", "Creation journal root identity is missing")
    reservation_generation = int(base["reservation_generation"])
    return canonical_json_bytes(
        {
            **base,
            "phase": phase,
            "root_identity": (
                None if root_identity is None else [root_identity[0], root_identity[1]]
            ),
            "workspace_generation": (
                0 if phase_index >= _CREATION_PHASE_INDEX["workspace_committed"] else None
            ),
            "grant_generation": (
                reservation_generation + 1
                if phase_index >= _CREATION_PHASE_INDEX["grant_consumed"]
                else reservation_generation
            ),
        }
    )


def _expected_journal_history(
    base: dict[str, Any],
    *,
    through_phase: str,
    root_identity: tuple[int, int] | None,
) -> tuple[bytes, ...]:
    through = _CREATION_PHASE_INDEX[through_phase]
    return tuple(
        _journal_payload(
            base,
            phase=phase,
            root_identity=(
                root_identity if index >= _CREATION_PHASE_INDEX["target_published"] else None
            ),
        )
        for index, phase in enumerate(_CREATION_PHASES[: through + 1])
    )


def _read_creation_journal(
    path: Path,
    base: dict[str, Any],
) -> tuple[tuple[bytes, ...], tuple[int, int], str, tuple[int, int] | None] | None:
    try:
        loaded = read_append_only_journal_history_state(
            path,
            max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
            max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
        )
    except DirectoryPublishError as exc:
        raise StudioError("recovery_ambiguous", "Creation journal is unavailable") from exc
    if loaded is None:
        return None
    history, identity, partial_tail = loaded
    if partial_tail or not history:
        raise StudioError("recovery_ambiguous", "Creation journal does not match its reservation")
    try:
        last = decode_json_object(history[-1], source="Studio creation journal")
        phase = last["phase"]
        encoded_root_identity = last["root_identity"]
        root_identity = (
            None
            if encoded_root_identity is None
            else (int(encoded_root_identity[0]), int(encoded_root_identity[1]))
        )
        expected = _expected_journal_history(
            base,
            through_phase=phase,
            root_identity=root_identity,
        )
    except (KeyError, TypeError, ValueError, RuntimeIOError, StudioError) as exc:
        raise StudioError(
            "recovery_ambiguous", "Creation journal does not match its reservation"
        ) from exc
    if history != expected:
        raise StudioError("recovery_ambiguous", "Creation journal does not match its reservation")
    return history, identity, phase, root_identity


def _create_creation_journal(
    path: Path,
    base: dict[str, Any],
) -> tuple[tuple[bytes, ...], tuple[int, int]]:
    history = _expected_journal_history(
        base,
        through_phase="before_publication",
        root_identity=None,
    )
    try:
        identity = create_append_only_journal(
            path,
            history[0],
            max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
        )
        fsync_directory(path.parent, context="Studio creation journal directory")
    except FileExistsError:
        raise StudioError(
            "recovery_ambiguous",
            "Creation journal namespace was occupied before identity binding",
        ) from None
    except DirectoryPublishError as exc:
        raise StudioError("recovery_failed", "Could not publish creation journal") from exc
    return history, identity


def _advance_creation_journal(
    path: Path,
    base: dict[str, Any],
    *,
    identity: tuple[int, int],
    current_phase: str,
    updated_phase: str,
    root_identity: tuple[int, int],
) -> tuple[bytes, ...]:
    if _CREATION_PHASE_INDEX[updated_phase] != _CREATION_PHASE_INDEX[current_phase] + 1:
        raise StudioError("internal_error", "Creation journal transition is not contiguous")
    history = _expected_journal_history(
        base,
        through_phase=current_phase,
        root_identity=root_identity,
    )
    updated_history = _expected_journal_history(
        base,
        through_phase=updated_phase,
        root_identity=root_identity,
    )
    try:
        append_append_only_journal(
            path,
            expected_identity=identity,
            expected_payload=history[-1],
            expected_history=history,
            updated_payload=updated_history[-1],
            max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
            max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
        )
    except DirectoryPublishError as exc:
        raise StudioError("recovery_ambiguous", "Creation journal transition failed") from exc
    return updated_history


class CreationWorkspaceManager:
    """Pathless registry and read-only projection for integral generic creation projects."""

    def __init__(
        self,
        store: StudioStore,
        *,
        grants: CreationRootGrantManager | None = None,
        transition_hook: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self.store = store
        self.grants = grants or CreationRootGrantManager(store)
        self._transition_hook = transition_hook

    def _notify(self, phase: str, **context: object) -> None:
        if self._transition_hook is not None:
            self._transition_hook(phase, context)

    def register(self, params: object) -> dict[str, Any]:
        parsed = self._register_params(params)
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            reserved, binding = self.grants.reserve(
                parsed["grant_id"],
                workspace_id=parsed["workspace_id"],
                expected_generation=parsed["expected_grant_generation"],
                role="existing_root",
            )
            root = binding["path"]
            root_identity = binding["root_identity"]
            assert isinstance(root, Path) and root_identity is not None
            project, summaries, source_revision = _source_snapshot(root, root_identity)
            del summaries
            if not hmac.compare_digest(
                project.project["content_hash"],
                parsed["expected_project_hash"],
            ):
                raise conflict("Creation project hash changed before registration")
            workflow = _workflow_snapshot(root, project, root_identity)
            record = self._insert_workspace(
                parsed["workspace_id"],
                root,
                root_identity,
                project,
                source_revision,
                workflow["status_hash"],
            )
            self.grants.consume(
                reserved["grant_id"],
                expected_generation=reserved["generation"],
            )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_workspace.registered",
                entity_type="creation_workspace",
                entity_id=record["workspace_id"],
                payload={"source_revision": record["source_revision"]},
                created_at=record["created_at"],
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation workspace {parsed['workspace_id']} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    def create(self, params: object) -> dict[str, Any]:
        parsed = self._create_params(params)
        attempt = self._attempt_row(parsed["workspace_id"])
        current = self.grants.get(parsed["grant_id"])
        if current["generation"] != parsed["expected_grant_generation"]:
            raise conflict("Creation root grant generation changed")
        if attempt is not None:
            return self._resume_creation_attempt(parsed, current, attempt)
        if current["state"] == "consumed":
            return self.get(parsed["workspace_id"])
        if current["state"] in {"reserved", "recovery_required"}:
            binding = (
                self.grants.reserved_binding(
                    current["grant_id"],
                    workspace_id=parsed["workspace_id"],
                )
                if current["state"] == "reserved"
                else self.grants.recovery_binding(
                    current["grant_id"],
                    workspace_id=parsed["workspace_id"],
                )
            )
            self._require_matching_creation_spec(parsed, binding)
            root = binding["path"]
            assert isinstance(root, Path)
            attempt = self._create_attempt(
                parsed,
                current,
                allow_visible_target=root.exists(),
            )
            self._notify(
                "reservation_committed",
                journal_path=self._attempt_journal_path(attempt),
            )
            return self._resume_creation_attempt(parsed, current, attempt)
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            reserved, binding = self.grants.reserve(
                parsed["grant_id"],
                workspace_id=parsed["workspace_id"],
                expected_generation=parsed["expected_grant_generation"],
                role="new_target",
                creation_spec=parsed,
            )
            attempt = self._insert_attempt(parsed, reserved)
            self.store.connection.commit()
        except BaseException:
            self.store.connection.rollback()
            raise
        del binding
        self._notify(
            "reservation_committed",
            journal_path=self._attempt_journal_path(attempt),
        )
        return self._resume_creation_attempt(parsed, reserved, attempt)

    def recover(
        self,
        workspace_id: object,
        *,
        expected_root_generation: object,
    ) -> dict[str, Any]:
        identifier = _identifier(workspace_id, field="workspace_id", workspace=True)
        expected = _expected_generation(
            expected_root_generation,
            field="expected_root_generation",
        )
        attempt = self._attempt_row(identifier)
        if attempt is None:
            record = self.get(identifier)
            if record["root_generation"] != expected:
                raise conflict("Creation workspace generation changed")
            return {"workspace": record, "state": "complete"}
        grant = self.grants.get(attempt["grant_id"])
        binding = self._attempt_binding(identifier, grant)
        recorded_spec = binding["creation_spec"]
        if not isinstance(recorded_spec, dict):
            raise StudioError("internal_error", "Creation recovery specification is missing")
        parsed = self._recorded_creation_params(
            recorded_spec,
            workspace_id=identifier,
            expected_grant_generation=grant["generation"],
        )
        record = self._resume_creation_attempt(parsed, grant, attempt)
        if record["root_generation"] != expected:
            raise conflict("Creation workspace generation changed")
        return {
            "workspace": record,
            "state": ("complete" if self._attempt_row(identifier) is None else "cleanup_pending"),
        }

    def _resume_creation_attempt(
        self,
        parsed: dict[str, Any],
        grant: dict[str, Any],
        attempt: sqlite3.Row,
    ) -> dict[str, Any]:
        if attempt["grant_id"] != parsed["grant_id"]:
            raise conflict("Creation workspace attempt belongs to another grant")
        binding = self._attempt_binding(parsed["workspace_id"], grant)
        self._require_matching_creation_spec(parsed, binding)
        root = binding["path"]
        parent_identity = binding["parent_identity"]
        recorded_spec = binding["creation_spec"]
        assert isinstance(root, Path) and parent_identity is not None
        assert isinstance(recorded_spec, dict)
        reservation_generation = int(recorded_spec["expected_grant_generation"]) + 1
        base = _creation_journal_base(
            workspace_id=parsed["workspace_id"],
            grant_id=parsed["grant_id"],
            reservation_generation=reservation_generation,
            root=root,
            parent_identity=parent_identity,
            creation_spec=recorded_spec,
        )
        journal_path = self._attempt_journal_path(attempt)
        if attempt["journal_dev"] is None:
            if attempt["journal_ino"] is not None:
                raise StudioError("internal_error", "Stored journal identity is incomplete")
            loaded = None
        else:
            if attempt["journal_ino"] is None:
                raise StudioError("internal_error", "Stored journal identity is incomplete")
            try:
                loaded = _read_creation_journal(journal_path, base)
            except StudioError:
                if (
                    attempt["phase"] == "reserved"
                    and grant["state"] == "reserved"
                    and not root.exists()
                    and not root.is_symlink()
                ):
                    self._abandon_unowned_attempt(parsed, grant)
                raise
        if loaded is None:
            if attempt["phase"] == "cleanup_authorized" and grant["state"] == "consumed":
                record = self._committed_workspace(parsed, root, attempt)
                self._try_finish_attempt_cleanup(parsed, grant)
                return record
            if attempt["phase"] != "reserved":
                raise StudioError("recovery_ambiguous", "Creation journal disappeared")
            try:
                history, journal_identity = _create_creation_journal(journal_path, base)
            except StudioError:
                if not root.exists() and not root.is_symlink() and grant["state"] == "reserved":
                    self._abandon_unowned_attempt(parsed, grant)
                raise
            attempt = self._update_attempt(
                attempt,
                phase="before_publication",
                journal_identity=journal_identity,
            )
            journal_phase = "before_publication"
            journal_root_identity = None
        else:
            history, journal_identity, journal_phase, journal_root_identity = loaded
            attempt = self._reconcile_attempt_with_journal(
                attempt,
                journal_phase=journal_phase,
                journal_identity=journal_identity,
                root_identity=journal_root_identity,
            )

        root_identity = self._attempt_root_identity(attempt) or journal_root_identity
        if root.exists() or root.is_symlink():
            if not root.is_dir() or root.is_symlink():
                raise conflict("Creation recovery target is unavailable")
            observed_identity = directory_identity(root, context="created project root")
            if root_identity is not None and observed_identity != root_identity:
                raise conflict("Created project root identity changed")
            root_identity = observed_identity
        elif _CREATION_PHASE_INDEX[journal_phase] >= _CREATION_PHASE_INDEX["target_published"]:
            raise conflict("Created project root disappeared")

        if root_identity is not None and journal_phase == "before_publication":
            history = _advance_creation_journal(
                journal_path,
                base,
                identity=journal_identity,
                current_phase=journal_phase,
                updated_phase="target_published",
                root_identity=root_identity,
            )
            journal_phase = "target_published"
            attempt = self._update_attempt(
                attempt,
                phase=journal_phase,
                root_identity=root_identity,
            )
            self._notify("target_published", journal_path=journal_path, root=root)

        if root_identity is None:
            self._notify("before_publication", journal_path=journal_path)
            self.grants.recensus(
                grant["grant_id"],
                workspace_id=parsed["workspace_id"],
                allow_visible_target=False,
                expected_generation=grant["generation"],
            )
            try:
                create_creation_project(
                    root,
                    project_id=parsed["project_id"],
                    title=parsed["title"],
                    default_locale=parsed["default_locale"],
                    project_version=parsed["project_version"],
                    project_kind=parsed["project_kind"],
                    gameplay_family=parsed["gameplay_family"],
                    initial_core_verb=parsed["initial_core_verb"],
                    initial_core_loop=parsed["initial_core_loop"],
                    world_presence=parsed["world_presence"],
                    narrative_requirement=parsed["narrative_requirement"],
                    narrative_authorship=parsed["narrative_authorship"],
                    narrative_topology=parsed["narrative_topology"],
                    presentation_mode=parsed["presentation_mode"],
                    runtime_support_intent=parsed["runtime_support_intent"],
                    asset_content_mode=parsed["asset_content_mode"],
                )
            except CreationScaffoldError as exc:
                if root.exists() or root.is_symlink():
                    self._mark_recovery_if_reserved(grant)
                else:
                    try:
                        _remove_journal(journal_path, history, journal_identity)
                    except StudioError:
                        pass
                    else:
                        self._release_owned_attempt(parsed, grant)
                raise StudioError(
                    "invalid_state",
                    "Creation failed before workspace registration",
                    details=_bounded_scaffold_failure_details(exc, phase=journal_phase),
                ) from exc
            except BaseException as exc:
                if root.exists() or root.is_symlink():
                    self._mark_recovery_if_reserved(grant)
                else:
                    try:
                        _remove_journal(journal_path, history, journal_identity)
                    except StudioError:
                        pass
                    else:
                        self._release_owned_attempt(parsed, grant)
                if isinstance(exc, StudioError):
                    raise
                raise StudioError(
                    "invalid_state", "Creation failed before workspace registration"
                ) from exc
            root_identity = directory_identity(root, context="created project root")
            history = _advance_creation_journal(
                journal_path,
                base,
                identity=journal_identity,
                current_phase=journal_phase,
                updated_phase="target_published",
                root_identity=root_identity,
            )
            journal_phase = "target_published"
            attempt = self._update_attempt(
                attempt,
                phase=journal_phase,
                root_identity=root_identity,
            )
            self._notify("target_published", journal_path=journal_path, root=root)

        try:
            self.grants.recensus(
                grant["grant_id"],
                workspace_id=parsed["workspace_id"],
                allow_visible_target=True,
                expected_generation=grant["generation"],
                expected_visible_identity=root_identity,
            )
            record, grant = self._finalize_created_project(
                parsed,
                grant,
                root,
                root_identity,
            )
        except BaseException:
            self._mark_recovery_if_reserved(grant)
            raise

        if journal_phase == "target_published":
            history = _advance_creation_journal(
                journal_path,
                base,
                identity=journal_identity,
                current_phase=journal_phase,
                updated_phase="workspace_committed",
                root_identity=root_identity,
            )
            journal_phase = "workspace_committed"
            self._notify("workspace_committed", journal_path=journal_path)
        if journal_phase == "workspace_committed":
            history = _advance_creation_journal(
                journal_path,
                base,
                identity=journal_identity,
                current_phase=journal_phase,
                updated_phase="grant_consumed",
                root_identity=root_identity,
            )
            journal_phase = "grant_consumed"
            self._notify("grant_consumed", journal_path=journal_path)
        attempt = self._attempt_row(parsed["workspace_id"])
        assert attempt is not None
        if journal_phase == "grant_consumed":
            history = _advance_creation_journal(
                journal_path,
                base,
                identity=journal_identity,
                current_phase=journal_phase,
                updated_phase="cleanup_authorized",
                root_identity=root_identity,
            )
            journal_phase = "cleanup_authorized"
            attempt = self._update_attempt(attempt, phase=journal_phase)
            self._notify("cleanup_authorized", journal_path=journal_path)
        try:
            _remove_journal(journal_path, history, journal_identity)
        except StudioError:
            # The project, workspace and consumed grant are already durable.
            # Cleanup remains an explicit bounded recovery action and must not
            # turn committed creation into a reported failure.
            return record
        self._try_finish_attempt_cleanup(parsed, grant)
        return record

    def _finalize_created_project(
        self,
        parsed: dict[str, Any],
        grant: dict[str, Any],
        root: Path,
        root_identity: tuple[int, int],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        project, summaries, source_revision = _source_snapshot(root, root_identity)
        del summaries
        if (
            project.project["project_kind"] != parsed["project_kind"]
            or project.project["project_id"] != parsed["project_id"]
            or project.project["title"] != parsed["title"].strip()
            or project.project["default_locale"] != parsed["default_locale"]
            or project.project["project_version"] != parsed["project_version"]
        ):
            raise conflict("Created project does not match the reserved creation specification")
        workflow = _workflow_snapshot(root, project, root_identity)
        existing = self._raw_workspace_row(parsed["workspace_id"])
        if existing is not None:
            record = self._validated_row(existing)
            if grant["state"] != "consumed":
                raise StudioError("internal_error", "Creation commit state diverged")
            return record, grant
        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            self.grants.recensus(
                grant["grant_id"],
                workspace_id=parsed["workspace_id"],
                allow_visible_target=True,
                expected_generation=grant["generation"],
                expected_visible_identity=root_identity,
            )
            record = self._insert_workspace(
                parsed["workspace_id"],
                root,
                root_identity,
                project,
                source_revision,
                workflow["status_hash"],
            )
            consumed = self.grants.consume(
                grant["grant_id"],
                expected_generation=grant["generation"],
                created_root_identity=root_identity,
                retain_reservation=True,
            )
            self.grants.recensus(
                consumed["grant_id"],
                workspace_id=parsed["workspace_id"],
                allow_visible_target=True,
                expected_generation=consumed["generation"],
                expected_visible_identity=root_identity,
            )
            attempt = self._attempt_row(parsed["workspace_id"])
            if attempt is None:
                raise StudioError("internal_error", "Creation workspace attempt disappeared")
            self._update_attempt(attempt, phase="grant_consumed", commit=False)
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_workspace.created",
                entity_type="creation_workspace",
                entity_id=record["workspace_id"],
                payload={"source_revision": record["source_revision"]},
                created_at=record["created_at"],
            )
            self.store.connection.commit()
            return record, consumed
        except BaseException:
            self.store.connection.rollback()
            raise

    def _insert_attempt(
        self,
        parsed: dict[str, Any],
        grant: dict[str, Any],
    ) -> sqlite3.Row:
        timestamp = utc_now()
        journal_name = f"creation-{parsed['workspace_id']}-{uuid.uuid4().hex}.journal"
        self.store.connection.execute(
            "INSERT INTO creation_workspace_attempts "
            "(workspace_id, grant_id, phase, journal_name, journal_dev, journal_ino, "
            "root_dev, root_ino, generation, created_at, updated_at) "
            "VALUES (?, ?, 'reserved', ?, NULL, NULL, NULL, NULL, 0, ?, ?)",
            (
                parsed["workspace_id"],
                grant["grant_id"],
                journal_name,
                timestamp,
                timestamp,
            ),
        )
        attempt = self._attempt_row(parsed["workspace_id"])
        if attempt is None:
            raise StudioError("internal_error", "Creation workspace attempt was not persisted")
        return attempt

    def _create_attempt(
        self,
        parsed: dict[str, Any],
        grant: dict[str, Any],
        *,
        allow_visible_target: bool,
    ) -> sqlite3.Row:
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            self.grants.recensus(
                grant["grant_id"],
                workspace_id=parsed["workspace_id"],
                allow_visible_target=allow_visible_target,
                expected_generation=grant["generation"],
            )
            attempt = self._insert_attempt(parsed, grant)
            self.store.connection.commit()
            return attempt
        except BaseException:
            self.store.connection.rollback()
            raise

    def _attempt_row(self, workspace_id: str) -> sqlite3.Row | None:
        row = self.store.connection.execute(
            "SELECT * FROM creation_workspace_attempts WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            return None
        if row["phase"] not in {"reserved", *_CREATION_PHASES}:
            raise StudioError("internal_error", "Stored creation attempt phase is invalid")
        name = row["journal_name"]
        if (
            not isinstance(name, str)
            or not name.startswith(f"creation-{workspace_id}-")
            or not name.endswith(".journal")
            or Path(name).name != name
        ):
            raise StudioError("internal_error", "Stored creation journal name is invalid")
        return row

    def _attempt_journal_path(self, attempt: sqlite3.Row) -> Path:
        return self.store.journals_dir / str(attempt["journal_name"])

    @staticmethod
    def _attempt_root_identity(attempt: sqlite3.Row) -> tuple[int, int] | None:
        if attempt["root_dev"] is None:
            if attempt["root_ino"] is not None:
                raise StudioError("internal_error", "Stored creation root identity is incomplete")
            return None
        if attempt["root_ino"] is None:
            raise StudioError("internal_error", "Stored creation root identity is incomplete")
        return int(attempt["root_dev"]), int(attempt["root_ino"])

    def _update_attempt(
        self,
        attempt: sqlite3.Row,
        *,
        phase: str,
        journal_identity: tuple[int, int] | None = None,
        root_identity: tuple[int, int] | None = None,
        commit: bool = True,
    ) -> sqlite3.Row:
        if phase not in {"reserved", *_CREATION_PHASES}:
            raise StudioError("internal_error", "Creation attempt phase is invalid")
        fields = ["phase = ?", "generation = generation + 1", "updated_at = ?"]
        values: list[object] = [phase, utc_now()]
        if journal_identity is not None:
            fields.extend(["journal_dev = ?", "journal_ino = ?"])
            values.extend([str(journal_identity[0]), str(journal_identity[1])])
        if root_identity is not None:
            fields.extend(["root_dev = ?", "root_ino = ?"])
            values.extend([str(root_identity[0]), str(root_identity[1])])
        values.extend([attempt["workspace_id"], attempt["generation"]])
        cursor = self.store.connection.execute(
            f"UPDATE creation_workspace_attempts SET {', '.join(fields)} "  # noqa: S608
            "WHERE workspace_id = ? AND generation = ?",
            tuple(values),
        )
        if cursor.rowcount != 1:
            raise conflict("Creation workspace attempt changed concurrently")
        updated = self._attempt_row(attempt["workspace_id"])
        if updated is None:
            raise StudioError("internal_error", "Creation workspace attempt disappeared")
        if commit:
            self.store.connection.commit()
        return updated

    def _reconcile_attempt_with_journal(
        self,
        attempt: sqlite3.Row,
        *,
        journal_phase: str,
        journal_identity: tuple[int, int],
        root_identity: tuple[int, int] | None,
    ) -> sqlite3.Row:
        stored_journal_identity = (
            None
            if attempt["journal_dev"] is None
            else (int(attempt["journal_dev"]), int(attempt["journal_ino"]))
        )
        if stored_journal_identity is not None and stored_journal_identity != journal_identity:
            raise StudioError("recovery_ambiguous", "Creation journal identity changed")
        stored_root_identity = self._attempt_root_identity(attempt)
        if (
            stored_root_identity is not None
            and root_identity is not None
            and stored_root_identity != root_identity
        ):
            raise StudioError("recovery_ambiguous", "Creation journal root identity changed")
        attempt_index = (
            -1 if attempt["phase"] == "reserved" else _CREATION_PHASE_INDEX[attempt["phase"]]
        )
        journal_index = _CREATION_PHASE_INDEX[journal_phase]
        if journal_index > attempt_index:
            attempt = self._update_attempt(
                attempt,
                phase=journal_phase,
                journal_identity=journal_identity,
                root_identity=root_identity,
            )
        elif stored_journal_identity is None:
            attempt = self._update_attempt(
                attempt,
                phase=attempt["phase"],
                journal_identity=journal_identity,
                root_identity=root_identity,
            )
        return attempt

    def _attempt_binding(
        self,
        workspace_id: str,
        grant: dict[str, Any],
    ) -> dict[str, Any]:
        if grant["state"] == "reserved":
            return self.grants.reserved_binding(grant["grant_id"], workspace_id=workspace_id)
        if grant["state"] == "recovery_required":
            return self.grants.recovery_binding(grant["grant_id"], workspace_id=workspace_id)
        if grant["state"] == "consumed":
            return self.grants.consumed_binding(grant["grant_id"], workspace_id=workspace_id)
        raise invalid_state("Creation root grant has no resumable creation attempt")

    @staticmethod
    def _recorded_creation_params(
        recorded_spec: dict[str, Any],
        *,
        workspace_id: str,
        expected_grant_generation: int,
    ) -> dict[str, Any]:
        comparable_spec = dict(recorded_spec)
        if "project_kind" not in comparable_spec:
            legacy_fields = {
                "workspace_id",
                "grant_id",
                "expected_grant_generation",
                "project_id",
                "title",
                "default_locale",
                "project_version",
            }
            unexpected = set(comparable_spec) - legacy_fields
            if unexpected:
                raise StudioError(
                    "internal_error",
                    "Stored legacy creation reservation specification is invalid",
                )
            comparable_spec["project_kind"] = "universe_library"
        recorded_workspace_id = comparable_spec.get("workspace_id", workspace_id)
        if recorded_workspace_id != workspace_id:
            raise StudioError(
                "internal_error",
                "Stored creation reservation workspace identity changed",
            )
        comparable_spec["workspace_id"] = workspace_id
        comparable_spec["expected_grant_generation"] = expected_grant_generation
        try:
            return CreationWorkspaceManager._create_params(comparable_spec)
        except StudioError as exc:
            raise StudioError(
                "internal_error", "Stored creation reservation specification is invalid"
            ) from exc

    @staticmethod
    def _require_matching_creation_spec(
        parsed: dict[str, Any],
        binding: dict[str, Any],
    ) -> None:
        recorded_spec = binding["creation_spec"]
        if not isinstance(recorded_spec, dict):
            raise StudioError("internal_error", "Creation reservation specification is missing")
        normalized_recorded = CreationWorkspaceManager._recorded_creation_params(
            recorded_spec,
            workspace_id=parsed["workspace_id"],
            expected_grant_generation=parsed["expected_grant_generation"],
        )
        if normalized_recorded != parsed:
            raise conflict("Creation reservation specification changed")

    def _mark_recovery_if_reserved(self, grant: dict[str, Any]) -> None:
        current = self.grants.get(grant["grant_id"])
        if current["state"] != "reserved":
            return
        with self.store.connection:
            self.grants.mark_recovery_required(
                current["grant_id"],
                expected_generation=current["generation"],
            )

    def _release_owned_attempt(
        self,
        parsed: dict[str, Any],
        grant: dict[str, Any],
    ) -> None:
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.grants.get(grant["grant_id"])
            if current["state"] == "reserved":
                self.grants.release(
                    current["grant_id"],
                    expected_generation=current["generation"],
                )
            self.store.connection.execute(
                "DELETE FROM creation_workspace_attempts WHERE workspace_id = ?",
                (parsed["workspace_id"],),
            )
            self.store.connection.commit()
        except BaseException:
            self.store.connection.rollback()
            raise

    def _abandon_unowned_attempt(
        self,
        parsed: dict[str, Any],
        grant: dict[str, Any],
    ) -> None:
        # The random name is occupied by bytes that were never identity-bound
        # to this attempt. Preserve them, revoke no external authority, and
        # make the grant retryable with a fresh random name.
        self._release_owned_attempt(parsed, grant)

    def _finish_attempt_cleanup(
        self,
        parsed: dict[str, Any],
        grant: dict[str, Any],
    ) -> None:
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.grants.get(grant["grant_id"])
            if current["state"] != "consumed":
                raise invalid_state("Creation grant is not committed")
            self.grants.finish_consumed_reservation(
                current["grant_id"],
                workspace_id=parsed["workspace_id"],
                expected_generation=current["generation"],
            )
            self.store.connection.execute(
                "DELETE FROM creation_workspace_attempts WHERE workspace_id = ?",
                (parsed["workspace_id"],),
            )
            self.store.connection.commit()
        except BaseException:
            self.store.connection.rollback()
            raise

    def _try_finish_attempt_cleanup(
        self,
        parsed: dict[str, Any],
        grant: dict[str, Any],
    ) -> bool:
        try:
            self._finish_attempt_cleanup(parsed, grant)
        except Exception:
            # Workspace and grant commitment precede this private bookkeeping.
            # A later recovery call retries cleanup without changing success.
            return False
        return True

    def _committed_workspace(
        self,
        parsed: dict[str, Any],
        root: Path,
        attempt: sqlite3.Row,
    ) -> dict[str, Any]:
        row = self._raw_workspace_row(parsed["workspace_id"])
        if row is None:
            raise StudioError("internal_error", "Committed creation workspace is missing")
        record = self._validated_row(row)
        expected_identity = self._attempt_root_identity(attempt)
        if (
            expected_identity is None
            or (int(row["root_dev"]), int(row["root_ino"])) != expected_identity
        ):
            raise conflict("Committed creation workspace root identity changed")
        if Path(row["absolute_root"]) != root:
            raise conflict("Committed creation workspace root binding changed")
        return record

    def _raw_workspace_row(self, workspace_id: str) -> sqlite3.Row | None:
        return self.store.connection.execute(
            "SELECT * FROM creation_workspaces WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()

    def get(self, workspace_id: object) -> dict[str, Any]:
        row = self._row(workspace_id)
        return self._validated_row(row)

    def list(self) -> list[dict[str, Any]]:
        rows = self.store.connection.execute(
            "SELECT * FROM creation_workspaces ORDER BY workspace_id"
        ).fetchall()
        return [self._validated_row(row) for row in rows]

    def open(self, workspace_id: object) -> dict[str, Any]:
        record, _project, _summaries, revision, workflow = self._refresh_snapshot(workspace_id)
        return {
            "workspace": record,
            "route": "generic",
            "project_kind": record["project_kind"],
            "source_revision": revision,
            "workflow_status_hash": workflow["status_hash"],
            "current_phase": workflow["current_phase"],
        }

    def list_documents(
        self,
        workspace_id: object,
        *,
        expected_source_revision: object,
    ) -> list[dict[str, Any]]:
        _project, summaries, revision, _workflow = self._read_snapshot(
            workspace_id,
            expected_source_revision=expected_source_revision,
        )
        return summaries

    def read_document(
        self,
        workspace_id: object,
        path: object,
        *,
        expected_source_revision: object,
    ) -> dict[str, Any]:
        project, summaries, revision, _workflow = self._read_snapshot(
            workspace_id,
            expected_source_revision=expected_source_revision,
        )
        relative = portable_relative_path(path)
        by_path = {item["path"]: item for item in summaries}
        if relative is None or relative.as_posix() not in by_path:
            raise conflict("Document is outside the expected source revision allowlist")
        canonical_by_path = {
            item_path.as_posix(): document for item_path, document in _module_paths(project)
        }
        summary = by_path[relative.as_posix()]
        return {**summary, "document": canonical_by_path[relative.as_posix()]}

    def workflow(
        self,
        workspace_id: object,
    ) -> dict[str, Any]:
        _record, _project, _summaries, revision, workflow = self._refresh_snapshot(workspace_id)
        return {"source_revision": revision, **workflow}

    def _authorized_workflow_action(
        self,
        workspace_id: object,
        *,
        expected_root_generation: object,
        expected_source_revision: object,
        expected_workflow_status_hash: object,
        action: Callable[[Path, LoadedCreationProject, dict[str, Any]], Any],
    ) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        identifier = _identifier(workspace_id, field="workspace_id", workspace=True)
        expected_generation = _expected_generation(
            expected_root_generation,
            field="expected_root_generation",
        )
        expected_revision = _expected_hash(
            expected_source_revision,
            field="expected_source_revision",
        )
        expected_workflow = _expected_nullable_hash(
            expected_workflow_status_hash,
            field="expected_workflow_status_hash",
        )
        row = self._row(identifier)
        root, root_identity = self._verified_root(row)
        try:
            with exclusive_world_lifecycle(root, error_type=ValueError):
                workspace, project, _summaries, revision, workflow = self._refresh_snapshot(
                    identifier
                )
                current_root, current_identity = self._verified_root(self._row(identifier))
                if current_root != root or current_identity != root_identity:
                    raise conflict("Creation workspace root identity changed")
                if (
                    workspace["root_generation"] != expected_generation
                    or not hmac.compare_digest(revision, expected_revision)
                    or workflow["status_hash"] != expected_workflow
                ):
                    raise conflict("Creation workspace authority changed")
                result = action(root, project, workflow)
                refreshed, _project, _summaries, refreshed_revision, refreshed_workflow = (
                    self._refresh_snapshot(identifier)
                )
                final_root, final_identity = self._verified_root(self._row(identifier))
                if final_root != root or final_identity != root_identity:
                    raise conflict("Creation workspace root identity changed")
                return (
                    result,
                    refreshed,
                    {"source_revision": refreshed_revision, **refreshed_workflow},
                )
        except CreationWorkflowError as exc:
            if exc.reason_code == "creation_workflow_expected_status_hash_mismatch":
                raise conflict("Creation workspace workflow status changed") from exc
            if exc.reason_code == "creation_workflow_expected_status_hash_invalid":
                raise invalid_request("Creation workspace workflow status hash is invalid") from exc
            raise StudioError(
                "invalid_state",
                "Creation workflow operation is invalid",
                details={"reason_code": exc.reason_code},
            ) from exc
        except ValueError as exc:
            raise conflict("Creation workspace lifecycle authority changed") from exc

    def validate_phase(
        self,
        workspace_id: object,
        *,
        expected_root_generation: object,
        expected_source_revision: object,
        expected_workflow_status_hash: object,
        report: object,
        artifact_registry: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any]:
        expected_workflow = _expected_nullable_hash(
            expected_workflow_status_hash,
            field="expected_workflow_status_hash",
        )

        def action(
            root: Path,
            _project: LoadedCreationProject,
            _workflow: dict[str, Any],
        ) -> dict[str, Any]:
            return validate_creation_phase_inline_locked(
                root,
                report,
                expected_status_hash=expected_workflow,
                artifact_registry=artifact_registry,
            )

        checked, workspace, workflow = self._authorized_workflow_action(
            workspace_id,
            expected_root_generation=expected_root_generation,
            expected_source_revision=expected_source_revision,
            expected_workflow_status_hash=expected_workflow,
            action=action,
        )
        return {"workspace": workspace, "workflow": workflow, "report": checked}

    def read_phase_report(
        self,
        workspace_id: object,
        phase_id: object,
        *,
        expected_root_generation: object,
        expected_source_revision: object,
        expected_workflow_status_hash: object,
    ) -> dict[str, Any]:
        if not isinstance(phase_id, str) or phase_id not in PHASE_IDS:
            raise invalid_request("Creation phase ID is unsupported")
        expected_workflow = _expected_hash(
            expected_workflow_status_hash,
            field="expected_workflow_status_hash",
        )

        def action(
            root: Path,
            _project: LoadedCreationProject,
            workflow: dict[str, Any],
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            status = workflow.get("status")
            if not isinstance(status, dict) or phase_id not in status.get("completed_phases", []):
                raise not_found("Reviewed creation phase report was not found")
            references = [
                item
                for item in status.get("reports", [])
                if isinstance(item, dict) and item.get("phase") == phase_id
            ]
            if len(references) != 1:
                raise not_found("Reviewed creation phase report was not found")
            reference = references[0]
            content_hash = reference.get("content_hash")
            expected_path = f".worldforge/phase_reports/{phase_id}-{content_hash}.json"
            relative = portable_relative_path(reference.get("path"))
            if (
                relative is None
                or relative.as_posix() != expected_path
                or relative.parts[:2] != (".worldforge", "phase_reports")
            ):
                raise conflict("Reviewed creation phase report reference is invalid")
            try:
                payload = read_workspace_file_snapshot(
                    root,
                    relative,
                    world_identity=directory_identity(root, context="creation workspace root"),
                    context="reviewed creation phase report",
                    limit=_MAX_DOCUMENT_BYTES,
                )
                report = decode_json_object(payload, source="reviewed creation phase report")
            except (RuntimeIOError, StudioError, OSError, ValueError) as exc:
                raise conflict("Reviewed creation phase report is unavailable") from exc
            try:
                checked = _validate_recorded_phase_report(
                    report,
                    phase_id=phase_id,
                    reference=reference,
                    status=status,
                )
            except (PhaseReportV3Error, TypeError, ValueError) as exc:
                raise conflict("Reviewed creation phase report is invalid") from exc
            if canonical_json_bytes(checked) != payload:
                raise conflict("Reviewed creation phase report bytes are not canonical")
            return (
                {
                    "phase": reference["phase"],
                    "status": reference["status"],
                    "content_hash": reference["content_hash"],
                    "invalidation_dependencies": reference["invalidation_dependencies"],
                },
                checked,
            )

        (reference, report), workspace, workflow = self._authorized_workflow_action(
            workspace_id,
            expected_root_generation=expected_root_generation,
            expected_source_revision=expected_source_revision,
            expected_workflow_status_hash=expected_workflow,
            action=action,
        )
        return {
            "workspace": workspace,
            "workflow": workflow,
            "reference": reference,
            "report": report,
        }

    def complete_phase(
        self,
        workspace_id: object,
        *,
        expected_root_generation: object,
        expected_source_revision: object,
        expected_workflow_status_hash: object,
        report: object,
        artifact_registry: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any]:
        expected_workflow = _expected_hash(
            expected_workflow_status_hash,
            field="expected_workflow_status_hash",
        )

        def action(
            root: Path,
            _project: LoadedCreationProject,
            _workflow: dict[str, Any],
        ) -> dict[str, Any]:
            return complete_creation_phase_inline_locked(
                root,
                report,
                expected_status_hash=expected_workflow,
                artifact_registry=artifact_registry,
            )

        _status, workspace, workflow = self._authorized_workflow_action(
            workspace_id,
            expected_root_generation=expected_root_generation,
            expected_source_revision=expected_source_revision,
            expected_workflow_status_hash=expected_workflow,
            action=action,
        )
        return {"workspace": workspace, "workflow": workflow}

    def reopen_phase(
        self,
        workspace_id: object,
        phase_id: object,
        *,
        reason: object,
        approved_by: object,
        expected_root_generation: object,
        expected_source_revision: object,
        expected_workflow_status_hash: object,
    ) -> dict[str, Any]:
        if (
            not isinstance(phase_id, str)
            or not isinstance(reason, str)
            or not isinstance(approved_by, str)
        ):
            raise invalid_request("Creation phase reopen fields are invalid")
        expected_workflow = _expected_hash(
            expected_workflow_status_hash,
            field="expected_workflow_status_hash",
        )

        def action(
            root: Path,
            _project: LoadedCreationProject,
            _workflow: dict[str, Any],
        ) -> dict[str, Any]:
            return reopen_creation_phase_locked(
                root,
                phase_id,
                reason=reason,
                approved_by=approved_by,
                expected_status_hash=expected_workflow,
            )

        _status, workspace, workflow = self._authorized_workflow_action(
            workspace_id,
            expected_root_generation=expected_root_generation,
            expected_source_revision=expected_source_revision,
            expected_workflow_status_hash=expected_workflow,
            action=action,
        )
        return {"workspace": workspace, "workflow": workflow}

    def reconcile_workflow(
        self,
        workspace_id: object,
        *,
        expected_root_generation: object,
        expected_source_revision: object,
        expected_workflow_status_hash: object,
        artifact_registry: tuple[Mapping[str, Any], ...],
    ) -> dict[str, Any]:
        expected_workflow = _expected_nullable_hash(
            expected_workflow_status_hash,
            field="expected_workflow_status_hash",
        )

        def action(
            root: Path,
            _project: LoadedCreationProject,
            _workflow: dict[str, Any],
        ) -> dict[str, Any]:
            return reconcile_creation_workflow_locked(
                root,
                expected_status_hash=expected_workflow,
                artifact_registry=artifact_registry,
            )

        _status, workspace, workflow = self._authorized_workflow_action(
            workspace_id,
            expected_root_generation=expected_root_generation,
            expected_source_revision=expected_source_revision,
            expected_workflow_status_hash=expected_workflow,
            action=action,
        )
        return {"workspace": workspace, "workflow": workflow}

    def readiness(
        self,
        workspace_id: object,
    ) -> dict[str, Any]:
        record = self.get(workspace_id)
        try:
            _record, _project, _summaries, revision, workflow = self._refresh_snapshot(workspace_id)
        except (CreationContractError, CreationWorkflowError, StudioError):
            return {
                "state": "invalid",
                "source_revision": record["source_revision"],
                "workflow_status_hash": record["workflow_status_hash"],
                "current_phase": None,
                "release": "blocked",
                "blocker_reason_codes": ["source_invalid"],
                "report": None,
            }
        if workflow["state"] == "invalid":
            return {
                "state": "invalid",
                "source_revision": revision,
                "workflow_status_hash": workflow["status_hash"],
                "current_phase": None,
                "release": "blocked",
                "blocker_reason_codes": ["workflow_invalid"],
                "report": None,
            }
        if workflow["state"] == "missing":
            try:
                report = build_creation_readiness(_project)
            except CreationReadinessError:
                return {
                    "state": "invalid",
                    "source_revision": revision,
                    "workflow_status_hash": None,
                    "current_phase": None,
                    "release": "blocked",
                    "blocker_reason_codes": ["source_invalid"],
                    "report": None,
                }
            return {
                "state": "missing",
                "source_revision": revision,
                "workflow_status_hash": None,
                "current_phase": None,
                "release": report["dimensions"]["release"],
                "blocker_reason_codes": sorted(
                    {"workflow_missing", *report["blocker_reason_codes"]},
                    key=lambda item: item.encode("utf-8"),
                ),
                "report": report,
            }
        try:
            from worldforge.studio.creation_evidence import CreationEvidenceManager

            return CreationEvidenceManager(self).legacy_readiness(workspace_id)
        except (CreationContractError, CreationReadinessError, CreationWorkflowError, StudioError):
            return {
                "state": "invalid",
                "source_revision": record["source_revision"],
                "workflow_status_hash": record["workflow_status_hash"],
                "current_phase": None,
                "release": "blocked",
                "blocker_reason_codes": ["source_invalid"],
                "report": None,
            }

    def _read_snapshot(
        self,
        workspace_id: object,
        *,
        expected_source_revision: object,
    ) -> tuple[LoadedCreationProject, list[dict[str, Any]], str, dict[str, Any]]:
        expected = _expected_hash(
            expected_source_revision,
            field="expected_source_revision",
        )
        _record, project, summaries, revision, workflow = self._refresh_snapshot(workspace_id)
        if not hmac.compare_digest(revision, expected):
            raise conflict("Creation workspace source revision changed")
        return project, summaries, revision, workflow

    def _scan_integral_snapshot(
        self,
        identifier: str,
        root: Path,
        root_identity: tuple[int, int],
        *,
        root_generation: int,
        notify: bool,
    ) -> tuple[LoadedCreationProject, list[dict[str, Any]], str, dict[str, Any]] | None:
        project, summaries, revision = _source_snapshot(root, root_identity)
        workflow = _workflow_snapshot(root, project, root_identity)
        if notify:
            self._notify(
                "snapshot_scanned",
                workspace_id=identifier,
                root_generation=root_generation,
            )
        confirmed_project, confirmed_summaries, confirmed_revision = _source_snapshot(
            root,
            root_identity,
        )
        confirmed_workflow = _workflow_snapshot(root, confirmed_project, root_identity)
        if (
            document_identity(project.project) != document_identity(confirmed_project.project)
            or summaries != confirmed_summaries
            or revision != confirmed_revision
            or workflow != confirmed_workflow
        ):
            return None
        return confirmed_project, confirmed_summaries, confirmed_revision, confirmed_workflow

    def _refresh_snapshot(
        self,
        workspace_id: object,
    ) -> tuple[
        dict[str, Any],
        LoadedCreationProject,
        list[dict[str, Any]],
        str,
        dict[str, Any],
    ]:
        identifier = _identifier(workspace_id, field="workspace_id", workspace=True)
        for _attempt in range(3):
            row = self._row(identifier)
            record = self._validated_row(row)
            root, root_identity = self._verified_root(row)
            snapshot = self._scan_integral_snapshot(
                identifier,
                root,
                root_identity,
                root_generation=record["root_generation"],
                notify=True,
            )
            if snapshot is None:
                continue
            project, summaries, revision, workflow = snapshot
            project_identity = document_identity(project.project)
            changed = (
                record["project"] != project_identity
                or record["project_kind"] != project.project["project_kind"]
                or record["source_revision"] != revision
                or record["workflow_status_hash"] != workflow["status_hash"]
            )
            if not changed:
                current_record = self._validated_row(self._row(identifier))
                if current_record["root_generation"] != record["root_generation"]:
                    continue
                return record, project, summaries, revision, workflow
            if self.store.connection.in_transaction:
                raise conflict("Creation workspace authority changed during a serialized snapshot")
            self.store.connection.execute("BEGIN IMMEDIATE")
            try:
                current_row = self._row(identifier)
                current_record = self._validated_row(current_row)
                if current_record["root_generation"] != record["root_generation"]:
                    self.store.connection.rollback()
                    continue
                current_root, current_identity = self._verified_root(current_row)
                current_snapshot = self._scan_integral_snapshot(
                    identifier,
                    current_root,
                    current_identity,
                    root_generation=current_record["root_generation"],
                    notify=False,
                )
                if current_snapshot is None:
                    self.store.connection.rollback()
                    continue
                current_project, current_summaries, current_revision, current_workflow = (
                    current_snapshot
                )
                if (
                    document_identity(current_project.project) != project_identity
                    or current_summaries != summaries
                    or current_revision != revision
                    or current_workflow != workflow
                ):
                    self.store.connection.rollback()
                    continue
                updated = {
                    **current_record,
                    "project": project_identity,
                    "project_kind": current_project.project["project_kind"],
                    "source_revision": current_revision,
                    "workflow_status_hash": current_workflow["status_hash"],
                    "root_generation": current_record["root_generation"] + 1,
                    "updated_at": utc_now(),
                }
                validate_studio_creation_workspace(updated)
                cursor = self.store.connection.execute(
                    "UPDATE creation_workspaces SET record_json = ?, generation = ? "
                    "WHERE workspace_id = ? AND generation = ?",
                    (
                        encode_json(updated),
                        updated["root_generation"],
                        identifier,
                        current_record["root_generation"],
                    ),
                )
                if cursor.rowcount != 1:
                    self.store.connection.rollback()
                    continue
                self.store.record_creation_event(
                    workspace_id=identifier,
                    topic="creation_workspace.refreshed",
                    entity_type="creation_workspace",
                    entity_id=identifier,
                    payload={
                        "previous_source_revision": current_record["source_revision"],
                        "source_revision": current_revision,
                        "previous_workflow_status_hash": current_record["workflow_status_hash"],
                        "workflow_status_hash": current_workflow["status_hash"],
                    },
                    created_at=updated["updated_at"],
                )
                self.store.connection.commit()
                return (
                    updated,
                    current_project,
                    current_summaries,
                    current_revision,
                    current_workflow,
                )
            except BaseException:
                self.store.connection.rollback()
                raise
        raise conflict("Creation workspace changed repeatedly during snapshot refresh")

    def _insert_workspace(
        self,
        workspace_id: str,
        root: Path,
        root_identity: tuple[int, int],
        project: LoadedCreationProject,
        source_revision: str,
        workflow_status_hash: str | None,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        record = {
            "format": CREATION_WORKSPACE_FORMAT,
            "format_version": 1,
            "workspace_id": workspace_id,
            "project": document_identity(project.project),
            "project_kind": project.project["project_kind"],
            "source_revision": source_revision,
            "workflow_status_hash": workflow_status_hash,
            "root_generation": 0,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        try:
            validate_studio_creation_workspace(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Creation workspace record is invalid") from exc
        self._reject_workspace_overlap(workspace_id, root, root_identity)
        self.store.connection.execute(
            "INSERT INTO creation_workspaces "
            "(workspace_id, record_json, absolute_root, root_dev, root_ino, generation) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (
                workspace_id,
                encode_json(record),
                str(root),
                str(root_identity[0]),
                str(root_identity[1]),
            ),
        )
        return record

    def _reject_workspace_overlap(
        self,
        workspace_id: str,
        root: Path,
        root_identity: tuple[int, int],
    ) -> None:
        for row in self.store.connection.execute("SELECT * FROM creation_workspaces"):
            if row["workspace_id"] == workspace_id:
                raise conflict(f"Creation workspace {workspace_id} already exists")
            if (int(row["root_dev"]), int(row["root_ino"])) == root_identity:
                raise conflict("Creation project root is already registered")
            existing = Path(row["absolute_root"])
            left = tuple(part.casefold() for part in root.parts)
            right = tuple(part.casefold() for part in existing.parts)
            common = min(len(left), len(right))
            if left[:common] == right[:common]:
                raise conflict("Creation project root overlaps an existing workspace")

    def _verified_root(self, row: sqlite3.Row) -> tuple[Path, tuple[int, int]]:
        root = Path(row["absolute_root"])
        expected = (int(row["root_dev"]), int(row["root_ino"]))
        try:
            with _pinned_ancestor_identities(root, context="Creation workspace root") as identities:
                if identities[-1] != expected:
                    raise conflict("Creation workspace root identity changed")
        except StudioError as exc:
            raise conflict(
                "Creation workspace root identity changed",
                reason_code=str(exc.details.get("reason_code", "creation_project_root_changed")),
            ) from exc
        return root, expected

    def _row(self, workspace_id: object) -> sqlite3.Row:
        identifier = _identifier(workspace_id, field="workspace_id", workspace=True)
        row = self.store.connection.execute(
            "SELECT * FROM creation_workspaces WHERE workspace_id = ?",
            (identifier,),
        ).fetchone()
        if row is None:
            raise not_found(f"Creation workspace {identifier} was not found")
        return row

    @staticmethod
    def _validated_row(row: sqlite3.Row) -> dict[str, Any]:
        record = decode_object(row["record_json"], context="creation workspace")
        try:
            checked = validate_studio_creation_workspace(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored creation workspace is invalid") from exc
        if checked["root_generation"] != row["generation"]:
            raise StudioError("internal_error", "Stored creation workspace generation diverged")
        return checked

    @staticmethod
    def _register_params(params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("creation_workspace.register params must be an object")
        allowed = {
            "workspace_id",
            "grant_id",
            "expected_grant_generation",
            "expected_project_hash",
        }
        required = allowed - {"workspace_id"}
        missing = required - set(params)
        unknown = set(params) - allowed
        if missing or unknown:
            fields = missing or unknown
            raise invalid_request(
                "creation_workspace.register has invalid fields: " + ", ".join(sorted(fields))
            )
        return {
            "workspace_id": _identifier(
                params.get("workspace_id") or f"workspace_{uuid.uuid4().hex}",
                field="workspace_id",
                workspace=True,
            ),
            "grant_id": _identifier(params["grant_id"], field="grant_id"),
            "expected_grant_generation": _expected_generation(
                params["expected_grant_generation"],
                field="expected_grant_generation",
            ),
            "expected_project_hash": _expected_hash(
                params["expected_project_hash"],
                field="expected_project_hash",
            ),
        }

    @staticmethod
    def _create_params(params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("creation_workspace.create params must be an object")
        allowed = {
            "workspace_id",
            "grant_id",
            "expected_grant_generation",
            "project_kind",
            "project_id",
            "title",
            "default_locale",
            "project_version",
            "gameplay_family",
            "initial_core_verb",
            "initial_core_loop",
            "world_presence",
            "narrative_requirement",
            "narrative_authorship",
            "narrative_topology",
            "presentation_mode",
            "runtime_support_intent",
            "asset_content_mode",
        }
        required = {
            "grant_id",
            "expected_grant_generation",
            "project_kind",
            "project_id",
            "title",
            "default_locale",
            "project_version",
        }
        missing = required - set(params)
        unknown = set(params) - allowed
        if missing or unknown:
            fields = missing or unknown
            raise invalid_request(
                "creation_workspace.create has invalid fields: " + ", ".join(sorted(fields))
            )
        title = params["title"]
        if not isinstance(title, str) or not title.strip() or len(title) > 256:
            raise invalid_request("title must be a non-empty string of at most 256 characters")
        for field in ("default_locale", "project_version"):
            value = params[field]
            if not isinstance(value, str) or not value or len(value) > 64:
                raise invalid_request(f"{field} is invalid")
        try:
            facets = normalize_creation_scaffold_facets(
                project_kind=params["project_kind"],
                gameplay_family=params.get("gameplay_family"),
                initial_core_verb=params.get("initial_core_verb"),
                initial_core_loop=params.get("initial_core_loop"),
                world_presence=params.get("world_presence"),
                narrative_requirement=params.get("narrative_requirement"),
                narrative_authorship=params.get("narrative_authorship"),
                narrative_topology=params.get("narrative_topology"),
                presentation_mode=params.get("presentation_mode"),
                runtime_support_intent=params.get("runtime_support_intent"),
                asset_content_mode=params.get("asset_content_mode"),
            )
        except CreationScaffoldError as exc:
            raise invalid_request(exc.detail) from exc
        return {
            "workspace_id": _identifier(
                params.get("workspace_id") or f"workspace_{uuid.uuid4().hex}",
                field="workspace_id",
                workspace=True,
            ),
            "grant_id": _identifier(params["grant_id"], field="grant_id"),
            "expected_grant_generation": _expected_generation(
                params["expected_grant_generation"],
                field="expected_grant_generation",
            ),
            "project_kind": facets.project_kind,
            "project_id": _identifier(params["project_id"], field="project_id"),
            "title": title,
            "default_locale": params["default_locale"],
            "project_version": params["project_version"],
            "gameplay_family": facets.gameplay_family,
            "initial_core_verb": facets.initial_core_verb,
            "initial_core_loop": facets.initial_core_loop,
            "world_presence": facets.world_presence,
            "narrative_requirement": facets.narrative_requirement,
            "narrative_authorship": facets.narrative_authorship,
            "narrative_topology": facets.narrative_topology,
            "presentation_mode": facets.presentation_mode,
            "runtime_support_intent": facets.runtime_support_intent,
            "asset_content_mode": facets.asset_content_mode,
        }
