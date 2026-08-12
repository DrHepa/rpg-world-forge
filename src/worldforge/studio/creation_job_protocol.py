from __future__ import annotations

import copy
import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from gamepack_runtime import GameLogicError
from gamepack_runtime.distribution import (
    GAME_LOCK_PATH,
    GAME_MANIFEST_PATH,
    StandaloneDistributionError,
    canonical_contract_bytes,
    validate_standalone_game_document,
    validate_standalone_game_lock_document,
)
from gamepack_runtime.game_package import (
    MAX_GAME_PACKAGE_ARCHIVE_BYTES,
    GamePackageError,
    build_game_package_from_files,
    validate_game_package_document,
    verify_game_package_bytes,
)
from gamepack_runtime.headless import (
    MAX_GAME_EXECUTION_SCRIPT_BYTES,
    serialize_game_execution_script,
)
from worldforge.creation_contracts import LoadedCreationProject, validate_creation_documents
from worldforge.game_analysis import analyze_gamepack
from worldforge.game_materialization_bundle import (
    build_game_materialization_bundle_manifest,
    serialize_game_materialization_bundle,
    validate_game_materialization_bundle_document,
    verify_game_materialization_bundle,
)
from worldforge.game_package import WorldForgeGamePackageError, verify_game_package
from worldforge.game_package_extraction import (
    GamePackageExtractionEvidenceError,
    build_game_package_extraction_evidence,
)
from worldforge.game_runtime_bundle import (
    build_game_runtime_bundle_manifest_from_objects,
    serialize_game_runtime_bundle,
    validate_game_runtime_bundle_document,
    verify_game_runtime_bundle,
)
from worldforge.gamepack import (
    GamepackError,
    build_authoring_capability_ledger,
    build_gamepack,
    validate_gamepack_document,
)
from worldforge.generic_asset_authority import (
    GenericAssetAuthorityError,
    RetainedAssetQaReviewRecord,
    RetainedAssetReleaseAuthorityRecord,
    VerifiedAssetQaReview,
    build_asset_qa_review_receipt,
    build_asset_release_authority,
    derive_asset_release_blockers,
    validate_asset_qa_review_receipt_document,
    validate_asset_release_authority_document,
    verify_asset_qa_review,
    verify_asset_release_authority,
)
from worldforge.generic_asset_limits import MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
from worldforge.generic_asset_processing import (
    GenericAssetProcessingError,
    build_asset_manifest,
    build_asset_processing_receipt,
    build_asset_processing_recipe,
    build_asset_qa_report,
)
from worldforge.generic_asset_production import (
    GenericAssetProductionError,
    read_verified_artifact_bytes,
)
from worldforge.generic_assetpack import (
    GenericAssetpackError,
    build_generic_assetpack_manifest,
    validate_generic_assetpack_document,
    verify_generic_assetpack,
)
from worldforge.generic_assets import (
    GenericAssetError,
    validate_asset_inventory_document,
)
from worldforge.generic_headless import (
    GenericHeadlessError,
    build_headless_evidence_tree,
)
from worldforge.generic_runtime import (
    RuntimeContractError,
    build_builtin_runtime_adapters,
    build_game_runtime_composition,
    build_game_runtime_snapshot,
    build_runtime_adapter_registry,
    build_runtime_support_report,
    resolve_runtime_build_readiness,
    validate_game_runtime_composition_document,
    validate_runtime_adapter_registry_document,
    validate_runtime_snapshot_document,
    validate_runtime_support_report_document,
)
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.phase_report_v3 import (
    PhaseReportV3Error,
    artifact_dependency_identities,
    document_identity,
    validate_artifact_documents,
)
from worldforge.runtime_support_authority import (
    RuntimeSupportAuthorityError,
    attach_verified_headless_evidence,
    derive_runtime_evidence,
    derive_runtime_support_report,
    initialize_runtime_support_authority,
)
from worldforge.standalone_game import (
    StandaloneGameError,
    build_standalone_game_documents,
    verify_standalone_game,
)
from worldforge.studio.contracts import (
    CREATION_ANALYSIS_STATUSES,
    ENTITY_ID_PATTERN,
    MAX_CHANGESET_BYTES,
    OPERATION_PATTERN,
    SHA256_PATTERN,
    WORKSPACE_ID_PATTERN,
)
from worldforge.validation_memo import validation_memo_scope

PRIVATE_CREATION_REQUEST_FORMAT = "world-forge.studio_creation_job_request"
PRIVATE_CREATION_REQUEST_VERSION = 1
PRIVATE_CREATION_ASSET_REQUEST_VERSION = 2
PRIVATE_CREATION_ASSET_SEAL_REQUEST_VERSION = 3
PRIVATE_CREATION_RUNTIME_COMPOSE_REQUEST_VERSION = 4
PRIVATE_CREATION_RUNTIME_BUNDLE_REQUEST_VERSION = 5
PRIVATE_CREATION_MATERIALIZATION_BUNDLE_REQUEST_VERSION = 6
PRIVATE_CREATION_GAME_MATERIALIZE_REQUEST_VERSION = 7
PRIVATE_CREATION_GAME_PACKAGE_REQUEST_VERSION = 8
PRIVATE_CREATION_GAME_PACKAGE_EXTRACT_REQUEST_VERSION = 9
PRIVATE_CREATION_ASSET_QA_REVIEW_REQUEST_VERSION = 10
PRIVATE_CREATION_ASSET_RELEASE_AUTHORIZE_REQUEST_VERSION = 11
PRIVATE_CREATION_RUNTIME_HEADLESS_REQUEST_VERSION = 12
MAX_PRIVATE_CREATION_REQUEST_BYTES = 64 * 1024 * 1024
ADMISSION_FORMATS = frozenset(
    {
        "world-forge.gamepack",
        "world-forge.game_analysis",
        "world-forge.mechanic_capability_ledger",
        "world-forge.asset_subject",
        "world-forge.asset_target",
        "world-forge.asset_style",
        "world-forge.asset_inventory",
        "world-forge.asset_spec",
        "world-forge.asset_production_request",
        "world-forge.asset_production_receipt",
        "world-forge.asset_selection",
        "world-forge.asset_provenance_record",
        "world-forge.asset_license_record",
        "world-forge.asset_processing_recipe",
        "world-forge.asset_processing_receipt",
        "world-forge.asset_qa_report",
        "world-forge.asset_qa_review_receipt",
        "world-forge.asset_release_authority",
        "world-forge.asset_manifest",
        "world-forge.assetpack",
        "world-forge.runtime_adapter",
        "world-forge.runtime_adapter_registry",
        "world-forge.game_runtime_snapshot",
        "world-forge.game_runtime_composition",
        "world-forge.runtime_evidence",
        "world-forge.runtime_support_report",
        "world-forge.runtime_support_authority",
        "world-forge.game_execution_script",
        "world-forge.game_runtime_bundle",
        "world-forge.game_package",
        "world-forge.game_package_extraction",
        "world-forge.creation_readiness",
        "world-forge.creation_handoff",
    }
)
_FORBIDDEN_ADMISSION_KEYS = frozenset(
    {
        "api_key",
        "command",
        "credential",
        "credentials",
        "env",
        "environment",
        "model_id",
        "native_path",
        "prompt",
        "prompts",
        "provider",
        "provider_id",
        "secret",
        "token",
        "tool_id",
    }
)
_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "artifact",
    "dependency_documents",
}
_ASSET_PROCESS_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "lineage_documents",
    "recipe_id",
    "processing_receipt_id",
    "qa_report_id",
    "acceptance_results",
    "staged_inputs",
}
_ASSET_RELEASE_SEAL_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "lineage_documents",
    "manifest_id",
    "target_grant_id",
    "target_grant_generation",
    "staged_inputs",
}
_RUNTIME_COMPOSE_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "lineage_documents",
    "target_grant_id",
    "target_grant_generation",
    "staged_inputs",
}
_RUNTIME_BUNDLE_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "lineage_documents",
    "source_grant_id",
    "source_grant_generation",
    "target_grant_id",
    "target_grant_generation",
    "staged_inputs",
}
_MATERIALIZATION_BUNDLE_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "runtime_bundle_manifest",
    "source_grant_id",
    "source_grant_generation",
    "target_grant_id",
    "target_grant_generation",
    "staged_inputs",
}
_GAME_MATERIALIZE_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "materialization_bundle_manifest",
    "source_grant_id",
    "source_grant_generation",
    "target_grant_id",
    "target_grant_generation",
    "staged_inputs",
}
_GAME_PACKAGE_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "standalone_game_manifest",
    "standalone_game_lock",
    "game_package_manifest",
    "archive_output",
    "source_grant_id",
    "source_grant_generation",
    "target_grant_id",
    "target_grant_generation",
    "staged_inputs",
}
_GAME_PACKAGE_EXTRACT_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "game_package_manifest",
    "archive_input",
    "source_grant_id",
    "source_grant_generation",
    "target_grant_id",
    "target_grant_generation",
    "staged_inputs",
}
_ASSET_QA_REVIEW_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "lineage_documents",
    "review_receipt_id",
    "output_role",
    "decisions",
    "blockers",
    "staged_inputs",
}
_ASSET_RELEASE_AUTHORIZE_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "lineage_documents",
    "review_documents",
    "manifest_id",
    "assetpack_id",
    "release_authority_id",
    "blockers",
    "target_grant_id",
    "target_grant_generation",
    "staged_inputs",
}
_RUNTIME_HEADLESS_PRIVATE_FIELDS = {
    "format",
    "format_version",
    "job_id",
    "workspace_id",
    "operation",
    "authority",
    "inputs",
    "source",
    "artifact_documents",
    "asset_release_request",
    "platform_id",
    "source_grant_id",
    "source_grant_generation",
    "target_grant_id",
    "target_grant_generation",
    "staged_inputs",
}
_SOURCE_FIELDS = {
    "project",
    "profile",
    "source_manifest",
    "world_modules",
    "activity_modules",
    "narrative_modules",
    "system_modules",
    "logic_modules",
}
_ASSET_PROCESS_FORMAT_ORDER = (
    "world-forge.gamepack",
    "world-forge.asset_subject",
    "world-forge.asset_target",
    "world-forge.asset_style",
    "world-forge.asset_inventory",
    "world-forge.asset_spec",
    "world-forge.asset_production_request",
    "world-forge.asset_production_receipt",
    "world-forge.asset_selection",
    "world-forge.asset_provenance_record",
    "world-forge.asset_license_record",
)
_ASSET_PROCESS_FORMATS = frozenset(_ASSET_PROCESS_FORMAT_ORDER)
_ASSET_PROCESS_SINGULAR_FORMATS = frozenset(_ASSET_PROCESS_FORMAT_ORDER[:-1])
_ASSET_RELEASE_ROOT_FORMAT_ORDER = (
    "world-forge.gamepack",
    "world-forge.asset_subject",
    "world-forge.asset_target",
    "world-forge.asset_style",
    "world-forge.asset_inventory",
)
_ASSET_RELEASE_RECORD_FORMAT_ORDER = (
    "world-forge.asset_spec",
    "world-forge.asset_production_request",
    "world-forge.asset_production_receipt",
    "world-forge.asset_selection",
    "world-forge.asset_provenance_record",
    "world-forge.asset_license_record",
    "world-forge.asset_processing_recipe",
    "world-forge.asset_processing_receipt",
    "world-forge.asset_qa_report",
)
_ASSET_RELEASE_FORMATS = frozenset(
    (*_ASSET_RELEASE_ROOT_FORMAT_ORDER, *_ASSET_RELEASE_RECORD_FORMAT_ORDER)
)
_ASSET_QA_REVIEW_FORMAT_ORDER = (
    "world-forge.gamepack",
    "world-forge.asset_subject",
    "world-forge.asset_target",
    "world-forge.asset_style",
    "world-forge.asset_inventory",
    "world-forge.asset_spec",
    "world-forge.asset_production_request",
    "world-forge.asset_production_receipt",
    "world-forge.asset_selection",
    "world-forge.asset_provenance_record",
    "world-forge.asset_license_record",
    "world-forge.asset_processing_recipe",
    "world-forge.asset_processing_receipt",
    "world-forge.asset_qa_report",
)
_ASSET_STAGED_INPUT_FIELDS = {
    "candidate_artifact_id",
    "role",
    "source_locator",
    "sha256",
    "size_bytes",
}
_ASSET_SEAL_STAGED_INPUT_FIELDS = {
    "asset_id",
    "role",
    "source_locator",
    "sha256",
    "size_bytes",
}
_RUNTIME_COMPOSE_STAGED_INPUT_FIELDS = {
    "source_locator",
    "sha256",
    "size_bytes",
}
_RUNTIME_HEADLESS_STAGED_INPUT_FIELDS = {
    "source_locator",
    "sha256",
    "size_bytes",
}
_RUNTIME_HEADLESS_FORMAT_ORDER = (
    "world-forge.gamepack",
    "world-forge.asset_inventory",
    "world-forge.assetpack",
    "world-forge.asset_release_authority",
    "world-forge.game_runtime_snapshot",
    "world-forge.runtime_adapter_registry",
    "world-forge.game_runtime_composition",
    "world-forge.game_runtime_bundle",
    "world-forge.game_execution_script",
)
_RUNTIME_HEADLESS_PLATFORM_IDS = frozenset({"platform:linux_x86_64", "platform:windows_x86_64"})
_ASSET_ACCEPTANCE_FIELDS = {
    "criterion_index",
    "criterion_sha256",
    "status",
    "evidence_hashes",
}


class CreationWorkerProtocolError(ValueError):
    """A closed private request or deterministic worker result is invalid."""


@dataclass(frozen=True)
class CreationWorkerOutput:
    locator: str
    subject: dict[str, Any]
    payload: bytes


@dataclass(frozen=True)
class CreationWorkerBinaryOutput:
    locator: str
    payload: bytes


@dataclass(frozen=True)
class CreationWorkerResult:
    outputs: tuple[CreationWorkerOutput, ...]
    analysis_status: str
    reason_codes: tuple[str, ...]
    binary_outputs: tuple[CreationWorkerBinaryOutput, ...] = ()


class _PrivateAssetReviewResolver:
    """Resolve only request-bound review bytes inside the isolated worker."""

    def __init__(self) -> None:
        self.reviews: dict[tuple[str, str], RetainedAssetQaReviewRecord] = {}
        self.releases: dict[tuple[str, str], RetainedAssetReleaseAuthorityRecord] = {}

    def resolve_asset_qa_review(
        self,
        *,
        review_receipt_id: str,
        content_hash: str,
    ) -> RetainedAssetQaReviewRecord:
        try:
            return self.reviews[(review_receipt_id, content_hash)]
        except KeyError as exc:
            raise GenericAssetAuthorityError(
                "authority_resolver_failed",
                "private request review authority is unavailable",
            ) from exc

    def resolve_asset_release_authority(
        self,
        *,
        release_authority_id: str,
        content_hash: str,
    ) -> RetainedAssetReleaseAuthorityRecord:
        try:
            return self.releases[(release_authority_id, content_hash)]
        except KeyError as exc:
            raise GenericAssetAuthorityError(
                "release_authority_unavailable",
                "private request release authority is unavailable",
            ) from exc


def _invalid_fields(value: dict[str, Any], expected: set[str], context: str) -> None:
    invalid = (expected - set(value)) | (set(value) - expected)
    if invalid:
        raise CreationWorkerProtocolError(
            f"{context} has invalid fields: {', '.join(sorted(invalid))}"
        )


def _identifier(value: object, *, context: str, workspace: bool = False) -> str:
    pattern = WORKSPACE_ID_PATTERN if workspace else ENTITY_ID_PATTERN
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CreationWorkerProtocolError(f"{context} is not a valid identifier")
    return value


def _identity(value: object, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CreationWorkerProtocolError(f"{context} must be an object")
    _invalid_fields(value, {"format", "format_version", "id", "content_hash"}, context)
    if not isinstance(value["format"], str) or OPERATION_PATTERN.fullmatch(value["format"]) is None:
        raise CreationWorkerProtocolError(f"{context}/format is invalid")
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        raise CreationWorkerProtocolError(f"{context}/format_version must be 1")
    _identifier(value["id"], context=f"{context}/id")
    if (
        not isinstance(value["content_hash"], str)
        or SHA256_PATTERN.fullmatch(value["content_hash"]) is None
    ):
        raise CreationWorkerProtocolError(f"{context}/content_hash is invalid")
    return value


def _authority(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CreationWorkerProtocolError("private request authority must be an object")
    fields = {
        "root_generation",
        "source_revision",
        "workflow_status_hash",
        "artifact_snapshot_hash",
    }
    _invalid_fields(value, fields, "private request authority")
    generation = value["root_generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise CreationWorkerProtocolError("private request root generation is invalid")
    for field in ("source_revision", "artifact_snapshot_hash"):
        if not isinstance(value[field], str) or SHA256_PATTERN.fullmatch(value[field]) is None:
            raise CreationWorkerProtocolError(f"private request {field} is invalid")
    workflow_hash = value["workflow_status_hash"]
    if workflow_hash is not None and (
        not isinstance(workflow_hash, str) or SHA256_PATTERN.fullmatch(workflow_hash) is None
    ):
        raise CreationWorkerProtocolError("private request workflow_status_hash is invalid")
    return value


def _source_payload(project: LoadedCreationProject) -> dict[str, Any]:
    return {
        "project": copy.deepcopy(project.project),
        "profile": copy.deepcopy(project.profile),
        "source_manifest": copy.deepcopy(project.manifest),
        "world_modules": [copy.deepcopy(item) for item in project.world_modules],
        "activity_modules": [copy.deepcopy(item) for item in project.activity_modules],
        "narrative_modules": [copy.deepcopy(item) for item in project.narrative_modules],
        "system_modules": [copy.deepcopy(item) for item in project.system_modules],
        "logic_modules": [copy.deepcopy(item) for item in project.logic_modules],
    }


def _loaded_source(value: object) -> LoadedCreationProject:
    if not isinstance(value, dict):
        raise CreationWorkerProtocolError("private request source must be an object")
    _invalid_fields(value, _SOURCE_FIELDS, "private request source")
    collections: dict[str, tuple[dict[str, Any], ...]] = {}
    for field in (
        "world_modules",
        "activity_modules",
        "narrative_modules",
        "system_modules",
        "logic_modules",
    ):
        raw = value[field]
        if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
            raise CreationWorkerProtocolError(f"private request source/{field} is invalid")
        collections[field] = tuple(raw)
    try:
        return validate_creation_documents(
            value["project"],
            value["profile"],
            value["source_manifest"],
            collections["world_modules"],
            collections["activity_modules"],
            collections["narrative_modules"],
            collections["system_modules"],
            collections["logic_modules"],
        )
    except (TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError("private request source is not integral") from exc


def _source_documents(project: LoadedCreationProject) -> tuple[dict[str, Any], ...]:
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


def _artifact_id(identity: dict[str, Any]) -> str:
    return "artifact_" + canonical_payload_hash({"subject": identity})


def _input_reference(document: dict[str, Any]) -> dict[str, Any]:
    identity = document_identity(document)
    return {"artifact_id": _artifact_id(identity), "subject": identity}


def _identity_key(document_or_identity: dict[str, Any]) -> tuple[str, int, str, str]:
    identity = (
        document_or_identity
        if set(document_or_identity) == {"format", "format_version", "id", "content_hash"}
        else document_identity(document_or_identity)
    )
    return (
        str(identity["format"]),
        int(identity["format_version"]),
        str(identity["id"]),
        str(identity["content_hash"]),
    )


def _canonical_asset_lineage(
    project: LoadedCreationProject,
    value: object,
) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(not isinstance(item, dict) for item in value)
    ):
        raise CreationWorkerProtocolError("private asset lineage is invalid")
    documents = [copy.deepcopy(item) for item in value]
    keys: list[tuple[str, int, str, str]] = []
    by_key: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    by_format: dict[str, list[dict[str, Any]]] = {}
    try:
        for document in documents:
            identity = document_identity(document)
            key = _identity_key(identity)
            if key in by_key:
                raise CreationWorkerProtocolError("private asset lineage contains a duplicate")
            keys.append(key)
            by_key[key] = document
            by_format.setdefault(str(identity["format"]), []).append(document)
    except (PhaseReportV3Error, TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError("private asset lineage identity is invalid") from exc
    if set(by_format) != _ASSET_PROCESS_FORMATS or any(
        len(by_format.get(format_name, ())) != 1 for format_name in _ASSET_PROCESS_SINGULAR_FORMATS
    ):
        raise CreationWorkerProtocolError("private asset lineage format closure is not exact")
    licenses = by_format["world-forge.asset_license_record"]
    if not 1 <= len(licenses) <= 4:
        raise CreationWorkerProtocolError("private asset license closure is not exact")

    source_keys = {_identity_key(document) for document in _source_documents(project)}
    required = {_identity_key(document) for document in licenses}
    pending = [
        dependency
        for document in licenses
        for dependency in artifact_dependency_identities(document)
    ]
    while pending:
        dependency = pending.pop()
        key = _identity_key(dependency)
        if key in source_keys or key in required:
            continue
        document = by_key.get(key)
        if document is None:
            raise CreationWorkerProtocolError("private asset lineage closure is incomplete")
        required.add(key)
        pending.extend(artifact_dependency_identities(document))
    if required != set(keys):
        raise CreationWorkerProtocolError("private asset lineage contains unrelated documents")
    try:
        checked = validate_artifact_documents(
            project,
            documents,
            allowed_formats=_ASSET_PROCESS_FORMATS,
        )
    except (PhaseReportV3Error, TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError("private asset lineage is not integral") from exc
    if {canonical_json_bytes(item) for item in checked} != {
        canonical_json_bytes(item) for item in documents
    }:
        raise CreationWorkerProtocolError("private asset lineage validation changed documents")
    return tuple(
        [by_format[format_name][0] for format_name in _ASSET_PROCESS_FORMAT_ORDER[:-1]]
        + sorted(
            licenses,
            key=lambda item: _identity_key(item)[2].encode("utf-8"),
        )
    )


def _validate_acceptance_results(
    value: object,
    specification: dict[str, Any],
) -> list[dict[str, Any]]:
    criteria = specification.get("acceptance_criteria")
    if (
        not isinstance(value, list)
        or not isinstance(criteria, list)
        or len(value) != len(criteria)
        or not value
        or len(value) > MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
    ):
        raise CreationWorkerProtocolError("private asset acceptance results are not exact")
    checked: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise CreationWorkerProtocolError("private asset acceptance result is invalid")
        _invalid_fields(raw, _ASSET_ACCEPTANCE_FIELDS, f"private asset acceptance/{index}")
        evidence = raw["evidence_hashes"]
        expected_hash = hashlib.sha256(str(criteria[index]).encode("utf-8")).hexdigest()
        if (
            raw["criterion_index"] != index
            or raw["criterion_sha256"] != expected_hash
            or raw["status"] not in {"passed", "failed"}
            or not isinstance(evidence, list)
            or not evidence
            or len(evidence) > MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
            or evidence != sorted(set(evidence))
            or any(
                not isinstance(item, str) or SHA256_PATTERN.fullmatch(item) is None
                for item in evidence
            )
        ):
            raise CreationWorkerProtocolError("private asset acceptance result is invalid")
        checked.append(raw)
    return checked


def _validate_staged_inputs(
    value: object,
    receipt: dict[str, Any],
) -> list[dict[str, Any]]:
    outputs = receipt.get("outputs")
    if not isinstance(value, list) or not isinstance(outputs, list) or len(value) != len(outputs):
        raise CreationWorkerProtocolError("private asset staged inputs are not exact")
    expected: list[dict[str, Any]] = []
    for output in outputs:
        if not isinstance(output, dict):
            raise CreationWorkerProtocolError("private asset receipt output is invalid")
        expected.append(
            {
                "candidate_artifact_id": output.get("candidate_artifact_id"),
                "role": output.get("role"),
                "source_locator": output.get("locator"),
                "sha256": output.get("sha256"),
                "size_bytes": output.get("size_bytes"),
            }
        )
    if value != expected:
        raise CreationWorkerProtocolError("private asset staged input identities changed")
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise CreationWorkerProtocolError("private asset staged input is invalid")
        _invalid_fields(raw, _ASSET_STAGED_INPUT_FIELDS, f"private asset staged input/{index}")
        _identifier(
            raw["candidate_artifact_id"], context=f"private asset staged input/{index}/candidate"
        )
        _identifier(raw["role"], context=f"private asset staged input/{index}/role")
        locator = raw["source_locator"]
        if (
            not isinstance(locator, str)
            or not locator
            or locator.startswith(("/", "\\"))
            or "\\" in locator
            or any(part in {"", ".", ".."} for part in Path(locator).parts)
            or not isinstance(raw["sha256"], str)
            or SHA256_PATTERN.fullmatch(raw["sha256"]) is None
            or isinstance(raw["size_bytes"], bool)
            or not isinstance(raw["size_bytes"], int)
            or not 1 <= raw["size_bytes"] <= 16 * 1024 * 1024
        ):
            raise CreationWorkerProtocolError("private asset staged input is invalid")
    return value


def _asset_id(document: Mapping[str, Any], *, context: str) -> str:
    asset = document.get("asset")
    if not isinstance(asset, Mapping):
        raise CreationWorkerProtocolError(f"{context} has no asset identity")
    return _identifier(asset.get("asset_id"), context=f"{context}/asset_id")


def _asset_release_lineage(
    project: LoadedCreationProject,
    value: object,
) -> tuple[
    tuple[dict[str, Any], ...],
    dict[str, dict[str, Any]],
    tuple[dict[str, Any], ...],
]:
    if (
        not isinstance(value, (list, tuple))
        or not value
        or len(value) > 128
        or any(not isinstance(item, dict) for item in value)
    ):
        raise CreationWorkerProtocolError("private asset release lineage is invalid")
    documents = [copy.deepcopy(item) for item in value]
    by_key: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    by_format: dict[str, list[dict[str, Any]]] = {}
    try:
        for document in documents:
            identity = document_identity(document)
            key = _identity_key(identity)
            if key in by_key:
                raise CreationWorkerProtocolError(
                    "private asset release lineage contains a duplicate"
                )
            by_key[key] = document
            by_format.setdefault(str(identity["format"]), []).append(document)
    except (PhaseReportV3Error, TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError(
            "private asset release lineage identity is invalid"
        ) from exc
    if set(by_format) != _ASSET_RELEASE_FORMATS or any(
        len(by_format.get(format_name, ())) != 1 for format_name in _ASSET_RELEASE_ROOT_FORMAT_ORDER
    ):
        raise CreationWorkerProtocolError(
            "private asset release lineage format closure is not exact"
        )
    roots = {
        format_name: by_format[format_name][0] for format_name in _ASSET_RELEASE_ROOT_FORMAT_ORDER
    }
    inventory_assets = roots["world-forge.asset_inventory"].get("assets")
    if not isinstance(inventory_assets, list) or not inventory_assets:
        raise CreationWorkerProtocolError("private asset release inventory is invalid")
    inventory_ids = []
    for index, asset in enumerate(inventory_assets):
        if not isinstance(asset, Mapping):
            raise CreationWorkerProtocolError("private asset release inventory asset is invalid")
        inventory_ids.append(
            _identifier(
                asset.get("asset_id"),
                context=f"private asset release inventory/{index}/asset_id",
            )
        )
    if len(inventory_ids) != len(set(item.casefold() for item in inventory_ids)):
        raise CreationWorkerProtocolError("private asset release inventory IDs collide")

    records: list[dict[str, Any]] = []
    canonical_documents = [roots[item] for item in _ASSET_RELEASE_ROOT_FORMAT_ORDER]
    qa_documents = by_format["world-forge.asset_qa_report"]
    qa_asset_ids = [_asset_id(item, context="private asset release QA") for item in qa_documents]
    if sorted(qa_asset_ids, key=lambda item: item.encode("utf-8")) != sorted(
        inventory_ids,
        key=lambda item: item.encode("utf-8"),
    ):
        raise CreationWorkerProtocolError(
            "private asset release QA coverage must include every inventory asset exactly once"
        )
    for asset_id in sorted(inventory_ids, key=lambda item: item.encode("utf-8")):
        grouped: dict[str, list[dict[str, Any]]] = {}
        for format_name in _ASSET_RELEASE_RECORD_FORMAT_ORDER:
            grouped[format_name] = [
                document
                for document in by_format[format_name]
                if _asset_id(
                    document,
                    context=f"private asset release {format_name}",
                )
                == asset_id
            ]
        for format_name in _ASSET_RELEASE_RECORD_FORMAT_ORDER:
            count = len(grouped[format_name])
            if format_name == "world-forge.asset_license_record":
                if not 1 <= count <= 16:
                    raise CreationWorkerProtocolError(
                        f"private asset release license coverage is invalid for {asset_id}"
                    )
            elif count != 1:
                raise CreationWorkerProtocolError(
                    f"private asset release {format_name} coverage is invalid for {asset_id}"
                )
        qa_report = grouped["world-forge.asset_qa_report"][0]
        if qa_report.get("status") != "passed" or qa_report.get("blockers") != []:
            raise CreationWorkerProtocolError(
                f"private asset release QA is not passed for {asset_id}"
            )
        licenses = sorted(
            grouped["world-forge.asset_license_record"],
            key=lambda item: _identity_key(item)[2].encode("utf-8"),
        )
        record = {
            "specification": grouped["world-forge.asset_spec"][0],
            "request": grouped["world-forge.asset_production_request"][0],
            "receipt": grouped["world-forge.asset_production_receipt"][0],
            "selection": grouped["world-forge.asset_selection"][0],
            "provenance": grouped["world-forge.asset_provenance_record"][0],
            "license_records": licenses,
            "recipe": grouped["world-forge.asset_processing_recipe"][0],
            "processing_receipt": grouped["world-forge.asset_processing_receipt"][0],
            "qa_report": qa_report,
        }
        records.append(record)
        canonical_documents.extend(
            [
                record["specification"],
                record["request"],
                record["receipt"],
                record["selection"],
                record["provenance"],
                *licenses,
                record["recipe"],
                record["processing_receipt"],
                record["qa_report"],
            ]
        )

    source_keys = {_identity_key(document) for document in _source_documents(project)}
    required = {_identity_key(document) for document in qa_documents}
    pending = [
        dependency
        for document in qa_documents
        for dependency in artifact_dependency_identities(document)
    ]
    while pending:
        dependency = pending.pop()
        key = _identity_key(dependency)
        if key in source_keys or key in required:
            continue
        document = by_key.get(key)
        if document is None:
            raise CreationWorkerProtocolError("private asset release lineage closure is incomplete")
        required.add(key)
        pending.extend(artifact_dependency_identities(document))
    if required != set(by_key):
        raise CreationWorkerProtocolError(
            "private asset release lineage contains unrelated documents"
        )
    try:
        checked = validate_artifact_documents(
            project,
            canonical_documents,
            allowed_formats=_ASSET_RELEASE_FORMATS,
        )
    except (PhaseReportV3Error, TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError("private asset release lineage is not integral") from exc
    if {canonical_json_bytes(item) for item in checked} != {
        canonical_json_bytes(item) for item in canonical_documents
    }:
        raise CreationWorkerProtocolError(
            "private asset release lineage validation changed documents"
        )
    return tuple(canonical_documents), roots, tuple(records)


def _validate_asset_seal_staged_inputs(
    value: object,
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    expected: list[dict[str, Any]] = []
    for record in records:
        for receipt_key, receipt_context in (
            ("receipt", "production receipt"),
            ("processing_receipt", "processing receipt"),
        ):
            receipt = record[receipt_key]
            asset_id = _asset_id(
                receipt,
                context=f"private asset release {receipt_context}",
            )
            outputs = receipt.get("outputs")
            if receipt.get("status") != "completed" or not isinstance(outputs, list):
                raise CreationWorkerProtocolError(
                    f"private asset release {receipt_context} is incomplete for {asset_id}"
                )
            for output in outputs:
                if not isinstance(output, Mapping):
                    raise CreationWorkerProtocolError(
                        f"private asset release {receipt_context} output is invalid"
                    )
                expected.append(
                    {
                        "asset_id": asset_id,
                        "role": output.get("role"),
                        "source_locator": output.get("locator"),
                        "sha256": output.get("sha256"),
                        "size_bytes": output.get("size_bytes"),
                    }
                )
    expected.sort(
        key=lambda item: (
            str(item["asset_id"]).encode("utf-8"),
            str(item["role"]).encode("utf-8"),
            str(item["source_locator"]).encode("utf-8"),
        )
    )
    if value != expected:
        raise CreationWorkerProtocolError("private asset release staged input identities changed")
    locators: list[str] = []
    for index, raw in enumerate(expected):
        _invalid_fields(
            raw,
            _ASSET_SEAL_STAGED_INPUT_FIELDS,
            f"private asset release staged input/{index}",
        )
        _identifier(raw["asset_id"], context=f"private asset release staged/{index}/asset")
        _identifier(raw["role"], context=f"private asset release staged/{index}/role")
        locator = raw["source_locator"]
        if (
            not isinstance(locator, str)
            or not locator
            or locator.startswith(("/", "\\"))
            or "\\" in locator
            or any(part in {"", ".", ".."} for part in Path(locator).parts)
            or not isinstance(raw["sha256"], str)
            or SHA256_PATTERN.fullmatch(raw["sha256"]) is None
            or isinstance(raw["size_bytes"], bool)
            or not isinstance(raw["size_bytes"], int)
            or not 1 <= raw["size_bytes"] <= 16 * 1024 * 1024
        ):
            raise CreationWorkerProtocolError("private asset release staged input is invalid")
        locators.append(locator)
    if len(locators) != len(set(locators)):
        raise CreationWorkerProtocolError(
            "private asset release staged input locators must be unique"
        )
    return expected


def _runtime_compose_lineage(
    project: LoadedCreationProject,
    value: object,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(not isinstance(item, dict) for item in value)
    ):
        raise CreationWorkerProtocolError("private runtime composition lineage is invalid")
    documents = tuple(copy.deepcopy(item) for item in value)
    if tuple(document.get("format") for document in documents) != (
        "world-forge.gamepack",
        "world-forge.asset_inventory",
        "world-forge.assetpack",
    ):
        raise CreationWorkerProtocolError(
            "private runtime composition lineage format closure is not exact"
        )
    try:
        gamepack = validate_gamepack_document(documents[0])
        inventory = validate_asset_inventory_document(documents[1])
        assetpack = validate_generic_assetpack_document(documents[2])
        checked_gamepack = validate_artifact_documents(
            project,
            [gamepack],
            allowed_formats={"world-forge.gamepack"},
        )
    except (
        GamepackError,
        GenericAssetError,
        GenericAssetpackError,
        PhaseReportV3Error,
        TypeError,
        ValueError,
    ) as exc:
        raise CreationWorkerProtocolError(
            "private runtime composition lineage is not integral"
        ) from exc
    gamepack_identity = document_identity(gamepack)
    inventory_identity = document_identity(inventory)
    if (
        checked_gamepack != (gamepack,)
        or inventory.get("gamepack") != gamepack_identity
        or assetpack.get("gamepack") != gamepack_identity
        or assetpack.get("asset_inventory") != inventory_identity
    ):
        raise CreationWorkerProtocolError(
            "private runtime composition lineage crosses immutable subjects"
        )
    return gamepack, inventory, assetpack


def _validate_runtime_compose_staged_inputs(
    value: object,
    assetpack: Mapping[str, Any],
) -> list[dict[str, Any]]:
    manifest_payload = canonical_json_bytes(assetpack)
    expected = [
        {
            "source_locator": "assetpack.json",
            "sha256": hashlib.sha256(manifest_payload).hexdigest(),
            "size_bytes": len(manifest_payload),
        },
        *(
            {
                "source_locator": item["path"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
            for item in assetpack["inventory"]["files"]
        ),
    ]
    expected.sort(key=lambda item: str(item["source_locator"]).encode("utf-8"))
    if value != expected:
        raise CreationWorkerProtocolError(
            "private runtime composition assetpack inputs are not exact"
        )
    for index, raw in enumerate(expected):
        _invalid_fields(
            raw,
            _RUNTIME_COMPOSE_STAGED_INPUT_FIELDS,
            f"private runtime composition staged input/{index}",
        )
        locator = raw["source_locator"]
        if (
            not isinstance(locator, str)
            or not locator
            or locator.startswith(("/", "\\"))
            or "\\" in locator
            or any(part in {"", ".", ".."} for part in Path(locator).parts)
            or not isinstance(raw["sha256"], str)
            or SHA256_PATTERN.fullmatch(raw["sha256"]) is None
            or isinstance(raw["size_bytes"], bool)
            or not isinstance(raw["size_bytes"], int)
            or not 1 <= raw["size_bytes"] <= 16 * 1024 * 1024
        ):
            raise CreationWorkerProtocolError("private runtime composition staged input is invalid")
    return expected


def _runtime_headless_documents(
    project: LoadedCreationProject,
    value: object,
) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != len(_RUNTIME_HEADLESS_FORMAT_ORDER)
        or any(not isinstance(item, dict) for item in value)
    ):
        raise CreationWorkerProtocolError("private runtime headless artifacts are invalid")
    documents = tuple(copy.deepcopy(item) for item in value)
    if tuple(document.get("format") for document in documents) != _RUNTIME_HEADLESS_FORMAT_ORDER:
        raise CreationWorkerProtocolError("private runtime headless artifact closure is not exact")
    try:
        gamepack = validate_gamepack_document(documents[0])
        inventory = validate_asset_inventory_document(documents[1])
        assetpack = validate_generic_assetpack_document(documents[2])
        release = validate_asset_release_authority_document(documents[3])
        snapshot = validate_runtime_snapshot_document(documents[4])
        validate_runtime_adapter_registry_document(documents[5], snapshot=snapshot)
        composition = validate_game_runtime_composition_document(documents[6])
        runtime_bundle = validate_game_runtime_bundle_document(documents[7])
        serialize_game_execution_script(documents[8])
        checked_gamepack = validate_artifact_documents(
            project,
            [gamepack],
            allowed_formats={"world-forge.gamepack"},
        )
    except (
        GameLogicError,
        GamepackError,
        GenericAssetAuthorityError,
        GenericAssetError,
        GenericAssetpackError,
        PhaseReportV3Error,
        RuntimeContractError,
        TypeError,
        ValueError,
    ) as exc:
        raise CreationWorkerProtocolError(
            "private runtime headless artifact closure is not integral"
        ) from exc
    script = documents[8]
    gamepack_identity = document_identity(gamepack)
    inventory_identity = document_identity(inventory)
    assetpack_identity = {
        "format": assetpack["format"],
        "format_version": assetpack["format_version"],
        "id": assetpack["assetpack_id"],
        "content_hash": assetpack["content_hash"],
    }
    bindings = script.get("bindings")
    expected_script_bindings = {
        "gamepack": gamepack_identity,
        "runtime_composition": document_identity(composition),
        "runtime_bundle": document_identity(runtime_bundle),
        "runtime_snapshot": document_identity(snapshot),
    }
    if (
        checked_gamepack != (gamepack,)
        or inventory.get("gamepack") != gamepack_identity
        or assetpack.get("gamepack") != gamepack_identity
        or assetpack.get("asset_inventory") != inventory_identity
        or release.get("status") != "authorized"
        or release.get("candidate_assetpack") != assetpack_identity
        or not isinstance(bindings, dict)
        or any(
            bindings.get(field) != identity for field, identity in expected_script_bindings.items()
        )
    ):
        raise CreationWorkerProtocolError(
            "private runtime headless artifacts cross immutable subjects"
        )
    return documents


def _validate_runtime_headless_staged_inputs(
    value: object,
    *,
    asset_release_request: Mapping[str, Any],
    execution_script: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 512
        or any(not isinstance(item, dict) for item in value)
    ):
        raise CreationWorkerProtocolError("private runtime headless staged inputs are invalid")
    checked: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        _invalid_fields(
            raw,
            _RUNTIME_HEADLESS_STAGED_INPUT_FIELDS,
            f"private runtime headless staged input/{index}",
        )
        locator = raw["source_locator"]
        if (
            not isinstance(locator, str)
            or not locator
            or locator.startswith(("/", "\\"))
            or "\\" in locator
            or any(part in {"", ".", ".."} for part in PurePosixPath(locator).parts)
            or not isinstance(raw["sha256"], str)
            or SHA256_PATTERN.fullmatch(raw["sha256"]) is None
            or isinstance(raw["size_bytes"], bool)
            or not isinstance(raw["size_bytes"], int)
            or not 1 <= raw["size_bytes"] <= 16 * 1024 * 1024
        ):
            raise CreationWorkerProtocolError("private runtime headless staged input is invalid")
        checked.append(copy.deepcopy(raw))
    expected_order = sorted(
        checked,
        key=lambda item: str(item["source_locator"]).encode("utf-8"),
    )
    locators = [str(item["source_locator"]) for item in checked]
    if checked != expected_order or len({locator.casefold() for locator in locators}) != len(
        locators
    ):
        raise CreationWorkerProtocolError(
            "private runtime headless staged inputs are not canonical"
        )
    by_locator = {str(item["source_locator"]): item for item in checked}
    reviewed_inputs = asset_release_request.get("staged_inputs")
    if not isinstance(reviewed_inputs, list):
        raise CreationWorkerProtocolError(
            "private runtime headless asset authority inputs are invalid"
        )
    reviewed_locators: set[str] = set()
    for raw in reviewed_inputs:
        locator = str(raw["source_locator"])
        expected = {
            "source_locator": locator,
            "sha256": raw["sha256"],
            "size_bytes": raw["size_bytes"],
        }
        if by_locator.get(locator) != expected:
            raise CreationWorkerProtocolError(
                "private runtime headless reviewed bytes are incomplete"
            )
        reviewed_locators.add(locator)
    script_payload = serialize_game_execution_script(execution_script)
    expected_script = {
        "source_locator": "execution/script.json",
        "sha256": hashlib.sha256(script_payload).hexdigest(),
        "size_bytes": len(script_payload),
    }
    if by_locator.get("execution/script.json") != expected_script:
        raise CreationWorkerProtocolError("private runtime headless execution script bytes changed")
    allowed_fixed = {*reviewed_locators, "execution/script.json"}
    for locator in locators:
        if locator in allowed_fixed:
            continue
        if locator.startswith("assetpack/") or locator.startswith("runtime-bundle/"):
            continue
        raise CreationWorkerProtocolError("private runtime headless staged input is unrelated")
    if not any(locator.startswith("assetpack/") for locator in locators) or not any(
        locator.startswith("runtime-bundle/") for locator in locators
    ):
        raise CreationWorkerProtocolError(
            "private runtime headless staged tree closure is incomplete"
        )
    return checked


def _validate_materialization_staged_inputs(
    value: object,
    runtime_bundle: Mapping[str, Any],
) -> list[dict[str, Any]]:
    manifest_payload = serialize_game_runtime_bundle(runtime_bundle)
    expected = [
        {
            "source_locator": "game-runtime-bundle.json",
            "sha256": hashlib.sha256(manifest_payload).hexdigest(),
            "size_bytes": len(manifest_payload),
        },
        *(
            {
                "source_locator": item["path"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
            for item in runtime_bundle["files"]
        ),
    ]
    expected.sort(key=lambda item: str(item["source_locator"]).encode("utf-8"))
    if value != expected:
        raise CreationWorkerProtocolError(
            "private materialization runtime bundle inputs are not exact"
        )
    for index, raw in enumerate(expected):
        _invalid_fields(
            raw,
            _RUNTIME_COMPOSE_STAGED_INPUT_FIELDS,
            f"private materialization staged input/{index}",
        )
        locator = raw["source_locator"]
        if (
            not isinstance(locator, str)
            or not locator
            or locator.startswith(("/", "\\"))
            or "\\" in locator
            or any(part in {"", ".", ".."} for part in Path(locator).parts)
            or not isinstance(raw["sha256"], str)
            or SHA256_PATTERN.fullmatch(raw["sha256"]) is None
            or isinstance(raw["size_bytes"], bool)
            or not isinstance(raw["size_bytes"], int)
            or not 1 <= raw["size_bytes"] <= 16 * 1024 * 1024
        ):
            raise CreationWorkerProtocolError("private materialization staged input is invalid")
    return expected


def _validate_game_materialize_staged_inputs(
    value: object,
    materialization_bundle: Mapping[str, Any],
) -> list[dict[str, Any]]:
    manifest_payload = serialize_game_materialization_bundle(materialization_bundle)
    expected = [
        {
            "source_locator": "game-materialization-bundle.json",
            "sha256": hashlib.sha256(manifest_payload).hexdigest(),
            "size_bytes": len(manifest_payload),
        },
        *(
            {
                "source_locator": item["path"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
            for item in materialization_bundle["files"]
        ),
    ]
    expected.sort(key=lambda item: str(item["source_locator"]).encode("utf-8"))
    if value != expected:
        raise CreationWorkerProtocolError(
            "private game materialization bundle inputs are not exact"
        )
    for index, raw in enumerate(expected):
        _invalid_fields(
            raw,
            _RUNTIME_COMPOSE_STAGED_INPUT_FIELDS,
            f"private game materialization staged input/{index}",
        )
        locator = raw["source_locator"]
        if (
            not isinstance(locator, str)
            or not locator
            or locator.startswith(("/", "\\"))
            or "\\" in locator
            or any(part in {"", ".", ".."} for part in Path(locator).parts)
            or not isinstance(raw["sha256"], str)
            or SHA256_PATTERN.fullmatch(raw["sha256"]) is None
            or isinstance(raw["size_bytes"], bool)
            or not isinstance(raw["size_bytes"], int)
            or not 1 <= raw["size_bytes"] <= 32 * 1024 * 1024
        ):
            raise CreationWorkerProtocolError(
                "private game materialization staged input is invalid"
            )
    return expected


def _validate_game_package_staged_inputs(
    value: object,
    standalone_manifest: Mapping[str, Any],
    standalone_lock: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected = [
        {
            "source_locator": GAME_MANIFEST_PATH,
            "sha256": hashlib.sha256(canonical_contract_bytes(standalone_manifest)).hexdigest(),
            "size_bytes": len(canonical_contract_bytes(standalone_manifest)),
        },
        {
            "source_locator": GAME_LOCK_PATH,
            "sha256": hashlib.sha256(canonical_contract_bytes(standalone_lock)).hexdigest(),
            "size_bytes": len(canonical_contract_bytes(standalone_lock)),
        },
        *(
            {
                "source_locator": item["path"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            }
            for item in standalone_lock["files"]
        ),
    ]
    expected.sort(key=lambda item: str(item["source_locator"]).encode("utf-8"))
    if value != expected:
        raise CreationWorkerProtocolError("private game package standalone inputs are not exact")
    for index, raw in enumerate(expected):
        _invalid_fields(
            raw,
            _RUNTIME_COMPOSE_STAGED_INPUT_FIELDS,
            f"private game package staged input/{index}",
        )
        locator = raw["source_locator"]
        if (
            not isinstance(locator, str)
            or not locator
            or locator.startswith(("/", "\\"))
            or "\\" in locator
            or any(part in {"", ".", ".."} for part in Path(locator).parts)
            or not isinstance(raw["sha256"], str)
            or SHA256_PATTERN.fullmatch(raw["sha256"]) is None
            or isinstance(raw["size_bytes"], bool)
            or not isinstance(raw["size_bytes"], int)
            or not 1 <= raw["size_bytes"] <= 32 * 1024 * 1024
        ):
            raise CreationWorkerProtocolError("private game package staged input is invalid")
    return expected


def _validate_game_package_extract_staged_inputs(
    value: object,
    archive: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected = [
        {
            "source_locator": "game_package_archive.wfgame",
            "sha256": archive.get("sha256"),
            "size_bytes": archive.get("size_bytes"),
        }
    ]
    if value != expected:
        raise CreationWorkerProtocolError("private game package extraction inputs are not exact")
    raw = expected[0]
    _invalid_fields(
        raw,
        _RUNTIME_COMPOSE_STAGED_INPUT_FIELDS,
        "private game package extraction staged input/0",
    )
    if (
        raw["source_locator"] != "game_package_archive.wfgame"
        or not isinstance(raw["sha256"], str)
        or SHA256_PATTERN.fullmatch(raw["sha256"]) is None
        or isinstance(raw["size_bytes"], bool)
        or not isinstance(raw["size_bytes"], int)
        or not 1 <= raw["size_bytes"] <= MAX_GAME_PACKAGE_ARCHIVE_BYTES
    ):
        raise CreationWorkerProtocolError("private game package extraction staged input is invalid")
    return expected


def _runtime_bundle_lineage(
    project: LoadedCreationProject,
    value: object,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    expected_formats = (
        "world-forge.gamepack",
        "world-forge.asset_inventory",
        "world-forge.assetpack",
        "world-forge.game_runtime_snapshot",
        "world-forge.runtime_adapter_registry",
        "world-forge.game_runtime_composition",
        "world-forge.runtime_support_report",
    )
    if (
        not isinstance(value, (list, tuple))
        or len(value) != len(expected_formats)
        or any(not isinstance(item, dict) for item in value)
    ):
        raise CreationWorkerProtocolError("private runtime bundle lineage is invalid")
    documents = tuple(copy.deepcopy(item) for item in value)
    if tuple(document.get("format") for document in documents) != expected_formats:
        raise CreationWorkerProtocolError(
            "private runtime bundle lineage format closure is not exact"
        )
    gamepack, inventory, assetpack = _runtime_compose_lineage(project, documents[:3])
    try:
        checked_snapshot = validate_runtime_snapshot_document(documents[3])
        checked_registry = validate_runtime_adapter_registry_document(
            documents[4], snapshot=checked_snapshot
        )
        checked_composition = validate_game_runtime_composition_document(documents[5])
        checked_support = validate_runtime_support_report_document(documents[6])
    except (RuntimeContractError, TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError("private runtime bundle lineage is not integral") from exc
    gamepack_identity = document_identity(gamepack)
    inventory_identity = document_identity(inventory)
    assetpack_identity = document_identity(assetpack)
    snapshot_identity = document_identity(checked_snapshot)
    registry_identity = document_identity(checked_registry)
    composition_identity = document_identity(checked_composition)
    if (
        checked_composition.get("gamepack") != gamepack_identity
        or checked_composition.get("asset_inventory") != inventory_identity
        or {
            key: checked_composition.get("assetpack", {}).get(key)
            for key in ("format", "format_version", "id", "content_hash")
        }
        != assetpack_identity
        or checked_composition.get("runtime_snapshot") != snapshot_identity
        or checked_composition.get("registry") != registry_identity
        or checked_support.get("gamepack") != gamepack_identity
        or checked_support.get("composition") != composition_identity
        or checked_support.get("evidence") != []
        or checked_support.get("supported") is not False
        or checked_support.get("dimensions", {}).get("release") != "blocked"
    ):
        raise CreationWorkerProtocolError("private runtime bundle lineage crosses subjects")
    return (
        gamepack,
        inventory,
        assetpack,
        checked_snapshot,
        checked_registry,
        checked_composition,
        checked_support,
    )


def _validate_inputs(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 128:
        raise CreationWorkerProtocolError("private request inputs are invalid")
    result: list[dict[str, Any]] = []
    artifact_ids: list[str] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise CreationWorkerProtocolError(f"private request inputs/{index} is invalid")
        _invalid_fields(raw, {"artifact_id", "subject"}, f"private request inputs/{index}")
        artifact_id = _identifier(
            raw["artifact_id"], context=f"private request inputs/{index}/artifact_id"
        )
        subject = _identity(raw["subject"], context=f"private request inputs/{index}/subject")
        if artifact_id != _artifact_id(subject):
            raise CreationWorkerProtocolError(
                f"private request inputs/{index} artifact identity differs"
            )
        artifact_ids.append(artifact_id)
        result.append(raw)
    if len(artifact_ids) != len(set(artifact_ids)):
        raise CreationWorkerProtocolError("private request inputs contain duplicates")
    return result


def _reject_admission_secrets(value: object, *, context: str) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_admission_secrets(item, context=f"{context}/{index}")
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        folded = key.casefold()
        if folded in _FORBIDDEN_ADMISSION_KEYS:
            raise CreationWorkerProtocolError(f"{context} contains forbidden field {key}")
        if (
            folded.endswith("path")
            and isinstance(item, str)
            and (
                item.startswith(("/", "\\"))
                or (len(item) >= 3 and item[1] == ":" and item[2] in {"/", "\\"})
            )
        ):
            raise CreationWorkerProtocolError(f"{context} contains native path field {key}")
        _reject_admission_secrets(item, context=f"{context}/{key}")


def build_private_compile_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
) -> dict[str, Any]:
    source = _source_payload(project)
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "creation.compile",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(document) for document in _source_documents(project)],
        "source": source,
        "artifact": None,
        "dependency_documents": [],
    }
    validate_private_creation_request(request)
    return request


def build_private_admission_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    document: dict[str, Any],
    dependency_documents: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "artifact.admit",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(item) for item in dependency_documents],
        "source": _source_payload(project),
        "artifact": copy.deepcopy(document),
        "dependency_documents": [copy.deepcopy(item) for item in dependency_documents],
    }
    validate_private_creation_request(request)
    return request


def build_private_asset_process_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    lineage_documents: tuple[dict[str, Any], ...],
    recipe_id: str,
    processing_receipt_id: str,
    qa_report_id: str,
    acceptance_results: list[dict[str, Any]],
    staged_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    lineage = _canonical_asset_lineage(project, lineage_documents)
    by_format = {str(document["format"]): document for document in lineage}
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_ASSET_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "asset.process",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(document) for document in lineage],
        "source": _source_payload(project),
        "lineage_documents": [copy.deepcopy(document) for document in lineage],
        "recipe_id": recipe_id,
        "processing_receipt_id": processing_receipt_id,
        "qa_report_id": qa_report_id,
        "acceptance_results": copy.deepcopy(acceptance_results),
        "staged_inputs": copy.deepcopy(staged_inputs),
    }
    _validate_acceptance_results(
        request["acceptance_results"],
        by_format["world-forge.asset_spec"],
    )
    _validate_staged_inputs(
        request["staged_inputs"],
        by_format["world-forge.asset_production_receipt"],
    )
    validate_private_creation_request(request)
    return request


def build_private_asset_release_seal_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    lineage_documents: Sequence[dict[str, Any]],
    manifest_id: str,
    target_grant_id: str,
    target_grant_generation: int,
    staged_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    lineage, _roots, records = _asset_release_lineage(project, lineage_documents)
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_ASSET_SEAL_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "asset.release.seal",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(document) for document in lineage],
        "source": _source_payload(project),
        "lineage_documents": [copy.deepcopy(document) for document in lineage],
        "manifest_id": manifest_id,
        "target_grant_id": target_grant_id,
        "target_grant_generation": target_grant_generation,
        "staged_inputs": copy.deepcopy(staged_inputs),
    }
    _validate_asset_seal_staged_inputs(request["staged_inputs"], records)
    validate_private_creation_request(request)
    return request


def build_private_runtime_compose_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    lineage_documents: Sequence[dict[str, Any]],
    target_grant_id: str,
    target_grant_generation: int,
    staged_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    gamepack, inventory, assetpack = _runtime_compose_lineage(
        project,
        lineage_documents,
    )
    lineage = (gamepack, inventory, assetpack)
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_RUNTIME_COMPOSE_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "runtime.compose",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(document) for document in lineage],
        "source": _source_payload(project),
        "lineage_documents": [copy.deepcopy(document) for document in lineage],
        "target_grant_id": target_grant_id,
        "target_grant_generation": target_grant_generation,
        "staged_inputs": copy.deepcopy(staged_inputs),
    }
    _validate_runtime_compose_staged_inputs(request["staged_inputs"], assetpack)
    validate_private_creation_request(request)
    return request


def build_private_runtime_bundle_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    lineage_documents: Sequence[dict[str, Any]],
    source_grant_id: str,
    source_grant_generation: int,
    target_grant_id: str,
    target_grant_generation: int,
    staged_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    lineage = _runtime_bundle_lineage(project, lineage_documents)
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_RUNTIME_BUNDLE_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "runtime.bundle.build",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(document) for document in lineage],
        "source": _source_payload(project),
        "lineage_documents": [copy.deepcopy(document) for document in lineage],
        "source_grant_id": source_grant_id,
        "source_grant_generation": source_grant_generation,
        "target_grant_id": target_grant_id,
        "target_grant_generation": target_grant_generation,
        "staged_inputs": copy.deepcopy(staged_inputs),
    }
    _validate_runtime_compose_staged_inputs(request["staged_inputs"], lineage[2])
    validate_private_creation_request(request)
    return request


def build_private_materialization_bundle_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    runtime_bundle_manifest: dict[str, Any],
    source_grant_id: str,
    source_grant_generation: int,
    target_grant_id: str,
    target_grant_generation: int,
    staged_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        runtime_bundle = validate_game_runtime_bundle_document(runtime_bundle_manifest)
    except (TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError(
            "private materialization runtime bundle manifest is invalid"
        ) from exc
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_MATERIALIZATION_BUNDLE_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "game.materialization.bundle.build",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(runtime_bundle)],
        "source": _source_payload(project),
        "runtime_bundle_manifest": copy.deepcopy(runtime_bundle),
        "source_grant_id": source_grant_id,
        "source_grant_generation": source_grant_generation,
        "target_grant_id": target_grant_id,
        "target_grant_generation": target_grant_generation,
        "staged_inputs": copy.deepcopy(staged_inputs),
    }
    _validate_materialization_staged_inputs(request["staged_inputs"], runtime_bundle)
    validate_private_creation_request(request)
    return request


def build_private_game_materialize_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    materialization_bundle_manifest: dict[str, Any],
    source_grant_id: str,
    source_grant_generation: int,
    target_grant_id: str,
    target_grant_generation: int,
    staged_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        materialization = validate_game_materialization_bundle_document(
            materialization_bundle_manifest
        )
    except (TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError(
            "private game materialization bundle manifest is invalid"
        ) from exc
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_GAME_MATERIALIZE_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "game.materialize",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(materialization)],
        "source": _source_payload(project),
        "materialization_bundle_manifest": copy.deepcopy(materialization),
        "source_grant_id": source_grant_id,
        "source_grant_generation": source_grant_generation,
        "target_grant_id": target_grant_id,
        "target_grant_generation": target_grant_generation,
        "staged_inputs": copy.deepcopy(staged_inputs),
    }
    _validate_game_materialize_staged_inputs(request["staged_inputs"], materialization)
    validate_private_creation_request(request)
    return request


def build_private_game_package_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    standalone_game_manifest: dict[str, Any],
    standalone_game_lock: dict[str, Any],
    game_package_manifest: dict[str, Any],
    archive_sha256: str,
    archive_size_bytes: int,
    source_grant_id: str,
    source_grant_generation: int,
    target_grant_id: str,
    target_grant_generation: int,
    staged_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        standalone = validate_standalone_game_document(standalone_game_manifest)
        lock = validate_standalone_game_lock_document(standalone_game_lock)
        package = validate_game_package_document(game_package_manifest)
    except (GamePackageError, StandaloneDistributionError, TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError(
            "private game package source or manifest is invalid"
        ) from exc
    if (
        package["standalone_game"]
        != {
            "format": standalone["format"],
            "format_version": standalone["format_version"],
            "game_id": standalone["game_id"],
            "content_hash": standalone["content_hash"],
        }
        or package["payload_lock"]
        != {
            "format": lock["format"],
            "format_version": lock["format_version"],
            "id": lock["lock_id"],
            "content_hash": lock["content_hash"],
            "tree_hash": lock["tree_hash"],
        }
        or package["lineage"] != standalone["lineage"]
    ):
        raise CreationWorkerProtocolError("private game package lineage crosses subjects")
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_GAME_PACKAGE_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "game.package",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(standalone)],
        "source": _source_payload(project),
        "standalone_game_manifest": copy.deepcopy(standalone),
        "standalone_game_lock": copy.deepcopy(lock),
        "game_package_manifest": copy.deepcopy(package),
        "archive_output": {
            "locator": "game_package_archive",
            "sha256": archive_sha256,
            "size_bytes": archive_size_bytes,
        },
        "source_grant_id": source_grant_id,
        "source_grant_generation": source_grant_generation,
        "target_grant_id": target_grant_id,
        "target_grant_generation": target_grant_generation,
        "staged_inputs": copy.deepcopy(staged_inputs),
    }
    _validate_game_package_staged_inputs(
        request["staged_inputs"],
        standalone,
        lock,
    )
    validate_private_creation_request(request)
    return request


def build_private_game_package_extract_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    game_package_manifest: dict[str, Any],
    archive_sha256: str,
    archive_size_bytes: int,
    source_grant_id: str,
    source_grant_generation: int,
    target_grant_id: str,
    target_grant_generation: int,
    staged_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        package = validate_game_package_document(game_package_manifest)
    except (GamePackageError, TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError(
            "private game package extraction source is invalid"
        ) from exc
    archive_input = {
        "locator": "game_package_archive.wfgame",
        "sha256": archive_sha256,
        "size_bytes": archive_size_bytes,
    }
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_GAME_PACKAGE_EXTRACT_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "game.package.extract",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(package)],
        "source": _source_payload(project),
        "game_package_manifest": copy.deepcopy(package),
        "archive_input": archive_input,
        "source_grant_id": source_grant_id,
        "source_grant_generation": source_grant_generation,
        "target_grant_id": target_grant_id,
        "target_grant_generation": target_grant_generation,
        "staged_inputs": copy.deepcopy(staged_inputs),
    }
    _validate_game_package_extract_staged_inputs(
        request["staged_inputs"],
        archive_input,
    )
    validate_private_creation_request(request)
    return request


def _asset_qa_review_lineage(
    project: LoadedCreationProject,
    value: object,
) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(value, (list, tuple))
        or not 14 <= len(value) <= 17
        or any(not isinstance(item, dict) for item in value)
    ):
        raise CreationWorkerProtocolError("private asset QA review lineage is invalid")
    documents = tuple(copy.deepcopy(item) for item in value)
    formats = tuple(str(document.get("format")) for document in documents)
    license_count = len(documents) - 13
    expected_formats = (
        *_ASSET_QA_REVIEW_FORMAT_ORDER[:10],
        *(["world-forge.asset_license_record"] * license_count),
        *_ASSET_QA_REVIEW_FORMAT_ORDER[11:],
    )
    if formats != tuple(expected_formats):
        raise CreationWorkerProtocolError(
            "private asset QA review lineage format closure is not exact"
        )
    try:
        checked = validate_artifact_documents(project, documents)
    except (PhaseReportV3Error, TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError(
            "private asset QA review lineage is not integral"
        ) from exc
    if checked != documents:
        raise CreationWorkerProtocolError("private asset QA review lineage changed")
    qa_report = documents[-1]
    expected = {
        "gamepack": document_identity(documents[0]),
        "asset_subject": document_identity(documents[1]),
        "target": document_identity(documents[2]),
        "style": document_identity(documents[3]),
        "inventory": document_identity(documents[4]),
        "specification": document_identity(documents[5]),
        "request": document_identity(documents[6]),
        "receipt": document_identity(documents[7]),
        "selection": document_identity(documents[8]),
        "provenance": document_identity(documents[9]),
        "recipe": document_identity(documents[-3]),
        "processing_receipt": document_identity(documents[-2]),
    }
    if any(qa_report.get(field) != identity for field, identity in expected.items()):
        raise CreationWorkerProtocolError(
            "private asset QA review lineage crosses immutable subjects"
        )
    return documents


def _validate_review_decisions(
    decisions: object,
    blockers: object,
    specification: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    criteria = specification.get("acceptance_criteria")
    if (
        not isinstance(criteria, list)
        or not isinstance(decisions, list)
        or len(decisions) != len(criteria)
        or not decisions
        or len(decisions) > MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
        or any(decision not in {"approved", "rejected"} for decision in decisions)
    ):
        raise CreationWorkerProtocolError(
            "private asset QA review decisions do not exactly cover criteria"
        )
    if (
        not isinstance(blockers, list)
        or len(blockers) > 64
        or any(
            not isinstance(blocker, str) or ENTITY_ID_PATTERN.fullmatch(blocker) is None
            for blocker in blockers
        )
        or blockers != sorted(set(blockers), key=lambda item: item.encode("utf-8"))
    ):
        raise CreationWorkerProtocolError("private asset QA review blockers are invalid")
    rejected = any(decision == "rejected" for decision in decisions)
    if rejected != bool(blockers):
        raise CreationWorkerProtocolError("private asset QA review blockers contradict decisions")
    return list(decisions), list(blockers)


def build_private_asset_qa_review_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    lineage_documents: Sequence[dict[str, Any]],
    review_receipt_id: str,
    output_role: str,
    decisions: Sequence[str],
    blockers: Sequence[str],
    staged_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    lineage = _asset_qa_review_lineage(project, lineage_documents)
    by_format = {str(document["format"]): document for document in lineage}
    decision_values, blocker_values = _validate_review_decisions(
        list(decisions),
        list(blockers),
        by_format["world-forge.asset_spec"],
    )
    qa_outputs = [
        output
        for output in by_format["world-forge.asset_qa_report"]["outputs"]
        if output["role"] == output_role
    ]
    if len(qa_outputs) != 1:
        raise CreationWorkerProtocolError("private asset QA review output role is not exact")
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_ASSET_QA_REVIEW_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "asset.qa.review",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(document) for document in lineage],
        "source": _source_payload(project),
        "lineage_documents": [copy.deepcopy(document) for document in lineage],
        "review_receipt_id": review_receipt_id,
        "output_role": output_role,
        "decisions": decision_values,
        "blockers": blocker_values,
        "staged_inputs": copy.deepcopy(staged_inputs),
    }
    _identifier(review_receipt_id, context="private asset QA review/review_receipt_id")
    _identifier(output_role, context="private asset QA review/output_role")
    _validate_staged_inputs(request["staged_inputs"], {"outputs": qa_outputs})
    validate_private_creation_request(request)
    return request


def build_private_asset_release_authorize_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    lineage_documents: Sequence[dict[str, Any]],
    review_documents: Sequence[dict[str, Any]],
    manifest_id: str,
    assetpack_id: str,
    release_authority_id: str,
    blockers: Sequence[str],
    target_grant_id: str,
    target_grant_generation: int,
    staged_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    lineage, _roots, records = _asset_release_lineage(project, lineage_documents)
    checked_reviews = [
        validate_asset_qa_review_receipt_document(document) for document in review_documents
    ]
    checked_reviews.sort(key=lambda item: item["review_receipt_id"].encode("utf-8"))
    review_ids = [item["review_receipt_id"] for item in checked_reviews]
    if len(set(review_ids)) != len(review_ids):
        raise CreationWorkerProtocolError("private asset release authority reviews are not unique")
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_ASSET_RELEASE_AUTHORIZE_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "asset.release.authorize",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(document) for document in checked_reviews],
        "source": _source_payload(project),
        "lineage_documents": [copy.deepcopy(document) for document in lineage],
        "review_documents": [copy.deepcopy(document) for document in checked_reviews],
        "manifest_id": manifest_id,
        "assetpack_id": assetpack_id,
        "release_authority_id": release_authority_id,
        "blockers": list(blockers),
        "target_grant_id": target_grant_id,
        "target_grant_generation": target_grant_generation,
        "staged_inputs": copy.deepcopy(staged_inputs),
    }
    _validate_asset_seal_staged_inputs(request["staged_inputs"], records)
    validate_private_creation_request(request)
    return request


def build_private_runtime_headless_request(
    *,
    job_id: str,
    workspace_id: str,
    authority: dict[str, Any],
    project: LoadedCreationProject,
    artifact_documents: Sequence[dict[str, Any]],
    asset_release_request: Mapping[str, Any],
    platform_id: str,
    source_grant_id: str,
    source_grant_generation: int,
    target_grant_id: str,
    target_grant_generation: int,
    staged_inputs: list[dict[str, Any]],
) -> dict[str, Any]:
    documents = _runtime_headless_documents(project, artifact_documents)
    retained_release_request = validate_private_creation_request(
        copy.deepcopy(dict(asset_release_request))
    )
    if retained_release_request["operation"] != "asset.release.authorize":
        raise CreationWorkerProtocolError(
            "private runtime headless asset authority request is invalid"
        )
    request = {
        "format": PRIVATE_CREATION_REQUEST_FORMAT,
        "format_version": PRIVATE_CREATION_RUNTIME_HEADLESS_REQUEST_VERSION,
        "job_id": job_id,
        "workspace_id": workspace_id,
        "operation": "runtime.headless.verify",
        "authority": copy.deepcopy(authority),
        "inputs": [_input_reference(document) for document in documents],
        "source": _source_payload(project),
        "artifact_documents": [copy.deepcopy(document) for document in documents],
        "asset_release_request": copy.deepcopy(retained_release_request),
        "platform_id": platform_id,
        "source_grant_id": source_grant_id,
        "source_grant_generation": source_grant_generation,
        "target_grant_id": target_grant_id,
        "target_grant_generation": target_grant_generation,
        "staged_inputs": copy.deepcopy(staged_inputs),
    }
    validate_private_creation_request(request)
    return request


def _build_asset_release_authorize_outputs(
    *,
    project: LoadedCreationProject,
    lineage_documents: Sequence[Mapping[str, Any]],
    reviews: Sequence[VerifiedAssetQaReview],
    manifest_id: str,
    assetpack_id: str,
    release_authority_id: str,
    blockers: Sequence[str],
    authority: Mapping[str, Any],
    artifact_root: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    """Rebuild exact v11 candidates from verified reviews and trusted blockers."""

    _lineage, roots, records = _asset_release_lineage(project, lineage_documents)
    expected_blockers = derive_asset_release_blockers(reviews, blockers)
    manifest = build_asset_manifest(
        roots["world-forge.gamepack"],
        roots["world-forge.asset_subject"],
        roots["world-forge.asset_target"],
        roots["world-forge.asset_style"],
        roots["world-forge.asset_inventory"],
        manifest_id=manifest_id,
        state="release_ready",
        asset_records=records,
        artifact_root=artifact_root,
        qa_reviews=reviews,
    )
    assetpack = build_generic_assetpack_manifest(
        manifest,
        gamepack=roots["world-forge.gamepack"],
        subject=roots["world-forge.asset_subject"],
        target=roots["world-forge.asset_target"],
        style=roots["world-forge.asset_style"],
        inventory=roots["world-forge.asset_inventory"],
        asset_records=records,
        artifact_root=artifact_root,
        qa_reviews=reviews,
    )
    if assetpack["assetpack_id"] != assetpack_id:
        raise CreationWorkerProtocolError("private asset release assetpack identity changed")
    release_authority = build_asset_release_authority(
        manifest,
        assetpack,
        reviews,
        release_authority_id=release_authority_id,
        blockers=expected_blockers,
        authority=authority,
    )
    return manifest, assetpack, release_authority, expected_blockers


def _validate_admission(
    project: LoadedCreationProject,
    inputs: list[dict[str, Any]],
    artifact: object,
    dependency_documents: object,
) -> None:
    if not isinstance(artifact, dict):
        raise CreationWorkerProtocolError("private admission artifact must be an object")
    if not isinstance(dependency_documents, list) or any(
        not isinstance(item, dict) for item in dependency_documents
    ):
        raise CreationWorkerProtocolError("private admission dependencies are invalid")
    _reject_admission_secrets(artifact, context="private admission artifact")
    try:
        identity = document_identity(artifact)
    except (PhaseReportV3Error, TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError("private admission artifact identity is invalid") from exc
    if identity["format"] not in ADMISSION_FORMATS:
        raise CreationWorkerProtocolError("private admission artifact format is not allowed")
    expected_inputs = [_input_reference(document) for document in dependency_documents]
    if inputs != expected_inputs:
        raise CreationWorkerProtocolError("private admission dependency references changed")
    try:
        validated = validate_artifact_documents(
            project,
            [*dependency_documents, artifact],
            allowed_formats=ADMISSION_FORMATS,
        )
    except (PhaseReportV3Error, TypeError, ValueError) as exc:
        raise CreationWorkerProtocolError("private admission closure is not integral") from exc
    if validated[-1] != artifact:
        raise CreationWorkerProtocolError("private admission validation changed artifact bytes")

    source_keys = {
        tuple(document_identity(document).values()) for document in _source_documents(project)
    }
    dependency_by_key = {
        tuple(document_identity(document).values()): document for document in dependency_documents
    }
    required: set[tuple[object, ...]] = set()
    pending = list(artifact_dependency_identities(artifact))
    while pending:
        dependency = pending.pop()
        key = tuple(dependency.values())
        if key in source_keys or key in required:
            continue
        document = dependency_by_key.get(key)
        if document is None:
            raise CreationWorkerProtocolError("private admission dependency closure is incomplete")
        required.add(key)
        pending.extend(artifact_dependency_identities(document))
    if required != set(dependency_by_key):
        raise CreationWorkerProtocolError("private admission dependencies are not exact")


def validate_private_creation_request(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CreationWorkerProtocolError("private request must be an object")
    if value["format"] != PRIVATE_CREATION_REQUEST_FORMAT:
        raise CreationWorkerProtocolError("private request format is unsupported")
    version = value.get("format_version")
    if type(version) is not int or version not in {
        PRIVATE_CREATION_REQUEST_VERSION,
        PRIVATE_CREATION_ASSET_REQUEST_VERSION,
        PRIVATE_CREATION_ASSET_SEAL_REQUEST_VERSION,
        PRIVATE_CREATION_RUNTIME_COMPOSE_REQUEST_VERSION,
        PRIVATE_CREATION_RUNTIME_BUNDLE_REQUEST_VERSION,
        PRIVATE_CREATION_MATERIALIZATION_BUNDLE_REQUEST_VERSION,
        PRIVATE_CREATION_GAME_MATERIALIZE_REQUEST_VERSION,
        PRIVATE_CREATION_GAME_PACKAGE_REQUEST_VERSION,
        PRIVATE_CREATION_GAME_PACKAGE_EXTRACT_REQUEST_VERSION,
        PRIVATE_CREATION_ASSET_QA_REVIEW_REQUEST_VERSION,
        PRIVATE_CREATION_ASSET_RELEASE_AUTHORIZE_REQUEST_VERSION,
        PRIVATE_CREATION_RUNTIME_HEADLESS_REQUEST_VERSION,
    }:
        raise CreationWorkerProtocolError("private request format_version is unsupported")
    expected_fields = (
        _ASSET_PROCESS_PRIVATE_FIELDS
        if version == PRIVATE_CREATION_ASSET_REQUEST_VERSION
        else _ASSET_RELEASE_SEAL_PRIVATE_FIELDS
        if version == PRIVATE_CREATION_ASSET_SEAL_REQUEST_VERSION
        else _RUNTIME_COMPOSE_PRIVATE_FIELDS
        if version == PRIVATE_CREATION_RUNTIME_COMPOSE_REQUEST_VERSION
        else _RUNTIME_BUNDLE_PRIVATE_FIELDS
        if version == PRIVATE_CREATION_RUNTIME_BUNDLE_REQUEST_VERSION
        else _MATERIALIZATION_BUNDLE_PRIVATE_FIELDS
        if version == PRIVATE_CREATION_MATERIALIZATION_BUNDLE_REQUEST_VERSION
        else _GAME_MATERIALIZE_PRIVATE_FIELDS
        if version == PRIVATE_CREATION_GAME_MATERIALIZE_REQUEST_VERSION
        else _GAME_PACKAGE_PRIVATE_FIELDS
        if version == PRIVATE_CREATION_GAME_PACKAGE_REQUEST_VERSION
        else _GAME_PACKAGE_EXTRACT_PRIVATE_FIELDS
        if version == PRIVATE_CREATION_GAME_PACKAGE_EXTRACT_REQUEST_VERSION
        else _ASSET_QA_REVIEW_PRIVATE_FIELDS
        if version == PRIVATE_CREATION_ASSET_QA_REVIEW_REQUEST_VERSION
        else _ASSET_RELEASE_AUTHORIZE_PRIVATE_FIELDS
        if version == PRIVATE_CREATION_ASSET_RELEASE_AUTHORIZE_REQUEST_VERSION
        else _RUNTIME_HEADLESS_PRIVATE_FIELDS
        if version == PRIVATE_CREATION_RUNTIME_HEADLESS_REQUEST_VERSION
        else _PRIVATE_FIELDS
    )
    _invalid_fields(value, expected_fields, "private request")
    _identifier(value["job_id"], context="private request/job_id")
    _identifier(value["workspace_id"], context="private request/workspace_id", workspace=True)
    operation = value["operation"]
    if version == 1 and operation not in {"artifact.admit", "creation.compile"}:
        raise CreationWorkerProtocolError("private request v1 operation is unsupported")
    if version == 2 and operation != "asset.process":
        raise CreationWorkerProtocolError("private request v2 operation is unsupported")
    if version == 3 and operation != "asset.release.seal":
        raise CreationWorkerProtocolError("private request v3 operation is unsupported")
    if version == 4 and operation != "runtime.compose":
        raise CreationWorkerProtocolError("private request v4 operation is unsupported")
    if version == 5 and operation != "runtime.bundle.build":
        raise CreationWorkerProtocolError("private request v5 operation is unsupported")
    if version == 6 and operation != "game.materialization.bundle.build":
        raise CreationWorkerProtocolError("private request v6 operation is unsupported")
    if version == 7 and operation != "game.materialize":
        raise CreationWorkerProtocolError("private request v7 operation is unsupported")
    if version == 8 and operation != "game.package":
        raise CreationWorkerProtocolError("private request v8 operation is unsupported")
    if version == 9 and operation != "game.package.extract":
        raise CreationWorkerProtocolError("private request v9 operation is unsupported")
    if version == 10 and operation != "asset.qa.review":
        raise CreationWorkerProtocolError("private request v10 operation is unsupported")
    if version == 11 and operation != "asset.release.authorize":
        raise CreationWorkerProtocolError("private request v11 operation is unsupported")
    if version == 12 and operation != "runtime.headless.verify":
        raise CreationWorkerProtocolError("private request v12 operation is unsupported")
    _authority(value["authority"])
    inputs = _validate_inputs(value["inputs"])
    project = _loaded_source(value["source"])
    if operation == "creation.compile":
        if value["artifact"] is not None or value["dependency_documents"] != []:
            raise CreationWorkerProtocolError("private compile payload is invalid")
        expected_inputs = [_input_reference(document) for document in _source_documents(project)]
        if inputs != expected_inputs:
            raise CreationWorkerProtocolError("private compile source references changed")
    elif operation == "artifact.admit":
        _validate_admission(
            project,
            inputs,
            value["artifact"],
            value["dependency_documents"],
        )
    elif operation == "asset.process":
        lineage = _canonical_asset_lineage(project, value["lineage_documents"])
        expected_inputs = [_input_reference(document) for document in lineage]
        if inputs != expected_inputs:
            raise CreationWorkerProtocolError("private asset lineage references changed")
        by_format = {str(document["format"]): document for document in lineage}
        for field in ("recipe_id", "processing_receipt_id", "qa_report_id"):
            _identifier(value[field], context=f"private asset/{field}")
        _validate_acceptance_results(
            value["acceptance_results"],
            by_format["world-forge.asset_spec"],
        )
        _validate_staged_inputs(
            value["staged_inputs"],
            by_format["world-forge.asset_production_receipt"],
        )
    elif operation == "asset.release.seal":
        lineage, _roots, records = _asset_release_lineage(
            project,
            value["lineage_documents"],
        )
        expected_inputs = [_input_reference(document) for document in lineage]
        if inputs != expected_inputs:
            raise CreationWorkerProtocolError("private asset release lineage references changed")
        _identifier(value["manifest_id"], context="private asset release/manifest_id")
        _identifier(
            value["target_grant_id"],
            context="private asset release/target_grant_id",
        )
        grant_generation = value["target_grant_generation"]
        if (
            isinstance(grant_generation, bool)
            or not isinstance(grant_generation, int)
            or grant_generation < 0
        ):
            raise CreationWorkerProtocolError(
                "private asset release target grant generation is invalid"
            )
        _validate_asset_seal_staged_inputs(value["staged_inputs"], records)
    elif operation == "asset.qa.review":
        lineage = _asset_qa_review_lineage(project, value["lineage_documents"])
        expected_inputs = [_input_reference(document) for document in lineage]
        if inputs != expected_inputs:
            raise CreationWorkerProtocolError("private asset QA review references changed")
        by_format = {str(document["format"]): document for document in lineage}
        _identifier(
            value["review_receipt_id"],
            context="private asset QA review/review_receipt_id",
        )
        _identifier(
            value["output_role"],
            context="private asset QA review/output_role",
        )
        _validate_review_decisions(
            value["decisions"],
            value["blockers"],
            by_format["world-forge.asset_spec"],
        )
        qa_outputs = [
            output
            for output in by_format["world-forge.asset_qa_report"]["outputs"]
            if output["role"] == value["output_role"]
        ]
        if len(qa_outputs) != 1:
            raise CreationWorkerProtocolError("private asset QA review output role is not exact")
        _validate_staged_inputs(value["staged_inputs"], {"outputs": qa_outputs})
    elif operation == "asset.release.authorize":
        _lineage, _roots, records = _asset_release_lineage(
            project,
            value["lineage_documents"],
        )
        reviews_raw = value["review_documents"]
        if (
            not isinstance(reviews_raw, list)
            or not 1 <= len(reviews_raw) <= 128
            or any(not isinstance(item, dict) for item in reviews_raw)
        ):
            raise CreationWorkerProtocolError("private asset release authority reviews are invalid")
        try:
            reviews = [validate_asset_qa_review_receipt_document(item) for item in reviews_raw]
        except GenericAssetAuthorityError as exc:
            raise CreationWorkerProtocolError(
                "private asset release authority review is invalid"
            ) from exc
        review_ids = [str(item["review_receipt_id"]) for item in reviews]
        if review_ids != sorted(set(review_ids), key=lambda item: item.encode("utf-8")):
            raise CreationWorkerProtocolError(
                "private asset release authority reviews are not canonical"
            )
        expected_inputs = [_input_reference(document) for document in reviews]
        if inputs != expected_inputs:
            raise CreationWorkerProtocolError(
                "private asset release authority input references changed"
            )
        lineage_identities = {
            _identity_key(document_identity(document)) for document in value["lineage_documents"]
        }
        for review in reviews:
            if review["authority"]["workspace_id"] != value["workspace_id"]:
                raise CreationWorkerProtocolError(
                    "private asset release authority review crosses workspaces"
                )
            for field in ("root_generation", "source_revision", "workflow_status_hash"):
                if review["authority"][field] != value["authority"][field]:
                    raise CreationWorkerProtocolError(
                        "private asset release authority review crosses immutable authority"
                    )
            for identity in artifact_dependency_identities(review):
                if _identity_key(identity) not in lineage_identities:
                    raise CreationWorkerProtocolError(
                        "private asset release authority review lineage is incomplete"
                    )
        for field in (
            "manifest_id",
            "assetpack_id",
            "release_authority_id",
            "target_grant_id",
        ):
            _identifier(value[field], context=f"private asset release authority/{field}")
        generation = value["target_grant_generation"]
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise CreationWorkerProtocolError(
                "private asset release authority target grant generation is invalid"
            )
        blockers = value["blockers"]
        if (
            not isinstance(blockers, list)
            or len(blockers) > 64
            or any(
                not isinstance(item, str) or ENTITY_ID_PATTERN.fullmatch(item) is None
                for item in blockers
            )
            or blockers != sorted(set(blockers), key=lambda item: item.encode("utf-8"))
        ):
            raise CreationWorkerProtocolError(
                "private asset release authority blockers are invalid"
            )
        _validate_asset_seal_staged_inputs(value["staged_inputs"], records)
    elif operation == "runtime.headless.verify":
        documents = _runtime_headless_documents(project, value["artifact_documents"])
        expected_inputs = [_input_reference(document) for document in documents]
        if inputs != expected_inputs:
            raise CreationWorkerProtocolError("private runtime headless input references changed")
        retained_release_request = validate_private_creation_request(value["asset_release_request"])
        release = documents[3]
        assetpack = documents[2]
        release_binding = release.get("authority")
        if (
            retained_release_request["operation"] != "asset.release.authorize"
            or retained_release_request["workspace_id"] != value["workspace_id"]
            or retained_release_request["assetpack_id"] != assetpack["assetpack_id"]
            or retained_release_request["release_authority_id"] != release["release_authority_id"]
            or not isinstance(release_binding, dict)
            or release_binding.get("workspace_id") != value["workspace_id"]
            or release_binding.get("producer_job_id") != retained_release_request["job_id"]
            or release_binding.get("producer_operation") != "asset.release.authorize"
            or release_binding.get("producer_output_position") != 2
        ):
            raise CreationWorkerProtocolError(
                "private runtime headless asset authority crosses retained lineage"
            )
        for field in ("source_grant_id", "target_grant_id"):
            _identifier(value[field], context=f"private runtime headless/{field}")
        if value["source_grant_id"] == value["target_grant_id"]:
            raise CreationWorkerProtocolError("private runtime headless grants overlap")
        for field in ("source_grant_generation", "target_grant_generation"):
            generation = value[field]
            if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
                raise CreationWorkerProtocolError(f"private runtime headless {field} is invalid")
        if value["platform_id"] not in _RUNTIME_HEADLESS_PLATFORM_IDS:
            raise CreationWorkerProtocolError("private runtime headless platform is unsupported")
        _validate_runtime_headless_staged_inputs(
            value["staged_inputs"],
            asset_release_request=retained_release_request,
            execution_script=documents[8],
        )
    elif operation == "runtime.compose":
        gamepack, inventory, assetpack = _runtime_compose_lineage(
            project,
            value["lineage_documents"],
        )
        expected_inputs = [
            _input_reference(document) for document in (gamepack, inventory, assetpack)
        ]
        if inputs != expected_inputs:
            raise CreationWorkerProtocolError(
                "private runtime composition lineage references changed"
            )
        _identifier(
            value["target_grant_id"],
            context="private runtime composition/target_grant_id",
        )
        grant_generation = value["target_grant_generation"]
        if (
            isinstance(grant_generation, bool)
            or not isinstance(grant_generation, int)
            or grant_generation < 0
        ):
            raise CreationWorkerProtocolError(
                "private runtime composition target grant generation is invalid"
            )
        _validate_runtime_compose_staged_inputs(value["staged_inputs"], assetpack)
    elif operation == "runtime.bundle.build":
        lineage = _runtime_bundle_lineage(project, value["lineage_documents"])
        expected_inputs = [_input_reference(document) for document in lineage]
        if inputs != expected_inputs:
            raise CreationWorkerProtocolError("private runtime bundle references changed")
        for field in ("source_grant_id", "target_grant_id"):
            _identifier(value[field], context=f"private runtime bundle/{field}")
        if value["source_grant_id"] == value["target_grant_id"]:
            raise CreationWorkerProtocolError("private runtime bundle grants overlap")
        for field in ("source_grant_generation", "target_grant_generation"):
            generation = value[field]
            if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
                raise CreationWorkerProtocolError(f"private runtime bundle {field} is invalid")
        _validate_runtime_compose_staged_inputs(value["staged_inputs"], lineage[2])
    elif operation == "game.materialization.bundle.build":
        try:
            runtime_bundle = validate_game_runtime_bundle_document(value["runtime_bundle_manifest"])
        except (TypeError, ValueError) as exc:
            raise CreationWorkerProtocolError(
                "private materialization runtime bundle manifest is invalid"
            ) from exc
        if inputs != [_input_reference(runtime_bundle)]:
            raise CreationWorkerProtocolError(
                "private materialization runtime bundle reference changed"
            )
        for field in ("source_grant_id", "target_grant_id"):
            _identifier(value[field], context=f"private materialization/{field}")
        if value["source_grant_id"] == value["target_grant_id"]:
            raise CreationWorkerProtocolError("private materialization grants overlap")
        for field in ("source_grant_generation", "target_grant_generation"):
            generation = value[field]
            if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
                raise CreationWorkerProtocolError(f"private materialization {field} is invalid")
        _validate_materialization_staged_inputs(value["staged_inputs"], runtime_bundle)
    elif operation == "game.materialize":
        try:
            materialization = validate_game_materialization_bundle_document(
                value["materialization_bundle_manifest"]
            )
        except (TypeError, ValueError) as exc:
            raise CreationWorkerProtocolError(
                "private game materialization bundle manifest is invalid"
            ) from exc
        if inputs != [_input_reference(materialization)]:
            raise CreationWorkerProtocolError(
                "private game materialization bundle reference changed"
            )
        for field in ("source_grant_id", "target_grant_id"):
            _identifier(value[field], context=f"private game materialization/{field}")
        if value["source_grant_id"] == value["target_grant_id"]:
            raise CreationWorkerProtocolError("private game materialization grants overlap")
        for field in ("source_grant_generation", "target_grant_generation"):
            generation = value[field]
            if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
                raise CreationWorkerProtocolError(
                    f"private game materialization {field} is invalid"
                )
        _validate_game_materialize_staged_inputs(
            value["staged_inputs"],
            materialization,
        )
    elif operation == "game.package":
        try:
            standalone = validate_standalone_game_document(value["standalone_game_manifest"])
            lock = validate_standalone_game_lock_document(value["standalone_game_lock"])
            package = validate_game_package_document(value["game_package_manifest"])
        except (GamePackageError, StandaloneDistributionError, TypeError, ValueError) as exc:
            raise CreationWorkerProtocolError(
                "private game package source or manifest is invalid"
            ) from exc
        if inputs != [_input_reference(standalone)]:
            raise CreationWorkerProtocolError("private game package standalone reference changed")
        if (
            package["standalone_game"]["content_hash"] != standalone["content_hash"]
            or package["payload_lock"]["content_hash"] != lock["content_hash"]
            or package["payload_lock"]["tree_hash"] != lock["tree_hash"]
            or package["lineage"] != standalone["lineage"]
        ):
            raise CreationWorkerProtocolError("private game package lineage crosses subjects")
        for field in ("source_grant_id", "target_grant_id"):
            _identifier(value[field], context=f"private game package/{field}")
        if value["source_grant_id"] == value["target_grant_id"]:
            raise CreationWorkerProtocolError("private game package grants overlap")
        for field in ("source_grant_generation", "target_grant_generation"):
            generation = value[field]
            if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
                raise CreationWorkerProtocolError(f"private game package {field} is invalid")
        archive = value["archive_output"]
        if not isinstance(archive, dict):
            raise CreationWorkerProtocolError("private game package archive output is invalid")
        _invalid_fields(
            archive,
            {"locator", "sha256", "size_bytes"},
            "private game package archive output",
        )
        if (
            archive["locator"] != "game_package_archive"
            or not isinstance(archive["sha256"], str)
            or SHA256_PATTERN.fullmatch(archive["sha256"]) is None
            or isinstance(archive["size_bytes"], bool)
            or not isinstance(archive["size_bytes"], int)
            or not 1 <= archive["size_bytes"] <= MAX_GAME_PACKAGE_ARCHIVE_BYTES
        ):
            raise CreationWorkerProtocolError("private game package archive output is invalid")
        _validate_game_package_staged_inputs(value["staged_inputs"], standalone, lock)
    else:
        try:
            package = validate_game_package_document(value["game_package_manifest"])
        except (GamePackageError, TypeError, ValueError) as exc:
            raise CreationWorkerProtocolError(
                "private game package extraction source is invalid"
            ) from exc
        if inputs != [_input_reference(package)]:
            raise CreationWorkerProtocolError("private game package extraction reference changed")
        for field in ("source_grant_id", "target_grant_id"):
            _identifier(value[field], context=f"private game package extraction/{field}")
        if value["source_grant_id"] == value["target_grant_id"]:
            raise CreationWorkerProtocolError("private game package extraction grants overlap")
        for field in ("source_grant_generation", "target_grant_generation"):
            generation = value[field]
            if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
                raise CreationWorkerProtocolError(
                    f"private game package extraction {field} is invalid"
                )
        archive = value["archive_input"]
        if not isinstance(archive, dict):
            raise CreationWorkerProtocolError(
                "private game package extraction archive input is invalid"
            )
        _invalid_fields(
            archive,
            {"locator", "sha256", "size_bytes"},
            "private game package extraction archive input",
        )
        if archive.get("locator") != "game_package_archive.wfgame":
            raise CreationWorkerProtocolError(
                "private game package extraction archive input is invalid"
            )
        _validate_game_package_extract_staged_inputs(value["staged_inputs"], archive)
    if len(canonical_json_bytes(value)) > MAX_PRIVATE_CREATION_REQUEST_BYTES:
        raise CreationWorkerProtocolError("private request exceeds its byte limit")
    return value


def _asset_lineage_arguments(
    request: dict[str, Any],
    artifact_root: str | Path,
) -> dict[str, object]:
    by_format: dict[str, list[dict[str, Any]]] = {}
    for document in request["lineage_documents"]:
        by_format.setdefault(str(document["format"]), []).append(document)
    return {
        "gamepack": by_format["world-forge.gamepack"][0],
        "subject": by_format["world-forge.asset_subject"][0],
        "target": by_format["world-forge.asset_target"][0],
        "style": by_format["world-forge.asset_style"][0],
        "inventory": by_format["world-forge.asset_inventory"][0],
        "specification": by_format["world-forge.asset_spec"][0],
        "request": by_format["world-forge.asset_production_request"][0],
        "receipt": by_format["world-forge.asset_production_receipt"][0],
        "selection": by_format["world-forge.asset_selection"][0],
        "provenance": by_format["world-forge.asset_provenance_record"][0],
        "license_records": tuple(by_format["world-forge.asset_license_record"]),
        "artifact_root": artifact_root,
    }


def _worker_output(locator: str, document: dict[str, Any]) -> CreationWorkerOutput:
    return CreationWorkerOutput(
        locator, document_identity(document), canonical_json_bytes(document)
    )


def _private_verified_asset_reviews(
    request: Mapping[str, Any],
    *,
    artifact_root: Path,
) -> list[Any]:
    lineage_by_key = {
        _identity_key(document_identity(document)): document
        for document in request["lineage_documents"]
    }
    resolver = _PrivateAssetReviewResolver()
    reviews = [
        validate_asset_qa_review_receipt_document(document)
        for document in request["review_documents"]
    ]
    for review in reviews:
        review_lineage = review["lineage"]
        try:
            specification = lineage_by_key[_identity_key(review_lineage["specification"])]
            processing_receipt = lineage_by_key[_identity_key(review_lineage["processing_receipt"])]
            qa_report = lineage_by_key[_identity_key(review_lineage["qa_report"])]
        except KeyError as exc:
            raise CreationWorkerProtocolError(
                "private asset release review sources are unavailable"
            ) from exc
        reviewed = review["reviewed_output"]
        retained = read_verified_artifact_bytes(
            artifact_root,
            reviewed["locator"],
            expected_sha256=reviewed["sha256"],
            expected_size_bytes=reviewed["size_bytes"],
            limit=16 * 1024 * 1024,
        )
        payload = canonical_json_bytes(review)
        binding = review["authority"]
        resolver.reviews[(review["review_receipt_id"], review["content_hash"])] = (
            RetainedAssetQaReviewRecord(
                document_bytes=payload,
                document_blob_sha256=hashlib.sha256(payload).hexdigest(),
                document_size_bytes=len(payload),
                specification_bytes=canonical_json_bytes(specification),
                processing_receipt_bytes=canonical_json_bytes(processing_receipt),
                qa_report_bytes=canonical_json_bytes(qa_report),
                retained_output_bytes=retained,
                retained_output_sha256=hashlib.sha256(retained).hexdigest(),
                retained_output_size_bytes=len(retained),
                workspace_id=binding["workspace_id"],
                root_generation=binding["root_generation"],
                source_revision=binding["source_revision"],
                workflow_status_hash=binding["workflow_status_hash"],
                artifact_snapshot_hash=binding["artifact_snapshot_hash"],
                producer_job_id=binding["producer_job_id"],
                producer_operation=binding["producer_operation"],
                producer_output_position=binding["producer_output_position"],
            )
        )
    return [verify_asset_qa_review(review, resolver=resolver) for review in reviews]


def _private_verified_asset_release(
    request: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    *,
    artifact_root: Path,
) -> tuple[Any, Any]:
    retained_request = request["asset_release_request"]
    retained_project = _loaded_source(retained_request["source"])
    reviews = _private_verified_asset_reviews(
        retained_request,
        artifact_root=artifact_root,
    )
    expected_manifest, expected_assetpack, expected_release, expected_blockers = (
        _build_asset_release_authorize_outputs(
            project=retained_project,
            lineage_documents=retained_request["lineage_documents"],
            reviews=reviews,
            manifest_id=retained_request["manifest_id"],
            assetpack_id=retained_request["assetpack_id"],
            release_authority_id=retained_request["release_authority_id"],
            blockers=retained_request["blockers"],
            authority={
                "workspace_id": retained_request["workspace_id"],
                **copy.deepcopy(dict(retained_request["authority"])),
                "producer_job_id": retained_request["job_id"],
                "producer_operation": "asset.release.authorize",
                "producer_output_position": 2,
            },
            artifact_root=artifact_root,
        )
    )
    retained_assetpack = documents[2]
    retained_release = documents[3]
    if (
        expected_assetpack != retained_assetpack
        or expected_release != retained_release
        or expected_release["status"] != "authorized"
        or expected_blockers
    ):
        raise CreationWorkerProtocolError("private runtime headless asset authority changed")
    release_payload = canonical_json_bytes(retained_release)
    release_binding = retained_release["authority"]
    resolver = _PrivateAssetReviewResolver()
    resolver.releases[
        (retained_release["release_authority_id"], retained_release["content_hash"])
    ] = RetainedAssetReleaseAuthorityRecord(
        document_bytes=release_payload,
        document_blob_sha256=hashlib.sha256(release_payload).hexdigest(),
        document_size_bytes=len(release_payload),
        workspace_id=release_binding["workspace_id"],
        root_generation=release_binding["root_generation"],
        source_revision=release_binding["source_revision"],
        workflow_status_hash=release_binding["workflow_status_hash"],
        artifact_snapshot_hash=release_binding["artifact_snapshot_hash"],
        producer_job_id=release_binding["producer_job_id"],
        producer_operation=release_binding["producer_operation"],
        producer_output_position=release_binding["producer_output_position"],
    )
    release_handle = verify_asset_release_authority(
        retained_release,
        manifest=expected_manifest,
        assetpack=retained_assetpack,
        reviews=reviews,
        resolver=resolver,
    )
    assetpack_handle = verify_generic_assetpack(
        artifact_root / "assetpack",
        expected_content_hash=retained_assetpack["content_hash"],
    )
    if assetpack_handle.manifest != retained_assetpack:
        assetpack_handle.close()
        raise CreationWorkerProtocolError("private runtime headless assetpack publication changed")
    return assetpack_handle, release_handle


def _execute_private_creation_request_uncached(
    value: object,
    *,
    artifact_root: str | Path | None = None,
) -> CreationWorkerResult:
    request = validate_private_creation_request(value)
    project = _loaded_source(request["source"])
    if request["operation"] == "artifact.admit":
        document = copy.deepcopy(request["artifact"])
        payload = canonical_json_bytes(document)
        identity = document_identity(document)
        return CreationWorkerResult(
            outputs=(CreationWorkerOutput("output_0001", identity, payload),),
            analysis_status="not_applicable",
            reason_codes=(),
        )

    if request["operation"] == "asset.process":
        if artifact_root is None:
            raise CreationWorkerProtocolError("private asset process root is unavailable")
        lineage = _asset_lineage_arguments(request, artifact_root)
        try:
            recipe = build_asset_processing_recipe(
                recipe_id=request["recipe_id"],
                **lineage,
            )
            try:
                receipt = build_asset_processing_receipt(
                    recipe,
                    processing_receipt_id=request["processing_receipt_id"],
                    **lineage,
                )
            except GenericAssetProcessingError as exc:
                if exc.recovery_receipt is None:
                    raise
                failed_receipt = exc.recovery_receipt
                reasons = tuple(failed_receipt["failure_reasons"])
                return CreationWorkerResult(
                    outputs=(
                        _worker_output("output_0001", recipe),
                        _worker_output("output_0002", failed_receipt),
                    ),
                    analysis_status="failed",
                    reason_codes=reasons,
                )
            qa_report = build_asset_qa_report(
                receipt,
                recipe=recipe,
                qa_report_id=request["qa_report_id"],
                acceptance_results=request["acceptance_results"],
                **lineage,
            )
        except GenericAssetProcessingError as exc:
            raise CreationWorkerProtocolError("private asset processing is not integral") from exc
        blockers = tuple(str(item) for item in qa_report["blockers"])
        return CreationWorkerResult(
            outputs=(
                _worker_output("output_0001", recipe),
                _worker_output("output_0002", receipt),
                _worker_output("output_0003", qa_report),
            ),
            analysis_status=str(qa_report["status"]),
            reason_codes=blockers,
        )

    if request["operation"] == "asset.qa.review":
        if artifact_root is None:
            raise CreationWorkerProtocolError(
                "private asset QA review retained output root is unavailable"
            )
        lineage = _asset_qa_review_lineage(project, request["lineage_documents"])
        by_format = {str(document["format"]): document for document in lineage}
        staged = request["staged_inputs"]
        if len(staged) != 1:
            raise CreationWorkerProtocolError(
                "private asset QA review retained output is not exact"
            )
        binding = staged[0]
        try:
            retained_output = read_verified_artifact_bytes(
                artifact_root,
                binding["source_locator"],
                expected_sha256=binding["sha256"],
                expected_size_bytes=binding["size_bytes"],
                limit=16 * 1024 * 1024,
            )
            receipt = build_asset_qa_review_receipt(
                by_format["world-forge.asset_qa_report"],
                by_format["world-forge.asset_spec"],
                by_format["world-forge.asset_processing_receipt"],
                review_receipt_id=request["review_receipt_id"],
                output_role=request["output_role"],
                decisions=request["decisions"],
                blockers=request["blockers"],
                authority={
                    "workspace_id": request["workspace_id"],
                    **copy.deepcopy(dict(request["authority"])),
                    "producer_job_id": request["job_id"],
                    "producer_operation": "asset.qa.review",
                    "producer_output_position": 0,
                },
                retained_output=retained_output,
            )
        except (GenericAssetAuthorityError, GenericAssetProductionError) as exc:
            raise CreationWorkerProtocolError("private asset QA review is not integral") from exc
        return CreationWorkerResult(
            outputs=(_worker_output("output_0001", receipt),),
            analysis_status=("passed" if receipt["status"] == "approved" else "failed"),
            reason_codes=tuple(receipt["blockers"]),
        )

    if request["operation"] == "asset.release.authorize":
        if artifact_root is None:
            raise CreationWorkerProtocolError("private asset release authority root is unavailable")
        try:
            reviews = _private_verified_asset_reviews(
                request,
                artifact_root=Path(artifact_root),
            )
            manifest, assetpack, authority, _expected_blockers = (
                _build_asset_release_authorize_outputs(
                    project=project,
                    lineage_documents=request["lineage_documents"],
                    reviews=reviews,
                    manifest_id=request["manifest_id"],
                    assetpack_id=request["assetpack_id"],
                    release_authority_id=request["release_authority_id"],
                    blockers=request["blockers"],
                    authority={
                        "workspace_id": request["workspace_id"],
                        **copy.deepcopy(dict(request["authority"])),
                        "producer_job_id": request["job_id"],
                        "producer_operation": "asset.release.authorize",
                        "producer_output_position": 2,
                    },
                    artifact_root=artifact_root,
                )
            )
        except CreationWorkerProtocolError:
            raise
        except (
            GenericAssetAuthorityError,
            GenericAssetProcessingError,
            GenericAssetpackError,
        ) as exc:
            raise CreationWorkerProtocolError(
                "private asset release authority is not integral"
            ) from exc
        return CreationWorkerResult(
            outputs=(
                _worker_output("output_0001", manifest),
                _worker_output("output_0002", assetpack),
                _worker_output("output_0003", authority),
            ),
            analysis_status=("passed" if authority["status"] == "authorized" else "failed"),
            reason_codes=tuple(authority["blockers"]),
        )

    if request["operation"] == "runtime.headless.verify":
        if artifact_root is None:
            raise CreationWorkerProtocolError(
                "private runtime headless retained trees are unavailable"
            )
        root = Path(artifact_root)
        documents = _runtime_headless_documents(
            project,
            request["artifact_documents"],
        )
        staged_script = next(
            (
                item
                for item in request["staged_inputs"]
                if item["source_locator"] == "execution/script.json"
            ),
            None,
        )
        if staged_script is None:
            raise CreationWorkerProtocolError(
                "private runtime headless execution script is unavailable"
            )
        try:
            script_bytes = read_verified_artifact_bytes(
                root,
                "execution/script.json",
                expected_sha256=staged_script["sha256"],
                expected_size_bytes=staged_script["size_bytes"],
                limit=MAX_GAME_EXECUTION_SCRIPT_BYTES,
            )
            runtime_bundle = verify_game_runtime_bundle(
                root / "runtime-bundle",
                expected_content_hash=documents[7]["content_hash"],
            )
            try:
                if runtime_bundle.manifest != documents[7]:
                    raise CreationWorkerProtocolError(
                        "private runtime headless runtime bundle publication changed"
                    )
            finally:
                runtime_bundle.close()
            assetpack_handle, release_handle = _private_verified_asset_release(
                request,
                documents,
                artifact_root=root,
            )
            try:
                initial = initialize_runtime_support_authority(
                    gamepack=documents[0],
                    inventory=documents[1],
                    composition=documents[6],
                    registry=documents[5],
                    snapshot=documents[4],
                    verified_assetpack=assetpack_handle,
                    asset_release_authority=release_handle,
                )
                evidence_set = build_headless_evidence_tree(
                    root / "headless-evidence",
                    bundle_root=root / "runtime-bundle",
                    script_bytes=script_bytes,
                    expected_bundle_hash=documents[7]["content_hash"],
                )
                try:
                    if (
                        evidence_set.manifest["runtime_evidence"]["platform"]["platform_id"]
                        != request["platform_id"]
                    ):
                        raise CreationWorkerProtocolError(
                            "private runtime headless platform differs from execution"
                        )
                    authority = attach_verified_headless_evidence(
                        initial,
                        evidence_set,
                        bundle_root=root / "runtime-bundle",
                    )
                finally:
                    evidence_set.close()
            finally:
                assetpack_handle.close()
            evidence = derive_runtime_evidence(authority)
            support = derive_runtime_support_report(authority)
        except CreationWorkerProtocolError:
            raise
        except (
            GenericAssetAuthorityError,
            GenericAssetProductionError,
            GenericAssetpackError,
            GenericHeadlessError,
            RuntimeSupportAuthorityError,
            RuntimeContractError,
            TypeError,
            ValueError,
        ) as exc:
            raise CreationWorkerProtocolError(
                "private runtime headless verification is not integral"
            ) from exc
        if (
            len(evidence) != 1
            or support["supported"] is not False
            or support["dimensions"]["release"] != "blocked"
            or evidence[0]["execution_status"] != "headless_verified"
            or evidence[0]["platform"]["platform_id"] != request["platform_id"]
            or authority.document["supported"] is not False
            or authority.document["release_status"] != "blocked"
        ):
            raise CreationWorkerProtocolError(
                "private runtime headless authority overclaims support"
            )
        return CreationWorkerResult(
            outputs=(
                _worker_output("output_0001", authority.document),
                _worker_output("output_0002", evidence[0]),
                _worker_output("output_0003", support),
            ),
            analysis_status="passed",
            reason_codes=tuple(support["reason_codes"]),
        )

    if request["operation"] == "asset.release.seal":
        raise CreationWorkerProtocolError(
            "asset_release_authority_required: new asset releases require retained "
            "v10 QA reviews and asset.release.authorize v11"
        )

    if request["operation"] == "runtime.compose":
        if artifact_root is None:
            raise CreationWorkerProtocolError(
                "private runtime composition assetpack root is unavailable"
            )
        gamepack, inventory, _assetpack = _runtime_compose_lineage(
            project,
            request["lineage_documents"],
        )
        runtime_root = Path(__file__).resolve().parents[2]
        try:
            adapters = build_builtin_runtime_adapters()
            snapshot = build_game_runtime_snapshot(
                runtime_root / "gamepack_runtime",
                adapter_runtime_root=runtime_root / "gamepack_raylib_2d",
                adapters=adapters,
            )
            registry = build_runtime_adapter_registry(
                snapshot=snapshot,
                adapters=adapters,
            )
            readiness = resolve_runtime_build_readiness(
                gamepack,
                registry=registry,
                snapshot=snapshot,
            )
            if readiness["status"] != "materialization_ready":
                rendered = ",".join(readiness["reason_codes"])
                raise CreationWorkerProtocolError(
                    "private runtime composition is unsupported: " + rendered
                )
            composition = build_game_runtime_composition(
                gamepack,
                inventory,
                artifact_root,
                registry=registry,
                snapshot=snapshot,
            )
            support = build_runtime_support_report(
                composition,
                gamepack=gamepack,
                registry=registry,
                snapshot=snapshot,
                evidence=[],
            )
        except RuntimeContractError as exc:
            raise CreationWorkerProtocolError(
                "private runtime composition is not integral"
            ) from exc
        return CreationWorkerResult(
            outputs=(
                _worker_output("output_0001", snapshot),
                _worker_output("output_0002", registry),
                _worker_output("output_0003", composition),
                _worker_output("output_0004", support),
            ),
            analysis_status="passed",
            reason_codes=tuple(readiness["reason_codes"]),
        )

    if request["operation"] == "runtime.bundle.build":
        if artifact_root is None:
            raise CreationWorkerProtocolError(
                "private runtime bundle assetpack root is unavailable"
            )
        (
            gamepack,
            inventory,
            assetpack,
            snapshot,
            registry,
            composition,
            support,
        ) = _runtime_bundle_lineage(project, request["lineage_documents"])
        try:
            manifest, _payloads = build_game_runtime_bundle_manifest_from_objects(
                gamepack=gamepack,
                inventory=inventory,
                assetpack=assetpack,
                assetpack_root=artifact_root,
                snapshot=snapshot,
                registry=registry,
                composition=composition,
                support_report=support,
            )
        except (RuntimeContractError, ValueError) as exc:
            raise CreationWorkerProtocolError(
                "private runtime bundle build is not integral"
            ) from exc
        return CreationWorkerResult(
            outputs=(_worker_output("output_0001", manifest),),
            analysis_status="passed",
            reason_codes=tuple(support["reason_codes"]),
        )

    if request["operation"] == "game.materialization.bundle.build":
        if artifact_root is None:
            raise CreationWorkerProtocolError(
                "private materialization runtime bundle root is unavailable"
            )
        try:
            manifest, _payloads = build_game_materialization_bundle_manifest(
                runtime_bundle_root=artifact_root,
            )
        except (TypeError, ValueError) as exc:
            raise CreationWorkerProtocolError(
                "private materialization bundle build is not integral"
            ) from exc
        if (
            manifest["lineage"]["runtime_bundle_hash"]
            != request["runtime_bundle_manifest"]["content_hash"]
        ):
            raise CreationWorkerProtocolError(
                "private materialization bundle crossed its runtime bundle"
            )
        return CreationWorkerResult(
            outputs=(_worker_output("output_0001", manifest),),
            analysis_status="passed",
            reason_codes=("native_execution_unverified", "release_blocked"),
        )

    if request["operation"] == "game.materialize":
        if artifact_root is None:
            raise CreationWorkerProtocolError(
                "private game materialization source root is unavailable"
            )
        try:
            with verify_game_materialization_bundle(
                artifact_root,
                expected_content_hash=request["materialization_bundle_manifest"]["content_hash"],
            ) as source:
                if source.manifest != request["materialization_bundle_manifest"]:
                    raise CreationWorkerProtocolError(
                        "private game materialization source manifest changed"
                    )
                manifest, _lock, _platform = build_standalone_game_documents(source)
        except CreationWorkerProtocolError:
            raise
        except (TypeError, ValueError) as exc:
            raise CreationWorkerProtocolError(
                "private standalone game build is not integral"
            ) from exc
        return CreationWorkerResult(
            outputs=(_worker_output("output_0001", manifest),),
            analysis_status="passed",
            reason_codes=("native_execution_unverified", "release_blocked"),
        )

    if request["operation"] == "game.package":
        if artifact_root is None:
            raise CreationWorkerProtocolError("private game package standalone root is unavailable")
        try:
            with verify_standalone_game(
                artifact_root,
                expected_content_hash=request["standalone_game_manifest"]["content_hash"],
            ) as source:
                if (
                    source.manifest != request["standalone_game_manifest"]
                    or source.lock != request["standalone_game_lock"]
                ):
                    raise CreationWorkerProtocolError(
                        "private game package standalone source changed"
                    )
                built = build_game_package_from_files(source.files)
            verified = verify_game_package_bytes(built.archive_bytes)
            if (
                verified.manifest != request["game_package_manifest"]
                or verified.archive_sha256 != request["archive_output"]["sha256"]
                or len(verified.archive_bytes) != request["archive_output"]["size_bytes"]
            ):
                raise CreationWorkerProtocolError("private game package archive identity changed")
        except CreationWorkerProtocolError:
            raise
        except (GamePackageError, StandaloneGameError, TypeError, ValueError) as exc:
            raise CreationWorkerProtocolError("private game package build is not integral") from exc
        return CreationWorkerResult(
            outputs=(_worker_output("output_0001", verified.manifest),),
            analysis_status="passed",
            reason_codes=(
                "extraction_unverified",
                "native_execution_unverified",
                "release_blocked",
            ),
            binary_outputs=(
                CreationWorkerBinaryOutput(
                    str(request["archive_output"]["locator"]),
                    verified.archive_bytes,
                ),
            ),
        )

    if request["operation"] == "game.package.extract":
        if artifact_root is None:
            raise CreationWorkerProtocolError(
                "private game package extraction source root is unavailable"
            )
        archive_path = Path(artifact_root) / str(request["archive_input"]["locator"])
        try:
            verified = verify_game_package(archive_path)
            try:
                if (
                    verified.manifest != request["game_package_manifest"]
                    or verified.archive_sha256 != request["archive_input"]["sha256"]
                    or len(verified.archive_bytes) != request["archive_input"]["size_bytes"]
                ):
                    raise CreationWorkerProtocolError(
                        "private game package extraction source changed"
                    )
                evidence = build_game_package_extraction_evidence(
                    verified.manifest,
                    archive_sha256=verified.archive_sha256,
                    archive_size_bytes=len(verified.archive_bytes),
                )
            finally:
                verified.close()
        except CreationWorkerProtocolError:
            raise
        except (
            GamePackageExtractionEvidenceError,
            WorldForgeGamePackageError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            raise CreationWorkerProtocolError(
                "private game package extraction verification is not integral"
            ) from exc
        return CreationWorkerResult(
            outputs=(_worker_output("output_0001", evidence),),
            analysis_status="passed",
            reason_codes=("native_execution_unverified", "release_blocked"),
        )

    gamepack = build_gamepack(project)
    ledger = build_authoring_capability_ledger(gamepack)
    analysis = analyze_gamepack(gamepack)
    documents = (gamepack, ledger, analysis)
    outputs: list[CreationWorkerOutput] = []
    for index, document in enumerate(documents, 1):
        payload = canonical_json_bytes(document)
        if not payload or len(payload) > MAX_CHANGESET_BYTES:
            raise CreationWorkerProtocolError("creation worker output exceeds its byte limit")
        identity = document_identity(document)
        if not hmac.compare_digest(identity["content_hash"], document["content_hash"]):
            raise CreationWorkerProtocolError("creation worker output identity changed")
        outputs.append(CreationWorkerOutput(f"output_{index:04d}", identity, payload))
    analysis_status = str(analysis["status"])
    if analysis_status not in CREATION_ANALYSIS_STATUSES:
        raise CreationWorkerProtocolError("creation analysis status is unsupported")
    reason_codes = analysis.get("reason_codes")
    if not isinstance(reason_codes, list) or any(
        not isinstance(item, str) for item in reason_codes
    ):
        raise CreationWorkerProtocolError("creation analysis reason codes are invalid")
    return CreationWorkerResult(
        outputs=tuple(outputs),
        analysis_status=analysis_status,
        reason_codes=tuple(sorted(set(reason_codes), key=lambda item: item.encode("utf-8"))),
    )


def execute_private_creation_request(
    value: object,
    *,
    artifact_root: str | Path | None = None,
) -> CreationWorkerResult:
    """Execute one request with bounded reuse of exact pure validation results."""

    with validation_memo_scope():
        return _execute_private_creation_request_uncached(
            value,
            artifact_root=artifact_root,
        )
