from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from worldforge.creation_contracts import (
    LoadedCreationProject,
    canonical_creation_hash,
    validate_creation_documents,
)
from worldforge.creation_workflow import (
    CREATION_WORKFLOW_FORMAT,
    validate_creation_workflow_status,
)
from worldforge.phase_report_v3 import document_identity, validate_artifact_documents
from worldforge.runtime_support_authority import (
    RuntimeSupportAuthorityError,
    VerifiedRuntimeSupportAuthority,
    derive_runtime_evidence,
    derive_runtime_support_report,
)

CREATION_READINESS_FORMAT = "world-forge.creation_readiness"
CREATION_READINESS_VERSION = 1
CREATION_HANDOFF_FORMAT = "world-forge.creation_handoff"
CREATION_HANDOFF_VERSION = 1

_READINESS_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "readiness_id",
        "project",
        "profile",
        "source_manifest",
        "dimensions",
        "blocker_reason_codes",
        "release_ready",
        "evidence",
        "content_hash",
    }
)
_DIMENSION_FIELDS = frozenset(
    {
        "authoring",
        "compilation",
        "assets",
        "adapter",
        "execution",
        "packaging",
        "release",
    }
)
_EXECUTION_FIELDS = frozenset({"platform", "status", "evidence_ids"})
_HANDOFF_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "handoff_id",
        "project",
        "profile",
        "source_manifest",
        "workflow_status",
        "readiness",
        "artifacts",
        "handoff_status",
        "release_blockers",
        "content_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_ARTIFACT_ID_FIELDS = {
    "world-forge.gamepack": "game_id",
    "world-forge.game_analysis": "analysis_id",
    "world-forge.asset_inventory": "inventory_id",
    "world-forge.asset_manifest": "manifest_id",
    "world-forge.assetpack": "assetpack_id",
    "world-forge.runtime_adapter_registry": "registry_id",
    "world-forge.game_runtime_snapshot": "snapshot_id",
    "world-forge.game_runtime_composition": "composition_id",
    "world-forge.runtime_support_report": "report_id",
    "world-forge.runtime_evidence": "evidence_id",
    "world-forge.game_package": "package_id",
}


class CreationReadinessError(ValueError):
    """Raised when readiness or handoff evidence is inconsistent."""


def _validated_project(project: LoadedCreationProject) -> LoadedCreationProject:
    if not isinstance(project, LoadedCreationProject):
        raise CreationReadinessError("readiness requires a loaded creation project")
    try:
        return validate_creation_documents(
            project.project,
            project.profile,
            project.manifest,
            project.world_modules,
            project.activity_modules,
            project.narrative_modules,
            project.system_modules,
            project.logic_modules,
        )
    except ValueError as exc:
        raise CreationReadinessError(f"creation project is invalid: {exc}") from exc


def _exact(value: Mapping[str, Any], fields: frozenset[str], context: str) -> None:
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown:
        raise CreationReadinessError(f"{context} contains unknown fields")
    if missing:
        raise CreationReadinessError(f"{context} is missing fields")


def _artifact_identity(document: Mapping[str, Any]) -> dict[str, Any]:
    format_name = document.get("format")
    field = _ARTIFACT_ID_FIELDS.get(format_name)
    if field is None:
        raise CreationReadinessError(f"unsupported readiness artifact: {format_name!r}")
    if format_name == "world-forge.gamepack":
        game = document.get("game")
        identifier = game.get("id") if isinstance(game, Mapping) else None
    else:
        identifier = document.get(field)
    identity = {
        "format": format_name,
        "format_version": document.get("format_version"),
        "id": identifier,
        "content_hash": document.get("content_hash"),
    }
    _validate_identity(identity, "readiness artifact identity")
    return identity


def _validate_identity(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CreationReadinessError(f"{context} must be an object")
    _exact(value, _IDENTITY_FIELDS, context)
    if not isinstance(value.get("format"), str) or not value["format"]:
        raise CreationReadinessError(f"{context}.format is invalid")
    if value.get("format_version") != 1 or isinstance(value.get("format_version"), bool):
        raise CreationReadinessError(f"{context}.format_version must be 1")
    identifier = value.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise CreationReadinessError(f"{context}.id is invalid")
    content_hash = value.get("content_hash")
    if (
        not isinstance(content_hash, str)
        or len(content_hash) != 64
        or any(character not in "0123456789abcdef" for character in content_hash)
    ):
        raise CreationReadinessError(f"{context}.content_hash is invalid")
    return value


def _readiness_id(project: LoadedCreationProject) -> str:
    seed = canonical_creation_hash(
        {
            "project": document_identity(project.project),
            "profile": document_identity(project.profile),
            "source_manifest": document_identity(project.manifest),
        }
    )
    return f"readiness_{seed[:24]}"


def _artifact_map(
    project: LoadedCreationProject,
    artifacts: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, Any]]]:
    try:
        checked_artifacts = validate_artifact_documents(
            project,
            artifacts,
        )
    except ValueError as exc:
        raise CreationReadinessError(f"readiness artifact validation failed: {exc}") from exc
    by_format: dict[str, Mapping[str, Any]] = {}
    identities: list[dict[str, Any]] = []
    for artifact in checked_artifacts:
        if artifact.get("format") not in _ARTIFACT_ID_FIELDS:
            continue
        identity = _artifact_identity(artifact)
        format_name = identity["format"]
        if format_name in by_format:
            raise CreationReadinessError(
                f"readiness accepts at most one artifact for {format_name}"
            )
        by_format[format_name] = artifact
        identities.append(identity)
    identities.sort(
        key=lambda item: (
            item["format"].encode("utf-8"),
            item["id"].encode("utf-8"),
            item["content_hash"].encode("ascii"),
        )
    )
    return by_format, identities


def _build_creation_readiness_document(
    project: LoadedCreationProject,
    *,
    artifacts: Sequence[Mapping[str, Any]],
    runtime_support_authority: VerifiedRuntimeSupportAuthority | None,
) -> dict[str, Any]:
    by_format, evidence = _artifact_map(project, artifacts)
    profile = project.profile
    gamepack = by_format.get("world-forge.gamepack")
    analysis = by_format.get("world-forge.game_analysis")
    if gamepack is not None:
        compilation = "compiled"
    elif analysis is not None and analysis.get("status") == "unsupported":
        compilation = "unsupported"
    else:
        compilation = "not_requested"

    assets_not_applicable = (
        profile["production"]["content_modes"]["assets"] == "not_applicable"
        and not profile["runtime_target"]["asset_formats"]
    )
    if "world-forge.assetpack" in by_format:
        assets = "sealed"
    elif "world-forge.asset_manifest" in by_format:
        assets = "processed"
    elif "world-forge.asset_inventory" in by_format:
        assets = "planned"
    else:
        assets = "unplanned"

    raw_support = by_format.get("world-forge.runtime_support_report")
    raw_runtime_evidence = by_format.get("world-forge.runtime_evidence")
    raw_package = by_format.get("world-forge.game_package")
    support: Mapping[str, Any] | None = None
    authority_document: Mapping[str, Any] | None = None
    if runtime_support_authority is not None:
        try:
            support = derive_runtime_support_report(runtime_support_authority)
            authoritative_evidence = derive_runtime_evidence(runtime_support_authority)
            authority_document = runtime_support_authority.document
        except (RuntimeSupportAuthorityError, TypeError, ValueError) as exc:
            raise CreationReadinessError(f"runtime support authority is invalid: {exc}") from exc

        bindings = (
            ("world-forge.gamepack", "gamepack"),
            ("world-forge.asset_inventory", "asset_inventory"),
            ("world-forge.assetpack", "assetpack"),
            ("world-forge.game_runtime_composition", "composition"),
            ("world-forge.runtime_adapter_registry", "registry"),
            ("world-forge.game_runtime_snapshot", "runtime_snapshot"),
        )
        for format_name, authority_field in bindings:
            artifact = by_format.get(format_name)
            reference = authority_document[authority_field]
            if artifact is None or _artifact_identity(artifact) != {
                "format": reference["format"],
                "format_version": reference["format_version"],
                "id": reference["id"],
                "content_hash": reference["content_hash"],
            }:
                raise CreationReadinessError(
                    f"runtime support authority crosses {format_name} readiness evidence"
                )
        if raw_support != support:
            raise CreationReadinessError(
                "runtime support authority report does not match readiness evidence"
            )
        expected_evidence = [] if raw_runtime_evidence is None else [raw_runtime_evidence]
        if authoritative_evidence != expected_evidence:
            raise CreationReadinessError(
                "runtime support authority evidence does not match readiness artifacts"
            )
        package_reference = authority_document["package_evidence"]
        if package_reference is None:
            if raw_package is not None:
                raise CreationReadinessError("game package has no exact runtime support authority")
        else:
            package = package_reference["package"]
            if raw_package is None or _artifact_identity(raw_package) != {
                "format": package["format"],
                "format_version": package["format_version"],
                "id": package["id"],
                "content_hash": package["content_hash"],
            }:
                raise CreationReadinessError(
                    "runtime support authority package does not match readiness evidence"
                )
    requested_adapter = profile["runtime_target"]["requested_adapter"]
    if support is not None:
        support_adapter = support.get("dimensions", {}).get("adapter")
        adapter = support_adapter if support_adapter in {"declared", "verified"} else "declared"
    else:
        adapter = "absent" if requested_adapter is None else "declared"

    support_execution = {
        item.get("platform", {}).get("platform_id"): item
        for item in (
            support.get("dimensions", {}).get("execution", []) if support is not None else []
        )
        if isinstance(item, dict)
    }
    execution: list[dict[str, Any]] = []
    for platform in profile["runtime_target"]["platforms"]:
        supplied = support_execution.get(platform)
        status = supplied.get("status") if isinstance(supplied, dict) else "untested"
        if status not in {"untested", "headless_verified", "native_verified", "failed"}:
            status = "failed"
        evidence_ids = supplied.get("evidence_ids", []) if isinstance(supplied, dict) else []
        execution.append(
            {
                "platform": platform,
                "status": status,
                "evidence_ids": list(evidence_ids),
            }
        )
    packaging = (
        "verified"
        if authority_document is not None and authority_document["package_evidence"] is not None
        else "unverified"
    )
    if packaging not in {"unverified", "verified", "failed"}:
        packaging = "failed"

    blockers: list[str] = []
    if compilation != "compiled":
        blockers.append(
            "compilation_unsupported"
            if compilation == "unsupported"
            else "compilation_not_requested"
        )
    if not assets_not_applicable and assets != "sealed":
        blockers.append("assets_not_sealed")
    if requested_adapter is None:
        blockers.append("runtime_adapter_not_requested")
    elif adapter != "verified":
        blockers.append("runtime_adapter_not_verified")
    if runtime_support_authority is None and (
        raw_support is not None or raw_runtime_evidence is not None
    ):
        blockers.append("runtime_evidence_authority_missing")
    if any(item["status"] != "native_verified" for item in execution):
        blockers.append("native_evidence_missing")
        if requested_adapter is not None:
            blockers.append("native_evidence_authority_unavailable")
    if packaging != "verified":
        blockers.append("packaging_evidence_missing")
        if raw_package is not None and runtime_support_authority is None:
            blockers.append("packaging_evidence_authority_missing")
    blockers = sorted(set(blockers), key=lambda item: item.encode("utf-8"))
    release_ready = not blockers
    result = {
        "format": CREATION_READINESS_FORMAT,
        "format_version": CREATION_READINESS_VERSION,
        "readiness_id": _readiness_id(project),
        "project": document_identity(project.project),
        "profile": document_identity(project.profile),
        "source_manifest": document_identity(project.manifest),
        "dimensions": {
            "authoring": "valid",
            "compilation": compilation,
            "assets": assets,
            "adapter": adapter,
            "execution": execution,
            "packaging": packaging,
            "release": "ready" if release_ready else "blocked",
        },
        "blocker_reason_codes": blockers,
        "release_ready": release_ready,
        "evidence": evidence,
        "content_hash": "",
    }
    result["content_hash"] = canonical_creation_hash(result)
    return result


def build_creation_readiness(
    source_project: LoadedCreationProject,
    *,
    artifacts: Sequence[Mapping[str, Any]] = (),
    runtime_support_authority: VerifiedRuntimeSupportAuthority | None = None,
) -> dict[str, Any]:
    project = _validated_project(source_project)
    result = _build_creation_readiness_document(
        project,
        artifacts=artifacts,
        runtime_support_authority=runtime_support_authority,
    )
    return validate_creation_readiness(
        result,
        project,
        artifacts=artifacts,
        runtime_support_authority=runtime_support_authority,
    )


def validate_creation_readiness(
    value: object,
    source_project: LoadedCreationProject,
    *,
    artifacts: Sequence[Mapping[str, Any]] = (),
    runtime_support_authority: VerifiedRuntimeSupportAuthority | None = None,
) -> dict[str, Any]:
    project = _validated_project(source_project)
    if not isinstance(value, dict):
        raise CreationReadinessError("creation readiness must be an object")
    report = value
    _exact(report, _READINESS_FIELDS, "creation readiness")
    if (
        report.get("format") != CREATION_READINESS_FORMAT
        or report.get("format_version") != CREATION_READINESS_VERSION
        or isinstance(report.get("format_version"), bool)
    ):
        raise CreationReadinessError("creation readiness version is unsupported")
    if report.get("readiness_id") != _readiness_id(project):
        raise CreationReadinessError("creation readiness ID does not match its project")
    for field, document in (
        ("project", project.project),
        ("profile", project.profile),
        ("source_manifest", project.manifest),
    ):
        if report.get(field) != document_identity(document):
            raise CreationReadinessError(f"creation readiness {field} does not match")
    dimensions = report.get("dimensions")
    if not isinstance(dimensions, dict):
        raise CreationReadinessError("creation readiness dimensions must be an object")
    _exact(dimensions, _DIMENSION_FIELDS, "creation readiness dimensions")
    if dimensions.get("authoring") != "valid":
        raise CreationReadinessError("validated creation readiness authoring must be valid")
    allowed = {
        "compilation": {"not_requested", "compiled", "unsupported", "failed"},
        "assets": {"unplanned", "planned", "produced", "processed", "sealed", "failed"},
        "adapter": {"absent", "declared", "verified"},
        "packaging": {"unverified", "verified", "failed"},
        "release": {"blocked", "ready"},
    }
    for field, values in allowed.items():
        if dimensions.get(field) not in values:
            raise CreationReadinessError(f"creation readiness {field} is unsupported")
    execution = dimensions.get("execution")
    if not isinstance(execution, list):
        raise CreationReadinessError("creation readiness execution must be an array")
    expected_platforms = project.profile["runtime_target"]["platforms"]
    if [item.get("platform") for item in execution if isinstance(item, dict)] != expected_platforms:
        raise CreationReadinessError("creation readiness execution platforms do not match")
    for index, item in enumerate(execution):
        if not isinstance(item, dict):
            raise CreationReadinessError(f"creation readiness execution/{index} is invalid")
        _exact(item, _EXECUTION_FIELDS, f"creation readiness execution/{index}")
        if item.get("status") not in {
            "untested",
            "headless_verified",
            "native_verified",
            "failed",
        }:
            raise CreationReadinessError(
                f"creation readiness execution/{index}.status is unsupported"
            )
        if not isinstance(item.get("evidence_ids"), list) or any(
            not isinstance(identifier, str) for identifier in item["evidence_ids"]
        ):
            raise CreationReadinessError(
                f"creation readiness execution/{index}.evidence_ids is invalid"
            )
    blockers = report.get("blocker_reason_codes")
    if (
        not isinstance(blockers, list)
        or any(not isinstance(item, str) or not item for item in blockers)
        or blockers != sorted(set(blockers), key=lambda item: item.encode("utf-8"))
    ):
        raise CreationReadinessError("creation readiness blockers are not canonical")
    release_ready = report.get("release_ready")
    if not isinstance(release_ready, bool):
        raise CreationReadinessError("creation readiness release_ready must be boolean")
    if release_ready != (not blockers) or dimensions["release"] != (
        "ready" if release_ready else "blocked"
    ):
        raise CreationReadinessError("creation readiness release state is inconsistent")
    _by_format, expected_evidence = _artifact_map(project, artifacts)
    if report.get("evidence") != expected_evidence:
        raise CreationReadinessError("creation readiness evidence does not match artifacts")
    if canonical_creation_hash(report) != report.get("content_hash"):
        raise CreationReadinessError("creation readiness content hash does not match")
    expected = _build_creation_readiness_document(
        project,
        artifacts=artifacts,
        runtime_support_authority=runtime_support_authority,
    )
    if report != expected:
        raise CreationReadinessError(
            "creation readiness is not the canonical derivation of its validated artifacts"
        )
    return copy.deepcopy(report)


def _workflow_identity(status: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": CREATION_WORKFLOW_FORMAT,
        "format_version": 1,
        "id": status["workflow_id"],
        "content_hash": status["content_hash"],
    }


def _readiness_identity(readiness: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": CREATION_READINESS_FORMAT,
        "format_version": 1,
        "id": readiness["readiness_id"],
        "content_hash": readiness["content_hash"],
    }


def build_creation_handoff(
    source_project: LoadedCreationProject,
    *,
    status: Mapping[str, Any],
    readiness: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]] = (),
    runtime_support_authority: VerifiedRuntimeSupportAuthority | None = None,
) -> dict[str, Any]:
    project = _validated_project(source_project)
    checked_status = validate_creation_workflow_status(status, project)
    checked_readiness = validate_creation_readiness(
        readiness,
        project,
        artifacts=artifacts,
        runtime_support_authority=runtime_support_authority,
    )
    _by_format, artifact_identities = _artifact_map(project, artifacts)
    handoff_status = (
        "implementation_ready" if checked_readiness["release_ready"] else "authoring_ready"
    )
    seed = canonical_creation_hash(
        {
            "workflow": _workflow_identity(checked_status),
            "readiness": _readiness_identity(checked_readiness),
        }
    )
    result = {
        "format": CREATION_HANDOFF_FORMAT,
        "format_version": CREATION_HANDOFF_VERSION,
        "handoff_id": f"handoff_{seed[:24]}",
        "project": document_identity(project.project),
        "profile": document_identity(project.profile),
        "source_manifest": document_identity(project.manifest),
        "workflow_status": _workflow_identity(checked_status),
        "readiness": _readiness_identity(checked_readiness),
        "artifacts": artifact_identities,
        "handoff_status": handoff_status,
        "release_blockers": copy.deepcopy(checked_readiness["blocker_reason_codes"]),
        "content_hash": "",
    }
    result["content_hash"] = canonical_creation_hash(result)
    return validate_creation_handoff(
        result,
        project,
        status=checked_status,
        readiness=checked_readiness,
        artifacts=artifacts,
        runtime_support_authority=runtime_support_authority,
    )


def validate_creation_handoff(
    value: object,
    source_project: LoadedCreationProject,
    *,
    status: Mapping[str, Any],
    readiness: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]] = (),
    runtime_support_authority: VerifiedRuntimeSupportAuthority | None = None,
) -> dict[str, Any]:
    project = _validated_project(source_project)
    checked_status = validate_creation_workflow_status(status, project)
    checked_readiness = validate_creation_readiness(
        readiness,
        project,
        artifacts=artifacts,
        runtime_support_authority=runtime_support_authority,
    )
    if not isinstance(value, dict):
        raise CreationReadinessError("creation handoff must be an object")
    handoff = value
    _exact(handoff, _HANDOFF_FIELDS, "creation handoff")
    if (
        handoff.get("format") != CREATION_HANDOFF_FORMAT
        or handoff.get("format_version") != CREATION_HANDOFF_VERSION
        or isinstance(handoff.get("format_version"), bool)
    ):
        raise CreationReadinessError("creation handoff version is unsupported")
    for field, document in (
        ("project", project.project),
        ("profile", project.profile),
        ("source_manifest", project.manifest),
    ):
        if handoff.get(field) != document_identity(document):
            raise CreationReadinessError(f"creation handoff {field} does not match")
    if handoff.get("workflow_status") != _workflow_identity(checked_status):
        raise CreationReadinessError("creation handoff workflow identity does not match")
    if handoff.get("readiness") != _readiness_identity(checked_readiness):
        raise CreationReadinessError("creation handoff readiness identity does not match")
    _by_format, artifact_identities = _artifact_map(project, artifacts)
    if handoff.get("artifacts") != artifact_identities:
        raise CreationReadinessError("creation handoff artifacts do not match")
    expected_status = (
        "implementation_ready" if checked_readiness["release_ready"] else "authoring_ready"
    )
    if handoff.get("handoff_status") != expected_status:
        raise CreationReadinessError("creation handoff status is inconsistent")
    if handoff.get("release_blockers") != checked_readiness["blocker_reason_codes"]:
        raise CreationReadinessError("creation handoff blockers do not match readiness")
    if canonical_creation_hash(handoff) != handoff.get("content_hash"):
        raise CreationReadinessError("creation handoff content hash does not match")
    return copy.deepcopy(handoff)
