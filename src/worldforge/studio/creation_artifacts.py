from __future__ import annotations

import copy
import hashlib
import hmac
import sqlite3
import stat
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gamepack_runtime.distribution import (
    MAX_STANDALONE_JSON_BYTES,
    validate_standalone_game_document,
)
from gamepack_runtime.game_package import (
    MAX_GAME_PACKAGE_MANIFEST_BYTES,
    GamePackageError,
    build_game_package_from_files,
    validate_game_package_document,
    verify_game_package_file,
)
from isoworld.content.file_stat import path_file_stat
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.creation_contracts import LoadedCreationProject
from worldforge.game_materialization_bundle import (
    MAX_GAME_MATERIALIZATION_BUNDLE_MANIFEST_BYTES,
    GameMaterializationBundleError,
    build_game_materialization_bundle_manifest,
    validate_game_materialization_bundle_document,
    verify_game_materialization_bundle,
)
from worldforge.game_package_extraction import (
    GamePackageExtractionEvidenceError,
    build_game_package_extraction_evidence,
    validate_game_package_extraction_evidence,
)
from worldforge.game_runtime_bundle import (
    MAX_GAME_RUNTIME_BUNDLE_MANIFEST_BYTES,
    GameRuntimeBundleError,
    build_game_runtime_bundle_manifest_from_objects,
    validate_game_runtime_bundle_document,
    verify_game_runtime_bundle,
)
from worldforge.generic_asset_authority import (
    GenericAssetAuthorityError,
    build_asset_qa_review_receipt,
    verify_asset_qa_review,
    verify_asset_release_authority,
)
from worldforge.generic_asset_processing import (
    GenericAssetProcessingError,
    build_asset_manifest,
    validate_asset_processing_receipt,
    validate_asset_processing_recipe,
    validate_asset_qa_report,
)
from worldforge.generic_asset_production import (
    GenericAssetProductionError,
    read_verified_artifact_bytes,
)
from worldforge.generic_assetpack import (
    MAX_GENERIC_ASSETPACK_MANIFEST_BYTES,
    GenericAssetpackError,
    build_generic_assetpack_manifest,
    validate_generic_assetpack_document,
    verify_generic_assetpack,
)
from worldforge.generic_headless import GenericHeadlessError, verify_headless_evidence_set
from worldforge.generic_runtime import (
    RuntimeContractError,
    build_builtin_runtime_adapters,
    build_game_runtime_composition,
    build_game_runtime_snapshot,
    build_runtime_adapter_registry,
    build_runtime_support_report,
    resolve_runtime_build_readiness,
    validate_runtime_evidence_document,
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
    validate_runtime_support_authority_document,
)
from worldforge.standalone_game import (
    StandaloneGameError,
    build_standalone_game_documents,
    verify_standalone_game,
)
from worldforge.studio.contracts import (
    CREATION_ARTIFACT_FORMAT,
    MAX_CREATION_ARTIFACTS,
    SHA256_PATTERN,
    StudioContractError,
    validate_studio_creation_artifact,
    validate_studio_creation_job,
    validate_studio_creation_output_grant_v6,
)
from worldforge.studio.creation_authoring import CreationAuthoringManager
from worldforge.studio.creation_executor import (
    VerifiedCreationBinaryOutput,
    VerifiedCreationOutput,
)
from worldforge.studio.creation_job_protocol import (
    ADMISSION_FORMATS,
    _asset_lineage_arguments,
    _asset_release_lineage,
    _build_asset_release_authorize_outputs,
)
from worldforge.studio.creation_workspaces import CreationWorkspaceManager
from worldforge.studio.errors import StudioError, conflict, invalid_state, not_found
from worldforge.studio.storage import StudioStore, decode_object, encode_json

_COMPILE_FORMATS = frozenset(
    {
        "world-forge.gamepack",
        "world-forge.mechanic_capability_ledger",
        "world-forge.game_analysis",
    }
)
_COMPILE_ORDER = (
    "world-forge.gamepack",
    "world-forge.mechanic_capability_ledger",
    "world-forge.game_analysis",
)
_ASSET_PROCESS_FORMATS = frozenset(
    {
        "world-forge.asset_processing_recipe",
        "world-forge.asset_processing_receipt",
        "world-forge.asset_qa_report",
    }
)
_ASSET_PROCESS_SUCCESS_ORDER = (
    "world-forge.asset_processing_recipe",
    "world-forge.asset_processing_receipt",
    "world-forge.asset_qa_report",
)
_ASSET_PROCESS_FAILURE_ORDER = (
    "world-forge.asset_processing_recipe",
    "world-forge.asset_processing_receipt",
)
_ASSET_RELEASE_ORDER = (
    "world-forge.asset_manifest",
    "world-forge.assetpack",
)
_ASSET_RELEASE_AUTHORIZE_ORDER = (
    "world-forge.asset_manifest",
    "world-forge.assetpack",
    "world-forge.asset_release_authority",
)
_ASSET_QA_REVIEW_ORDER = ("world-forge.asset_qa_review_receipt",)
_RUNTIME_COMPOSE_ORDER = (
    "world-forge.game_runtime_snapshot",
    "world-forge.runtime_adapter_registry",
    "world-forge.game_runtime_composition",
    "world-forge.runtime_support_report",
)
_RUNTIME_COMPOSE_INPUT_ORDER = (
    "world-forge.gamepack",
    "world-forge.asset_inventory",
    "world-forge.assetpack",
)
_RUNTIME_BUNDLE_ORDER = ("world-forge.game_runtime_bundle",)
_RUNTIME_BUNDLE_INPUT_ORDER = (
    "world-forge.gamepack",
    "world-forge.asset_inventory",
    "world-forge.assetpack",
    "world-forge.game_runtime_snapshot",
    "world-forge.runtime_adapter_registry",
    "world-forge.game_runtime_composition",
    "world-forge.runtime_support_report",
)
_RUNTIME_HEADLESS_ORDER = (
    "world-forge.runtime_support_authority",
    "world-forge.runtime_evidence",
    "world-forge.runtime_support_report",
)
_RUNTIME_HEADLESS_INPUT_ORDER = (
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
_MATERIALIZATION_BUNDLE_ORDER = ("world-forge.game_materialization_bundle",)
_MATERIALIZATION_BUNDLE_INPUT_ORDER = ("world-forge.game_runtime_bundle",)
_STANDALONE_GAME_ORDER = ("world-forge.standalone_game",)
_STANDALONE_GAME_INPUT_ORDER = ("world-forge.game_materialization_bundle",)
_GAME_PACKAGE_ORDER = ("world-forge.game_package",)
_GAME_PACKAGE_INPUT_ORDER = ("world-forge.standalone_game",)
_GAME_PACKAGE_EXTRACTION_ORDER = ("world-forge.game_package_extraction",)
_GAME_PACKAGE_EXTRACTION_INPUT_ORDER = ("world-forge.game_package",)
_ASSET_PROCESS_RETENTION_FORMAT = "world-forge.studio_asset_process_retention"
_ASSET_PROCESS_RETENTION_VERSION = 1
_ROLE_BY_FORMAT = {
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
    "world-forge.asset_qa_review_receipt": "asset_qa_review",
    "world-forge.asset_release_authority": "asset_release_authority",
    "world-forge.asset_manifest": "asset_manifest",
    "world-forge.assetpack": "sealed_assetpack",
    "world-forge.runtime_adapter": "runtime_adapter",
    "world-forge.runtime_adapter_registry": "runtime_registry",
    "world-forge.game_runtime_snapshot": "runtime_snapshot",
    "world-forge.game_runtime_composition": "runtime_composition",
    "world-forge.runtime_evidence": "runtime_evidence",
    "world-forge.runtime_support_report": "runtime_support",
    "world-forge.runtime_support_authority": "runtime_support_authority",
    "world-forge.game_execution_script": "headless_execution_script",
    "world-forge.game_runtime_bundle": "runtime_bundle",
    "world-forge.game_materialization_bundle": "materialization_bundle",
    "world-forge.standalone_game": "standalone_game",
    "world-forge.game_package": "game_package",
    "world-forge.game_package_extraction": "game_package_extraction",
    "world-forge.creation_readiness": "creation_readiness",
    "world-forge.creation_handoff": "creation_handoff",
}


def artifact_id_for_identity(identity: Mapping[str, Any]) -> str:
    return "artifact_" + canonical_payload_hash({"subject": dict(identity)})


def artifact_roles(document: Mapping[str, Any]) -> list[str]:
    role = _ROLE_BY_FORMAT.get(str(document.get("format")), "registered_artifact")
    roles = {role}
    if str(document.get("format")).startswith("world-forge.asset_"):
        roles.add("asset_lineage")
    return sorted(roles, key=lambda item: item.encode("utf-8"))


def _assetpack_candidate_publication(
    store: StudioStore,
    row: Mapping[str, Any],
    *,
    workspace_id: str,
    job_id: str,
    artifact_id: str,
    producer_operation: str = "asset.release.seal",
) -> dict[str, Any]:
    """Join one published seal result to its exact immutable candidate bytes."""

    try:
        record = validate_studio_creation_artifact(
            decode_object(
                str(row["record_json"]),
                context="assetpack publication candidate",
            )
        )
        digest = str(row["document_blob_sha256"])
        payload = read_verified_artifact_bytes(
            store.blobs_dir,
            f"{digest[:2]}/{digest}",
            expected_sha256=digest,
            expected_size_bytes=int(row["document_size"]),
            limit=MAX_GENERIC_ASSETPACK_MANIFEST_BYTES,
        )
        info = path_file_stat(store.blob_path(digest))
        roles_wrapper = decode_object(
            '{"roles":' + str(row["roles_json"]) + "}",
            context="assetpack publication candidate roles",
        )
        document = validate_generic_assetpack_document(
            decode_json_object(payload, source="assetpack publication candidate")
        )
        identity = document_identity(document)
    except (
        GenericAssetProductionError,
        GenericAssetpackError,
        OSError,
        PhaseReportV3Error,
        RuntimeIOError,
        StudioContractError,
        StudioError,
        TypeError,
        ValueError,
    ) as exc:
        raise invalid_state("Stored creation job publication projection diverged") from exc

    subject = record["subject"]
    expected_identity = {
        "format": "world-forge.assetpack",
        "format_version": 1,
        "id": document["assetpack_id"],
        "content_hash": document["content_hash"],
    }
    if (
        row["artifact_id"] != artifact_id
        or row["workspace_id"] != workspace_id
        or row["producer_job_id"] != job_id
        or row["producer_operation"] != producer_operation
        or int(row["producer_output_position"]) != 1
        or record["artifact_id"] != artifact_id
        or record["lifecycle"] != "candidate"
        or record["producer"]
        != {
            "kind": "future_candidate",
            "phase_id": None,
            "reference_id": job_id,
        }
        or record["authority"]["workspace_id"] != workspace_id
        or record["authority"]
        != {
            "workspace_id": row["workspace_id"],
            "root_generation": row["root_generation"],
            "source_revision": row["source_revision"],
            "workflow_status_hash": row["workflow_status_hash"],
        }
        or set(roles_wrapper) != {"roles"}
        or record["roles"] != roles_wrapper["roles"]
        or subject != expected_identity
        or subject["format"] != row["subject_format"]
        or subject["format_version"] != row["subject_version"]
        or subject["id"] != row["subject_id"]
        or subject["content_hash"] != row["content_hash"]
        or artifact_id_for_identity(subject) != artifact_id
        or canonical_json_bytes(document) != payload
        or identity != expected_identity
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (str(info.st_dev), str(info.st_ino)) != (str(row["blob_dev"]), str(row["blob_ino"]))
    ):
        raise invalid_state("Stored creation job publication projection diverged")
    return {
        "format": "world-forge.assetpack",
        "format_version": 1,
        "id": document["assetpack_id"],
        "content_hash": document["content_hash"],
        "inventory_hash": document["inventory"]["content_hash"],
    }


def _runtime_bundle_candidate_publication(
    store: StudioStore,
    row: Mapping[str, Any],
    *,
    workspace_id: str,
    job_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    try:
        record = validate_studio_creation_artifact(
            decode_object(str(row["record_json"]), context="runtime bundle publication candidate")
        )
        digest = str(row["document_blob_sha256"])
        payload = read_verified_artifact_bytes(
            store.blobs_dir,
            f"{digest[:2]}/{digest}",
            expected_sha256=digest,
            expected_size_bytes=int(row["document_size"]),
            limit=MAX_GAME_RUNTIME_BUNDLE_MANIFEST_BYTES,
        )
        info = path_file_stat(store.blob_path(digest))
        roles_wrapper = decode_object(
            '{"roles":' + str(row["roles_json"]) + "}",
            context="runtime bundle publication candidate roles",
        )
        document = validate_game_runtime_bundle_document(
            decode_json_object(payload, source="runtime bundle publication candidate")
        )
        identity = document_identity(document)
    except (
        GenericAssetProductionError,
        OSError,
        PhaseReportV3Error,
        RuntimeIOError,
        StudioContractError,
        StudioError,
        TypeError,
        ValueError,
    ) as exc:
        raise invalid_state("Stored runtime bundle publication projection diverged") from exc
    expected_identity = {
        "format": "world-forge.game_runtime_bundle",
        "format_version": 1,
        "id": document["bundle_id"],
        "content_hash": document["content_hash"],
    }
    subject = record["subject"]
    if (
        row["artifact_id"] != artifact_id
        or row["workspace_id"] != workspace_id
        or row["producer_job_id"] != job_id
        or row["producer_operation"] != "runtime.bundle.build"
        or int(row["producer_output_position"]) != 0
        or record["artifact_id"] != artifact_id
        or record["lifecycle"] != "candidate"
        or record["producer"]
        != {"kind": "future_candidate", "phase_id": None, "reference_id": job_id}
        or record["authority"]["workspace_id"] != workspace_id
        or record["authority"]
        != {
            "workspace_id": row["workspace_id"],
            "root_generation": row["root_generation"],
            "source_revision": row["source_revision"],
            "workflow_status_hash": row["workflow_status_hash"],
        }
        or set(roles_wrapper) != {"roles"}
        or record["roles"] != roles_wrapper["roles"]
        or subject != expected_identity
        or subject["format"] != row["subject_format"]
        or subject["format_version"] != row["subject_version"]
        or subject["id"] != row["subject_id"]
        or subject["content_hash"] != row["content_hash"]
        or identity != expected_identity
        or artifact_id_for_identity(identity) != artifact_id
        or canonical_json_bytes(document) != payload
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (str(info.st_dev), str(info.st_ino)) != (str(row["blob_dev"]), str(row["blob_ino"]))
    ):
        raise invalid_state("Stored runtime bundle publication projection diverged")
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document["bundle_id"],
        "content_hash": document["content_hash"],
        "tree_hash": document["tree_hash"],
    }


def _materialization_bundle_candidate_publication(
    store: StudioStore,
    row: Mapping[str, Any],
    *,
    workspace_id: str,
    job_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    try:
        record = validate_studio_creation_artifact(
            decode_object(
                str(row["record_json"]),
                context="materialization bundle publication candidate",
            )
        )
        digest = str(row["document_blob_sha256"])
        payload = read_verified_artifact_bytes(
            store.blobs_dir,
            f"{digest[:2]}/{digest}",
            expected_sha256=digest,
            expected_size_bytes=int(row["document_size"]),
            limit=MAX_GAME_MATERIALIZATION_BUNDLE_MANIFEST_BYTES,
        )
        info = path_file_stat(store.blob_path(digest))
        roles_wrapper = decode_object(
            '{"roles":' + str(row["roles_json"]) + "}",
            context="materialization bundle publication candidate roles",
        )
        document = validate_game_materialization_bundle_document(
            decode_json_object(payload, source="materialization bundle publication candidate")
        )
        identity = document_identity(document)
    except (
        GameMaterializationBundleError,
        GenericAssetProductionError,
        OSError,
        PhaseReportV3Error,
        RuntimeIOError,
        StudioContractError,
        StudioError,
        TypeError,
        ValueError,
    ) as exc:
        raise invalid_state(
            "Stored materialization bundle publication projection diverged"
        ) from exc
    expected_identity = {
        "format": "world-forge.game_materialization_bundle",
        "format_version": 1,
        "id": document["materialization_bundle_id"],
        "content_hash": document["content_hash"],
    }
    subject = record["subject"]
    if (
        row["artifact_id"] != artifact_id
        or row["workspace_id"] != workspace_id
        or row["producer_job_id"] != job_id
        or row["producer_operation"] != "game.materialization.bundle.build"
        or int(row["producer_output_position"]) != 0
        or record["artifact_id"] != artifact_id
        or record["lifecycle"] != "candidate"
        or record["producer"]
        != {"kind": "future_candidate", "phase_id": None, "reference_id": job_id}
        or record["authority"]["workspace_id"] != workspace_id
        or record["authority"]
        != {
            "workspace_id": row["workspace_id"],
            "root_generation": row["root_generation"],
            "source_revision": row["source_revision"],
            "workflow_status_hash": row["workflow_status_hash"],
        }
        or set(roles_wrapper) != {"roles"}
        or record["roles"] != roles_wrapper["roles"]
        or subject != expected_identity
        or subject["format"] != row["subject_format"]
        or subject["format_version"] != row["subject_version"]
        or subject["id"] != row["subject_id"]
        or subject["content_hash"] != row["content_hash"]
        or identity != expected_identity
        or artifact_id_for_identity(identity) != artifact_id
        or canonical_json_bytes(document) != payload
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (str(info.st_dev), str(info.st_ino)) != (str(row["blob_dev"]), str(row["blob_ino"]))
    ):
        raise invalid_state("Stored materialization bundle publication projection diverged")
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document["materialization_bundle_id"],
        "content_hash": document["content_hash"],
        "tree_hash": document["tree_hash"],
    }


def _standalone_game_candidate_publication(
    store: StudioStore,
    row: Mapping[str, Any],
    *,
    workspace_id: str,
    job_id: str,
    artifact_id: str,
    tree_hash: str,
) -> dict[str, Any]:
    try:
        record = validate_studio_creation_artifact(
            decode_object(
                str(row["record_json"]),
                context="standalone game publication candidate",
            )
        )
        digest = str(row["document_blob_sha256"])
        payload = read_verified_artifact_bytes(
            store.blobs_dir,
            f"{digest[:2]}/{digest}",
            expected_sha256=digest,
            expected_size_bytes=int(row["document_size"]),
            limit=MAX_STANDALONE_JSON_BYTES,
        )
        info = path_file_stat(store.blob_path(digest))
        roles_wrapper = decode_object(
            '{"roles":' + str(row["roles_json"]) + "}",
            context="standalone game publication candidate roles",
        )
        document = validate_standalone_game_document(
            decode_json_object(payload, source="standalone game publication candidate")
        )
        identity = document_identity(document)
        if not isinstance(tree_hash, str) or SHA256_PATTERN.fullmatch(tree_hash) is None:
            raise ValueError("standalone tree hash is invalid")
    except (
        GenericAssetProductionError,
        OSError,
        PhaseReportV3Error,
        RuntimeIOError,
        StudioContractError,
        StudioError,
        TypeError,
        ValueError,
    ) as exc:
        raise invalid_state("Stored standalone publication projection diverged") from exc
    expected_identity = {
        "format": "world-forge.standalone_game",
        "format_version": 1,
        "id": document["game_id"],
        "content_hash": document["content_hash"],
    }
    subject = record["subject"]
    if (
        row["artifact_id"] != artifact_id
        or row["workspace_id"] != workspace_id
        or row["producer_job_id"] != job_id
        or row["producer_operation"] != "game.materialize"
        or int(row["producer_output_position"]) != 0
        or record["artifact_id"] != artifact_id
        or record["lifecycle"] != "candidate"
        or record["producer"]
        != {"kind": "future_candidate", "phase_id": None, "reference_id": job_id}
        or record["authority"]["workspace_id"] != workspace_id
        or record["authority"]
        != {
            "workspace_id": row["workspace_id"],
            "root_generation": row["root_generation"],
            "source_revision": row["source_revision"],
            "workflow_status_hash": row["workflow_status_hash"],
        }
        or set(roles_wrapper) != {"roles"}
        or record["roles"] != roles_wrapper["roles"]
        or subject != expected_identity
        or subject["format"] != row["subject_format"]
        or subject["format_version"] != row["subject_version"]
        or subject["id"] != row["subject_id"]
        or subject["content_hash"] != row["content_hash"]
        or identity != expected_identity
        or artifact_id_for_identity(identity) != artifact_id
        or canonical_json_bytes(document) != payload
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (str(info.st_dev), str(info.st_ino)) != (str(row["blob_dev"]), str(row["blob_ino"]))
    ):
        raise invalid_state("Stored standalone publication projection diverged")
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document["game_id"],
        "content_hash": document["content_hash"],
        "tree_hash": tree_hash,
    }


def _game_package_candidate_publication(
    store: StudioStore,
    row: Mapping[str, Any],
    *,
    workspace_id: str,
    job_id: str,
    artifact_id: str,
    archive_sha256: str,
    size_bytes: int,
) -> dict[str, Any]:
    try:
        record = validate_studio_creation_artifact(
            decode_object(
                str(row["record_json"]),
                context="game package publication candidate",
            )
        )
        digest = str(row["document_blob_sha256"])
        payload = read_verified_artifact_bytes(
            store.blobs_dir,
            f"{digest[:2]}/{digest}",
            expected_sha256=digest,
            expected_size_bytes=int(row["document_size"]),
            limit=MAX_GAME_PACKAGE_MANIFEST_BYTES,
        )
        info = path_file_stat(store.blob_path(digest))
        roles_wrapper = decode_object(
            '{"roles":' + str(row["roles_json"]) + "}",
            context="game package publication candidate roles",
        )
        document = validate_game_package_document(
            decode_json_object(payload, source="game package publication candidate")
        )
        identity = document_identity(document)
        if (
            not isinstance(archive_sha256, str)
            or SHA256_PATTERN.fullmatch(archive_sha256) is None
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 1
        ):
            raise ValueError("game package archive identity is invalid")
    except (
        GamePackageError,
        GenericAssetProductionError,
        OSError,
        PhaseReportV3Error,
        RuntimeIOError,
        StudioContractError,
        StudioError,
        TypeError,
        ValueError,
    ) as exc:
        raise invalid_state("Stored game package publication projection diverged") from exc
    expected_identity = {
        "format": "world-forge.game_package",
        "format_version": 1,
        "id": document["package_id"],
        "content_hash": document["content_hash"],
    }
    subject = record["subject"]
    if (
        row["artifact_id"] != artifact_id
        or row["workspace_id"] != workspace_id
        or row["producer_job_id"] != job_id
        or row["producer_operation"] != "game.package"
        or int(row["producer_output_position"]) != 0
        or record["artifact_id"] != artifact_id
        or record["lifecycle"] != "candidate"
        or record["producer"]
        != {"kind": "future_candidate", "phase_id": None, "reference_id": job_id}
        or record["authority"]["workspace_id"] != workspace_id
        or record["authority"]
        != {
            "workspace_id": row["workspace_id"],
            "root_generation": row["root_generation"],
            "source_revision": row["source_revision"],
            "workflow_status_hash": row["workflow_status_hash"],
        }
        or set(roles_wrapper) != {"roles"}
        or record["roles"] != roles_wrapper["roles"]
        or subject != expected_identity
        or subject["format"] != row["subject_format"]
        or subject["format_version"] != row["subject_version"]
        or subject["id"] != row["subject_id"]
        or subject["content_hash"] != row["content_hash"]
        or identity != expected_identity
        or artifact_id_for_identity(identity) != artifact_id
        or canonical_json_bytes(document) != payload
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (str(info.st_dev), str(info.st_ino)) != (str(row["blob_dev"]), str(row["blob_ino"]))
    ):
        raise invalid_state("Stored game package publication projection diverged")
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document["package_id"],
        "content_hash": document["content_hash"],
        "archive_sha256": archive_sha256,
        "size_bytes": size_bytes,
    }


def _game_package_extraction_candidate_publication(
    store: StudioStore,
    row: Mapping[str, Any],
    *,
    workspace_id: str,
    job_id: str,
    artifact_id: str,
    tree_hash: str,
) -> dict[str, Any]:
    try:
        record = validate_studio_creation_artifact(
            decode_object(
                str(row["record_json"]),
                context="game package extraction publication candidate",
            )
        )
        digest = str(row["document_blob_sha256"])
        payload = read_verified_artifact_bytes(
            store.blobs_dir,
            f"{digest[:2]}/{digest}",
            expected_sha256=digest,
            expected_size_bytes=int(row["document_size"]),
            limit=MAX_GAME_PACKAGE_MANIFEST_BYTES,
        )
        info = path_file_stat(store.blob_path(digest))
        roles_wrapper = decode_object(
            '{"roles":' + str(row["roles_json"]) + "}",
            context="game package extraction publication candidate roles",
        )
        document = validate_game_package_extraction_evidence(
            decode_json_object(payload, source="game package extraction publication candidate")
        )
        identity = document_identity(document)
        if (
            not isinstance(tree_hash, str)
            or SHA256_PATTERN.fullmatch(tree_hash) is None
            or document["extracted_tree_hash"] != tree_hash
        ):
            raise ValueError("game package extraction tree hash is invalid")
    except (
        GamePackageExtractionEvidenceError,
        GenericAssetProductionError,
        OSError,
        PhaseReportV3Error,
        RuntimeIOError,
        StudioContractError,
        StudioError,
        TypeError,
        ValueError,
    ) as exc:
        raise invalid_state("Stored game package extraction projection diverged") from exc
    expected_identity = {
        "format": "world-forge.game_package_extraction",
        "format_version": 1,
        "id": document["extraction_id"],
        "content_hash": document["content_hash"],
    }
    subject = record["subject"]
    if (
        row["artifact_id"] != artifact_id
        or row["workspace_id"] != workspace_id
        or row["producer_job_id"] != job_id
        or row["producer_operation"] != "game.package.extract"
        or int(row["producer_output_position"]) != 0
        or record["artifact_id"] != artifact_id
        or record["lifecycle"] != "candidate"
        or record["producer"]
        != {"kind": "future_candidate", "phase_id": None, "reference_id": job_id}
        or record["authority"]["workspace_id"] != workspace_id
        or record["authority"]
        != {
            "workspace_id": row["workspace_id"],
            "root_generation": row["root_generation"],
            "source_revision": row["source_revision"],
            "workflow_status_hash": row["workflow_status_hash"],
        }
        or set(roles_wrapper) != {"roles"}
        or record["roles"] != roles_wrapper["roles"]
        or subject != expected_identity
        or subject["format"] != row["subject_format"]
        or subject["format_version"] != row["subject_version"]
        or subject["id"] != row["subject_id"]
        or subject["content_hash"] != row["content_hash"]
        or identity != expected_identity
        or artifact_id_for_identity(identity) != artifact_id
        or canonical_json_bytes(document) != payload
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (str(info.st_dev), str(info.st_ino)) != (str(row["blob_dev"]), str(row["blob_ino"]))
    ):
        raise invalid_state("Stored game package extraction projection diverged")
    standalone = document["standalone_game"]
    return {
        "format": standalone["format"],
        "format_version": standalone["format_version"],
        "id": standalone["game_id"],
        "content_hash": standalone["content_hash"],
        "tree_hash": tree_hash,
    }


def _runtime_headless_candidate_publication(
    store: StudioStore,
    rows: Sequence[Mapping[str, Any]],
    *,
    artifacts: Any,
    job: Mapping[str, Any],
    workspace_id: str,
    job_id: str,
    artifact_ids: Sequence[str],
    result_identities: Sequence[Mapping[str, Any]],
    tree_hash: str,
) -> dict[str, Any]:
    expected_formats = (
        "world-forge.runtime_support_authority",
        "world-forge.runtime_evidence",
        "world-forge.runtime_support_report",
    )
    validators = (
        validate_runtime_support_authority_document,
        validate_runtime_evidence_document,
        validate_runtime_support_report_document,
    )
    if len(rows) != 3 or len(artifact_ids) != 3 or len(result_identities) != 3:
        raise invalid_state("Stored runtime headless publication projection diverged")
    documents: list[dict[str, Any]] = []
    for position, (row, artifact_id, result_identity, expected_format, validator) in enumerate(
        zip(
            rows,
            artifact_ids,
            result_identities,
            expected_formats,
            validators,
            strict=True,
        )
    ):
        try:
            record = validate_studio_creation_artifact(
                decode_object(
                    str(row["record_json"]),
                    context="runtime headless publication candidate",
                )
            )
            digest = str(row["document_blob_sha256"])
            payload = read_verified_artifact_bytes(
                store.blobs_dir,
                f"{digest[:2]}/{digest}",
                expected_sha256=digest,
                expected_size_bytes=int(row["document_size"]),
                limit=64 * 1024 * 1024,
            )
            info = path_file_stat(store.blob_path(digest))
            document = validator(
                decode_json_object(payload, source="runtime headless publication candidate")
            )
            identity = document_identity(document)
        except (
            GenericAssetProductionError,
            OSError,
            PhaseReportV3Error,
            RuntimeIOError,
            StudioContractError,
            TypeError,
            ValueError,
        ) as exc:
            raise invalid_state("Stored runtime headless publication projection diverged") from exc
        if (
            row["artifact_id"] != artifact_id
            or row["workspace_id"] != workspace_id
            or row["producer_job_id"] != job_id
            or row["producer_operation"] != "runtime.headless.verify"
            or int(row["producer_output_position"]) != position
            or record["artifact_id"] != artifact_id
            or record["lifecycle"] != "candidate"
            or record["producer"]
            != {"kind": "future_candidate", "phase_id": None, "reference_id": job_id}
            or record["subject"] != result_identity
            or identity != result_identity
            or identity["format"] != expected_format
            or artifact_id_for_identity(identity) != artifact_id
            or canonical_json_bytes(document) != payload
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (str(info.st_dev), str(info.st_ino)) != (str(row["blob_dev"]), str(row["blob_ino"]))
        ):
            raise invalid_state("Stored runtime headless publication projection diverged")
        documents.append(document)
    authority, evidence, support = documents
    headless = authority.get("headless_evidence")
    if (
        not isinstance(headless, list)
        or len(headless) != 1
        or headless[0]["runtime_evidence"]["content_hash"] != evidence["content_hash"]
        or authority["runtime_support_report"]["content_hash"] != support["content_hash"]
        or authority["supported"] is not False
        or authority["release_status"] != "blocked"
        or support["supported"] is not False
        or support["dimensions"]["release"] != "blocked"
    ):
        raise invalid_state("Stored runtime headless authority projection diverged")
    evidence_set = headless[0]["evidence_set"]
    candidate = {
        "format": evidence_set["format"],
        "format_version": evidence_set["format_version"],
        "id": evidence_set["id"],
        "content_hash": evidence_set["content_hash"],
        "tree_hash": tree_hash,
    }
    try:
        from worldforge.studio.creation_runtime_authority import (
            StudioRuntimeAuthorityResolver,
        )

        reconstructed = StudioRuntimeAuthorityResolver(
            store,
            artifacts=artifacts,
        ).reconstruct(
            job=job,
            retained_documents=documents,
        )
    except StudioError:
        raise
    except Exception as exc:
        raise invalid_state("Stored runtime headless authority reconstruction failed") from exc
    if (
        reconstructed.documents != tuple(documents)
        or reconstructed.publication["headless_evidence_set"] != candidate
    ):
        raise invalid_state("Stored runtime headless authority reconstruction diverged")
    return candidate


def _validate_creation_job_result_projection(
    store: StudioStore,
    row: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    artifacts: Any = None,
    allow_registry_committing: bool = False,
) -> None:
    job_id = str(row["job_id"])
    output_rows = store.connection.execute(
        "SELECT position, artifact_id FROM creation_job_outputs WHERE job_id = ? ORDER BY position",
        (job_id,),
    ).fetchall()
    candidate_rows = store.connection.execute(
        "SELECT * "
        "FROM creation_artifacts WHERE producer_job_id = ? "
        "ORDER BY producer_output_position",
        (job_id,),
    ).fetchall()
    output_ids = [
        item["artifact_id"]
        for position, item in enumerate(output_rows)
        if int(item["position"]) == position
    ]
    candidate_ids = [
        item["artifact_id"]
        for position, item in enumerate(candidate_rows)
        if int(item["producer_output_position"]) == position
        and item["workspace_id"] == row["workspace_id"]
    ]
    succeeded_events = store.connection.execute(
        "SELECT workspace_id, payload_json, created_at FROM creation_events "
        "WHERE topic = 'creation_job.succeeded' AND entity_type = 'creation_job' "
        "AND entity_id = ? ORDER BY event_id",
        (job_id,),
    ).fetchall()
    projection_is_contiguous = len(output_ids) == len(output_rows) and len(candidate_ids) == len(
        candidate_rows
    )
    if record["state"] == "succeeded":
        result = record["result"]
        expected_ids = result["output_artifact_ids"]
        expected_generation = int(record["generation"]) - int(record["progress"] == "committed")
        if len(succeeded_events) == 1:
            event = succeeded_events[0]
            event_payload = decode_object(
                event["payload_json"], context="creation job succeeded event"
            )
        else:
            event = None
            event_payload = None
        expected_event_payload = {
            "generation": expected_generation,
            "output_artifact_ids": expected_ids,
            "artifact_snapshot_hash": result["artifact_snapshot_hash"],
            "cleanup_pending": True,
        }
        if record["format_version"] == 10:
            expected_event_payload["review_receipt"] = result["review_receipt"]
            expected_event_payload["review_status"] = result["review_status"]
        if record["format_version"] == 11:
            for field in (
                "asset_manifest",
                "assetpack",
                "asset_release_authority",
                "release_status",
                "publication",
            ):
                expected_event_payload[field] = result[field]
            if len(candidate_rows) != len(_ASSET_RELEASE_AUTHORIZE_ORDER):
                raise invalid_state("Stored creation job publication projection diverged")
            if result["publication"] is None:
                reserved = store.connection.execute(
                    "SELECT 1 FROM creation_output_grants WHERE reserved_job_id = ?",
                    (job_id,),
                ).fetchone()
                if result["release_status"] != "blocked" or reserved is not None:
                    raise invalid_state("Stored creation job publication projection diverged")
        if record["format_version"] == 12:
            for field in (
                "runtime_support_authority",
                "runtime_evidence",
                "runtime_support_report",
                "release_status",
                "native_status",
                "supported",
                "publication",
            ):
                expected_event_payload[field] = result[field]
        if record["format_version"] in {3, 5, 6, 7, 8, 9, 12} or (
            record["format_version"] == 11 and result["publication"] is not None
        ):
            expected_event_payload["publication"] = result["publication"]
            publication = result["publication"]
            publication_field = {
                3: "assetpack",
                5: "runtime_bundle",
                6: "materialization_bundle",
                7: "standalone_game",
                8: "game_package",
                9: "standalone_game",
                11: "assetpack",
                12: "headless_evidence_set",
            }[record["format_version"]]
            grant_row = store.connection.execute(
                "SELECT * FROM creation_output_grants WHERE grant_id = ?",
                (publication["grant_id"],),
            ).fetchone()
            if grant_row is None:
                raise invalid_state("Stored creation job publication projection diverged")
            try:
                grant_record = validate_studio_creation_output_grant_v6(
                    decode_object(
                        grant_row["record_json"],
                        context="creation output grant publication",
                    )
                )
            except StudioContractError as exc:
                raise invalid_state("Stored creation job publication projection diverged") from exc
            if (
                grant_row["reserved_job_id"] != job_id
                or grant_row["workspace_id"] != row["workspace_id"]
                or grant_row["kind"] != publication["kind"]
                or grant_row["state"] != "published"
                or int(grant_row["generation"]) != publication["grant_generation"]
                or grant_row["published_dev"] is None
                or grant_row["published_ino"] is None
                or grant_record["grant_id"] != publication["grant_id"]
                or grant_record["workspace_id"] != row["workspace_id"]
                or grant_record["kind"] != publication["kind"]
                or grant_record["state"] != "published"
                or grant_record["generation"] != publication["grant_generation"]
                or grant_record["publication"] != publication[publication_field]
            ):
                raise invalid_state("Stored creation job publication projection diverged")
            if record["format_version"] in {3, 11}:
                expected_count = (
                    len(_ASSET_RELEASE_AUTHORIZE_ORDER)
                    if record["format_version"] == 11
                    else len(_ASSET_RELEASE_ORDER)
                )
                if len(candidate_rows) != expected_count:
                    raise invalid_state("Stored creation job publication projection diverged")
                candidate_publication = _assetpack_candidate_publication(
                    store,
                    candidate_rows[1],
                    workspace_id=str(row["workspace_id"]),
                    job_id=job_id,
                    artifact_id=str(expected_ids[1]),
                    producer_operation=(
                        "asset.release.authorize"
                        if record["format_version"] == 11
                        else "asset.release.seal"
                    ),
                )
            elif record["format_version"] == 5:
                if len(candidate_rows) != 1:
                    raise invalid_state("Stored creation job publication projection diverged")
                candidate_publication = _runtime_bundle_candidate_publication(
                    store,
                    candidate_rows[0],
                    workspace_id=str(row["workspace_id"]),
                    job_id=job_id,
                    artifact_id=str(expected_ids[0]),
                )
            elif record["format_version"] == 6:
                if len(candidate_rows) != 1:
                    raise invalid_state("Stored creation job publication projection diverged")
                candidate_publication = _materialization_bundle_candidate_publication(
                    store,
                    candidate_rows[0],
                    workspace_id=str(row["workspace_id"]),
                    job_id=job_id,
                    artifact_id=str(expected_ids[0]),
                )
            elif record["format_version"] == 7:
                if len(candidate_rows) != 1:
                    raise invalid_state("Stored creation job publication projection diverged")
                candidate_publication = _standalone_game_candidate_publication(
                    store,
                    candidate_rows[0],
                    workspace_id=str(row["workspace_id"]),
                    job_id=job_id,
                    artifact_id=str(expected_ids[0]),
                    tree_hash=str(grant_row["expected_tree_hash"]),
                )
            elif record["format_version"] == 8:
                if len(candidate_rows) != 1:
                    raise invalid_state("Stored creation job publication projection diverged")
                candidate_publication = _game_package_candidate_publication(
                    store,
                    candidate_rows[0],
                    workspace_id=str(row["workspace_id"]),
                    job_id=job_id,
                    artifact_id=str(expected_ids[0]),
                    archive_sha256=grant_row["expected_archive_sha256"],
                    size_bytes=grant_row["expected_size_bytes"],
                )
            elif record["format_version"] == 9:
                if len(candidate_rows) != 1:
                    raise invalid_state("Stored creation job publication projection diverged")
                candidate_publication = _game_package_extraction_candidate_publication(
                    store,
                    candidate_rows[0],
                    workspace_id=str(row["workspace_id"]),
                    job_id=job_id,
                    artifact_id=str(expected_ids[0]),
                    tree_hash=str(grant_row["expected_tree_hash"]),
                )
            else:
                if artifacts is None:
                    raise invalid_state("Stored runtime headless authority resolver is unavailable")
                candidate_publication = _runtime_headless_candidate_publication(
                    store,
                    candidate_rows,
                    artifacts=artifacts,
                    job=record,
                    workspace_id=str(row["workspace_id"]),
                    job_id=job_id,
                    artifact_ids=[str(item) for item in expected_ids],
                    result_identities=(
                        result["runtime_support_authority"],
                        result["runtime_evidence"],
                        result["runtime_support_report"],
                    ),
                    tree_hash=str(grant_row["expected_tree_hash"]),
                )
            if publication[publication_field] != candidate_publication:
                raise invalid_state("Stored creation job publication projection diverged")
        if (
            not projection_is_contiguous
            or output_ids != expected_ids
            or candidate_ids != expected_ids
            or event is None
            or event["workspace_id"] != row["workspace_id"]
            or event["created_at"] != record["finished_at"]
            or event_payload != expected_event_payload
        ):
            raise invalid_state("Stored creation job result projection diverged")
        return
    registry_commit = (
        allow_registry_committing
        and record["state"] == "running"
        and record["progress"] == "registry_committing"
    )
    if (
        succeeded_events
        or not projection_is_contiguous
        or output_ids != candidate_ids
        or (output_ids and not registry_commit)
    ):
        raise invalid_state("Stored creation job result projection diverged")


def _identity_key(identity: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (
        str(identity["format"]),
        int(identity["format_version"]),
        str(identity["id"]),
        str(identity["content_hash"]),
    )


@dataclass(frozen=True)
class PreparedCreationArtifact:
    artifact_id: str
    subject: dict[str, Any]
    document: dict[str, Any]
    payload: bytes
    blob_sha256: str
    blob_identity: tuple[int, int]
    roles: tuple[str, ...]
    dependencies: tuple[tuple[str, dict[str, Any]], ...]
    record: dict[str, Any]


@dataclass(frozen=True)
class StoredCreationArtifact:
    record: dict[str, Any]
    document: dict[str, Any]
    dependencies: tuple[tuple[str, dict[str, Any]], ...]


@dataclass(frozen=True)
class PreparedAssetProcessRetention:
    document: dict[str, Any]
    payload: bytes
    blob_sha256: str
    blob_identity: tuple[int, int]


class CreationArtifactRegistry:
    """Private candidate artifact storage with exact DB/blob identity verification."""

    def __init__(
        self,
        store: StudioStore,
        *,
        workspaces: CreationWorkspaceManager,
    ) -> None:
        self.store = store
        self.workspaces = workspaces
        self._blob_io = CreationAuthoringManager(store, workspaces=workspaces)

    def store_job_payload(
        self,
        document: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str, int, tuple[int, int]]:
        try:
            copied = copy.deepcopy(dict(document))
            subject = document_identity(copied)
            payload = canonical_json_bytes(copied)
        except (PhaseReportV3Error, TypeError, ValueError) as exc:
            raise invalid_state("Creation job payload is not a typed artifact") from exc
        digest = hashlib.sha256(payload).hexdigest()
        self._blob_io._store_blob(payload, digest)  # noqa: SLF001
        if self._blob_io._read_blob(digest, len(payload)) != payload:  # noqa: SLF001
            raise conflict("Creation job payload CAS bytes changed")
        info = path_file_stat(self.store.blob_path(digest))
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise conflict("Creation job payload CAS entry is unsafe")
        return subject, digest, len(payload), (int(info.st_dev), int(info.st_ino))

    def load_job_payload(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = self._blob_io._read_blob(  # noqa: SLF001
            str(row["document_blob_sha256"]), int(row["document_size"])
        )
        info = path_file_stat(self.store.blob_path(str(row["document_blob_sha256"])))
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (str(info.st_dev), str(info.st_ino)) != (str(row["blob_dev"]), str(row["blob_ino"]))
            or not hmac.compare_digest(
                hashlib.sha256(payload).hexdigest(), str(row["document_blob_sha256"])
            )
        ):
            raise invalid_state("Creation job payload private identity changed")
        try:
            document = decode_json_object(payload, source="creation job payload")
            subject = document_identity(document)
        except (RuntimeIOError, PhaseReportV3Error, TypeError, ValueError) as exc:
            raise invalid_state("Creation job payload is invalid") from exc
        expected = {
            "format": row["subject_format"],
            "format_version": row["subject_version"],
            "id": row["subject_id"],
            "content_hash": row["content_hash"],
        }
        if canonical_json_bytes(document) != payload or subject != expected:
            raise invalid_state("Creation job payload subject changed")
        return document

    @staticmethod
    def _validate_asset_process_retention_document(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_state("Asset process retention index is not an object")
        required = {
            "format",
            "format_version",
            "producer_job_id",
            "workspace_id",
            "authority",
            "outputs",
            "content_hash",
        }
        if set(value) != required:
            raise invalid_state("Asset process retention index fields changed")
        if (
            value["format"] != _ASSET_PROCESS_RETENTION_FORMAT
            or value["format_version"] != _ASSET_PROCESS_RETENTION_VERSION
            or not isinstance(value["producer_job_id"], str)
            or not isinstance(value["workspace_id"], str)
        ):
            raise invalid_state("Asset process retention identity is invalid")
        authority = value["authority"]
        if not isinstance(authority, dict) or set(authority) != {
            "root_generation",
            "source_revision",
            "workflow_status_hash",
            "artifact_snapshot_hash",
        }:
            raise invalid_state("Asset process retention authority is invalid")
        if (
            isinstance(authority["root_generation"], bool)
            or not isinstance(authority["root_generation"], int)
            or authority["root_generation"] < 0
            or not isinstance(authority["source_revision"], str)
            or SHA256_PATTERN.fullmatch(authority["source_revision"]) is None
            or (
                authority["workflow_status_hash"] is not None
                and (
                    not isinstance(authority["workflow_status_hash"], str)
                    or SHA256_PATTERN.fullmatch(authority["workflow_status_hash"]) is None
                )
            )
            or not isinstance(authority["artifact_snapshot_hash"], str)
            or SHA256_PATTERN.fullmatch(authority["artifact_snapshot_hash"]) is None
        ):
            raise invalid_state("Asset process retention authority values are invalid")
        outputs = value["outputs"]
        if not isinstance(outputs, list) or not 1 <= len(outputs) <= 64:
            raise invalid_state("Asset process retention output set is invalid")
        roles: list[str] = []
        locators: list[str] = []
        for output in outputs:
            if not isinstance(output, dict) or set(output) != {
                "candidate_artifact_id",
                "role",
                "media_type",
                "runtime_path",
                "locator",
                "sha256",
                "size_bytes",
                "blob_dev",
                "blob_ino",
            }:
                raise invalid_state("Asset process retention output fields changed")
            for field in (
                "candidate_artifact_id",
                "role",
                "media_type",
                "runtime_path",
                "locator",
            ):
                if not isinstance(output[field], str) or not output[field]:
                    raise invalid_state("Asset process retention output text is invalid")
            if (
                not isinstance(output["sha256"], str)
                or SHA256_PATTERN.fullmatch(output["sha256"]) is None
                or isinstance(output["size_bytes"], bool)
                or not isinstance(output["size_bytes"], int)
                or not 1 <= output["size_bytes"] <= 16 * 1024 * 1024
                or not isinstance(output["blob_dev"], str)
                or not output["blob_dev"].isdigit()
                or not isinstance(output["blob_ino"], str)
                or not output["blob_ino"].isdigit()
            ):
                raise invalid_state("Asset process retention output binding is invalid")
            roles.append(output["role"])
            locators.append(output["locator"])
        if len(set(roles)) != len(roles) or len(set(locators)) != len(locators):
            raise invalid_state("Asset process retention outputs are not unique")
        if (
            not isinstance(value["content_hash"], str)
            or SHA256_PATTERN.fullmatch(value["content_hash"]) is None
        ):
            raise invalid_state("Asset process retention content hash is invalid")
        if value["content_hash"] != canonical_payload_hash(value, hash_field="content_hash"):
            raise invalid_state("Asset process retention content hash changed")
        return copy.deepcopy(value)

    def prepare_asset_process_retention(
        self,
        *,
        job: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationBinaryOutput],
        processing_receipt: Mapping[str, Any],
    ) -> PreparedAssetProcessRetention:
        if job.get("operation") != "asset.process" or not outputs:
            raise invalid_state("Asset process retention source is invalid")
        raw_records = processing_receipt.get("outputs")
        if not isinstance(raw_records, list) or len(raw_records) != len(outputs):
            raise invalid_state("Asset process retention output coverage changed")
        output_by_locator = {output.locator: output for output in outputs}
        retained: list[dict[str, Any]] = []
        for record in raw_records:
            if not isinstance(record, Mapping):
                raise invalid_state("Asset process retention receipt output is invalid")
            locator = str(record.get("locator"))
            output = output_by_locator.get(locator)
            if (
                output is None
                or record.get("sha256") != output.sha256
                or record.get("size_bytes") != output.size
                or hashlib.sha256(output.payload).hexdigest() != output.sha256
                or len(output.payload) != output.size
            ):
                raise conflict("Asset process retention source bytes changed")
            self._blob_io._store_blob(output.payload, output.sha256)  # noqa: SLF001
            if self._blob_io._read_blob(output.sha256, output.size) != output.payload:  # noqa: SLF001
                raise conflict("Asset process retained binary CAS bytes changed")
            info = path_file_stat(self.store.blob_path(output.sha256))
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise conflict("Asset process retained binary CAS entry is unsafe")
            retained.append(
                {
                    field: copy.deepcopy(record[field])
                    for field in (
                        "candidate_artifact_id",
                        "role",
                        "media_type",
                        "runtime_path",
                        "locator",
                        "sha256",
                        "size_bytes",
                    )
                }
                | {"blob_dev": str(info.st_dev), "blob_ino": str(info.st_ino)}
            )
        if set(output_by_locator) != {str(record["locator"]) for record in raw_records}:
            raise invalid_state("Asset process retention contains extra binary outputs")
        document = {
            "format": _ASSET_PROCESS_RETENTION_FORMAT,
            "format_version": _ASSET_PROCESS_RETENTION_VERSION,
            "producer_job_id": str(job["job_id"]),
            "workspace_id": str(job["workspace_id"]),
            "authority": copy.deepcopy(dict(job["authority"])),
            "outputs": retained,
            "content_hash": "",
        }
        document["content_hash"] = canonical_payload_hash(document, hash_field="content_hash")
        checked = self._validate_asset_process_retention_document(document)
        payload = canonical_json_bytes(checked)
        digest = hashlib.sha256(payload).hexdigest()
        self._blob_io._store_blob(payload, digest)  # noqa: SLF001
        if self._blob_io._read_blob(digest, len(payload)) != payload:  # noqa: SLF001
            raise conflict("Asset process retention index CAS bytes changed")
        info = path_file_stat(self.store.blob_path(digest))
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise conflict("Asset process retention index CAS entry is unsafe")
        return PreparedAssetProcessRetention(
            document=checked,
            payload=payload,
            blob_sha256=digest,
            blob_identity=(int(info.st_dev), int(info.st_ino)),
        )

    def insert_asset_process_retention(
        self,
        job: Mapping[str, Any],
        prepared: PreparedAssetProcessRetention,
    ) -> None:
        document = self._validate_asset_process_retention_document(prepared.document)
        if (
            job.get("operation") != "asset.process"
            or document["producer_job_id"] != job.get("job_id")
            or document["workspace_id"] != job.get("workspace_id")
            or document["authority"] != job.get("authority")
            or canonical_json_bytes(document) != prepared.payload
            or hashlib.sha256(prepared.payload).hexdigest() != prepared.blob_sha256
        ):
            raise conflict("Asset process retention registry binding changed")
        self.store.connection.execute(
            "INSERT INTO creation_job_payloads "
            "(job_id, document_blob_sha256, document_size, blob_dev, blob_ino, "
            "subject_format, subject_version, subject_id, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job["job_id"],
                prepared.blob_sha256,
                len(prepared.payload),
                str(prepared.blob_identity[0]),
                str(prepared.blob_identity[1]),
                _ASSET_PROCESS_RETENTION_FORMAT,
                _ASSET_PROCESS_RETENTION_VERSION,
                job["job_id"],
                document["content_hash"],
            ),
        )

    def load_asset_process_retention(
        self,
        *,
        workspace_id: str,
        producer_job_id: str,
    ) -> dict[str, Any]:
        row = self.store.connection.execute(
            "SELECT payload.*, jobs.workspace_id, jobs.operation, jobs.state, jobs.progress, "
            "jobs.record_json FROM creation_job_payloads AS payload "
            "JOIN creation_jobs AS jobs ON jobs.job_id = payload.job_id "
            "WHERE payload.job_id = ?",
            (producer_job_id,),
        ).fetchone()
        if row is None:
            raise not_found("Asset process retained bytes were not found")
        payload = self._blob_io._read_blob(  # noqa: SLF001
            str(row["document_blob_sha256"]), int(row["document_size"])
        )
        info = path_file_stat(self.store.blob_path(str(row["document_blob_sha256"])))
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (str(info.st_dev), str(info.st_ino)) != (str(row["blob_dev"]), str(row["blob_ino"]))
            or hashlib.sha256(payload).hexdigest() != row["document_blob_sha256"]
        ):
            raise invalid_state("Asset process retention index private identity changed")
        try:
            document = decode_json_object(payload, source="asset process retention index")
        except RuntimeIOError as exc:
            raise invalid_state("Asset process retention index is invalid") from exc
        checked = self._validate_asset_process_retention_document(document)
        job = decode_object(row["record_json"], context="asset process retention producer")
        try:
            validate_studio_creation_job(job)
        except StudioContractError as exc:
            raise invalid_state("Asset process retention producer is invalid") from exc
        if (
            canonical_json_bytes(checked) != payload
            or row["subject_format"] != _ASSET_PROCESS_RETENTION_FORMAT
            or row["subject_version"] != _ASSET_PROCESS_RETENTION_VERSION
            or row["subject_id"] != producer_job_id
            or row["content_hash"] != checked["content_hash"]
            or row["workspace_id"] != workspace_id
            or row["operation"] != "asset.process"
            or row["state"] != "succeeded"
            or row["progress"] not in {"committed", "cleanup_pending"}
            or job["job_id"] != producer_job_id
            or job["workspace_id"] != workspace_id
            or job["operation"] != "asset.process"
            or job["authority"] != checked["authority"]
            or checked["producer_job_id"] != producer_job_id
            or checked["workspace_id"] != workspace_id
        ):
            raise invalid_state("Asset process retention producer binding changed")
        for output in checked["outputs"]:
            self._read_retained_asset_output(output)
        return checked

    def _read_retained_asset_output(self, output: Mapping[str, Any]) -> bytes:
        payload = self._blob_io._read_blob(  # noqa: SLF001
            str(output["sha256"]), int(output["size_bytes"])
        )
        info = path_file_stat(self.store.blob_path(str(output["sha256"])))
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (str(info.st_dev), str(info.st_ino))
            != (str(output["blob_dev"]), str(output["blob_ino"]))
            or hashlib.sha256(payload).hexdigest() != output["sha256"]
        ):
            raise invalid_state("Asset process retained binary private identity changed")
        return payload

    def read_retained_asset_output(
        self,
        retention: Mapping[str, Any],
        *,
        role: str,
    ) -> bytes:
        checked = self._validate_asset_process_retention_document(dict(retention))
        matches = [output for output in checked["outputs"] if output["role"] == role]
        if len(matches) != 1:
            raise not_found("Asset process retained output role was not found")
        return self._read_retained_asset_output(matches[0])

    def prepare_outputs(
        self,
        *,
        job: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationOutput],
        project: LoadedCreationProject,
        dependency_documents: Sequence[Mapping[str, Any]] = (),
        artifact_root: Path | None = None,
    ) -> tuple[PreparedCreationArtifact, ...]:
        documents: list[dict[str, Any]] = []
        for index, output in enumerate(outputs):
            try:
                document = decode_json_object(
                    output.payload,
                    source=f"creation job output {index}",
                )
                identity = document_identity(document)
            except (RuntimeIOError, PhaseReportV3Error, TypeError, ValueError) as exc:
                raise invalid_state("Creation worker output is not a typed artifact") from exc
            if (
                canonical_json_bytes(document) != output.payload
                or identity != output.subject
                or len(output.payload) != output.size
                or not hmac.compare_digest(
                    hashlib.sha256(output.payload).hexdigest(), output.sha256
                )
            ):
                raise invalid_state("Creation worker output identity changed")
            documents.append(document)
        formats = tuple(str(document["format"]) for document in documents)
        operation = job["operation"]
        direct_runtime_headless_documents: tuple[dict[str, Any], ...] = ()
        if operation == "creation.compile":
            if formats != _COMPILE_ORDER or dependency_documents or artifact_root is not None:
                raise invalid_state("Creation compiler output set is not exact")
            allowed_formats = _COMPILE_FORMATS
        elif operation == "artifact.admit":
            if (
                len(documents) != 1
                or formats[0] not in ADMISSION_FORMATS
                or artifact_root is not None
            ):
                raise invalid_state("Admitted artifact output set is not exact")
            allowed_formats = ADMISSION_FORMATS
        elif operation == "asset.process":
            if (
                formats not in {_ASSET_PROCESS_SUCCESS_ORDER, _ASSET_PROCESS_FAILURE_ORDER}
                or not dependency_documents
                or artifact_root is None
            ):
                raise invalid_state("Asset processing output set is not exact")
            allowed_formats = None
        elif operation == "asset.release.seal":
            if formats != _ASSET_RELEASE_ORDER or not dependency_documents or artifact_root is None:
                raise invalid_state("Asset release output set is not exact")
            allowed_formats = None
        elif operation == "asset.release.authorize":
            review_documents = tuple(
                document
                for document in dependency_documents
                if document.get("format") == "world-forge.asset_qa_review_receipt"
            )
            release_lineage_documents = tuple(
                document
                for document in dependency_documents
                if document.get("format") != "world-forge.asset_qa_review_receipt"
            )
            if (
                formats != _ASSET_RELEASE_AUTHORIZE_ORDER
                or not review_documents
                or not release_lineage_documents
                or artifact_root is None
                or tuple(document_identity(document) for document in review_documents)
                != tuple(item["subject"] for item in job["inputs"])
            ):
                raise invalid_state("Asset release authority output set is not exact")
            allowed_formats = None
        elif operation == "asset.qa.review":
            if (
                formats != _ASSET_QA_REVIEW_ORDER
                or not dependency_documents
                or artifact_root is None
                or tuple(document_identity(document) for document in dependency_documents)
                != tuple(item["subject"] for item in job["inputs"])
            ):
                raise invalid_state("Asset QA review output set is not exact")
            allowed_formats = None
        elif operation == "runtime.compose":
            direct_dependencies = [
                [
                    document
                    for document in dependency_documents
                    if document.get("format") == expected_format
                ]
                for expected_format in _RUNTIME_COMPOSE_INPUT_ORDER
            ]
            if (
                formats != _RUNTIME_COMPOSE_ORDER
                or any(len(matches) != 1 for matches in direct_dependencies)
                or artifact_root is None
            ):
                raise invalid_state("Runtime composition output set is not exact")
            direct_runtime_documents = tuple(matches[0] for matches in direct_dependencies)
            if tuple(document_identity(document) for document in direct_runtime_documents) != tuple(
                item["subject"] for item in job["inputs"]
            ):
                raise invalid_state("Runtime composition direct inputs changed")
            allowed_formats = None
        elif operation == "runtime.bundle.build":
            direct_dependencies = [
                [
                    document
                    for document in dependency_documents
                    if document.get("format") == expected_format
                ]
                for expected_format in _RUNTIME_BUNDLE_INPUT_ORDER
            ]
            if (
                formats != _RUNTIME_BUNDLE_ORDER
                or any(len(matches) != 1 for matches in direct_dependencies)
                or len(dependency_documents) != len(_RUNTIME_BUNDLE_INPUT_ORDER)
                or artifact_root is None
            ):
                raise invalid_state("Runtime bundle output set is not exact")
            direct_runtime_documents = tuple(matches[0] for matches in direct_dependencies)
            if tuple(document_identity(document) for document in direct_runtime_documents) != tuple(
                item["subject"] for item in job["inputs"]
            ):
                raise invalid_state("Runtime bundle direct inputs changed")
            allowed_formats = None
        elif operation == "runtime.headless.verify":
            direct_dependencies = [
                [
                    document
                    for document in dependency_documents
                    if document.get("format") == expected_format
                ]
                for expected_format in _RUNTIME_HEADLESS_INPUT_ORDER
            ]
            if (
                formats != _RUNTIME_HEADLESS_ORDER
                or any(len(matches) != 1 for matches in direct_dependencies)
                or artifact_root is None
            ):
                raise invalid_state("Runtime headless output set is not exact")
            direct_runtime_headless_documents = tuple(matches[0] for matches in direct_dependencies)
            if tuple(
                document_identity(document) for document in direct_runtime_headless_documents
            ) != tuple(item["subject"] for item in job["inputs"]):
                raise invalid_state("Runtime headless direct inputs changed")
            allowed_formats = None
        elif operation == "game.materialization.bundle.build":
            direct_dependencies = [
                [
                    document
                    for document in dependency_documents
                    if document.get("format") == expected_format
                ]
                for expected_format in _MATERIALIZATION_BUNDLE_INPUT_ORDER
            ]
            if (
                formats != _MATERIALIZATION_BUNDLE_ORDER
                or any(len(matches) != 1 for matches in direct_dependencies)
                or len(dependency_documents) != len(_MATERIALIZATION_BUNDLE_INPUT_ORDER)
                or artifact_root is None
            ):
                raise invalid_state("Materialization bundle output set is not exact")
            direct_runtime_documents = tuple(matches[0] for matches in direct_dependencies)
            if tuple(document_identity(document) for document in direct_runtime_documents) != tuple(
                item["subject"] for item in job["inputs"]
            ):
                raise invalid_state("Materialization bundle direct inputs changed")
            allowed_formats = None
        elif operation == "game.materialize":
            direct_dependencies = [
                [
                    document
                    for document in dependency_documents
                    if document.get("format") == expected_format
                ]
                for expected_format in _STANDALONE_GAME_INPUT_ORDER
            ]
            if (
                formats != _STANDALONE_GAME_ORDER
                or any(len(matches) != 1 for matches in direct_dependencies)
                or len(dependency_documents) != len(_STANDALONE_GAME_INPUT_ORDER)
                or artifact_root is None
            ):
                raise invalid_state("Standalone game output set is not exact")
            direct_runtime_documents = tuple(matches[0] for matches in direct_dependencies)
            if tuple(document_identity(document) for document in direct_runtime_documents) != tuple(
                item["subject"] for item in job["inputs"]
            ):
                raise invalid_state("Standalone game direct inputs changed")
            allowed_formats = None
        elif operation == "game.package":
            direct_dependencies = [
                [
                    document
                    for document in dependency_documents
                    if document.get("format") == expected_format
                ]
                for expected_format in _GAME_PACKAGE_INPUT_ORDER
            ]
            if (
                formats != _GAME_PACKAGE_ORDER
                or any(len(matches) != 1 for matches in direct_dependencies)
                or len(dependency_documents) != len(_GAME_PACKAGE_INPUT_ORDER)
                or artifact_root is None
            ):
                raise invalid_state("Game package output set is not exact")
            direct_runtime_documents = tuple(matches[0] for matches in direct_dependencies)
            if tuple(document_identity(document) for document in direct_runtime_documents) != tuple(
                item["subject"] for item in job["inputs"]
            ):
                raise invalid_state("Game package direct inputs changed")
            allowed_formats = None
        elif operation == "game.package.extract":
            direct_dependencies = [
                [
                    document
                    for document in dependency_documents
                    if document.get("format") == expected_format
                ]
                for expected_format in _GAME_PACKAGE_EXTRACTION_INPUT_ORDER
            ]
            if (
                formats != _GAME_PACKAGE_EXTRACTION_ORDER
                or any(len(matches) != 1 for matches in direct_dependencies)
                or len(dependency_documents) != len(_GAME_PACKAGE_EXTRACTION_INPUT_ORDER)
                or artifact_root is None
            ):
                raise invalid_state("Game package extraction output set is not exact")
            direct_runtime_documents = tuple(matches[0] for matches in direct_dependencies)
            if tuple(document_identity(document) for document in direct_runtime_documents) != tuple(
                item["subject"] for item in job["inputs"]
            ):
                raise invalid_state("Game package extraction direct inputs changed")
            allowed_formats = None
        else:
            raise invalid_state("Creation worker output operation is unsupported")
        try:
            checked = (
                tuple([*dependency_documents, *documents])
                if operation
                in {
                    "runtime.bundle.build",
                    "game.materialization.bundle.build",
                    "game.materialize",
                    "game.package",
                    "game.package.extract",
                }
                else validate_artifact_documents(
                    project,
                    [*dependency_documents, *documents],
                    allowed_formats=allowed_formats,
                )
            )
        except (PhaseReportV3Error, TypeError, ValueError) as exc:
            raise invalid_state("Creation worker output closure is not integral") from exc
        if tuple(checked[-len(documents) :]) != tuple(documents):
            raise invalid_state("Creation worker validation changed output documents")
        if operation == "asset.process":
            try:
                lineage = _asset_lineage_arguments(
                    {"lineage_documents": list(dependency_documents)},
                    artifact_root,
                )
                recipe = validate_asset_processing_recipe(documents[0], **lineage)
                receipt = validate_asset_processing_receipt(
                    documents[1],
                    recipe=recipe,
                    **lineage,
                )
                if len(documents) == 3:
                    if receipt["status"] != "completed":
                        raise GenericAssetProcessingError(
                            "processing_status_contradiction",
                            "successful asset processing output has a failed receipt",
                        )
                    validate_asset_qa_report(
                        documents[2],
                        recipe=recipe,
                        processing_receipt=receipt,
                        **lineage,
                    )
                elif receipt["status"] != "failed":
                    raise GenericAssetProcessingError(
                        "processing_status_contradiction",
                        "controlled asset processing failure has a completed receipt",
                    )
            except (GenericAssetProcessingError, KeyError, TypeError, ValueError) as exc:
                raise invalid_state("Asset processing output bytes are not integral") from exc
        elif operation == "asset.release.seal":
            try:
                _lineage, roots, records = _asset_release_lineage(
                    project,
                    tuple(dict(item) for item in dependency_documents),
                )
                expected_manifest = build_asset_manifest(
                    roots["world-forge.gamepack"],
                    roots["world-forge.asset_subject"],
                    roots["world-forge.asset_target"],
                    roots["world-forge.asset_style"],
                    roots["world-forge.asset_inventory"],
                    manifest_id=documents[0]["manifest_id"],
                    state="release_ready",
                    asset_records=records,
                    artifact_root=artifact_root,
                )
                expected_assetpack = build_generic_assetpack_manifest(
                    expected_manifest,
                    gamepack=roots["world-forge.gamepack"],
                    subject=roots["world-forge.asset_subject"],
                    target=roots["world-forge.asset_target"],
                    style=roots["world-forge.asset_style"],
                    inventory=roots["world-forge.asset_inventory"],
                    asset_records=records,
                    artifact_root=artifact_root,
                )
                if documents != [expected_manifest, expected_assetpack]:
                    raise ValueError("asset release output rebuild changed")
            except (
                GenericAssetProcessingError,
                GenericAssetpackError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                raise invalid_state("Asset release output bytes are not integral") from exc
        elif operation == "asset.release.authorize":
            try:
                from worldforge.studio.creation_asset_authority import (
                    StudioAssetAuthorityResolver,
                )

                resolver = StudioAssetAuthorityResolver(self.store, artifacts=self)
                reviews = [
                    verify_asset_qa_review(document, resolver=resolver)
                    for document in review_documents
                ]
                (
                    expected_manifest,
                    expected_assetpack,
                    expected_authority,
                    _expected_blockers,
                ) = _build_asset_release_authorize_outputs(
                    project=project,
                    lineage_documents=tuple(dict(item) for item in release_lineage_documents),
                    reviews=reviews,
                    manifest_id=str(job["operation_params"]["manifest_id"]),
                    assetpack_id=str(job["operation_params"]["assetpack_id"]),
                    release_authority_id=str(job["operation_params"]["release_authority_id"]),
                    blockers=job["operation_params"]["blockers"],
                    authority={
                        "workspace_id": job["workspace_id"],
                        **copy.deepcopy(dict(job["authority"])),
                        "producer_job_id": job["job_id"],
                        "producer_operation": "asset.release.authorize",
                        "producer_output_position": 2,
                    },
                    artifact_root=artifact_root,
                )
                if documents != [
                    expected_manifest,
                    expected_assetpack,
                    expected_authority,
                ]:
                    raise ValueError("asset release authority output rebuild changed")
            except (
                GenericAssetAuthorityError,
                GenericAssetProcessingError,
                GenericAssetpackError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                raise invalid_state(
                    "Asset release authority output bytes are not integral"
                ) from exc
        elif operation == "asset.qa.review":
            by_format = {str(document["format"]): document for document in dependency_documents}
            review = documents[0]
            try:
                specification = by_format["world-forge.asset_spec"]
                processing_receipt = by_format["world-forge.asset_processing_receipt"]
                qa_report = by_format["world-forge.asset_qa_report"]
                reviewed = review["reviewed_output"]
                retained_output = read_verified_artifact_bytes(
                    artifact_root,
                    reviewed["locator"],
                    expected_sha256=reviewed["sha256"],
                    expected_size_bytes=reviewed["size_bytes"],
                    limit=16 * 1024 * 1024,
                )
                expected = build_asset_qa_review_receipt(
                    qa_report,
                    specification,
                    processing_receipt,
                    review_receipt_id=str(job["operation_params"]["review_receipt_id"]),
                    output_role=str(job["operation_params"]["output_role"]),
                    decisions=job["operation_params"]["decisions"],
                    blockers=job["operation_params"]["blockers"],
                    authority={
                        "workspace_id": job["workspace_id"],
                        **copy.deepcopy(dict(job["authority"])),
                        "producer_job_id": job["job_id"],
                        "producer_operation": "asset.qa.review",
                        "producer_output_position": 0,
                    },
                    retained_output=retained_output,
                )
                if documents != [expected]:
                    raise ValueError("asset QA review output rebuild changed")
            except (
                GenericAssetAuthorityError,
                GenericAssetProductionError,
                KeyError,
                OSError,
                TypeError,
                ValueError,
            ) as exc:
                raise invalid_state("Asset QA review output bytes are not integral") from exc
        elif operation == "runtime.compose":
            gamepack, inventory, assetpack = direct_runtime_documents
            runtime_root = Path(__file__).resolve().parents[2]
            try:
                adapters = build_builtin_runtime_adapters()
                expected_snapshot = build_game_runtime_snapshot(
                    runtime_root / "gamepack_runtime",
                    adapter_runtime_root=runtime_root / "gamepack_raylib_2d",
                    adapters=adapters,
                )
                expected_registry = build_runtime_adapter_registry(
                    snapshot=expected_snapshot,
                    adapters=adapters,
                )
                readiness = resolve_runtime_build_readiness(
                    gamepack,
                    registry=expected_registry,
                    snapshot=expected_snapshot,
                )
                if readiness["status"] != "materialization_ready":
                    raise RuntimeContractError(
                        "runtime_build_unsupported",
                        "runtime composition is not materialization ready",
                    )
                expected_composition = build_game_runtime_composition(
                    gamepack,
                    inventory,
                    artifact_root,
                    registry=expected_registry,
                    snapshot=expected_snapshot,
                )
                expected_support = build_runtime_support_report(
                    expected_composition,
                    gamepack=gamepack,
                    registry=expected_registry,
                    snapshot=expected_snapshot,
                    evidence=[],
                )
                if documents != [
                    expected_snapshot,
                    expected_registry,
                    expected_composition,
                    expected_support,
                ]:
                    raise ValueError("runtime composition output rebuild changed")
            except (RuntimeContractError, KeyError, TypeError, ValueError) as exc:
                raise invalid_state("Runtime composition output bytes are not integral") from exc
        elif operation == "runtime.bundle.build":
            (
                gamepack,
                inventory,
                assetpack,
                snapshot,
                registry,
                composition,
                support,
            ) = direct_runtime_documents
            try:
                expected_manifest, _files = build_game_runtime_bundle_manifest_from_objects(
                    gamepack=gamepack,
                    inventory=inventory,
                    assetpack=assetpack,
                    assetpack_root=artifact_root,
                    snapshot=snapshot,
                    registry=registry,
                    composition=composition,
                    support_report=support,
                )
                if documents != [expected_manifest]:
                    raise ValueError("runtime bundle output rebuild changed")
            except (KeyError, TypeError, ValueError) as exc:
                raise invalid_state("Runtime bundle output bytes are not integral") from exc
        elif operation == "runtime.headless.verify":
            (
                gamepack,
                inventory,
                assetpack,
                release,
                snapshot,
                registry,
                composition,
                runtime_bundle,
                _script,
            ) = direct_runtime_headless_documents
            assetpack_handle = None
            runtime_bundle_handle = None
            evidence_set = None
            try:
                from worldforge.studio.creation_asset_authority import (
                    StudioAssetAuthorityResolver,
                )

                by_identity = {
                    _identity_key(document_identity(document)): document
                    for document in dependency_documents
                }
                manifest = by_identity[_identity_key(release["candidate_manifest"])]
                resolver = StudioAssetAuthorityResolver(self.store, artifacts=self)
                reviews = [
                    verify_asset_qa_review(
                        by_identity[_identity_key(identity)],
                        resolver=resolver,
                    )
                    for identity in release["qa_reviews"]
                ]
                assetpack_handle = verify_generic_assetpack(
                    artifact_root / "assetpack",
                    expected_content_hash=str(assetpack["content_hash"]),
                )
                if assetpack_handle.manifest != assetpack:
                    raise ValueError("runtime headless assetpack manifest changed")
                release_handle = verify_asset_release_authority(
                    release,
                    manifest=manifest,
                    assetpack=assetpack,
                    reviews=reviews,
                    resolver=resolver,
                )
                if not release_handle.authorized:
                    raise ValueError("runtime headless asset release is not authorized")
                runtime_bundle_handle = verify_game_runtime_bundle(
                    artifact_root / "runtime-bundle",
                    expected_content_hash=str(runtime_bundle["content_hash"]),
                )
                if runtime_bundle_handle.manifest != runtime_bundle:
                    raise ValueError("runtime headless runtime bundle manifest changed")
                initial = initialize_runtime_support_authority(
                    gamepack=gamepack,
                    inventory=inventory,
                    composition=composition,
                    registry=registry,
                    snapshot=snapshot,
                    verified_assetpack=assetpack_handle,
                    asset_release_authority=release_handle,
                )
                evidence_set = verify_headless_evidence_set(
                    artifact_root / "headless-evidence",
                    bundle_root=artifact_root / "runtime-bundle",
                )
                if (
                    evidence_set.manifest["runtime_evidence"]["platform"]["platform_id"]
                    != job["operation_params"]["platform_id"]
                ):
                    raise ValueError("runtime headless platform changed")
                authority = attach_verified_headless_evidence(
                    initial,
                    evidence_set,
                    bundle_root=artifact_root / "runtime-bundle",
                )
                evidence = derive_runtime_evidence(authority)
                support = derive_runtime_support_report(authority)
                if (
                    len(evidence) != 1
                    or documents != [authority.document, evidence[0], support]
                    or authority.document["supported"] is not False
                    or authority.document["release_status"] != "blocked"
                    or support["supported"] is not False
                    or support["dimensions"]["release"] != "blocked"
                ):
                    raise ValueError("runtime headless output rebuild changed")
            except (
                GameRuntimeBundleError,
                GenericAssetAuthorityError,
                GenericAssetpackError,
                GenericHeadlessError,
                KeyError,
                RuntimeSupportAuthorityError,
                StudioError,
                TypeError,
                ValueError,
            ) as exc:
                raise invalid_state("Runtime headless output bytes are not integral") from exc
            finally:
                if evidence_set is not None:
                    evidence_set.close()
                if runtime_bundle_handle is not None:
                    runtime_bundle_handle.close()
                if assetpack_handle is not None:
                    assetpack_handle.close()
        elif operation == "game.materialization.bundle.build":
            try:
                expected_manifest, _files = build_game_materialization_bundle_manifest(
                    runtime_bundle_root=artifact_root,
                )
                if documents != [expected_manifest]:
                    raise ValueError("materialization bundle output rebuild changed")
            except (GameMaterializationBundleError, KeyError, TypeError, ValueError) as exc:
                raise invalid_state("Materialization bundle output bytes are not integral") from exc
        elif operation == "game.materialize":
            source_manifest = direct_runtime_documents[0]
            try:
                with verify_game_materialization_bundle(
                    artifact_root,
                    expected_content_hash=str(source_manifest["content_hash"]),
                ) as source:
                    if source.manifest != source_manifest:
                        raise ValueError("standalone source manifest changed")
                    expected_manifest, _lock, _platform = build_standalone_game_documents(source)
                if documents != [expected_manifest]:
                    raise ValueError("standalone game output rebuild changed")
            except (
                GameMaterializationBundleError,
                StandaloneGameError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                raise invalid_state("Standalone game output bytes are not integral") from exc
        elif operation == "game.package":
            source_manifest = direct_runtime_documents[0]
            try:
                with verify_standalone_game(
                    artifact_root,
                    expected_content_hash=str(source_manifest["content_hash"]),
                ) as source:
                    if source.manifest != source_manifest:
                        raise ValueError("game package standalone manifest changed")
                    expected_package = build_game_package_from_files(source.files)
                if documents != [expected_package.manifest]:
                    raise ValueError("game package output rebuild changed")
            except (
                GamePackageError,
                StandaloneGameError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                raise invalid_state("Game package output bytes are not integral") from exc
        elif operation == "game.package.extract":
            source_manifest = direct_runtime_documents[0]
            try:
                verified = verify_game_package_file(artifact_root / "game_package_archive.wfgame")
                try:
                    if verified.manifest != source_manifest:
                        raise ValueError("game package extraction source manifest changed")
                    expected_evidence = build_game_package_extraction_evidence(
                        verified.manifest,
                        archive_sha256=verified.archive_sha256,
                        archive_size_bytes=len(verified.archive_bytes),
                    )
                finally:
                    verified.close()
                if documents != [expected_evidence]:
                    raise ValueError("game package extraction evidence rebuild changed")
            except (
                GamePackageError,
                GamePackageExtractionEvidenceError,
                KeyError,
                OSError,
                TypeError,
                ValueError,
            ) as exc:
                raise invalid_state(
                    "Game package extraction output bytes are not integral"
                ) from exc

        identity_to_artifact: dict[tuple[str, int, str, str], str] = {}
        for document in (
            project.project,
            project.profile,
            project.manifest,
            *project.world_modules,
            *project.activity_modules,
            *project.narrative_modules,
            *project.system_modules,
            *project.logic_modules,
        ):
            identity = document_identity(document)
            identity_to_artifact[_identity_key(identity)] = artifact_id_for_identity(identity)
        for item in job["inputs"]:
            identity_to_artifact[_identity_key(item["subject"])] = item["artifact_id"]
        for document in dependency_documents:
            identity = document_identity(document)
            identity_to_artifact[_identity_key(identity)] = artifact_id_for_identity(identity)
        for document in documents:
            identity = document_identity(document)
            identity_to_artifact[_identity_key(identity)] = artifact_id_for_identity(identity)

        dependency_rows: list[tuple[tuple[str, dict[str, Any]], ...]] = []
        dependent_counts: Counter[str] = Counter()
        for document in documents:
            rows: list[tuple[str, dict[str, Any]]] = []
            try:
                identities = artifact_dependency_identities(document)
            except PhaseReportV3Error as exc:
                raise invalid_state("Creation output dependency identities are invalid") from exc
            for identity in identities:
                artifact_id = identity_to_artifact.get(_identity_key(identity))
                if artifact_id is None:
                    raise invalid_state("Creation output dependency is not an exact job input")
                rows.append((artifact_id, identity))
                dependent_counts[artifact_id] += 1
            dependency_rows.append(tuple(rows))

        authority = {
            "workspace_id": job["workspace_id"],
            "root_generation": job["authority"]["root_generation"],
            "source_revision": job["authority"]["source_revision"],
            "workflow_status_hash": job["authority"]["workflow_status_hash"],
        }
        prepared: list[PreparedCreationArtifact] = []
        for _position, (output, document, dependencies) in enumerate(
            zip(outputs, documents, dependency_rows, strict=True)
        ):
            subject = document_identity(document)
            artifact_id = artifact_id_for_identity(subject)
            self._blob_io._store_blob(output.payload, output.sha256)  # noqa: SLF001
            confirmed = self._blob_io._read_blob(output.sha256, output.size)  # noqa: SLF001
            if confirmed != output.payload:
                raise conflict("Creation artifact CAS bytes changed")
            info = path_file_stat(self.store.blob_path(output.sha256))
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise conflict("Creation artifact CAS entry is unsafe")
            record = {
                "format": CREATION_ARTIFACT_FORMAT,
                "format_version": 1,
                "artifact_id": artifact_id,
                "subject": subject,
                "lifecycle": "candidate",
                "roles": artifact_roles(document),
                "producer": {
                    "kind": "future_candidate",
                    "phase_id": None,
                    "reference_id": job["job_id"],
                },
                "references": {
                    "dependency_count": len(dependencies),
                    "dependent_count": dependent_counts[artifact_id],
                },
                "authority": authority,
                "record_hash": "",
            }
            record["record_hash"] = canonical_payload_hash(record, hash_field="record_hash")
            try:
                validate_studio_creation_artifact(record)
            except StudioContractError as exc:
                raise invalid_state("Creation artifact candidate record is invalid") from exc
            prepared.append(
                PreparedCreationArtifact(
                    artifact_id=artifact_id,
                    subject=subject,
                    document=document,
                    payload=output.payload,
                    blob_sha256=output.sha256,
                    blob_identity=(int(info.st_dev), int(info.st_ino)),
                    roles=tuple(record["roles"]),
                    dependencies=dependencies,
                    record=record,
                )
            )
        if len(prepared) > MAX_CREATION_ARTIFACTS:
            raise invalid_state("Creation job produced too many artifacts")
        return tuple(prepared)

    def insert_prepared(
        self,
        job: Mapping[str, Any],
        prepared: Sequence[PreparedCreationArtifact],
        *,
        created_at: str,
    ) -> None:
        for position, artifact in enumerate(prepared):
            subject = artifact.subject
            self.store.connection.execute(
                "INSERT INTO creation_artifacts "
                "(artifact_id, workspace_id, lifecycle, subject_format, subject_version, "
                "subject_id, content_hash, roles_json, record_json, document_blob_sha256, "
                "document_size, blob_dev, blob_ino, producer_job_id, producer_operation, "
                "producer_output_position, root_generation, source_revision, "
                "workflow_status_hash, input_artifact_snapshot_hash, generation, created_at) "
                "VALUES (?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "0, ?)",
                (
                    artifact.artifact_id,
                    job["workspace_id"],
                    subject["format"],
                    subject["format_version"],
                    subject["id"],
                    subject["content_hash"],
                    encode_json(list(artifact.roles)),
                    encode_json(artifact.record),
                    artifact.blob_sha256,
                    len(artifact.payload),
                    str(artifact.blob_identity[0]),
                    str(artifact.blob_identity[1]),
                    job["job_id"],
                    job["operation"],
                    position,
                    job["authority"]["root_generation"],
                    job["authority"]["source_revision"],
                    job["authority"]["workflow_status_hash"],
                    job["authority"]["artifact_snapshot_hash"],
                    created_at,
                ),
            )
            self.store.connection.execute(
                "INSERT INTO creation_job_outputs "
                "(job_id, position, artifact_id, subject_format, subject_version, "
                "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    job["job_id"],
                    position,
                    artifact.artifact_id,
                    subject["format"],
                    subject["format_version"],
                    subject["id"],
                    subject["content_hash"],
                ),
            )
            for dependency_position, (dependency_id, dependency) in enumerate(
                artifact.dependencies
            ):
                self.store.connection.execute(
                    "INSERT INTO creation_artifact_dependencies "
                    "(workspace_id, artifact_id, position, dependency_artifact_id, "
                    "subject_format, subject_version, subject_id, content_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        job["workspace_id"],
                        artifact.artifact_id,
                        dependency_position,
                        dependency_id,
                        dependency["format"],
                        dependency["format_version"],
                        dependency["id"],
                        dependency["content_hash"],
                    ),
                )

    def validate_cleanup_outputs(
        self,
        job: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationOutput],
    ) -> None:
        """Bind mutable cleanup evidence to the committed artifact projection."""

        result = job.get("result")
        expected_ids = result.get("output_artifact_ids") if isinstance(result, Mapping) else None
        rows = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE producer_job_id = ? "
            "ORDER BY producer_output_position",
            (job["job_id"],),
        ).fetchall()
        if (
            job.get("state") != "succeeded"
            or job.get("progress") != "cleanup_pending"
            or not isinstance(expected_ids, list)
            or len(rows) != len(outputs)
            or len(expected_ids) != len(outputs)
        ):
            raise invalid_state("Creation job trusted cleanup projection diverged")
        for position, (row, output, expected_id) in enumerate(
            zip(rows, outputs, expected_ids, strict=True)
        ):
            stored = self._validated_row(row)
            trusted_payload = canonical_json_bytes(stored.document)
            if (
                int(row["producer_output_position"]) != position
                or row["workspace_id"] != job["workspace_id"]
                or row["producer_operation"] != job["operation"]
                or row["artifact_id"] != expected_id
                or stored.record["artifact_id"] != expected_id
                or output.subject != stored.record["subject"]
                or output.sha256 != row["document_blob_sha256"]
                or output.size != int(row["document_size"])
                or output.payload != trusted_payload
            ):
                raise invalid_state("Creation job trusted cleanup output diverged")

    def list_stored(self, workspace_id: str) -> tuple[StoredCreationArtifact, ...]:
        rows = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? ORDER BY artifact_id",
            (workspace_id,),
        ).fetchall()
        if len(rows) > MAX_CREATION_ARTIFACTS:
            raise invalid_state("Stored creation artifact registry exceeds its bound")
        return tuple(self._validated_row(row) for row in rows)

    def validate_recomputed_snapshot(
        self,
        *,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot_hash: str,
    ) -> None:
        producer_rows = self.store.connection.execute(
            "SELECT jobs.* FROM creation_jobs AS jobs WHERE jobs.workspace_id = ? "
            "AND (jobs.state = 'succeeded' OR EXISTS ("
            "SELECT 1 FROM creation_artifacts AS artifacts "
            "WHERE artifacts.producer_job_id = jobs.job_id)) "
            "ORDER BY jobs.sequence",
            (workspace_id,),
        ).fetchall()
        latest_producer: tuple[sqlite3.Row, dict[str, Any]] | None = None
        for producer in producer_rows:
            producer_record = decode_object(
                producer["record_json"], context="creation snapshot producer job"
            )
            try:
                validate_studio_creation_job(producer_record)
            except StudioContractError as exc:
                raise invalid_state("Stored creation snapshot producer is invalid") from exc
            if (
                producer_record["job_id"] != producer["job_id"]
                or producer_record["workspace_id"] != producer["workspace_id"]
                or producer_record["operation"] != producer["operation"]
                or producer_record["state"] != producer["state"]
                or producer_record["progress"] != producer["progress"]
                or producer_record["generation"] != producer["generation"]
            ):
                raise invalid_state("Stored creation snapshot producer projection diverged")
            _validate_creation_job_result_projection(
                self.store,
                producer,
                producer_record,
                artifacts=self,
                allow_registry_committing=True,
            )
            candidate = self.store.connection.execute(
                "SELECT 1 FROM creation_artifacts WHERE producer_job_id = ? LIMIT 1",
                (producer["job_id"],),
            ).fetchone()
            if candidate is not None:
                latest_producer = producer, producer_record
        if latest_producer is None:
            return
        producer, producer_record = latest_producer
        if producer_record["state"] == "running":
            if producer_record["progress"] != "registry_committing":
                raise invalid_state("Stored creation snapshot producer state diverged")
            return
        producer_authority = producer_record["authority"]
        if (
            producer["workspace_id"] == authority["workspace_id"]
            and producer_authority["root_generation"] == authority["root_generation"]
            and hmac.compare_digest(
                producer_authority["source_revision"], authority["source_revision"]
            )
            and producer_authority["workflow_status_hash"] == authority["workflow_status_hash"]
            and not hmac.compare_digest(
                producer_record["result"]["artifact_snapshot_hash"], snapshot_hash
            )
        ):
            raise invalid_state("Stored creation job recomputed snapshot diverged")

    def get_document(self, workspace_id: str, artifact_id: str) -> dict[str, Any]:
        row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, artifact_id),
        ).fetchone()
        if row is None:
            raise not_found("Creation artifact candidate was not found")
        return self._validated_row(row).document

    def _validated_row(self, row: sqlite3.Row) -> StoredCreationArtifact:
        record = decode_object(row["record_json"], context="creation artifact candidate")
        try:
            validate_studio_creation_artifact(record)
        except StudioContractError as exc:
            raise invalid_state("Stored creation artifact record is invalid") from exc
        subject = record["subject"]
        roles_wrapper = decode_object(
            '{"roles":' + row["roles_json"] + "}",
            context="creation artifact roles",
        )
        if (
            record["artifact_id"] != row["artifact_id"]
            or record["lifecycle"] != "candidate"
            or subject["format"] != row["subject_format"]
            or subject["format_version"] != row["subject_version"]
            or subject["id"] != row["subject_id"]
            or subject["content_hash"] != row["content_hash"]
            or set(roles_wrapper) != {"roles"}
            or record["roles"] != roles_wrapper["roles"]
            or record["authority"]
            != {
                "workspace_id": row["workspace_id"],
                "root_generation": row["root_generation"],
                "source_revision": row["source_revision"],
                "workflow_status_hash": row["workflow_status_hash"],
            }
            or record["producer"]
            != {
                "kind": "future_candidate",
                "phase_id": None,
                "reference_id": row["producer_job_id"],
            }
        ):
            raise invalid_state("Stored creation artifact DB projection diverged")
        payload = self._blob_io._read_blob(  # noqa: SLF001
            row["document_blob_sha256"], int(row["document_size"])
        )
        info = path_file_stat(self.store.blob_path(row["document_blob_sha256"]))
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (str(info.st_dev), str(info.st_ino)) != (row["blob_dev"], row["blob_ino"])
            or not hmac.compare_digest(
                hashlib.sha256(payload).hexdigest(), row["document_blob_sha256"]
            )
        ):
            raise invalid_state("Stored creation artifact private identity changed")
        try:
            document = decode_json_object(payload, source="stored creation artifact")
            identity = document_identity(document)
        except (RuntimeIOError, PhaseReportV3Error, TypeError, ValueError) as exc:
            raise invalid_state("Stored creation artifact document is invalid") from exc
        if canonical_json_bytes(document) != payload or identity != subject:
            raise invalid_state("Stored creation artifact document identity changed")
        output = self.store.connection.execute(
            "SELECT * FROM creation_job_outputs WHERE job_id = ? AND position = ?",
            (row["producer_job_id"], row["producer_output_position"]),
        ).fetchone()
        if (
            output is None
            or output["artifact_id"] != row["artifact_id"]
            or output["subject_format"] != subject["format"]
            or output["subject_version"] != subject["format_version"]
            or output["subject_id"] != subject["id"]
            or output["content_hash"] != subject["content_hash"]
        ):
            raise invalid_state("Stored creation artifact producer projection diverged")
        producer = self.store.connection.execute(
            "SELECT job_id, workspace_id, operation, state, progress, generation, record_json "
            "FROM creation_jobs WHERE job_id = ?",
            (row["producer_job_id"],),
        ).fetchone()
        if producer is None:
            raise invalid_state("Stored creation artifact producer is unavailable")
        producer_record = decode_object(
            producer["record_json"], context="creation artifact producer job"
        )
        try:
            validate_studio_creation_job(producer_record)
        except StudioContractError as exc:
            raise invalid_state("Stored creation artifact producer record is invalid") from exc
        valid_visibility = (
            producer["state"] == "succeeded"
            and producer["progress"] in {"committed", "cleanup_pending"}
        ) or (producer["state"] == "running" and producer["progress"] == "registry_committing")
        expected_producer_authority = {
            "root_generation": row["root_generation"],
            "source_revision": row["source_revision"],
            "workflow_status_hash": row["workflow_status_hash"],
            "artifact_snapshot_hash": row["input_artifact_snapshot_hash"],
        }
        if (
            producer["job_id"] != row["producer_job_id"]
            or producer["workspace_id"] != row["workspace_id"]
            or producer["operation"] != row["producer_operation"]
            or producer_record["job_id"] != producer["job_id"]
            or producer_record["workspace_id"] != producer["workspace_id"]
            or producer_record["operation"] != producer["operation"]
            or producer_record["state"] != producer["state"]
            or producer_record["progress"] != producer["progress"]
            or producer_record["generation"] != producer["generation"]
            or producer_record["authority"] != expected_producer_authority
            or not valid_visibility
        ):
            raise invalid_state("Stored creation artifact producer projection diverged")
        _validate_creation_job_result_projection(
            self.store,
            producer,
            producer_record,
            artifacts=self,
            allow_registry_committing=True,
        )
        dependency_rows = self.store.connection.execute(
            "SELECT * FROM creation_artifact_dependencies WHERE workspace_id = ? "
            "AND artifact_id = ? ORDER BY position",
            (row["workspace_id"], row["artifact_id"]),
        ).fetchall()
        dependencies = tuple(
            (
                dependency["dependency_artifact_id"],
                {
                    "format": dependency["subject_format"],
                    "format_version": dependency["subject_version"],
                    "id": dependency["subject_id"],
                    "content_hash": dependency["content_hash"],
                },
            )
            for dependency in dependency_rows
        )
        expected_dependencies = tuple(artifact_dependency_identities(document))
        dependent_count = self.store.connection.execute(
            "SELECT COUNT(*) FROM creation_artifact_dependencies AS dependency "
            "JOIN creation_artifacts AS dependent "
            "ON dependent.workspace_id = dependency.workspace_id "
            "AND dependent.artifact_id = dependency.artifact_id "
            "WHERE dependency.workspace_id = ? "
            "AND dependency.dependency_artifact_id = ? "
            "AND dependent.producer_job_id = ?",
            (row["workspace_id"], row["artifact_id"], row["producer_job_id"]),
        ).fetchone()[0]
        if (
            any(
                dependency["workspace_id"] != row["workspace_id"]
                or int(dependency["position"]) != position
                for position, dependency in enumerate(dependency_rows)
            )
            or tuple(item[1] for item in dependencies) != expected_dependencies
            or any(
                dependency_id != artifact_id_for_identity(identity)
                for dependency_id, identity in dependencies
            )
            or record["references"]
            != {
                "dependency_count": len(dependencies),
                "dependent_count": int(dependent_count),
            }
        ):
            raise invalid_state("Stored creation artifact dependency projection diverged")
        return StoredCreationArtifact(record, document, dependencies)
