from __future__ import annotations

import copy
import hmac
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.creation_contracts import (
    LoadedCreationProject,
    canonical_creation_hash,
    validate_creation_documents,
)
from worldforge.creation_readiness import build_creation_handoff, build_creation_readiness
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.phase_report_v3 import (
    PhaseReportV3Error,
    artifact_dependency_identities,
    document_identity,
    validate_artifact_documents,
    validate_artifact_identity,
)
from worldforge.retained_tree import (
    RetainedTreeCapacityError,
    RetainedTreeError,
    capture_retained_directory_file_census,
)
from worldforge.studio.changesets import read_workspace_file_snapshot
from worldforge.studio.contracts import (
    CREATION_ARTIFACT_FORMAT,
    CREATION_EVIDENCE_FORMAT,
    MAX_CREATION_ARTIFACTS,
    MAX_CREATION_EVIDENCE_BYTES,
    validate_studio_creation_artifact,
    validate_studio_creation_evidence,
)
from worldforge.studio.creation_workspaces import CreationWorkspaceManager, _module_paths
from worldforge.studio.errors import StudioError, conflict, invalid_state, not_found
from worldforge.world_lock import exclusive_world_lifecycle

if TYPE_CHECKING:
    from worldforge.studio.creation_artifacts import CreationArtifactRegistry

_MAX_ARCHIVED_DOCUMENT_BYTES = 16 * 1024 * 1024
_MAX_IGNORED_HISTORY = 100_000
_HISTORY_FILE_PATTERN = re.compile(r"[0-9a-f]{64}\.json\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_CREATION_FORMATS = frozenset(
    {
        "world-forge.project",
        "world-forge.creation_profile",
        "world-forge.creation_source_manifest",
        "world-forge.world_module",
        "world-forge.activity_module",
        "world-forge.narrative_module",
        "world-forge.system_module",
        "world-forge.logic_module",
    }
)
_IDENTITY_FIELDS = ("format", "format_version", "id", "content_hash")
_ROLE_BY_FORMAT = {
    "world-forge.project": "source_project",
    "world-forge.creation_profile": "creation_profile",
    "world-forge.creation_source_manifest": "source_manifest",
    "world-forge.world_module": "world_module",
    "world-forge.activity_module": "activity_module",
    "world-forge.narrative_module": "narrative_module",
    "world-forge.system_module": "system_module",
    "world-forge.logic_module": "logic_module",
    "world-forge.creation_workflow_status": "workflow_authority",
    "world-forge.gamepack": "compiled_logic",
    "world-forge.game_analysis": "game_analysis",
    "world-forge.mechanic_capability_ledger": "mechanic_ledger",
    "world-forge.asset_subject": "asset_subject",
    "world-forge.asset_target": "asset_target",
    "world-forge.asset_style": "asset_style",
    "world-forge.asset_inventory": "asset_inventory",
    "world-forge.asset_spec": "asset_specification",
    "world-forge.asset_production_request": "asset_request",
    "world-forge.asset_production_receipt": "asset_receipt",
    "world-forge.asset_selection": "asset_selection",
    "world-forge.asset_provenance_record": "asset_provenance",
    "world-forge.asset_license_record": "asset_license",
    "world-forge.asset_processing_recipe": "asset_processing_recipe",
    "world-forge.asset_processing_receipt": "asset_processing_receipt",
    "world-forge.asset_qa_report": "asset_qa",
    "world-forge.asset_manifest": "asset_manifest",
    "world-forge.assetpack": "sealed_assetpack",
    "world-forge.runtime_adapter": "runtime_adapter",
    "world-forge.runtime_adapter_registry": "runtime_registry",
    "world-forge.game_runtime_snapshot": "runtime_snapshot",
    "world-forge.game_runtime_composition": "runtime_composition",
    "world-forge.runtime_evidence": "runtime_evidence",
    "world-forge.runtime_support_report": "runtime_support",
    "world-forge.game_package": "game_package",
    "world-forge.creation_readiness": "creation_readiness",
    "world-forge.creation_handoff": "creation_handoff",
}
_STATUS_ORDER = (
    "supported_current",
    "game_extension_verified",
    "authoring_only",
    "blocked",
)


def _identity_key(identity: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (
        str(identity["format"]),
        int(identity["format_version"]),
        str(identity["id"]),
        str(identity["content_hash"]),
    )


def _public_authority(
    workspace: Mapping[str, Any],
    *,
    source_revision: str,
    workflow_status_hash: str | None,
) -> dict[str, Any]:
    return {
        "workspace_id": workspace["workspace_id"],
        "root_generation": workspace["root_generation"],
        "source_revision": source_revision,
        "workflow_status_hash": workflow_status_hash,
    }


def _artifact_id(identity: Mapping[str, Any]) -> str:
    return "artifact_" + canonical_payload_hash({"subject": dict(identity)})


def _read_archived_document(
    root: Path,
    root_identity: tuple[int, int],
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    checked = validate_artifact_identity(identity, context="Studio artifact history identity")
    content_hash = checked["content_hash"]
    relative = PurePosixPath(".worldforge", "artifact_history", f"{content_hash}.json")
    try:
        payload = read_workspace_file_snapshot(
            root,
            relative,
            world_identity=root_identity,
            context="Studio creation artifact history",
            limit=_MAX_ARCHIVED_DOCUMENT_BYTES,
        )
        document = decode_json_object(payload, source="Studio creation artifact history")
    except (OSError, RuntimeIOError, StudioError) as exc:
        raise invalid_state("Creation artifact history is missing or unsafe") from exc
    if canonical_json_bytes(document) != payload:
        raise invalid_state("Creation artifact history is not canonical JSON")
    try:
        actual = document_identity(document)
    except (PhaseReportV3Error, TypeError, ValueError) as exc:
        raise invalid_state("Creation artifact history identity is unsupported") from exc
    if actual != checked or canonical_creation_hash(document) != content_hash:
        raise invalid_state("Creation artifact history identity or hash changed")
    return document


def _read_archived_phase_report(
    root: Path,
    root_identity: tuple[int, int],
    *,
    phase: str,
    content_hash: str,
) -> dict[str, Any]:
    relative = PurePosixPath(".worldforge", "phase_reports", f"{phase}-{content_hash}.json")
    try:
        payload = read_workspace_file_snapshot(
            root,
            relative,
            world_identity=root_identity,
            context="Studio invalidated phase report",
            limit=_MAX_ARCHIVED_DOCUMENT_BYTES,
        )
        report = decode_json_object(payload, source="Studio invalidated phase report")
    except (OSError, RuntimeIOError, StudioError) as exc:
        raise invalid_state("Invalidated phase report is missing or unsafe") from exc
    if (
        canonical_json_bytes(report) != payload
        or report.get("format") != "world-forge.phase_report"
        or report.get("format_version") != 3
        or report.get("phase") != phase
        or report.get("content_hash") != content_hash
        or canonical_creation_hash(report) != content_hash
    ):
        raise invalid_state("Invalidated phase report is not canonical or changed")
    dependencies = report.get("invalidation_dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise invalid_state("Invalidated phase report dependency closure is missing")
    return report


def _project_documents(project: LoadedCreationProject) -> tuple[dict[str, Any], ...]:
    return tuple(document for _relative, document in _module_paths(project))


def _recorded_project_for_report(
    root: Path,
    root_identity: tuple[int, int],
    report: Mapping[str, Any],
) -> LoadedCreationProject:
    project_document = _read_archived_document(root, root_identity, report["project"])
    profile_document = _read_archived_document(root, root_identity, report["profile"])
    manifest_document = _read_archived_document(root, root_identity, report["source_manifest"])
    modules = manifest_document.get("modules")
    if not isinstance(modules, Mapping):
        raise invalid_state("Invalidated phase source manifest is invalid")

    def collection(name: str) -> tuple[dict[str, Any], ...]:
        values = modules.get(name)
        if not isinstance(values, list):
            raise invalid_state("Invalidated phase source manifest is invalid")
        result: list[dict[str, Any]] = []
        for value in values:
            if not isinstance(value, Mapping):
                raise invalid_state("Invalidated phase source manifest is invalid")
            identity = {
                "format": value.get("format"),
                "format_version": value.get("format_version"),
                "id": value.get("id"),
                "content_hash": value.get("content_hash"),
            }
            result.append(_read_archived_document(root, root_identity, identity))
        return tuple(result)

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
        raise invalid_state("Invalidated phase source snapshot is not integral") from exc


def _load_external_closure(
    root: Path,
    root_identity: tuple[int, int],
    identities: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    loaded: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    pending = [dict(identity) for identity in identities]
    while pending:
        identity = validate_artifact_identity(
            pending.pop(0), context="Studio artifact closure identity"
        )
        if identity["format"] in _CREATION_FORMATS:
            continue
        key = _identity_key(identity)
        if key in loaded:
            continue
        document = _read_archived_document(root, root_identity, identity)
        loaded[key] = document
        pending.extend(artifact_dependency_identities(document))
        if len(loaded) > MAX_CREATION_ARTIFACTS:
            raise invalid_state("Creation artifact closure exceeds Studio limits")
    return tuple(loaded[key] for key in sorted(loaded))


def _ignored_history_count(
    root: Path,
    root_identity: tuple[int, int],
    reachable_hashes: set[str],
) -> int:
    history = root / ".worldforge" / "artifact_history"
    reachable_names = {f"{content_hash}.json" for content_hash in reachable_hashes}
    try:
        census = capture_retained_directory_file_census(
            history,
            maximum_entries=_MAX_IGNORED_HISTORY,
            authority_root=root,
            expected_authority_identity=root_identity,
        )
    except RetainedTreeCapacityError as exc:
        raise invalid_state(
            "Creation artifact history capacity exceeded",
            reason_code="history_capacity_exceeded",
            maximum_entries=_MAX_IGNORED_HISTORY,
        ) from exc
    except RetainedTreeError as exc:
        raise invalid_state("Creation artifact history contains an unsafe entry") from exc
    if any(_HISTORY_FILE_PATTERN.fullmatch(name) is None for name in census.names):
        raise invalid_state(
            "Creation artifact history entry lacks a canonical content-hash filename"
        )
    return sum(name not in reachable_names for name in census.names)


def _roles(document: Mapping[str, Any]) -> list[str]:
    role = _ROLE_BY_FORMAT.get(str(document.get("format")), "registered_artifact")
    roles = {role}
    if document.get("format") in {
        "world-forge.asset_spec",
        "world-forge.asset_production_request",
        "world-forge.asset_production_receipt",
        "world-forge.asset_selection",
        "world-forge.asset_provenance_record",
        "world-forge.asset_license_record",
        "world-forge.asset_processing_recipe",
        "world-forge.asset_processing_receipt",
        "world-forge.asset_qa_report",
    }:
        roles.add("asset_lineage")
    return sorted(roles, key=lambda item: item.encode("utf-8"))


def _build_records(
    *,
    authority: Mapping[str, Any],
    documents: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
    lifecycles: Mapping[tuple[str, int, str, str], str],
    producers: Mapping[tuple[str, int, str, str], tuple[str, str | None, str]],
) -> tuple[dict[str, Any], ...]:
    dependencies: dict[tuple[str, int, str, str], set[tuple[str, int, str, str]]] = {}
    dependents: defaultdict[tuple[str, int, str, str], set[tuple[str, int, str, str]]] = (
        defaultdict(set)
    )
    for key, document in documents.items():
        try:
            targets = {_identity_key(item) for item in artifact_dependency_identities(document)}
        except PhaseReportV3Error:
            targets = set()
        dependencies[key] = targets
        for target in targets:
            dependents[target].add(key)

    records: list[dict[str, Any]] = []
    for key in sorted(documents):
        document = documents[key]
        identity = document_identity(document)
        producer_kind, phase_id, reference_id = producers[key]
        record = {
            "format": CREATION_ARTIFACT_FORMAT,
            "format_version": 1,
            "artifact_id": _artifact_id(identity),
            "subject": identity,
            "lifecycle": lifecycles[key],
            "roles": _roles(document),
            "producer": {
                "kind": producer_kind,
                "phase_id": phase_id,
                "reference_id": reference_id,
            },
            "references": {
                "dependency_count": min(MAX_CREATION_ARTIFACTS, len(dependencies[key])),
                "dependent_count": min(MAX_CREATION_ARTIFACTS, len(dependents[key])),
            },
            "authority": dict(authority),
            "record_hash": "",
        }
        record["record_hash"] = canonical_payload_hash(record, hash_field="record_hash")
        records.append(validate_studio_creation_artifact(record))
    return tuple(sorted(records, key=lambda item: item["artifact_id"].encode("utf-8")))


def _fact(key: str, value: str | int | bool | None | list[str]) -> dict[str, Any]:
    if isinstance(value, list):
        value = value[:128]
    return {"key": key, "value": value}


def _qa_criterion_hashes(document: Mapping[str, Any]) -> list[str]:
    criteria = document.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        raise invalid_state("QA report criterion hashes are missing")
    hashes: list[str] = []
    for criterion in criteria:
        if not isinstance(criterion, Mapping):
            raise invalid_state("QA report criterion hashes are malformed")
        digest = criterion.get("criterion_sha256")
        if not isinstance(digest, str) or _SHA256_PATTERN.fullmatch(digest) is None:
            raise invalid_state("QA report criterion hashes are malformed")
        hashes.append(digest)
    if len(set(hashes)) != len(hashes):
        raise invalid_state("QA report criterion hashes are not unique")
    return hashes


def _projection(
    document: Mapping[str, Any],
    record: Mapping[str, Any],
    records_by_key: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
    *,
    protocol_version: int = 4,
) -> dict[str, Any]:
    format_name = str(document["format"])
    facts: list[dict[str, Any]] = []
    projection_kind = _ROLE_BY_FORMAT.get(format_name, "registered_artifact")
    status = document.get("status") if isinstance(document.get("status"), str) else None
    if status is None and isinstance(document.get("state"), str):
        status = str(document["state"])

    if format_name == "world-forge.gamepack":
        game = document.get("game") if isinstance(document.get("game"), Mapping) else {}
        facts.extend(
            (
                _fact("game_id", str(game.get("id", record["subject"]["id"]))),
                _fact("action_count", len(document.get("actions", ()))),
                _fact("rule_count", len(document.get("rules", ()))),
                _fact("ending_count", len(document.get("endings", ()))),
            )
        )
    elif format_name == "world-forge.mechanic_capability_ledger":
        mechanics = document.get("mechanics", ())
        features = document.get("features", ())
        facts.extend(
            (
                _fact("mechanic_count", len(mechanics)),
                _fact("feature_count", len(features)),
                _fact("adapter_status", str(document.get("adapter", {}).get("status", "absent"))),
            )
        )
    elif format_name.startswith("world-forge.asset_") or format_name == "world-forge.assetpack":
        asset = document.get("asset") if isinstance(document.get("asset"), Mapping) else {}
        if isinstance(asset.get("asset_id"), str):
            facts.append(_fact("asset_id", str(asset["asset_id"])))
        if isinstance(document.get("assets"), list):
            facts.append(_fact("asset_count", len(document["assets"])))
        if isinstance(document.get("outputs"), list):
            facts.append(_fact("output_count", len(document["outputs"])))
        if format_name == "world-forge.asset_qa_report":
            facts.append(_fact("blocker_count", len(document.get("blockers", ()))))
            if protocol_version >= 5:
                facts.append(_fact("criterion_hashes", _qa_criterion_hashes(document)))
        if format_name == "world-forge.asset_license_record":
            candidate = document.get("candidate")
            if isinstance(candidate, Mapping):
                candidate_artifact_id = candidate.get("candidate_artifact_id")
                candidate_role = candidate.get("role")
                if isinstance(candidate_artifact_id, str) and isinstance(candidate_role, str):
                    facts.extend(
                        (
                            _fact("candidate_artifact_id", candidate_artifact_id),
                            _fact("candidate_role", candidate_role),
                        )
                    )
            basis = document.get("license_basis")
            if isinstance(basis, Mapping) and isinstance(basis.get("identifier"), str):
                facts.append(_fact("license_identifier", str(basis["identifier"])))
            permissions = document.get("permissions")
            if isinstance(permissions, Mapping):
                for permission in ("commercial_use", "modification", "redistribution"):
                    if isinstance(permissions.get(permission), bool):
                        facts.append(_fact(permission, bool(permissions[permission])))
        if format_name == "world-forge.asset_selection":
            selected_outputs = document.get("selected_outputs")
            if isinstance(selected_outputs, list):
                bindings = []
                for output in selected_outputs:
                    if not isinstance(output, Mapping):
                        continue
                    candidate_artifact_id = output.get("candidate_artifact_id")
                    candidate_role = output.get("role")
                    if isinstance(candidate_artifact_id, str) and isinstance(candidate_role, str):
                        bindings.append(f"{candidate_artifact_id}:{candidate_role}")
                facts.append(
                    _fact(
                        "selected_output_bindings",
                        sorted(bindings, key=lambda item: item.encode("utf-8")),
                    )
                )
        if format_name == "world-forge.asset_provenance_record":
            facts.append(_fact("lineage_nodes", len(document.get("lineage", ()))))
            facts.append(_fact("candidate_count", len(document.get("candidates", ()))))
    elif format_name == "world-forge.runtime_support_report":
        reason_codes = document.get("reason_codes", ())
        missing_capabilities = document.get("missing_capabilities", ())
        evidence = document.get("evidence", ())
        dimensions = document.get("dimensions")
        if not isinstance(dimensions, Mapping):
            dimensions = {}
        execution_statuses: list[str] = []
        execution = dimensions.get("execution", ())
        if isinstance(execution, list):
            for row in execution:
                if not isinstance(row, Mapping):
                    continue
                platform = row.get("platform")
                status_value = row.get("status")
                if (
                    isinstance(platform, Mapping)
                    and isinstance(platform.get("platform_id"), str)
                    and isinstance(status_value, str)
                ):
                    execution_statuses.append(f"{platform['platform_id']}:{status_value}")
        execution_statuses.sort(key=lambda item: item.encode("utf-8"))
        facts.extend(
            (
                _fact("supported", bool(document.get("supported"))),
                _fact(
                    "compatibility_status",
                    str(document.get("compatibility_status", "unsupported")),
                ),
                _fact("reason_code_count", len(document.get("reason_codes", ()))),
                _fact("reason_codes", list(reason_codes)),
                _fact("missing_capability_count", len(missing_capabilities)),
                _fact("missing_capabilities", list(missing_capabilities)),
                _fact("evidence_count", len(evidence)),
                _fact("authoring", str(dimensions.get("authoring", "invalid"))),
                _fact("compilation", str(dimensions.get("compilation", "failed"))),
                _fact("assets", str(dimensions.get("assets", "failed"))),
                _fact("adapter", str(dimensions.get("adapter", "absent"))),
                _fact("packaging", str(dimensions.get("packaging", "failed"))),
                _fact("release", str(dimensions.get("release", "blocked"))),
                _fact("execution_statuses", execution_statuses),
            )
        )
    elif format_name == "world-forge.game_analysis":
        facts.append(_fact("analysis_status", str(document.get("status", "failed"))))
    else:
        title = document.get("title")
        if isinstance(title, str):
            facts.append(_fact("title", title[:256]))

    lineage: list[dict[str, Any]] = []
    try:
        dependencies = artifact_dependency_identities(document)
    except PhaseReportV3Error:
        dependencies = ()
    for dependency in dependencies[:128]:
        dependency_record = records_by_key.get(_identity_key(dependency))
        if dependency_record is None:
            continue
        lineage.append(
            {
                "relation": "depends_on",
                "artifact_id": dependency_record["artifact_id"],
                "lifecycle": dependency_record["lifecycle"],
            }
        )
    lineage.sort(key=lambda item: item["artifact_id"].encode("utf-8"))
    return {
        "projection_kind": projection_kind,
        "title": str(record["subject"]["id"])[:256],
        "status": status,
        "facts": facts[:128],
        "lineage": lineage,
    }


class CreationEvidenceManager:
    """Build bounded, pathless read-only evidence from retained creation authority."""

    def __init__(
        self,
        workspaces: CreationWorkspaceManager,
        *,
        candidates: CreationArtifactRegistry | None = None,
    ) -> None:
        self.workspaces = workspaces
        self.candidates = candidates

    def _snapshot(self, params: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = params["workspace_id"]
        row = self.workspaces._row(workspace_id)
        root, root_identity = self.workspaces._verified_root(row)
        try:
            with exclusive_world_lifecycle(root, error_type=ValueError):
                workspace, project, _summaries, revision, workflow = (
                    self.workspaces._refresh_snapshot(workspace_id)
                )
                current_root, current_identity = self.workspaces._verified_root(
                    self.workspaces._row(workspace_id)
                )
                if current_root != root or current_identity != root_identity:
                    raise conflict("Creation workspace root identity changed")
                if (
                    workspace["root_generation"] != params["expected_root_generation"]
                    or not hmac.compare_digest(revision, params["expected_source_revision"])
                    or workflow["status_hash"] != params["expected_workflow_status_hash"]
                ):
                    raise conflict("Creation evidence authority changed")
                status = workflow.get("status")
                if not isinstance(status, Mapping):
                    raise invalid_state("Creation workflow evidence is unavailable")
                authority = _public_authority(
                    workspace,
                    source_revision=revision,
                    workflow_status_hash=workflow["status_hash"],
                )

                documents: dict[tuple[str, int, str, str], Mapping[str, Any]] = {}
                lifecycles: dict[tuple[str, int, str, str], str] = {}
                producers: dict[tuple[str, int, str, str], tuple[str, str | None, str]] = {}
                active_external_identities: list[Mapping[str, Any]] = []
                active_phase_by_key: dict[tuple[str, int, str, str], str] = {}
                for document in _project_documents(project):
                    identity = document_identity(document)
                    archived = _read_archived_document(root, root_identity, identity)
                    if archived != document:
                        raise invalid_state("Creation source and artifact history diverged")
                    key = _identity_key(identity)
                    documents[key] = document
                    lifecycles[key] = "active"
                    producers[key] = ("source_snapshot", None, "source_snapshot")
                workflow_identity = document_identity(status)
                workflow_key = _identity_key(workflow_identity)
                documents[workflow_key] = dict(status)
                lifecycles[workflow_key] = "active"
                producers[workflow_key] = ("source_snapshot", None, "workflow_snapshot")

                for reference in status["reports"]:
                    phase = str(reference["phase"])
                    for identity in reference["invalidation_dependencies"]:
                        key = _identity_key(identity)
                        active_phase_by_key.setdefault(key, phase)
                        if identity["format"] not in _CREATION_FORMATS:
                            active_external_identities.append(identity)
                active_external = _load_external_closure(
                    root, root_identity, active_external_identities
                )
                try:
                    checked_active = validate_artifact_documents(project, active_external)
                except (PhaseReportV3Error, TypeError, ValueError) as exc:
                    raise invalid_state("Active creation artifact closure is not integral") from exc
                for document in checked_active:
                    identity = document_identity(document)
                    key = _identity_key(identity)
                    phase = active_phase_by_key.get(key, "p14_handoff")
                    documents[key] = document
                    lifecycles[key] = "active"
                    producers[key] = (
                        "active_phase_report",
                        phase,
                        f"report_{phase}",
                    )

                invalidated_documents: dict[tuple[str, int, str, str], Mapping[str, Any]] = {}
                invalidated_producers: dict[
                    tuple[str, int, str, str], tuple[str, str | None, str]
                ] = {}
                for invalidated in status["invalidated_reports"]:
                    phase = str(invalidated["phase"])
                    report_hash = str(invalidated["report_content_hash"])
                    report = _read_archived_phase_report(
                        root,
                        root_identity,
                        phase=phase,
                        content_hash=report_hash,
                    )
                    recorded_project = _recorded_project_for_report(root, root_identity, report)
                    identities = report["invalidation_dependencies"]
                    external = _load_external_closure(root, root_identity, identities)
                    try:
                        checked = validate_artifact_documents(recorded_project, external)
                    except (PhaseReportV3Error, TypeError, ValueError) as exc:
                        raise invalid_state(
                            "Invalidated creation artifact closure is not integral"
                        ) from exc
                    recorded_documents = _project_documents(recorded_project)
                    for document in (*recorded_documents, *checked):
                        identity = document_identity(document)
                        key = _identity_key(identity)
                        if key in documents:
                            continue
                        invalidated_documents[key] = document
                        invalidated_producers[key] = (
                            "invalidated_phase_report",
                            phase,
                            f"report_{phase}",
                        )
                for key, document in invalidated_documents.items():
                    documents[key] = document
                    lifecycles[key] = "invalidated"
                    producers[key] = invalidated_producers[key]

                reachable_history_hashes = {key[3] for key in documents}
                if self.candidates is not None:
                    for stored in self.candidates.list_stored(workspace_id):
                        identity = document_identity(stored.document)
                        key = _identity_key(identity)
                        if key in documents:
                            raise invalid_state(
                                "Stored candidate duplicates a retained artifact identity"
                            )
                        producer = stored.record["producer"]
                        stored_authority = stored.record["authority"]
                        lifecycle = "candidate" if stored_authority == authority else "historical"
                        documents[key] = stored.document
                        lifecycles[key] = lifecycle
                        producers[key] = (
                            "future_candidate",
                            None,
                            str(producer["reference_id"]),
                        )

                if len(documents) > MAX_CREATION_ARTIFACTS:
                    raise invalid_state("Creation artifact census exceeds Studio limits")
                records = _build_records(
                    authority=authority,
                    documents=documents,
                    lifecycles=lifecycles,
                    producers=producers,
                )
                counts = Counter(record["lifecycle"] for record in records)
                public_counts = {
                    "active": counts["active"],
                    "invalidated": counts["invalidated"],
                    "historical": counts["historical"],
                    "candidate": counts["candidate"],
                    "ignored": _ignored_history_count(
                        root,
                        root_identity,
                        reachable_history_hashes,
                    ),
                }
                snapshot_hash = canonical_payload_hash(
                    {
                        "authority": authority,
                        "records": [record["record_hash"] for record in records],
                        "counts": public_counts,
                    }
                )
                if self.candidates is not None:
                    self.candidates.validate_recomputed_snapshot(
                        workspace_id=workspace_id,
                        authority=authority,
                        snapshot_hash=snapshot_hash,
                    )
                expected_snapshot = params["expected_artifact_snapshot_hash"]
                if expected_snapshot is not None and not hmac.compare_digest(
                    snapshot_hash, expected_snapshot
                ):
                    raise conflict("Creation artifact snapshot changed")
                return {
                    "authority": authority,
                    "artifact_snapshot_hash": snapshot_hash,
                    "counts": public_counts,
                    "records": records,
                    "documents": documents,
                    "active_external": checked_active,
                    "project": project,
                    "workflow": workflow,
                }
        except StudioError:
            raise
        except (OSError, ValueError) as exc:
            raise invalid_state("Creation evidence snapshot could not be verified") from exc

    def list(self, params: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = self._snapshot(params)
        records = [
            record
            for record in snapshot["records"]
            if params["lifecycle"] is None or record["lifecycle"] == params["lifecycle"]
        ]
        start = 0
        if params["cursor"] is not None:
            for index, record in enumerate(records):
                if record["artifact_id"] == params["cursor"]:
                    start = index + 1
                    break
            else:
                raise conflict("Creation artifact cursor is not in the exact snapshot")
        page = records[start : start + params["limit"]]
        next_cursor = page[-1]["artifact_id"] if start + len(page) < len(records) and page else None
        return {
            "authority": snapshot["authority"],
            "artifact_snapshot_hash": snapshot["artifact_snapshot_hash"],
            "artifacts": page,
            "next_cursor": next_cursor,
            "counts": snapshot["counts"],
        }

    def inspect(self, params: Mapping[str, Any], *, protocol_version: int = 4) -> dict[str, Any]:
        snapshot = self._snapshot(params)
        record = next(
            (item for item in snapshot["records"] if item["artifact_id"] == params["artifact_id"]),
            None,
        )
        if record is None:
            raise not_found("Creation artifact was not found in the exact snapshot")
        if record["lifecycle"] not in {"active", "candidate"}:
            raise conflict("Creation artifact is no longer active and is not a current candidate")
        records_by_key = {_identity_key(item["subject"]): item for item in snapshot["records"]}
        key = _identity_key(record["subject"])
        projection = _projection(
            snapshot["documents"][key],
            record,
            records_by_key,
            protocol_version=protocol_version,
        )
        return {
            "authority": snapshot["authority"],
            "artifact_snapshot_hash": snapshot["artifact_snapshot_hash"],
            "artifact": record,
            "projection": projection,
        }

    def legacy_readiness(self, workspace_id: object) -> dict[str, Any]:
        """Project the v3 readiness shape from the same active v4 artifact authority."""

        record = self.workspaces.get(workspace_id)
        snapshot = self._snapshot(
            {
                "workspace_id": record["workspace_id"],
                "expected_root_generation": record["root_generation"],
                "expected_source_revision": record["source_revision"],
                "expected_workflow_status_hash": record["workflow_status_hash"],
                "expected_artifact_snapshot_hash": None,
            }
        )
        report = build_creation_readiness(
            snapshot["project"], artifacts=snapshot["active_external"]
        )
        workflow = snapshot["workflow"]
        state = workflow["state"]
        if state == "invalid":
            readiness_state = "invalid"
            blockers = ["workflow_invalid", *report["blocker_reason_codes"]]
        elif state == "missing":
            readiness_state = "missing"
            blockers = ["workflow_missing", *report["blocker_reason_codes"]]
        elif state == "not_started":
            readiness_state = "not_started"
            blockers = ["workflow_not_started", *report["blocker_reason_codes"]]
        elif state == "complete":
            readiness_state = (
                "implementation_ready" if report["release_ready"] else "authoring_ready"
            )
            blockers = list(report["blocker_reason_codes"])
        else:
            readiness_state = "blocked"
            blockers = ["workflow_incomplete", *report["blocker_reason_codes"]]
        return {
            "state": readiness_state,
            "source_revision": snapshot["authority"]["source_revision"],
            "workflow_status_hash": snapshot["authority"]["workflow_status_hash"],
            "current_phase": workflow["current_phase"],
            "release": report["dimensions"]["release"],
            "blocker_reason_codes": sorted(set(blockers), key=lambda item: item.encode("utf-8")),
            "report": report,
        }

    def evidence(self, params: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = self._snapshot(params)
        project: LoadedCreationProject = snapshot["project"]
        status = snapshot["workflow"]["status"]
        active_external = snapshot["active_external"]
        readiness = build_creation_readiness(project, artifacts=active_external)
        handoff = build_creation_handoff(
            project,
            status=status,
            readiness=readiness,
            artifacts=active_external,
        )
        wrapper_blockers: list[str] = []
        workflow_state = snapshot["workflow"]["state"]
        if workflow_state == "not_started":
            wrapper_blockers.append("workflow_not_started")
        elif workflow_state != "complete":
            wrapper_blockers.append("workflow_incomplete")
        blockers = sorted(
            set((*readiness["blocker_reason_codes"], *wrapper_blockers)),
            key=lambda item: item.encode("utf-8"),
        )
        dimensions = copy.deepcopy(readiness["dimensions"])
        if blockers:
            dimensions["release"] = "blocked"
        dimensions["execution"] = sorted(
            dimensions["execution"], key=lambda item: item["platform"].encode("utf-8")
        )

        by_format = {str(document["format"]): document for document in active_external}
        records_by_format = {
            str(record["subject"]["format"]): record
            for record in snapshot["records"]
            if record["lifecycle"] == "active"
        }
        ledger = by_format.get("world-forge.mechanic_capability_ledger")
        mechanic_rows = ledger.get("mechanics", []) if isinstance(ledger, Mapping) else []
        status_counts = Counter(
            item.get("status") for item in mechanic_rows if isinstance(item, Mapping)
        )
        required_features = sorted(
            set(project.profile["runtime_target"]["required_features"]),
            key=lambda item: item.encode("utf-8"),
        )
        verified_features: set[str] = set()
        if isinstance(ledger, Mapping):
            for feature in ledger.get("features", ()):
                if isinstance(feature, Mapping) and feature.get("status") in {
                    "supported_current",
                    "game_extension_verified",
                }:
                    verified_features.add(str(feature.get("feature_id")))
        missing_features = sorted(
            set(required_features) - verified_features,
            key=lambda item: item.encode("utf-8"),
        )
        ledger_record = records_by_format.get("world-forge.mechanic_capability_ledger")
        mechanics = {
            "artifact_id": None if ledger_record is None else ledger_record["artifact_id"],
            "total": len(mechanic_rows),
            "status_counts": {status: status_counts[status] for status in _STATUS_ORDER},
            "required_features": required_features,
            "missing_features": missing_features,
        }

        support = by_format.get("world-forge.runtime_support_report")
        resolved_adapter = None
        if isinstance(support, Mapping):
            adapter = support.get("adapter")
            if isinstance(adapter, Mapping) and isinstance(adapter.get("id"), str):
                resolved_adapter = adapter["id"]
        runtime = {
            "requested_adapter": project.profile["runtime_target"]["requested_adapter"],
            "resolved_adapter": resolved_adapter,
            "required_features": required_features,
            "missing_features": missing_features,
            "platforms": copy.deepcopy(dimensions["execution"]),
        }

        inventory = by_format.get("world-forge.asset_inventory")
        inventory_assets = inventory.get("assets", []) if isinstance(inventory, Mapping) else []
        stages_by_asset: defaultdict[str, set[str]] = defaultdict(set)
        for document in active_external:
            asset = document.get("asset")
            if isinstance(asset, Mapping) and isinstance(asset.get("asset_id"), str):
                stages_by_asset[str(asset["asset_id"])].add(str(document["format"]))
        required_lineage = {
            "world-forge.asset_spec",
            "world-forge.asset_production_request",
            "world-forge.asset_production_receipt",
            "world-forge.asset_selection",
            "world-forge.asset_provenance_record",
            "world-forge.asset_license_record",
            "world-forge.asset_processing_recipe",
            "world-forge.asset_processing_receipt",
            "world-forge.asset_qa_report",
        }
        inventory_ids = {
            str(item.get("asset_id"))
            for item in inventory_assets
            if isinstance(item, Mapping) and isinstance(item.get("asset_id"), str)
        }
        complete = sum(
            1 for asset_id in inventory_ids if required_lineage <= stages_by_asset[asset_id]
        )
        partial = sum(
            1
            for asset_id in inventory_ids
            if stages_by_asset[asset_id] and not required_lineage <= stages_by_asset[asset_id]
        )
        qa_reports = [
            document
            for document in active_external
            if document.get("format") == "world-forge.asset_qa_report"
        ]
        assets = {
            "subject_artifact_id": _format_artifact_id(
                records_by_format, "world-forge.asset_subject"
            ),
            "target_artifact_id": _format_artifact_id(
                records_by_format, "world-forge.asset_target"
            ),
            "style_artifact_id": _format_artifact_id(records_by_format, "world-forge.asset_style"),
            "inventory_artifact_id": _format_artifact_id(
                records_by_format, "world-forge.asset_inventory"
            ),
            "assetpack_artifact_id": _format_artifact_id(
                records_by_format, "world-forge.assetpack"
            ),
            "inventory_assets": len(inventory_ids),
            "lineage_complete": complete,
            "lineage_partial": partial,
            "qa_passed": sum(1 for item in qa_reports if item.get("status") == "passed"),
            "qa_failed": sum(1 for item in qa_reports if item.get("status") != "passed"),
            "licensed": sum(
                1
                for document in active_external
                if document.get("format") == "world-forge.asset_license_record"
            ),
        }

        prerequisites = [
            {
                "code": "adapter_verified",
                "satisfied": dimensions["adapter"] == "verified",
                "message": "The selected runtime adapter must be verified.",
            },
            {
                "code": "assets_sealed",
                "satisfied": dimensions["assets"] == "sealed",
                "message": "All required runtime assets must be sealed.",
            },
            {
                "code": "compilation_complete",
                "satisfied": dimensions["compilation"] == "compiled",
                "message": "The immutable game logic must be compiled.",
            },
            {
                "code": "native_evidence_complete",
                "satisfied": bool(dimensions["execution"])
                and all(item["status"] == "native_verified" for item in dimensions["execution"]),
                "message": "Every declared platform needs native verification evidence.",
            },
            {
                "code": "packaging_verified",
                "satisfied": dimensions["packaging"] == "verified",
                "message": "Standalone packaging must be verified.",
            },
            {
                "code": "workflow_complete",
                "satisfied": workflow_state == "complete",
                "message": "The reviewed P00-P14 workflow must be complete.",
            },
        ]
        prerequisites.sort(key=lambda item: item["code"].encode("utf-8"))

        evidence = {
            "format": CREATION_EVIDENCE_FORMAT,
            "format_version": 1,
            "evidence_id": f"evidence_{snapshot['authority']['workspace_id']}",
            "authority": snapshot["authority"],
            "artifact_snapshot_hash": snapshot["artifact_snapshot_hash"],
            "artifact_counts": snapshot["counts"],
            "dimensions": dimensions,
            "blocker_reason_codes": blockers,
            "mechanics": mechanics,
            "runtime": runtime,
            "assets": assets,
            "materialization": {
                "enabled": False,
                "state": "blocked",
                "prerequisites": prerequisites,
            },
            "readiness": {
                "format": readiness["format"],
                "format_version": readiness["format_version"],
                "id": readiness["readiness_id"],
                "content_hash": readiness["content_hash"],
            },
            "handoff": {
                "format": handoff["format"],
                "format_version": handoff["format_version"],
                "id": handoff["handoff_id"],
                "content_hash": handoff["content_hash"],
            },
            "content_hash": "",
        }
        evidence["content_hash"] = canonical_creation_hash(evidence)
        checked = validate_studio_creation_evidence(evidence)
        if len(canonical_json_bytes(checked)) > MAX_CREATION_EVIDENCE_BYTES:
            raise invalid_state("Creation evidence exceeds the Studio transport limit")
        return {
            "authority": snapshot["authority"],
            "artifact_snapshot_hash": snapshot["artifact_snapshot_hash"],
            "evidence": checked,
        }


def _format_artifact_id(
    records_by_format: Mapping[str, Mapping[str, Any]], format_name: str
) -> str | None:
    record = records_by_format.get(format_name)
    return None if record is None else str(record["artifact_id"])
