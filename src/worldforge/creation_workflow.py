from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from typing import Any

from worldforge.asset_io import (
    AssetContractError,
    read_json_object,
    write_json_atomic,
    write_json_cooperative_replace,
)
from worldforge.creation_contracts import (
    CREATION_PROFILE_FORMAT,
    CREATION_PROJECT_FORMAT,
    CREATION_SOURCE_MANIFEST_FORMAT,
    LoadedCreationProject,
    canonical_creation_hash,
    load_creation_project,
    validate_creation_documents,
)
from worldforge.creation_route import CreationRouteError, route_creation_project
from worldforge.phase_report_v3 import (
    PHASE_REPORT_V3_PHASE_IDS,
    PhaseReportV3Error,
    artifact_dependency_identities,
    document_identity,
    validate_artifact_documents,
    validate_artifact_identity,
    validate_phase_report_v3,
)
from worldforge.world_lock import exclusive_world_lifecycle

CREATION_WORKFLOW_FORMAT = "world-forge.creation_workflow_status"
CREATION_WORKFLOW_VERSION = 1
PHASE_IDS = PHASE_REPORT_V3_PHASE_IDS
PHASE_INDEX = {phase: index for index, phase in enumerate(PHASE_IDS)}
PHASE_TITLES = (
    "Brief, audience, constraints, and non-goals",
    "Experience classification and player promise",
    "Interaction grammar, ontology, rules, and goals",
    "World presence, topology, and environments",
    "History, progression chronology, and time",
    "Societies, teams, factions, and institutions",
    "Player representation, actors, and personal arcs",
    "Core loops, systems, progression, and interaction matrix",
    "Narrative architecture or explicit no-narrative design",
    "Typed content architecture",
    "Playability, continuity, solvability, and content lock",
    "Presentation, visual, and audio direction",
    "Asset inventory, specification, production policy, and QA",
    "Runtime compatibility and implementation support",
    "Reviewed implementation handoff",
)

_STATUS_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "workflow_id",
        "project",
        "profile",
        "source_manifest",
        "current_phase",
        "completed_phases",
        "reports",
        "invalidated_reports",
        "revision",
        "content_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_REPORT_REFERENCE_FIELDS = frozenset(
    {"phase", "status", "path", "content_hash", "invalidation_dependencies"}
)
_INVALIDATED_FIELDS = frozenset({"phase", "report_content_hash", "reason", "revision"})
_RECORDED_IDENTITY_FORMATS = {
    "project": CREATION_PROJECT_FORMAT,
    "profile": CREATION_PROFILE_FORMAT,
    "source_manifest": CREATION_SOURCE_MANIFEST_FORMAT,
}
_CREATION_DOCUMENT_FORMATS = frozenset(
    {
        CREATION_PROJECT_FORMAT,
        CREATION_PROFILE_FORMAT,
        CREATION_SOURCE_MANIFEST_FORMAT,
        "world-forge.world_module",
        "world-forge.activity_module",
        "world-forge.narrative_module",
        "world-forge.system_module",
        "world-forge.logic_module",
    }
)


class CreationWorkflowError(ValueError):
    """Raised when a generic creation workflow transition fails closed."""

    def __init__(
        self,
        detail: str,
        *,
        reason_code: str = "creation_workflow_invalid",
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.reason_code = reason_code


def phase_catalog() -> list[dict[str, str]]:
    return [{"id": phase, "title": PHASE_TITLES[index]} for index, phase in enumerate(PHASE_IDS)]


def _workflow_id(project_id: str) -> str:
    suffix = "_workflow"
    candidate = f"{project_id}{suffix}"
    return candidate if len(candidate) <= 64 else project_id


def initial_creation_workflow_status(project: LoadedCreationProject) -> dict[str, Any]:
    status = {
        "format": CREATION_WORKFLOW_FORMAT,
        "format_version": CREATION_WORKFLOW_VERSION,
        "workflow_id": _workflow_id(project.project["project_id"]),
        "project": document_identity(project.project),
        "profile": document_identity(project.profile),
        "source_manifest": document_identity(project.manifest),
        "current_phase": PHASE_IDS[0],
        "completed_phases": [],
        "reports": [],
        "invalidated_reports": [],
        "revision": 0,
        "content_hash": "",
    }
    status["content_hash"] = canonical_creation_hash(status)
    return status


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], context: str) -> None:
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise CreationWorkflowError(
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    if missing:
        raise CreationWorkflowError(f"{context} is missing fields: {', '.join(sorted(missing))}")


def _validate_identity(value: object, expected: Mapping[str, Any], context: str) -> None:
    if not isinstance(value, dict):
        raise CreationWorkflowError(f"{context} must be an object")
    _require_exact_keys(value, _IDENTITY_FIELDS, context)
    if value != dict(expected):
        raise CreationWorkflowError(f"{context} does not match the loaded creation project")


def _validate_recorded_identity(
    value: object,
    *,
    expected_format: str,
    context: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CreationWorkflowError(f"{context} must be an object")
    _require_exact_keys(value, _IDENTITY_FIELDS, context)
    if (
        value.get("format") != expected_format
        or value.get("format_version") != 1
        or isinstance(value.get("format_version"), bool)
    ):
        raise CreationWorkflowError(f"{context} format/version is unsupported")
    identifier = value.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise CreationWorkflowError(f"{context}.id is invalid")
    content_hash = value.get("content_hash")
    if (
        not isinstance(content_hash, str)
        or len(content_hash) != 64
        or any(character not in "0123456789abcdef" for character in content_hash)
    ):
        raise CreationWorkflowError(f"{context}.content_hash is invalid")
    return value


def _validate_dependency_identity(value: object, context: str) -> dict[str, Any]:
    try:
        return validate_artifact_identity(value, context=context)
    except (PhaseReportV3Error, TypeError, ValueError) as exc:
        raise CreationWorkflowError(
            f"{context} is invalid: {exc}",
            reason_code="creation_workflow_dependency_identity_invalid",
        ) from exc


def _validate_creation_workflow_status_shape(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CreationWorkflowError("creation workflow status must be an object")
    status = value
    _require_exact_keys(status, _STATUS_FIELDS, "creation workflow status")
    if (
        status.get("format") != CREATION_WORKFLOW_FORMAT
        or status.get("format_version") != CREATION_WORKFLOW_VERSION
        or isinstance(status.get("format_version"), bool)
    ):
        raise CreationWorkflowError("creation workflow status version is unsupported")
    recorded = {
        field: _validate_recorded_identity(
            status.get(field),
            expected_format=expected_format,
            context=f"creation workflow status.{field}",
        )
        for field, expected_format in _RECORDED_IDENTITY_FORMATS.items()
    }
    if status.get("workflow_id") != _workflow_id(recorded["project"]["id"]):
        raise CreationWorkflowError("creation workflow status has the wrong workflow_id")

    completed = status.get("completed_phases")
    if not isinstance(completed, list) or any(not isinstance(item, str) for item in completed):
        raise CreationWorkflowError("creation workflow completed_phases must be an array")
    expected_completed = list(PHASE_IDS[: len(completed)])
    if completed != expected_completed:
        raise CreationWorkflowError("creation workflow completed phases must be an ordered prefix")
    expected_current = PHASE_IDS[len(completed)] if len(completed) < len(PHASE_IDS) else None
    if status.get("current_phase") != expected_current:
        raise CreationWorkflowError("creation workflow current phase does not follow its prefix")
    revision = status.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise CreationWorkflowError("creation workflow revision must be non-negative")

    reports = status.get("reports")
    if not isinstance(reports, list) or len(reports) != len(completed):
        raise CreationWorkflowError("creation workflow reports must cover the completed prefix")
    for index, item in enumerate(reports):
        context = f"creation workflow reports/{index}"
        if not isinstance(item, dict):
            raise CreationWorkflowError(f"{context} must be an object")
        _require_exact_keys(item, _REPORT_REFERENCE_FIELDS, context)
        if item.get("phase") != completed[index]:
            raise CreationWorkflowError(f"{context}.phase does not match its prefix position")
        if item.get("status") not in {"ready", "not_applicable"}:
            raise CreationWorkflowError(f"{context}.status is unsupported")
        expected_path = f".worldforge/phase_reports/{item['phase']}-{item['content_hash']}.json"
        if item.get("path") != expected_path:
            raise CreationWorkflowError(f"{context}.path is not content addressed")
        content_hash = item.get("content_hash")
        if (
            not isinstance(content_hash, str)
            or len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)
        ):
            raise CreationWorkflowError(f"{context}.content_hash is invalid")
        dependencies = item.get("invalidation_dependencies")
        if not isinstance(dependencies, list) or not dependencies:
            raise CreationWorkflowError(f"{context}.invalidation_dependencies must be non-empty")
        for dependency_index, dependency in enumerate(dependencies):
            _validate_dependency_identity(
                dependency,
                f"{context}.invalidation_dependencies/{dependency_index}",
            )

    invalidated = status.get("invalidated_reports")
    if not isinstance(invalidated, list):
        raise CreationWorkflowError("creation workflow invalidated_reports must be an array")
    for index, item in enumerate(invalidated):
        context = f"creation workflow invalidated_reports/{index}"
        if not isinstance(item, dict):
            raise CreationWorkflowError(f"{context} must be an object")
        _require_exact_keys(item, _INVALIDATED_FIELDS, context)
        if item.get("phase") not in PHASE_INDEX:
            raise CreationWorkflowError(f"{context}.phase is unsupported")
        if not isinstance(item.get("reason"), str) or not item["reason"]:
            raise CreationWorkflowError(f"{context}.reason is required")
        item_revision = item.get("revision")
        if (
            isinstance(item_revision, bool)
            or not isinstance(item_revision, int)
            or item_revision < 1
            or item_revision > revision
        ):
            raise CreationWorkflowError(f"{context}.revision is invalid")
    if canonical_creation_hash(status) != status.get("content_hash"):
        raise CreationWorkflowError("creation workflow status content hash does not match")
    return copy.deepcopy(status)


def validate_creation_workflow_status(
    value: object,
    project: LoadedCreationProject,
) -> dict[str, Any]:
    status = _validate_creation_workflow_status_shape(value)
    for field, document in (
        ("project", project.project),
        ("profile", project.profile),
        ("source_manifest", project.manifest),
    ):
        _validate_identity(
            status.get(field),
            document_identity(document),
            f"creation workflow status.{field}",
        )
    return status


def validate_recorded_creation_workflow_status(value: object) -> dict[str, Any]:
    """Validate a stored status without rebinding it to the current project snapshot."""

    return _validate_creation_workflow_status_shape(value)


def _load_project_and_status(root: Path) -> tuple[LoadedCreationProject, dict[str, Any]]:
    try:
        if route_creation_project(root) != "generic":
            raise CreationWorkflowError("generic creation workflow requires a generic project")
        project = load_creation_project(root / "project.json")
        status = read_json_object(root / ".worldforge/status.json")
        return project, validate_creation_workflow_status(status, project)
    except (AssetContractError, CreationRouteError, ValueError) as exc:
        if isinstance(exc, CreationWorkflowError):
            raise
        raise CreationWorkflowError(str(exc)) from exc


def load_creation_workflow_status(project_root: str | Path) -> dict[str, Any]:
    requested = Path(project_root)
    if requested.is_symlink():
        raise CreationWorkflowError("creation project root cannot be a symbolic link")
    root = requested.resolve()
    return _load_project_and_status(root)[1]


def _write_status(root: Path, previous: Mapping[str, Any], updated: dict[str, Any]) -> None:
    try:
        write_json_cooperative_replace(
            root / ".worldforge/status.json",
            updated,
            expected_cooperative_content_hash=str(previous["content_hash"]),
            durable_parent=True,
        )
    except AssetContractError as exc:
        raise CreationWorkflowError(f"could not publish creation workflow status: {exc}") from exc


def _publish_report(root: Path, report: Mapping[str, Any]) -> str:
    relative = f".worldforge/phase_reports/{report['phase']}-{report['content_hash']}.json"
    path = root.joinpath(*PurePosixPath(relative).parts)
    try:
        if path.exists() or path.is_symlink():
            existing = read_json_object(path)
            if existing != dict(report):
                raise CreationWorkflowError("content-addressed phase report path is occupied")
        else:
            write_json_atomic(path, report, durable_parent=True)
    except AssetContractError as exc:
        raise CreationWorkflowError(f"could not publish phase report: {exc}") from exc
    return relative


def _creation_documents(project: LoadedCreationProject) -> tuple[Mapping[str, Any], ...]:
    return (
        project.project,
        project.profile,
        project.manifest,
        *project.world_modules,
        *project.activity_modules,
        *project.narrative_modules,
        *project.system_modules,
        *project.logic_modules,
    )


def _artifact_archive_path(root: Path, content_hash: str) -> Path:
    return root / ".worldforge" / "artifact_history" / f"{content_hash}.json"


def _publish_artifact_history(
    root: Path,
    documents: Sequence[Mapping[str, Any]],
) -> None:
    for document in documents:
        identity = document_identity(document)
        content_hash = str(identity["content_hash"])
        path = _artifact_archive_path(root, content_hash)
        try:
            if path.exists() or path.is_symlink():
                existing = read_json_object(path)
                if existing != dict(document):
                    raise CreationWorkflowError(
                        "content-addressed artifact history path is occupied"
                    )
            else:
                write_json_atomic(path, document, durable_parent=True)
        except AssetContractError as exc:
            raise CreationWorkflowError(f"could not publish artifact history: {exc}") from exc


def _load_archived_artifact(
    root: Path,
    identity: Mapping[str, Any],
    *,
    context: str,
) -> dict[str, Any]:
    content_hash = identity.get("content_hash")
    if not isinstance(content_hash, str):
        raise CreationWorkflowError(f"{context} has no content hash")
    try:
        document = read_json_object(_artifact_archive_path(root, content_hash))
    except AssetContractError as exc:
        raise CreationWorkflowError(f"{context} is missing or unsafe: {exc}") from exc
    try:
        actual = document_identity(document)
    except PhaseReportV3Error as exc:
        raise CreationWorkflowError(f"{context} has an unsupported artifact: {exc}") from exc
    if actual != dict(identity):
        raise CreationWorkflowError(f"{context} identity does not match its archived artifact")
    return document


def _manifest_identity(reference: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": reference.get("format"),
        "format_version": reference.get("format_version"),
        "id": reference.get("id"),
        "content_hash": reference.get("content_hash"),
    }


def _load_recorded_project(
    root: Path,
    status: Mapping[str, Any],
    current: LoadedCreationProject,
) -> LoadedCreationProject:
    if all(
        status[field] == document_identity(document)
        for field, document in (
            ("project", current.project),
            ("profile", current.profile),
            ("source_manifest", current.manifest),
        )
    ):
        return current
    project_document = _load_archived_artifact(
        root,
        status["project"],
        context="recorded project",
    )
    profile_document = _load_archived_artifact(
        root,
        status["profile"],
        context="recorded profile",
    )
    manifest_document = _load_archived_artifact(
        root,
        status["source_manifest"],
        context="recorded source manifest",
    )
    modules = manifest_document.get("modules")
    if not isinstance(modules, Mapping):
        raise CreationWorkflowError("recorded source manifest modules are invalid")

    def collection(name: str) -> tuple[dict[str, Any], ...]:
        references = modules.get(name)
        if not isinstance(references, list):
            raise CreationWorkflowError(f"recorded source manifest {name} is invalid")
        return tuple(
            _load_archived_artifact(
                root,
                _manifest_identity(reference),
                context=f"recorded source manifest {name}/{index}",
            )
            for index, reference in enumerate(references)
            if isinstance(reference, Mapping)
        )

    try:
        return validate_creation_documents(
            project_document,
            profile_document,
            manifest_document,
            collection("world_modules"),
            collection("activity_modules"),
            collection("narrative_modules"),
            collection("system_modules"),
            collection("logic_modules"),
        )
    except ValueError as exc:
        raise CreationWorkflowError(f"recorded creation snapshot is invalid: {exc}") from exc


def _load_report_artifact_closure(
    root: Path,
    dependencies: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    validated_dependencies = [
        _validate_dependency_identity(
            identity,
            f"phase report dependency/{index}",
        )
        for index, identity in enumerate(dependencies)
    ]
    pending = [
        identity
        for identity in validated_dependencies
        if identity["format"] not in _CREATION_DOCUMENT_FORMATS
    ]
    loaded: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    while pending:
        identity = pending.pop(0)
        key = (
            identity["format"],
            identity["format_version"],
            identity["id"],
            identity["content_hash"],
        )
        if key in loaded:
            continue
        document = _load_archived_artifact(
            root,
            identity,
            context="phase report artifact dependency",
        )
        loaded[key] = document
        nested = [
            _validate_dependency_identity(
                dependency,
                f"phase report artifact dependency/{index}",
            )
            for index, dependency in enumerate(artifact_dependency_identities(document))
        ]
        pending.extend(
            dependency
            for dependency in nested
            if dependency["format"] not in _CREATION_DOCUMENT_FORMATS
        )
    return tuple(loaded.values())


def _validate_report_reference(
    root: Path,
    reference: Mapping[str, Any],
    project: LoadedCreationProject,
) -> str | None:
    path = root.joinpath(*PurePosixPath(str(reference["path"])).parts)
    try:
        report = read_json_object(path)
    except AssetContractError:
        return "report_missing_or_unsafe"
    if (
        report.get("content_hash") != reference["content_hash"]
        or canonical_creation_hash(report) != reference["content_hash"]
    ):
        return "report_hash_mismatch"
    if (
        report.get("phase") != reference["phase"]
        or report.get("status") != reference["status"]
        or report.get("invalidation_dependencies") != reference["invalidation_dependencies"]
    ):
        return "report_reference_mismatch"
    try:
        artifacts = _load_report_artifact_closure(
            root,
            reference["invalidation_dependencies"],
        )
        validate_phase_report_v3(
            report,
            project,
            artifact_registry=artifacts,
        )
    except (CreationWorkflowError, PhaseReportV3Error, TypeError, ValueError):
        return "report_dependency_stale"
    return None


def _require_valid_report_prefix(
    root: Path,
    status: Mapping[str, Any],
    project: LoadedCreationProject,
) -> None:
    for reference in status["reports"]:
        reason = _validate_report_reference(root, reference, project)
        if reason is not None:
            raise CreationWorkflowError(
                f"prior phase report {reference['phase']} is invalid: {reason}"
            )


def _validated_expected_status_hash(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise CreationWorkflowError(
            "expected creation workflow status hash must be 64 lowercase hexadecimal characters",
            reason_code="creation_workflow_expected_status_hash_invalid",
        )
    return value


def _require_expected_status(
    status: Mapping[str, Any],
    expected_status_hash: object,
) -> str:
    expected = _validated_expected_status_hash(expected_status_hash)
    if status["content_hash"] != expected:
        raise CreationWorkflowError(
            "expected creation workflow status hash does not match the current status",
            reason_code="creation_workflow_expected_status_hash_mismatch",
        )
    return expected


def _validated_inline_phase_report(
    project: LoadedCreationProject,
    report: object,
    *,
    artifact_registry: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    try:
        checked_artifacts = validate_artifact_documents(project, artifact_registry)
        checked_report = validate_phase_report_v3(
            report,
            project,
            artifact_registry=checked_artifacts,
        )
    except (PhaseReportV3Error, TypeError, ValueError) as exc:
        raise CreationWorkflowError(f"phase report is invalid: {exc}") from exc
    return checked_report, tuple(checked_artifacts)


def validate_creation_phase_inline_locked(
    root: Path,
    report: object,
    *,
    expected_status_hash: object,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Validate an inline phase report while the caller holds the lifecycle lock."""

    project, status = _load_project_and_status(root)
    _require_expected_status(status, expected_status_hash)
    _require_valid_report_prefix(root, status, project)
    checked_report, _checked_artifacts = _validated_inline_phase_report(
        project,
        report,
        artifact_registry=artifact_registry,
    )
    current = status["current_phase"]
    if current is None:
        raise CreationWorkflowError("creation workflow is already complete")
    if checked_report["phase"] != current:
        raise CreationWorkflowError(
            f"phase report is for {checked_report['phase']}; current phase is {current}"
        )
    return checked_report


def validate_creation_phase_inline(
    project_root: str | Path,
    report: object,
    *,
    expected_status_hash: object,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    try:
        with exclusive_world_lifecycle(
            project_root,
            error_type=CreationWorkflowError,
        ) as root:
            return validate_creation_phase_inline_locked(
                root,
                report,
                expected_status_hash=expected_status_hash,
                artifact_registry=artifact_registry,
            )
    except CreationWorkflowError:
        raise
    except OSError as exc:
        raise CreationWorkflowError(str(exc)) from exc


def complete_creation_phase_inline_locked(
    root: Path,
    report: object,
    *,
    expected_status_hash: object,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Complete an inline phase report while the caller holds the lifecycle lock."""

    project, status = _load_project_and_status(root)
    _require_expected_status(status, expected_status_hash)
    _require_valid_report_prefix(root, status, project)
    checked_report, checked_artifacts = _validated_inline_phase_report(
        project,
        report,
        artifact_registry=artifact_registry,
    )
    current = status["current_phase"]
    if checked_report["phase"] != current:
        if checked_report["phase"] in status["completed_phases"]:
            index = PHASE_INDEX[checked_report["phase"]]
            reference = status["reports"][index]
            if (
                reference["content_hash"] == checked_report["content_hash"]
                and reference["status"] == checked_report["status"]
                and reference["invalidation_dependencies"]
                == checked_report["invalidation_dependencies"]
            ):
                return status
        if current is None:
            raise CreationWorkflowError("creation workflow is already complete")
        raise CreationWorkflowError(
            f"phase report is for {checked_report['phase']}; current phase is {current}"
        )
    _publish_artifact_history(
        root,
        (*_creation_documents(project), *checked_artifacts),
    )
    relative = _publish_report(root, checked_report)
    reference = {
        "phase": checked_report["phase"],
        "status": checked_report["status"],
        "path": relative,
        "content_hash": checked_report["content_hash"],
        "invalidation_dependencies": copy.deepcopy(checked_report["invalidation_dependencies"]),
    }
    completed = [*status["completed_phases"], current]
    updated = {
        **status,
        "current_phase": (PHASE_IDS[len(completed)] if len(completed) < len(PHASE_IDS) else None),
        "completed_phases": completed,
        "reports": [*status["reports"], reference],
        "revision": status["revision"] + 1,
        "content_hash": "",
    }
    updated["content_hash"] = canonical_creation_hash(updated)
    validate_creation_workflow_status(updated, project)
    _write_status(root, status, updated)
    return updated


def complete_creation_phase_inline(
    project_root: str | Path,
    report: object,
    *,
    expected_status_hash: object,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    try:
        with exclusive_world_lifecycle(
            project_root,
            error_type=CreationWorkflowError,
        ) as root:
            return complete_creation_phase_inline_locked(
                root,
                report,
                expected_status_hash=expected_status_hash,
                artifact_registry=artifact_registry,
            )
    except CreationWorkflowError:
        raise
    except OSError as exc:
        raise CreationWorkflowError(str(exc)) from exc


def complete_creation_phase(
    project_root: str | Path,
    report_path: str | Path,
    *,
    expected_status_hash: object,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    try:
        report = read_json_object(report_path)
    except AssetContractError as exc:
        raise CreationWorkflowError(f"phase report is invalid: {exc}") from exc
    return complete_creation_phase_inline(
        project_root,
        report,
        expected_status_hash=expected_status_hash,
        artifact_registry=artifact_registry,
    )


def _invalidated_entry(
    reference: Mapping[str, Any],
    *,
    reason: str,
    revision: int,
) -> dict[str, Any]:
    return {
        "phase": reference["phase"],
        "report_content_hash": reference["content_hash"],
        "reason": reason,
        "revision": revision,
    }


def reopen_creation_phase_locked(
    root: Path,
    phase_id: str,
    *,
    reason: str,
    approved_by: str,
    expected_status_hash: object,
) -> dict[str, Any]:
    if phase_id not in PHASE_INDEX:
        raise CreationWorkflowError(f"unknown phase: {phase_id}")
    if not reason.strip() or not approved_by.strip():
        raise CreationWorkflowError("reason and approved_by are required")
    project, status = _load_project_and_status(root)
    _require_expected_status(status, expected_status_hash)
    _require_valid_report_prefix(root, status, project)
    if phase_id not in status["completed_phases"]:
        raise CreationWorkflowError(f"phase was not completed: {phase_id}")
    index = PHASE_INDEX[phase_id]
    retained = status["reports"][:index]
    discarded = status["reports"][index:]
    revision = status["revision"] + 1
    invalidated = [
        *status["invalidated_reports"],
        *(
            _invalidated_entry(
                reference,
                reason=f"manual_reopen:{reason.strip()}:{approved_by.strip()}",
                revision=revision,
            )
            for reference in discarded
        ),
    ]
    updated = {
        **status,
        "current_phase": phase_id,
        "completed_phases": list(PHASE_IDS[:index]),
        "reports": retained,
        "invalidated_reports": invalidated,
        "revision": revision,
        "content_hash": "",
    }
    updated["content_hash"] = canonical_creation_hash(updated)
    validate_creation_workflow_status(updated, project)
    _write_status(root, status, updated)
    return updated


def reopen_creation_phase(
    project_root: str | Path,
    phase_id: str,
    *,
    reason: str,
    approved_by: str,
    expected_status_hash: object,
) -> dict[str, Any]:
    with exclusive_world_lifecycle(
        project_root,
        error_type=CreationWorkflowError,
    ) as root:
        return reopen_creation_phase_locked(
            root,
            phase_id,
            reason=reason,
            approved_by=approved_by,
            expected_status_hash=expected_status_hash,
        )


def reconcile_creation_workflow(
    project_root: str | Path,
    *,
    expected_status_hash: str,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    expected = _validated_expected_status_hash(expected_status_hash)
    try:
        return _reconcile_creation_workflow(
            project_root,
            expected_status_hash=expected,
            artifact_registry=artifact_registry,
        )
    except CreationWorkflowError:
        raise
    except (
        AssetContractError,
        CreationRouteError,
        PhaseReportV3Error,
        TypeError,
        ValueError,
    ) as exc:
        raise CreationWorkflowError(
            f"creation workflow reconciliation failed: {exc}",
            reason_code="creation_workflow_reconciliation_failed",
        ) from exc


def reconcile_creation_workflow_locked(
    root: Path,
    *,
    expected_status_hash: object,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    expected = _validated_expected_status_hash(expected_status_hash)
    return _reconcile_creation_workflow(
        root,
        expected_status_hash=expected,
        artifact_registry=artifact_registry,
        lifecycle_locked=True,
    )


def _reconcile_creation_workflow(
    project_root: str | Path,
    *,
    expected_status_hash: str,
    artifact_registry: Sequence[Mapping[str, Any]],
    lifecycle_locked: bool = False,
) -> dict[str, Any]:
    lifecycle = (
        nullcontext(Path(project_root))
        if lifecycle_locked
        else exclusive_world_lifecycle(
            project_root,
            error_type=CreationWorkflowError,
        )
    )
    with lifecycle as root:
        try:
            if route_creation_project(root) != "generic":
                raise CreationWorkflowError("generic creation workflow requires a generic project")
            project = load_creation_project(root / "project.json")
            status = _validate_creation_workflow_status_shape(
                read_json_object(root / ".worldforge/status.json")
            )
        except (AssetContractError, CreationRouteError, ValueError) as exc:
            if isinstance(exc, CreationWorkflowError):
                raise
            raise CreationWorkflowError(str(exc)) from exc
        _require_expected_status(status, expected_status_hash)
        if status["project"]["id"] != project.project["project_id"]:
            raise CreationWorkflowError("recorded workflow and current project IDs do not match")
        recorded_project = _load_recorded_project(root, status, project)
        validate_creation_workflow_status(status, recorded_project)

        invalid_index: int | None = None
        invalid_reason = ""
        for index, reference in enumerate(status["reports"]):
            reason = _validate_report_reference(root, reference, recorded_project)
            if reason is not None:
                invalid_index = index
                invalid_reason = reason
                break

        try:
            current_artifacts = validate_artifact_documents(
                project,
                artifact_registry,
            )
        except PhaseReportV3Error as exc:
            raise CreationWorkflowError(f"artifact registry is invalid: {exc}") from exc
        current_documents = (*_creation_documents(project), *current_artifacts)
        current_by_subject: dict[tuple[str, str], dict[str, Any]] = {}
        for document in current_documents:
            identity = document_identity(document)
            subject = (identity["format"], identity["id"])
            if subject in current_by_subject:
                raise CreationWorkflowError(
                    "current artifact registry contains a logical identity collision"
                )
            current_by_subject[subject] = identity

        if invalid_index is None:
            for index, reference in enumerate(status["reports"]):
                for dependency_index, raw_dependency in enumerate(
                    reference["invalidation_dependencies"]
                ):
                    dependency = _validate_dependency_identity(
                        raw_dependency,
                        f"creation workflow reports/{index}.invalidation_dependencies/"
                        f"{dependency_index}",
                    )
                    subject = (
                        dependency["format"],
                        dependency["id"],
                    )
                    current_identity = current_by_subject.get(subject)
                    is_creation_document = dependency["format"] in _CREATION_DOCUMENT_FORMATS
                    if current_identity is None and not is_creation_document:
                        continue
                    if current_identity != dependency:
                        invalid_index = index
                        invalid_reason = "report_dependency_stale"
                        break
                if invalid_index is not None:
                    break

        current_snapshot = {
            "project": document_identity(project.project),
            "profile": document_identity(project.profile),
            "source_manifest": document_identity(project.manifest),
        }
        snapshot_changed = any(
            status[field] != identity for field, identity in current_snapshot.items()
        )
        if invalid_index is None and not snapshot_changed:
            return status
        revision = status["revision"] + 1
        discarded = [] if invalid_index is None else status["reports"][invalid_index:]
        retained_count = len(status["reports"]) if invalid_index is None else invalid_index
        updated = {
            **status,
            **current_snapshot,
            "current_phase": (
                PHASE_IDS[retained_count] if retained_count < len(PHASE_IDS) else None
            ),
            "completed_phases": list(PHASE_IDS[:retained_count]),
            "reports": status["reports"][:retained_count],
            "invalidated_reports": [
                *status["invalidated_reports"],
                *(
                    _invalidated_entry(
                        reference,
                        reason=invalid_reason,
                        revision=revision,
                    )
                    for reference in discarded
                ),
            ],
            "revision": revision,
            "content_hash": "",
        }
        updated["content_hash"] = canonical_creation_hash(updated)
        validate_creation_workflow_status(updated, project)
        _publish_artifact_history(root, current_documents)
        _write_status(root, status, updated)
        return updated
