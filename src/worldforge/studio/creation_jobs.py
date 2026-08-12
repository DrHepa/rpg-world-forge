from __future__ import annotations

import copy
import hashlib
import hmac
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path
from typing import Any

from gamepack_runtime.game_package import (
    GamePackageError,
    build_game_package_from_files,
    verify_game_package_bytes,
)
from gamepack_runtime.headless import serialize_game_execution_script
from isoworld.content.file_stat import path_file_stat
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.asset_io import (
    AssetContractError,
    open_verified_output_parent,
    write_bytes_atomic,
)
from worldforge.directory_publish import (
    DirectoryPublishError,
    DirectoryPublishIndeterminateError,
    DirectoryPublishRecoveryRequiredError,
    append_append_only_journal,
    create_append_only_journal,
    directory_identity,
    fsync_directory,
    publish_directory_noreplace,
    quarantine_and_remove_verified_directory,
    read_append_only_journal_history_state,
    remove_append_only_journal,
    remove_verified_empty_directory,
    retained_journal_evidence_path,
    retained_recovery_evidence,
    truncate_append_only_journal_partial_tail,
)
from worldforge.game_materialization_bundle import (
    GameMaterializationBundleError,
    build_game_materialization_bundle,
    recover_game_materialization_bundle,
    rollback_game_materialization_bundle,
    verify_game_materialization_bundle,
)
from worldforge.game_package import (
    WorldForgeGamePackageError,
    extract_game_package,
    publish_verified_game_package,
    recover_game_package_extraction,
    rollback_game_package_extraction,
    verify_game_package,
)
from worldforge.game_package_extraction import (
    GamePackageExtractionEvidenceError,
    build_game_package_extraction_evidence,
    validate_game_package_extraction_evidence,
)
from worldforge.game_runtime_bundle import (
    GameRuntimeBundleError,
    build_game_runtime_bundle_from_objects,
    recover_game_runtime_bundle,
    rollback_game_runtime_bundle,
    verify_game_runtime_bundle,
)
from worldforge.generic_asset_authority import (
    GenericAssetAuthorityError,
    RetainedAssetReleaseAuthorityRecord,
    build_asset_release_authority,
    derive_asset_release_blockers,
    verify_asset_qa_review,
    verify_asset_release_authority,
)
from worldforge.generic_asset_processing import (
    GenericAssetProcessingError,
    build_asset_processing_recipe,
    validate_asset_processing_receipt_document,
)
from worldforge.generic_asset_production import (
    GenericAssetProductionError,
    read_verified_artifact_bytes,
)
from worldforge.generic_assetpack import (
    GenericAssetpackError,
    recover_generic_assetpack,
    rollback_generic_assetpack,
    seal_generic_assetpack,
    verify_generic_assetpack,
)
from worldforge.generic_headless import (
    GenericHeadlessError,
    publish_headless_evidence_tree,
    recover_headless_evidence_set,
    verify_headless_evidence_set,
)
from worldforge.integrity import canonical_json_bytes
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
    materialize_game,
    recover_standalone_game,
    rollback_standalone_game,
    verify_standalone_game,
)
from worldforge.studio.contracts import (
    CREATION_JOB_FORMAT,
    ENTITY_ID_PATTERN,
    MAX_CREATION_JOB_PAGE,
    SHA256_PATTERN,
    WORKSPACE_ID_PATTERN,
    StudioContractError,
    _validate_asset_process_operation_params,
    _validate_asset_qa_review_operation_params,
    _validate_asset_release_authorize_operation_params,
    _validate_asset_release_seal_operation_params,
    _validate_game_materialize_operation_params,
    _validate_game_package_extract_operation_params,
    _validate_game_package_operation_params,
    _validate_materialization_bundle_operation_params,
    _validate_runtime_bundle_operation_params,
    _validate_runtime_compose_operation_params,
    _validate_runtime_headless_verify_operation_params,
    creation_job_record_hash,
    validate_studio_creation_job,
    validate_studio_recovery_evidence,
)
from worldforge.studio.creation_artifacts import (
    CreationArtifactRegistry,
    PreparedAssetProcessRetention,
    PreparedCreationArtifact,
    _assetpack_candidate_publication,
    _validate_creation_job_result_projection,
    artifact_id_for_identity,
)
from worldforge.studio.creation_asset_authority import StudioAssetAuthorityResolver
from worldforge.studio.creation_evidence import CreationEvidenceManager
from worldforge.studio.creation_executor import (
    CreationWorkerExecutionError,
    VerifiedCreationBinaryOutput,
    VerifiedCreationOutput,
    _read_bound_file,
    create_creation_stage,
    run_isolated_creation_worker,
    stage_private_asset_inputs,
    verify_creation_stage_outputs,
    write_private_request,
)
from worldforge.studio.creation_grants import CreationRootGrantManager
from worldforge.studio.creation_job_protocol import (
    _ASSET_QA_REVIEW_FORMAT_ORDER,
    _asset_lineage_arguments,
    _asset_release_lineage,
    _build_asset_release_authorize_outputs,
    _canonical_asset_lineage,
    _private_verified_asset_release,
    build_private_admission_request,
    build_private_asset_process_request,
    build_private_asset_qa_review_request,
    build_private_asset_release_authorize_request,
    build_private_asset_release_seal_request,
    build_private_compile_request,
    build_private_game_materialize_request,
    build_private_game_package_extract_request,
    build_private_game_package_request,
    build_private_materialization_bundle_request,
    build_private_runtime_bundle_request,
    build_private_runtime_compose_request,
    build_private_runtime_headless_request,
    execute_private_creation_request,
    validate_private_creation_request,
)
from worldforge.studio.creation_output_grants import CreationOutputGrantManager
from worldforge.studio.creation_workspaces import CreationWorkspaceManager
from worldforge.studio.errors import (
    StudioError,
    conflict,
    invalid_request,
    invalid_state,
    not_found,
)
from worldforge.studio.storage import StudioStore, decode_object, encode_json, utc_now
from worldforge.world_lock import exclusive_world_lifecycle

_JOURNAL_FORMAT = "world-forge.studio_creation_job_journal"
_JOURNAL_VERSION = 1
_JOURNAL_PHASES = (
    "reserved",
    "worker_started",
    "output_published",
    "registry_committing",
    "committed",
    "cleanup_pending",
)
_MAX_JOURNAL_RECORD_BYTES = 256 * 1024
_MAX_JOURNAL_FILE_BYTES = 2 * 1024 * 1024
_ATOMIC_SUBMISSION_BUSY_TIMEOUT_MS = 120_000


class _PendingAssetReleaseResolver:
    """Bind one same-job release candidate before its atomic registry commit."""

    def __init__(
        self,
        *,
        retained: StudioAssetAuthorityResolver,
        release: Mapping[str, Any],
        job: Mapping[str, Any],
    ) -> None:
        self.retained = retained
        self.release = copy.deepcopy(dict(release))
        self.job = copy.deepcopy(dict(job))

    def resolve_asset_qa_review(
        self,
        *,
        review_receipt_id: str,
        content_hash: str,
    ) -> Any:
        return self.retained.resolve_asset_qa_review(
            review_receipt_id=review_receipt_id,
            content_hash=content_hash,
        )

    def resolve_asset_release_authority(
        self,
        *,
        release_authority_id: str,
        content_hash: str,
    ) -> RetainedAssetReleaseAuthorityRecord:
        release = self.release
        if release["release_authority_id"] != release_authority_id or not hmac.compare_digest(
            str(release["content_hash"]), content_hash
        ):
            raise GenericAssetAuthorityError(
                "authority_resolver_invalid",
                "pending release authority identity differs",
            )
        payload = canonical_json_bytes(release)
        authority = release["authority"]
        return RetainedAssetReleaseAuthorityRecord(
            document_bytes=payload,
            document_blob_sha256=hashlib.sha256(payload).hexdigest(),
            document_size_bytes=len(payload),
            workspace_id=str(authority["workspace_id"]),
            root_generation=int(authority["root_generation"]),
            source_revision=str(authority["source_revision"]),
            workflow_status_hash=authority["workflow_status_hash"],
            artifact_snapshot_hash=str(authority["artifact_snapshot_hash"]),
            producer_job_id=str(authority["producer_job_id"]),
            producer_operation=str(authority["producer_operation"]),
            producer_output_position=int(authority["producer_output_position"]),
        )


def _identifier(value: object, *, field: str, workspace: bool = False) -> str:
    pattern = WORKSPACE_ID_PATTERN if workspace else ENTITY_ID_PATTERN
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise invalid_request(f"{field} is not a valid identifier")
    return value


def _digest(value: object, *, field: str, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise invalid_request(f"{field} must be a lowercase SHA-256 digest")
    return value


def _generation(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= 9_007_199_254_740_991
    ):
        raise invalid_request(f"{field} must be a non-negative safe integer")
    return value


def _journal_payload(
    *,
    job: Mapping[str, Any],
    phase: str,
    stage_locator: str,
    stage_identity: tuple[int, int],
    request_locator: str,
    request_sha256: str,
    outputs: Sequence[VerifiedCreationOutput],
) -> bytes:
    if phase not in _JOURNAL_PHASES:
        raise invalid_state("Creation job journal phase is invalid")
    return canonical_json_bytes(
        {
            "format": _JOURNAL_FORMAT,
            "format_version": _JOURNAL_VERSION,
            "job_id": job["job_id"],
            "job_generation": job["generation"],
            "phase": phase,
            "stage_locator": stage_locator,
            "stage_identity": [stage_identity[0], stage_identity[1]],
            "request": {
                "locator": request_locator,
                "sha256": request_sha256,
            },
            "outputs": [
                {
                    "locator": output.locator,
                    "subject": output.subject,
                    "size": output.size,
                    "sha256": output.sha256,
                    "file_identity": [output.file_identity[0], output.file_identity[1]],
                }
                for output in outputs
            ],
        }
    )


class CreationJobManager:
    """Durable public job registry with full authority and generation CAS."""

    def __init__(
        self,
        store: StudioStore,
        *,
        workspaces: CreationWorkspaceManager,
        evidence: CreationEvidenceManager,
        artifacts: CreationArtifactRegistry,
        output_grants: CreationOutputGrantManager,
    ) -> None:
        self.store = store
        self.workspaces = workspaces
        self.evidence = evidence
        self.artifacts = artifacts
        self.output_grants = output_grants

    def _pending_exact_jobs(
        self,
        *,
        workspace_id: str,
        operation: str,
        authority: Mapping[str, Any],
        inputs: Sequence[Mapping[str, Any]],
        operation_params: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], ...]:
        rows = self.store.connection.execute(
            "SELECT * FROM creation_jobs WHERE workspace_id = ? AND operation = ? "
            "AND state IN ('queued', 'running') ORDER BY sequence",
            (workspace_id, operation),
        ).fetchall()
        matches: list[dict[str, Any]] = []
        for row in rows:
            existing = self._validated_row(row)
            if existing["authority"] != authority or existing["inputs"] != list(inputs):
                continue
            if operation_params is None:
                if "operation_params" in existing:
                    continue
            elif existing.get("operation_params") != operation_params:
                continue
            matches.append(existing)
        return tuple(matches)

    def create(self, params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("Creation job params must be an object")
        operation = params.get("operation")
        if operation == "creation.compile":
            return self.create_compile(params)
        if operation == "artifact.admit":
            return self.create_admission(params)
        if operation == "asset.process":
            return self.create_asset_process(params)
        if operation == "asset.qa.review":
            return self.create_asset_qa_review(params)
        if operation == "asset.release.authorize":
            return self.create_asset_release_authorize(params)
        if operation == "asset.release.seal":
            return self.create_asset_release_seal(params)
        if operation == "runtime.compose":
            return self.create_runtime_compose(params)
        if operation == "runtime.bundle.build":
            return self.create_runtime_bundle(params)
        if operation == "runtime.headless.verify":
            return self.create_runtime_headless(params)
        if operation == "game.materialization.bundle.build":
            return self.create_materialization_bundle(params)
        if operation == "game.materialize":
            return self.create_game_materialize(params)
        if operation == "game.package":
            return self.create_game_package(params)
        if operation == "game.package.extract":
            return self.create_game_package_extract(params)
        raise invalid_request("Creation job operation is invalid")

    @staticmethod
    def _compile_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("creation.compile params must be an object")
        allowed = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
        }
        required = allowed - {"job_id", "operation"}
        invalid = (required - set(value)) | (set(value) - allowed)
        if invalid:
            raise invalid_request(
                "creation.compile params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        if value.get("operation", "creation.compile") != "creation.compile":
            raise invalid_request("creation.compile operation is invalid")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        return value

    def create_compile(self, params: object) -> dict[str, Any]:
        parsed = self._compile_params(params)
        snapshot_params = {
            "workspace_id": parsed["workspace_id"],
            "expected_root_generation": parsed["expected_root_generation"],
            "expected_source_revision": parsed["expected_source_revision"],
            "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
            "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        self.evidence._snapshot(snapshot_params)  # noqa: SLF001
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            snapshot = self.evidence._snapshot(snapshot_params)  # noqa: SLF001
            expected_authority = {
                "root_generation": parsed["expected_root_generation"],
                "source_revision": parsed["expected_source_revision"],
                "workflow_status_hash": parsed["expected_workflow_status_hash"],
                "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
            }
            rows = self.store.connection.execute(
                "SELECT * FROM creation_jobs WHERE workspace_id = ? "
                "AND operation = 'creation.compile' "
                "AND state IN ('queued', 'running') ORDER BY sequence",
                (parsed["workspace_id"],),
            ).fetchall()
            for row in rows:
                existing = self._validated_row(row)
                if existing["authority"] == expected_authority:
                    self.store.connection.commit()
                    return existing

            job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
            private_request = build_private_compile_request(
                job_id=job_id,
                workspace_id=parsed["workspace_id"],
                authority=expected_authority,
                project=snapshot["project"],
            )
            timestamp = utc_now()
            record: dict[str, Any] = {
                "format": CREATION_JOB_FORMAT,
                "format_version": 1,
                "job_id": job_id,
                "workspace_id": parsed["workspace_id"],
                "operation": "creation.compile",
                "state": "queued",
                "generation": 0,
                "authority": copy.deepcopy(private_request["authority"]),
                "inputs": copy.deepcopy(private_request["inputs"]),
                "progress": "queued",
                "result": None,
                "error": None,
                "created_at": timestamp,
                "started_at": None,
                "finished_at": None,
                "updated_at": timestamp,
                "record_hash": "",
            }
            record["record_hash"] = creation_job_record_hash(record)
            try:
                validate_studio_creation_job(record)
            except StudioContractError as exc:
                raise StudioError("internal_error", "Creation job record is invalid") from exc
            self.store.connection.execute(
                "INSERT INTO creation_jobs "
                "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                "VALUES (?, ?, ?, 'queued', 'queued', 0, ?)",
                (job_id, parsed["workspace_id"], "creation.compile", encode_json(record)),
            )
            for position, item in enumerate(record["inputs"]):
                subject = item["subject"]
                self.store.connection.execute(
                    "INSERT INTO creation_job_inputs "
                    "(job_id, position, artifact_id, subject_format, subject_version, "
                    "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        position,
                        item["artifact_id"],
                        subject["format"],
                        subject["format_version"],
                        subject["id"],
                        subject["content_hash"],
                    ),
                )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_job.queued",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"operation": "creation.compile", "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            requested_job_id = parsed.get("job_id") or "generated"
            raise conflict(f"Creation job {requested_job_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _admission_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("artifact.admit params must be an object")
        allowed = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
            "document",
            "dependency_artifact_ids",
        }
        required = allowed - {"job_id"}
        invalid = (required - set(value)) | (set(value) - allowed)
        if invalid:
            raise invalid_request(
                "artifact.admit params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "artifact.admit":
            raise invalid_request("artifact.admit operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        if not isinstance(value["document"], dict):
            raise invalid_request("artifact.admit document must be an object")
        dependency_ids = value["dependency_artifact_ids"]
        if not isinstance(dependency_ids, list) or len(dependency_ids) > 128:
            raise invalid_request("artifact.admit dependencies are invalid")
        checked = [
            _identifier(item, field=f"dependency_artifact_ids/{index}")
            for index, item in enumerate(dependency_ids)
        ]
        if checked != sorted(set(checked), key=lambda item: item.encode("utf-8")):
            raise invalid_request("artifact.admit dependencies must be unique and canonical")
        return value

    def create_admission(self, params: object) -> dict[str, Any]:
        parsed = self._admission_params(params)
        snapshot_params = {
            "workspace_id": parsed["workspace_id"],
            "expected_root_generation": parsed["expected_root_generation"],
            "expected_source_revision": parsed["expected_source_revision"],
            "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
            "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        snapshot = self.evidence._snapshot(snapshot_params)  # noqa: SLF001
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        dependency_documents: list[dict[str, Any]] = []
        for artifact_id in parsed["dependency_artifact_ids"]:
            record = records.get(artifact_id)
            if record is None or record["lifecycle"] not in {"active", "candidate"}:
                raise conflict("Artifact admission dependency is not current")
            subject = record["subject"]
            key = (
                subject["format"],
                subject["format_version"],
                subject["id"],
                subject["content_hash"],
            )
            document = snapshot["documents"].get(key)
            if document is None:
                raise invalid_state("Artifact admission dependency document is unavailable")
            dependency_documents.append(document)
        job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
        private_request = build_private_admission_request(
            job_id=job_id,
            workspace_id=parsed["workspace_id"],
            authority={
                "root_generation": parsed["expected_root_generation"],
                "source_revision": parsed["expected_source_revision"],
                "workflow_status_hash": parsed["expected_workflow_status_hash"],
                "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
            },
            project=snapshot["project"],
            document=parsed["document"],
            dependency_documents=tuple(dependency_documents),
        )
        if [item["artifact_id"] for item in private_request["inputs"]] != parsed[
            "dependency_artifact_ids"
        ]:
            raise conflict("Artifact admission dependency identities changed")
        desired_payload = canonical_json_bytes(private_request["artifact"])
        desired_subject = document_identity(private_request["artifact"])
        desired_digest = hashlib.sha256(desired_payload).hexdigest()
        authority = copy.deepcopy(private_request["authority"])
        inputs = copy.deepcopy(private_request["inputs"])
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.evidence._snapshot(snapshot_params)  # noqa: SLF001
            if current["artifact_snapshot_hash"] != snapshot["artifact_snapshot_hash"]:
                raise conflict("Artifact admission authority changed before persistence")
            for existing in self._pending_exact_jobs(
                workspace_id=parsed["workspace_id"],
                operation="artifact.admit",
                authority=authority,
                inputs=inputs,
                operation_params=None,
            ):
                payload_row = self.store.connection.execute(
                    "SELECT * FROM creation_job_payloads WHERE job_id = ?",
                    (existing["job_id"],),
                ).fetchone()
                if payload_row is None:
                    raise invalid_state("Pending artifact admission payload is unavailable")
                retained = self.artifacts.load_job_payload(payload_row)
                if (
                    canonical_json_bytes(retained) == desired_payload
                    and document_identity(retained) == desired_subject
                    and payload_row["document_blob_sha256"] == desired_digest
                    and int(payload_row["document_size"]) == len(desired_payload)
                ):
                    self.store.connection.commit()
                    return existing

            subject, blob_sha256, document_size, blob_identity = self.artifacts.store_job_payload(
                private_request["artifact"]
            )
            timestamp = utc_now()
            record: dict[str, Any] = {
                "format": CREATION_JOB_FORMAT,
                "format_version": 1,
                "job_id": job_id,
                "workspace_id": parsed["workspace_id"],
                "operation": "artifact.admit",
                "state": "queued",
                "generation": 0,
                "authority": authority,
                "inputs": inputs,
                "progress": "queued",
                "result": None,
                "error": None,
                "created_at": timestamp,
                "started_at": None,
                "finished_at": None,
                "updated_at": timestamp,
                "record_hash": "",
            }
            record["record_hash"] = creation_job_record_hash(record)
            try:
                validate_studio_creation_job(record)
            except StudioContractError as exc:
                raise StudioError("internal_error", "Creation job record is invalid") from exc
            self.store.connection.execute(
                "INSERT INTO creation_jobs "
                "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                "VALUES (?, ?, 'artifact.admit', 'queued', 'queued', 0, ?)",
                (job_id, parsed["workspace_id"], encode_json(record)),
            )
            for position, item in enumerate(record["inputs"]):
                input_subject = item["subject"]
                self.store.connection.execute(
                    "INSERT INTO creation_job_inputs "
                    "(job_id, position, artifact_id, subject_format, subject_version, "
                    "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        position,
                        item["artifact_id"],
                        input_subject["format"],
                        input_subject["format_version"],
                        input_subject["id"],
                        input_subject["content_hash"],
                    ),
                )
            self.store.connection.execute(
                "INSERT INTO creation_job_payloads "
                "(job_id, document_blob_sha256, document_size, blob_dev, blob_ino, "
                "subject_format, subject_version, subject_id, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    blob_sha256,
                    document_size,
                    str(blob_identity[0]),
                    str(blob_identity[1]),
                    subject["format"],
                    subject["format_version"],
                    subject["id"],
                    subject["content_hash"],
                ),
            )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_job.queued",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"operation": "artifact.admit", "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation job {job_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _asset_process_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("asset.process params must be an object")
        common = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
        }
        operation_fields = {
            "license_artifact_ids",
            "recipe_id",
            "processing_receipt_id",
            "qa_report_id",
            "acceptance_results",
        }
        allowed = common | operation_fields
        required = allowed - {"job_id"}
        invalid = (required - set(value)) | (set(value) - allowed)
        if invalid:
            raise invalid_request(
                "asset.process params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "asset.process":
            raise invalid_request("asset.process operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        operation_params = {field: value[field] for field in operation_fields}
        try:
            _validate_asset_process_operation_params(operation_params, "asset.process params")
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        return value

    @staticmethod
    def _document_key(document: Mapping[str, Any]) -> tuple[str, int, str, str]:
        identity = document_identity(document)
        return (
            str(identity["format"]),
            int(identity["format_version"]),
            str(identity["id"]),
            str(identity["content_hash"]),
        )

    def _asset_process_lineage(
        self,
        *,
        snapshot: Mapping[str, Any],
        license_artifact_ids: Sequence[str],
    ) -> tuple[dict[str, Any], ...]:
        project = snapshot["project"]
        source_keys = {
            self._document_key(document)
            for document in (
                project.project,
                project.profile,
                project.manifest,
                *project.world_modules,
                *project.activity_modules,
                *project.narrative_modules,
                *project.system_modules,
                *project.logic_modules,
            )
        }
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        records_by_key = {
            (
                record["subject"]["format"],
                record["subject"]["format_version"],
                record["subject"]["id"],
                record["subject"]["content_hash"],
            ): record
            for record in snapshot["records"]
        }
        documents = snapshot["documents"]
        selected: dict[tuple[str, int, str, str], dict[str, Any]] = {}
        pending: list[dict[str, Any]] = []
        for artifact_id in license_artifact_ids:
            record = records.get(artifact_id)
            if (
                record is None
                or record["lifecycle"] not in {"active", "candidate"}
                or record["subject"]["format"] != "world-forge.asset_license_record"
            ):
                raise conflict("Asset processing license artifact is not current")
            key = (
                record["subject"]["format"],
                record["subject"]["format_version"],
                record["subject"]["id"],
                record["subject"]["content_hash"],
            )
            document = documents.get(key)
            if document is None:
                raise invalid_state("Asset processing license document is unavailable")
            selected[key] = copy.deepcopy(document)
            pending.extend(artifact_dependency_identities(document))
        while pending:
            identity = pending.pop()
            key = (
                str(identity["format"]),
                int(identity["format_version"]),
                str(identity["id"]),
                str(identity["content_hash"]),
            )
            if key in source_keys or key in selected:
                continue
            record = records_by_key.get(key)
            if record is None:
                # Source-project documents are validated independently by the private request.
                continue
            if record["lifecycle"] not in {"active", "candidate"}:
                raise conflict("Asset processing lineage is stale")
            document = documents.get(key)
            if document is None:
                raise invalid_state("Asset processing lineage document is unavailable")
            selected[key] = copy.deepcopy(document)
            pending.extend(artifact_dependency_identities(document))
        try:
            return _canonical_asset_lineage(project, tuple(selected.values()))
        except (TypeError, ValueError) as exc:
            raise conflict("Asset processing lineage is not one exact integral closure") from exc

    def _verified_asset_process_request(
        self,
        *,
        job_id: str,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], tuple[tuple[str, bytes], ...]]:
        lineage = self._asset_process_lineage(
            snapshot=snapshot,
            license_artifact_ids=operation_params["license_artifact_ids"],
        )
        root_row = self.workspaces._row(workspace_id)  # noqa: SLF001
        root, root_identity = self.workspaces._verified_root(root_row)  # noqa: SLF001
        try:
            with exclusive_world_lifecycle(root, error_type=ValueError):
                recipe = build_asset_processing_recipe(
                    recipe_id=str(operation_params["recipe_id"]),
                    **_asset_lineage_arguments(
                        {"lineage_documents": list(lineage)},
                        root,
                    ),
                )
                staged_inputs: list[dict[str, Any]] = []
                staged_payloads: list[tuple[str, bytes]] = []
                for step in recipe["steps"]:
                    payload = read_verified_artifact_bytes(
                        root,
                        step["source_locator"],
                        expected_sha256=step["source_sha256"],
                        expected_size_bytes=step["source_size_bytes"],
                        limit=16 * 1024 * 1024,
                    )
                    staged_inputs.append(
                        {
                            "candidate_artifact_id": step["candidate_artifact_id"],
                            "role": step["role"],
                            "source_locator": step["source_locator"],
                            "sha256": step["source_sha256"],
                            "size_bytes": step["source_size_bytes"],
                        }
                    )
                    staged_payloads.append((str(step["source_locator"]), payload))
                current_root, current_identity = self.workspaces._verified_root(  # noqa: SLF001
                    self.workspaces._row(workspace_id)  # noqa: SLF001
                )
                if current_root != root or current_identity != root_identity:
                    raise conflict("Asset processing workspace root identity changed")
        except StudioError:
            raise
        except (OSError, ValueError) as exc:
            raise conflict("Asset processing source bytes are not integral") from exc
        request = build_private_asset_process_request(
            job_id=job_id,
            workspace_id=workspace_id,
            authority=copy.deepcopy(dict(authority)),
            project=snapshot["project"],
            lineage_documents=lineage,
            recipe_id=str(operation_params["recipe_id"]),
            processing_receipt_id=str(operation_params["processing_receipt_id"]),
            qa_report_id=str(operation_params["qa_report_id"]),
            acceptance_results=copy.deepcopy(operation_params["acceptance_results"]),
            staged_inputs=staged_inputs,
        )
        return request, lineage, tuple(staged_payloads)

    def create_asset_process(self, params: object) -> dict[str, Any]:
        parsed = self._asset_process_params(params)
        snapshot_params = {
            "workspace_id": parsed["workspace_id"],
            "expected_root_generation": parsed["expected_root_generation"],
            "expected_source_revision": parsed["expected_source_revision"],
            "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
            "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        snapshot = self.evidence._snapshot(snapshot_params)  # noqa: SLF001
        job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
        operation_params = {
            field: copy.deepcopy(parsed[field])
            for field in (
                "license_artifact_ids",
                "recipe_id",
                "processing_receipt_id",
                "qa_report_id",
                "acceptance_results",
            )
        }
        authority = {
            "root_generation": parsed["expected_root_generation"],
            "source_revision": parsed["expected_source_revision"],
            "workflow_status_hash": parsed["expected_workflow_status_hash"],
            "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        self.store.connection.execute(f"PRAGMA busy_timeout = {_ATOMIC_SUBMISSION_BUSY_TIMEOUT_MS}")
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.evidence._snapshot(snapshot_params)  # noqa: SLF001
            if current["artifact_snapshot_hash"] != snapshot["artifact_snapshot_hash"]:
                raise conflict("Asset processing authority changed before persistence")
            private_request, _lineage, _staged = self._verified_asset_process_request(
                job_id=job_id,
                workspace_id=parsed["workspace_id"],
                authority=authority,
                snapshot=current,
                operation_params=operation_params,
            )
            requested_licenses = set(operation_params["license_artifact_ids"])
            actual_licenses = {
                item["artifact_id"]
                for item in private_request["inputs"]
                if item["subject"]["format"] == "world-forge.asset_license_record"
            }
            if requested_licenses != actual_licenses:
                raise conflict("Asset processing license closure changed")
            inputs = copy.deepcopy(private_request["inputs"])
            matches = self._pending_exact_jobs(
                workspace_id=parsed["workspace_id"],
                operation="asset.process",
                authority=authority,
                inputs=inputs,
                operation_params=operation_params,
            )
            if matches:
                self.store.connection.commit()
                return matches[0]

            timestamp = utc_now()
            record: dict[str, Any] = {
                "format": CREATION_JOB_FORMAT,
                "format_version": 2,
                "job_id": job_id,
                "workspace_id": parsed["workspace_id"],
                "operation": "asset.process",
                "operation_params": operation_params,
                "state": "queued",
                "generation": 0,
                "authority": authority,
                "inputs": inputs,
                "progress": "queued",
                "result": None,
                "error": None,
                "created_at": timestamp,
                "started_at": None,
                "finished_at": None,
                "updated_at": timestamp,
                "record_hash": "",
            }
            record["record_hash"] = creation_job_record_hash(record)
            try:
                validate_studio_creation_job(record)
            except StudioContractError as exc:
                raise StudioError(
                    "internal_error", "Asset processing job record is invalid"
                ) from exc
            self.store.connection.execute(
                "INSERT INTO creation_jobs "
                "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                "VALUES (?, ?, 'asset.process', 'queued', 'queued', 0, ?)",
                (job_id, parsed["workspace_id"], encode_json(record)),
            )
            for position, item in enumerate(record["inputs"]):
                subject = item["subject"]
                self.store.connection.execute(
                    "INSERT INTO creation_job_inputs "
                    "(job_id, position, artifact_id, subject_format, subject_version, "
                    "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        position,
                        item["artifact_id"],
                        subject["format"],
                        subject["format_version"],
                        subject["id"],
                        subject["content_hash"],
                    ),
                )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_job.queued",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"operation": "asset.process", "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation job {job_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _asset_qa_review_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("asset.qa.review params must be an object")
        common = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
        }
        operation_fields = {
            "qa_report_artifact_id",
            "output_role",
            "review_receipt_id",
            "decisions",
            "blockers",
        }
        allowed = common | operation_fields
        required = allowed - {"job_id"}
        invalid = (required - set(value)) | (set(value) - allowed)
        if invalid:
            raise invalid_request(
                "asset.qa.review params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "asset.qa.review":
            raise invalid_request("asset.qa.review operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        try:
            _validate_asset_qa_review_operation_params(
                {field: value[field] for field in operation_fields},
                "asset.qa.review params",
            )
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        return value

    def _verified_asset_qa_review_request(
        self,
        *,
        job_id: str,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], tuple[tuple[str, bytes], ...]]:
        qa_artifact_id = str(operation_params["qa_report_artifact_id"])
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        qa_record = records.get(qa_artifact_id)
        if (
            qa_record is None
            or qa_record["lifecycle"] not in {"active", "candidate"}
            or qa_record["subject"]["format"] != "world-forge.asset_qa_report"
        ):
            raise conflict("Asset QA review report artifact is not current")
        qa_key = self._document_key(
            snapshot["documents"][
                (
                    qa_record["subject"]["format"],
                    qa_record["subject"]["format_version"],
                    qa_record["subject"]["id"],
                    qa_record["subject"]["content_hash"],
                )
            ]
        )
        qa_report = snapshot["documents"].get(qa_key)
        if qa_report is None:
            raise invalid_state("Asset QA review report document is unavailable")
        records_by_key = {
            (
                record["subject"]["format"],
                record["subject"]["format_version"],
                record["subject"]["id"],
                record["subject"]["content_hash"],
            ): record
            for record in snapshot["records"]
        }
        lineage_by_format: dict[str, dict[str, Any]] = {}
        for identity in artifact_dependency_identities(qa_report):
            key = (
                str(identity["format"]),
                int(identity["format_version"]),
                str(identity["id"]),
                str(identity["content_hash"]),
            )
            record = records_by_key.get(key)
            document = snapshot["documents"].get(key)
            if (
                record is None
                or document is None
                or record["lifecycle"] not in {"active", "candidate"}
            ):
                raise conflict("Asset QA review lineage is stale or incomplete")
            format_name = str(document["format"])
            if format_name in lineage_by_format:
                raise conflict("Asset QA review lineage contains duplicate formats")
            lineage_by_format[format_name] = copy.deepcopy(document)
        lineage_by_format[str(qa_report["format"])] = copy.deepcopy(qa_report)
        recipe = lineage_by_format.get("world-forge.asset_processing_recipe")
        if recipe is None:
            raise conflict("Asset QA review processing recipe is unavailable")
        licenses: list[dict[str, Any]] = []
        for identity in artifact_dependency_identities(recipe):
            if identity["format"] != "world-forge.asset_license_record":
                continue
            key = (
                str(identity["format"]),
                int(identity["format_version"]),
                str(identity["id"]),
                str(identity["content_hash"]),
            )
            record = records_by_key.get(key)
            document = snapshot["documents"].get(key)
            if (
                record is None
                or document is None
                or record["lifecycle"] not in {"active", "candidate"}
            ):
                raise conflict("Asset QA review license lineage is stale")
            licenses.append(copy.deepcopy(document))
        licenses.sort(
            key=lambda document: (
                str(document["license_record_id"]).encode("utf-8"),
                str(document["content_hash"]).encode("ascii"),
            )
        )
        if not 1 <= len(licenses) <= 4:
            raise conflict("Asset QA review license lineage is not exact")
        try:
            lineage = (
                *(
                    lineage_by_format[format_name]
                    for format_name in _ASSET_QA_REVIEW_FORMAT_ORDER[:10]
                ),
                *licenses,
                *(
                    lineage_by_format[format_name]
                    for format_name in _ASSET_QA_REVIEW_FORMAT_ORDER[11:]
                ),
            )
        except KeyError as exc:
            raise conflict("Asset QA review lineage format closure is incomplete") from exc
        if set(lineage_by_format) != set(_ASSET_QA_REVIEW_FORMAT_ORDER) - {
            "world-forge.asset_license_record"
        }:
            raise conflict("Asset QA review lineage format closure is not exact")

        qa_row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, qa_artifact_id),
        ).fetchone()
        if (
            qa_row is None
            or qa_row["producer_operation"] != "asset.process"
            or int(qa_row["producer_output_position"]) != 2
        ):
            raise conflict("Asset QA review report producer is not exact")
        self.artifacts._validated_row(qa_row)  # noqa: SLF001
        retention = self.artifacts.load_asset_process_retention(
            workspace_id=workspace_id,
            producer_job_id=str(qa_row["producer_job_id"]),
        )
        role = str(operation_params["output_role"])
        qa_outputs = [output for output in qa_report["outputs"] if output["role"] == role]
        retained_outputs = [output for output in retention["outputs"] if output["role"] == role]
        if len(qa_outputs) != 1 or len(retained_outputs) != 1:
            raise conflict("Asset QA review retained output role is not exact")
        qa_output = qa_outputs[0]
        retained_output = retained_outputs[0]
        for field in (
            "candidate_artifact_id",
            "role",
            "media_type",
            "runtime_path",
            "locator",
            "sha256",
            "size_bytes",
        ):
            if qa_output[field] != retained_output[field]:
                raise conflict("Asset QA review retained output lineage changed")
        payload = self.artifacts.read_retained_asset_output(retention, role=role)
        staged_inputs = [
            {
                "candidate_artifact_id": retained_output["candidate_artifact_id"],
                "role": role,
                "source_locator": retained_output["locator"],
                "sha256": retained_output["sha256"],
                "size_bytes": retained_output["size_bytes"],
            }
        ]
        request = build_private_asset_qa_review_request(
            job_id=job_id,
            workspace_id=workspace_id,
            authority=copy.deepcopy(dict(authority)),
            project=snapshot["project"],
            lineage_documents=lineage,
            review_receipt_id=str(operation_params["review_receipt_id"]),
            output_role=role,
            decisions=operation_params["decisions"],
            blockers=operation_params["blockers"],
            staged_inputs=staged_inputs,
        )
        return request, lineage, ((str(retained_output["locator"]), payload),)

    def create_asset_qa_review(self, params: object) -> dict[str, Any]:
        parsed = self._asset_qa_review_params(params)
        snapshot_params = {
            "workspace_id": parsed["workspace_id"],
            "expected_root_generation": parsed["expected_root_generation"],
            "expected_source_revision": parsed["expected_source_revision"],
            "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
            "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        snapshot = self.evidence._snapshot(snapshot_params)  # noqa: SLF001
        job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
        operation_params = {
            field: copy.deepcopy(parsed[field])
            for field in (
                "qa_report_artifact_id",
                "output_role",
                "review_receipt_id",
                "decisions",
                "blockers",
            )
        }
        authority = {
            "root_generation": parsed["expected_root_generation"],
            "source_revision": parsed["expected_source_revision"],
            "workflow_status_hash": parsed["expected_workflow_status_hash"],
            "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        self.store.connection.execute(f"PRAGMA busy_timeout = {_ATOMIC_SUBMISSION_BUSY_TIMEOUT_MS}")
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.evidence._snapshot(snapshot_params)  # noqa: SLF001
            if current["artifact_snapshot_hash"] != snapshot["artifact_snapshot_hash"]:
                raise conflict("Asset QA review authority changed before persistence")
            request, _lineage, _staged = self._verified_asset_qa_review_request(
                job_id=job_id,
                workspace_id=parsed["workspace_id"],
                authority=authority,
                snapshot=current,
                operation_params=operation_params,
            )
            inputs = copy.deepcopy(request["inputs"])
            matches = self._pending_exact_jobs(
                workspace_id=parsed["workspace_id"],
                operation="asset.qa.review",
                authority=authority,
                inputs=inputs,
                operation_params=operation_params,
            )
            if matches:
                self.store.connection.commit()
                return matches[0]
            timestamp = utc_now()
            record: dict[str, Any] = {
                "format": CREATION_JOB_FORMAT,
                "format_version": 10,
                "job_id": job_id,
                "workspace_id": parsed["workspace_id"],
                "operation": "asset.qa.review",
                "operation_params": operation_params,
                "state": "queued",
                "generation": 0,
                "authority": authority,
                "inputs": inputs,
                "progress": "queued",
                "result": None,
                "error": None,
                "created_at": timestamp,
                "started_at": None,
                "finished_at": None,
                "updated_at": timestamp,
                "record_hash": "",
            }
            record["record_hash"] = creation_job_record_hash(record)
            try:
                validate_studio_creation_job(record)
            except StudioContractError as exc:
                raise StudioError(
                    "internal_error", "Asset QA review job record is invalid"
                ) from exc
            self.store.connection.execute(
                "INSERT INTO creation_jobs "
                "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                "VALUES (?, ?, 'asset.qa.review', 'queued', 'queued', 0, ?)",
                (job_id, parsed["workspace_id"], encode_json(record)),
            )
            for position, item in enumerate(inputs):
                subject = item["subject"]
                self.store.connection.execute(
                    "INSERT INTO creation_job_inputs "
                    "(job_id, position, artifact_id, subject_format, subject_version, "
                    "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        position,
                        item["artifact_id"],
                        subject["format"],
                        subject["format_version"],
                        subject["id"],
                        subject["content_hash"],
                    ),
                )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_job.queued",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"operation": "asset.qa.review", "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation job {job_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _asset_release_authorize_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("asset.release.authorize params must be an object")
        common = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
        }
        operation_fields = {
            "review_receipt_artifact_ids",
            "manifest_id",
            "assetpack_id",
            "release_authority_id",
            "blockers",
            "target_grant_id",
            "expected_target_grant_generation",
        }
        allowed = common | operation_fields
        required = allowed - {"job_id"}
        invalid = (required - set(value)) | (set(value) - allowed)
        if invalid:
            raise invalid_request(
                "asset.release.authorize params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "asset.release.authorize":
            raise invalid_request("asset.release.authorize operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        try:
            _validate_asset_release_authorize_operation_params(
                {
                    "review_receipt_artifact_ids": value["review_receipt_artifact_ids"],
                    "manifest_id": value["manifest_id"],
                    "assetpack_id": value["assetpack_id"],
                    "release_authority_id": value["release_authority_id"],
                    "blockers": value["blockers"],
                    "target_grant_id": value["target_grant_id"],
                    "target_grant_generation": value["expected_target_grant_generation"],
                },
                "asset.release.authorize params",
            )
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        return value

    def _verified_asset_release_authorize_request(
        self,
        *,
        job_id: str,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any],
        tuple[dict[str, Any], ...],
        tuple[tuple[str, bytes], ...],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ]:
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        records_by_subject = {
            (
                str(record["subject"]["format"]),
                int(record["subject"]["format_version"]),
                str(record["subject"]["id"]),
                str(record["subject"]["content_hash"]),
            ): record
            for record in snapshot["records"]
        }
        documents = snapshot["documents"]
        resolver = StudioAssetAuthorityResolver(self.store, artifacts=self.artifacts)
        review_documents: list[dict[str, Any]] = []
        review_handles: list[Any] = []
        review_records: dict[tuple[str, str], Any] = {}
        qa_artifact_ids: list[str] = []
        for artifact_id in operation_params["review_receipt_artifact_ids"]:
            record = records.get(str(artifact_id))
            if (
                record is None
                or record["lifecycle"] not in {"active", "candidate"}
                or record["subject"]["format"] != "world-forge.asset_qa_review_receipt"
            ):
                raise conflict("Asset release review receipt artifact is not current")
            key = (
                str(record["subject"]["format"]),
                int(record["subject"]["format_version"]),
                str(record["subject"]["id"]),
                str(record["subject"]["content_hash"]),
            )
            review = documents.get(key)
            if review is None:
                raise invalid_state("Asset release review receipt document is unavailable")
            row = self.store.connection.execute(
                "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
                (workspace_id, artifact_id),
            ).fetchone()
            if row is None:
                raise conflict("Asset release review receipt artifact is unavailable")
            stored = self.artifacts._validated_row(row)  # noqa: SLF001
            producer = self.get(str(row["producer_job_id"]))
            if (
                producer["format_version"] != 10
                or producer["operation"] != "asset.qa.review"
                or row["producer_operation"] != "asset.qa.review"
                or int(row["producer_output_position"]) != 0
                or stored.document != review
                or artifact_id_for_identity(document_identity(review)) != artifact_id
            ):
                raise conflict("Asset release review receipt producer is not exact v10")
            try:
                handle = verify_asset_qa_review(review, resolver=resolver)
                retained = resolver.resolve_asset_qa_review(
                    review_receipt_id=str(review["review_receipt_id"]),
                    content_hash=str(review["content_hash"]),
                )
            except GenericAssetAuthorityError as exc:
                raise conflict("Asset release review receipt authority is not integral") from exc
            if handle.document != review:
                raise conflict("Asset release review receipt authority changed")
            review_handles.append(handle)
            review_key = (
                str(review["asset"]["asset_id"]),
                str(review["reviewed_output"]["role"]),
            )
            if review_key in review_records:
                raise conflict("Asset release review coverage contains a duplicate")
            review_records[review_key] = retained
            review_documents.append(copy.deepcopy(review))
            qa_identity = review["lineage"]["qa_report"]
            qa_record = records_by_subject.get(
                (
                    str(qa_identity["format"]),
                    int(qa_identity["format_version"]),
                    str(qa_identity["id"]),
                    str(qa_identity["content_hash"]),
                )
            )
            if (
                qa_record is None
                or qa_record["lifecycle"] not in {"active", "candidate"}
                or qa_record["subject"] != qa_identity
            ):
                raise conflict("Asset release review QA lineage is not current")
            qa_artifact_ids.append(str(qa_record["artifact_id"]))
        review_documents.sort(key=lambda item: str(item["review_receipt_id"]).encode("utf-8"))
        if len(set(qa_artifact_ids)) != len(qa_artifact_ids):
            raise conflict("Asset release review QA lineage is duplicated")
        lineage = self._asset_release_documents(
            snapshot=snapshot,
            qa_report_artifact_ids=qa_artifact_ids,
        )
        _canonical, _roots, asset_records = _asset_release_lineage(
            snapshot["project"],
            lineage,
        )
        root_row = self.workspaces._row(workspace_id)  # noqa: SLF001
        root, root_identity = self.workspaces._verified_root(root_row)  # noqa: SLF001
        staged_inputs: list[dict[str, Any]] = []
        staged_by_locator: dict[str, bytes] = {}
        expected_review_keys: set[tuple[str, str]] = set()
        try:
            with exclusive_world_lifecycle(root, error_type=ValueError):
                for asset_record in asset_records:
                    for receipt_key in ("receipt", "processing_receipt"):
                        receipt = asset_record[receipt_key]
                        asset_id = str(receipt["asset"]["asset_id"])
                        for output in receipt["outputs"]:
                            locator = str(output["locator"])
                            payload = read_verified_artifact_bytes(
                                root,
                                locator,
                                expected_sha256=str(output["sha256"]),
                                expected_size_bytes=int(output["size_bytes"]),
                                limit=16 * 1024 * 1024,
                            )
                            if receipt_key == "processing_receipt":
                                review_key = (asset_id, str(output["role"]))
                                retained = review_records.get(review_key)
                                if retained is None:
                                    raise conflict("Asset release review coverage is incomplete")
                                reviewed = next(
                                    item
                                    for item in review_documents
                                    if (
                                        str(item["asset"]["asset_id"]),
                                        str(item["reviewed_output"]["role"]),
                                    )
                                    == review_key
                                )["reviewed_output"]
                                if (
                                    any(
                                        reviewed[field] != output[field]
                                        for field in (
                                            "role",
                                            "media_type",
                                            "runtime_path",
                                            "locator",
                                            "sha256",
                                            "size_bytes",
                                        )
                                    )
                                    or payload != retained.retained_output_bytes
                                ):
                                    raise conflict("Asset release reviewed output identity changed")
                                expected_review_keys.add(review_key)
                            existing = staged_by_locator.get(locator)
                            if existing is not None and existing != payload:
                                raise conflict("Asset release staged locator bytes differ")
                            staged_by_locator[locator] = payload
                            staged_inputs.append(
                                {
                                    "asset_id": asset_id,
                                    "role": output["role"],
                                    "source_locator": locator,
                                    "sha256": output["sha256"],
                                    "size_bytes": output["size_bytes"],
                                }
                            )
                if expected_review_keys != set(review_records):
                    raise conflict("Asset release review coverage contains extra outputs")
                current_root, current_identity = self.workspaces._verified_root(  # noqa: SLF001
                    self.workspaces._row(workspace_id)  # noqa: SLF001
                )
                if current_root != root or current_identity != root_identity:
                    raise conflict("Asset release workspace root identity changed")
        except StudioError:
            raise
        except (OSError, StopIteration, ValueError) as exc:
            raise conflict("Asset release reviewed bytes are not integral") from exc
        staged_inputs.sort(
            key=lambda item: (
                str(item["asset_id"]).encode("utf-8"),
                str(item["role"]).encode("utf-8"),
                str(item["source_locator"]).encode("utf-8"),
            )
        )
        staged_payloads = tuple(
            (
                str(item["source_locator"]),
                staged_by_locator[str(item["source_locator"])],
            )
            for item in staged_inputs
        )
        try:
            (
                expected_manifest,
                expected_assetpack,
                expected_release_authority,
                expected_blockers,
            ) = _build_asset_release_authorize_outputs(
                project=snapshot["project"],
                lineage_documents=lineage,
                reviews=review_handles,
                manifest_id=str(operation_params["manifest_id"]),
                assetpack_id=str(operation_params["assetpack_id"]),
                release_authority_id=str(operation_params["release_authority_id"]),
                blockers=operation_params["blockers"],
                authority={
                    "workspace_id": workspace_id,
                    **copy.deepcopy(dict(authority)),
                    "producer_job_id": job_id,
                    "producer_operation": "asset.release.authorize",
                    "producer_output_position": 2,
                },
                artifact_root=root,
            )
        except (GenericAssetAuthorityError, GenericAssetProcessingError, ValueError) as exc:
            raise conflict("Asset release authority expectation is not integral") from exc
        request = build_private_asset_release_authorize_request(
            job_id=job_id,
            workspace_id=workspace_id,
            authority=copy.deepcopy(dict(authority)),
            project=snapshot["project"],
            lineage_documents=lineage,
            review_documents=review_documents,
            manifest_id=str(operation_params["manifest_id"]),
            assetpack_id=str(operation_params["assetpack_id"]),
            release_authority_id=str(operation_params["release_authority_id"]),
            blockers=expected_blockers,
            target_grant_id=str(operation_params["target_grant_id"]),
            target_grant_generation=int(operation_params["target_grant_generation"]),
            staged_inputs=staged_inputs,
        )
        try:
            result = execute_private_creation_request(request, artifact_root=root)
            if len(result.outputs) != 3:
                raise ValueError("asset release authority output count differs")
            output_documents = tuple(
                decode_json_object(
                    output.payload,
                    source=f"asset release authority preflight output {index}",
                )
                for index, output in enumerate(result.outputs)
            )
            release_manifest, assetpack_manifest, release_authority = output_documents
            expected_status = (
                "passed" if expected_release_authority["status"] == "authorized" else "failed"
            )
            if (
                result.analysis_status != expected_status
                or list(result.reason_codes) != expected_blockers
                or release_manifest != expected_manifest
                or assetpack_manifest != expected_assetpack
                or release_authority != expected_release_authority
            ):
                raise ValueError("asset release authority preflight output differs")
        except (RuntimeIOError, TypeError, ValueError) as exc:
            raise conflict("Asset release authority preflight is not integral") from exc
        return (
            request,
            (*lineage, *review_documents),
            staged_payloads,
            expected_manifest,
            expected_assetpack,
            expected_release_authority,
        )

    def _verify_pending_asset_release_authorize_grant(
        self,
        job: Mapping[str, Any],
        *,
        release_status: str,
        expected_generation: int,
        manifest_hash: str,
        assetpack_hash: str,
    ) -> None:
        grant_id = str(job["operation_params"]["target_grant_id"])
        if release_status == "blocked":
            grant = self.output_grants.get(grant_id)
            if (
                grant["workspace_id"] != job["workspace_id"]
                or grant["kind"] != "generic_assetpack_directory"
                or grant["state"] != "ready"
                or grant["generation"] != expected_generation
            ):
                raise conflict("Pending blocked asset release target grant changed")
            return
        row = self.store.connection.execute(
            "SELECT * FROM creation_output_grants WHERE grant_id = ?",
            (grant_id,),
        ).fetchone()
        if (
            row is None
            or row["workspace_id"] != job["workspace_id"]
            or row["kind"] != "generic_assetpack_directory"
            or row["reserved_job_id"] != job["job_id"]
            or row["state"] not in {"reserved", "recovery_required", "published"}
            or int(row["generation"]) != expected_generation
            or row["expected_manifest_hash"] != manifest_hash
            or row["expected_tree_hash"] != assetpack_hash
        ):
            raise conflict("Pending asset release authority grant reservation changed")

    def create_asset_release_authorize(self, params: object) -> dict[str, Any]:
        parsed = self._asset_release_authorize_params(params)
        snapshot_params = {
            "workspace_id": parsed["workspace_id"],
            "expected_root_generation": parsed["expected_root_generation"],
            "expected_source_revision": parsed["expected_source_revision"],
            "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
            "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        snapshot = self.evidence._snapshot(snapshot_params)  # noqa: SLF001
        job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
        authority = {
            "root_generation": parsed["expected_root_generation"],
            "source_revision": parsed["expected_source_revision"],
            "workflow_status_hash": parsed["expected_workflow_status_hash"],
            "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        requested_generation = int(parsed["expected_target_grant_generation"])
        operation_params = {
            "review_receipt_artifact_ids": copy.deepcopy(parsed["review_receipt_artifact_ids"]),
            "manifest_id": parsed["manifest_id"],
            "assetpack_id": parsed["assetpack_id"],
            "release_authority_id": parsed["release_authority_id"],
            "blockers": copy.deepcopy(parsed["blockers"]),
            "target_grant_id": parsed["target_grant_id"],
            "target_grant_generation": requested_generation,
        }
        self.store.connection.execute(f"PRAGMA busy_timeout = {_ATOMIC_SUBMISSION_BUSY_TIMEOUT_MS}")
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.evidence._snapshot(snapshot_params)  # noqa: SLF001
            if current["artifact_snapshot_hash"] != snapshot["artifact_snapshot_hash"]:
                raise conflict("Asset release authority changed before persistence")
            (
                request,
                _lineage,
                _payloads,
                release_manifest,
                assetpack_manifest,
                release_authority,
            ) = self._verified_asset_release_authorize_request(
                job_id=job_id,
                workspace_id=parsed["workspace_id"],
                authority=authority,
                snapshot=current,
                operation_params=operation_params,
            )
            inputs = copy.deepcopy(request["inputs"])
            release_status = str(release_authority["status"])
            stored_operation_params = copy.deepcopy(operation_params)
            stored_operation_params["blockers"] = copy.deepcopy(request["blockers"])
            if release_status == "authorized":
                stored_operation_params["target_grant_generation"] = requested_generation + 1
            matches = self._pending_exact_jobs(
                workspace_id=parsed["workspace_id"],
                operation="asset.release.authorize",
                authority=authority,
                inputs=inputs,
                operation_params=stored_operation_params,
            )
            if matches:
                existing = matches[0]
                self._verify_pending_asset_release_authorize_grant(
                    existing,
                    release_status=release_status,
                    expected_generation=stored_operation_params["target_grant_generation"],
                    manifest_hash=str(release_manifest["content_hash"]),
                    assetpack_hash=str(assetpack_manifest["content_hash"]),
                )
                self.store.connection.commit()
                return existing
            if release_status == "authorized":
                reserved, _binding = self.output_grants.reserve_for_job(
                    grant_id=parsed["target_grant_id"],
                    job_id=job_id,
                    workspace_id=parsed["workspace_id"],
                    expected_generation=requested_generation,
                    expected_manifest_hash=str(release_manifest["content_hash"]),
                    expected_tree_hash=str(assetpack_manifest["content_hash"]),
                )
                if reserved["kind"] != "generic_assetpack_directory":
                    raise invalid_request(
                        "Asset release authority requires a generic assetpack grant"
                    )
                if reserved["generation"] != requested_generation + 1:
                    raise conflict("Asset release authority output grant generation changed")
            else:
                grant = self.output_grants.get(parsed["target_grant_id"])
                if (
                    grant["workspace_id"] != parsed["workspace_id"]
                    or grant["kind"] != "generic_assetpack_directory"
                ):
                    raise invalid_request("Asset release authority target grant is not compatible")
                if grant["state"] != "ready":
                    raise invalid_state("Asset release authority target grant is not ready")
                if grant["generation"] != requested_generation:
                    raise conflict("Creation output grant generation changed")
            timestamp = utc_now()
            record: dict[str, Any] = {
                "format": CREATION_JOB_FORMAT,
                "format_version": 11,
                "job_id": job_id,
                "workspace_id": parsed["workspace_id"],
                "operation": "asset.release.authorize",
                "operation_params": stored_operation_params,
                "state": "queued",
                "generation": 0,
                "authority": authority,
                "inputs": inputs,
                "progress": "queued",
                "result": None,
                "error": None,
                "created_at": timestamp,
                "started_at": None,
                "finished_at": None,
                "updated_at": timestamp,
                "record_hash": "",
            }
            record["record_hash"] = creation_job_record_hash(record)
            try:
                validate_studio_creation_job(record)
            except StudioContractError as exc:
                raise StudioError(
                    "internal_error",
                    "Asset release authority job record is invalid",
                ) from exc
            self.store.connection.execute(
                "INSERT INTO creation_jobs "
                "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                "VALUES (?, ?, 'asset.release.authorize', 'queued', 'queued', 0, ?)",
                (job_id, parsed["workspace_id"], encode_json(record)),
            )
            for position, item in enumerate(inputs):
                subject = item["subject"]
                self.store.connection.execute(
                    "INSERT INTO creation_job_inputs "
                    "(job_id, position, artifact_id, subject_format, subject_version, "
                    "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        position,
                        item["artifact_id"],
                        subject["format"],
                        subject["format_version"],
                        subject["id"],
                        subject["content_hash"],
                    ),
                )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_job.queued",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"operation": "asset.release.authorize", "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation job {job_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _asset_release_seal_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("asset.release.seal params must be an object")
        common = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
        }
        operation_fields = {
            "qa_report_artifact_ids",
            "manifest_id",
            "target_grant_id",
            "expected_target_grant_generation",
        }
        allowed = common | operation_fields
        required = allowed - {"job_id"}
        invalid = (required - set(value)) | (set(value) - allowed)
        if invalid:
            raise invalid_request(
                "asset.release.seal params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "asset.release.seal":
            raise invalid_request("asset.release.seal operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        operation_params = {
            "qa_report_artifact_ids": value["qa_report_artifact_ids"],
            "manifest_id": value["manifest_id"],
            "target_grant_id": value["target_grant_id"],
            "target_grant_generation": value["expected_target_grant_generation"],
        }
        try:
            _validate_asset_release_seal_operation_params(
                operation_params,
                "asset.release.seal params",
            )
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        return value

    def _asset_release_documents(
        self,
        *,
        snapshot: Mapping[str, Any],
        qa_report_artifact_ids: Sequence[str],
    ) -> tuple[dict[str, Any], ...]:
        project = snapshot["project"]
        source_keys = {
            self._document_key(document)
            for document in (
                project.project,
                project.profile,
                project.manifest,
                *project.world_modules,
                *project.activity_modules,
                *project.narrative_modules,
                *project.system_modules,
                *project.logic_modules,
            )
        }
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        records_by_key = {
            (
                record["subject"]["format"],
                record["subject"]["format_version"],
                record["subject"]["id"],
                record["subject"]["content_hash"],
            ): record
            for record in snapshot["records"]
        }
        documents = snapshot["documents"]
        selected: dict[tuple[str, int, str, str], dict[str, Any]] = {}
        pending: list[dict[str, Any]] = []
        for artifact_id in qa_report_artifact_ids:
            record = records.get(artifact_id)
            if (
                record is None
                or record["lifecycle"] not in {"active", "candidate"}
                or record["subject"]["format"] != "world-forge.asset_qa_report"
            ):
                raise conflict("Asset release QA artifact is not current")
            key = (
                record["subject"]["format"],
                record["subject"]["format_version"],
                record["subject"]["id"],
                record["subject"]["content_hash"],
            )
            document = documents.get(key)
            if document is None:
                raise invalid_state("Asset release QA document is unavailable")
            selected[key] = copy.deepcopy(document)
            pending.extend(artifact_dependency_identities(document))
        while pending:
            identity = pending.pop()
            key = (
                str(identity["format"]),
                int(identity["format_version"]),
                str(identity["id"]),
                str(identity["content_hash"]),
            )
            if key in source_keys or key in selected:
                continue
            record = records_by_key.get(key)
            if record is None or record["lifecycle"] not in {"active", "candidate"}:
                raise conflict("Asset release lineage is incomplete or stale")
            document = documents.get(key)
            if document is None:
                raise invalid_state("Asset release lineage document is unavailable")
            selected[key] = copy.deepcopy(document)
            pending.extend(artifact_dependency_identities(document))
        try:
            lineage, _roots, _records = _asset_release_lineage(
                project,
                tuple(selected.values()),
            )
            return lineage
        except (TypeError, ValueError) as exc:
            raise conflict("Asset release lineage is not one exact integral closure") from exc

    def _verified_asset_release_request(
        self,
        *,
        job_id: str,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any],
        tuple[dict[str, Any], ...],
        tuple[tuple[str, bytes], ...],
        dict[str, Any],
        dict[str, Any],
    ]:
        lineage = self._asset_release_documents(
            snapshot=snapshot,
            qa_report_artifact_ids=operation_params["qa_report_artifact_ids"],
        )
        _canonical, _roots, records = _asset_release_lineage(
            snapshot["project"],
            lineage,
        )
        root_row = self.workspaces._row(workspace_id)  # noqa: SLF001
        root, root_identity = self.workspaces._verified_root(root_row)  # noqa: SLF001
        staged_inputs: list[dict[str, Any]] = []
        staged_payloads: list[tuple[str, bytes]] = []
        try:
            with exclusive_world_lifecycle(root, error_type=ValueError):
                for record in records:
                    for receipt_key in ("receipt", "processing_receipt"):
                        receipt = record[receipt_key]
                        asset_id = str(receipt["asset"]["asset_id"])
                        for output in receipt["outputs"]:
                            payload = read_verified_artifact_bytes(
                                root,
                                output["locator"],
                                expected_sha256=output["sha256"],
                                expected_size_bytes=output["size_bytes"],
                                limit=16 * 1024 * 1024,
                            )
                            staged_inputs.append(
                                {
                                    "asset_id": asset_id,
                                    "role": output["role"],
                                    "source_locator": output["locator"],
                                    "sha256": output["sha256"],
                                    "size_bytes": output["size_bytes"],
                                }
                            )
                            staged_payloads.append((str(output["locator"]), payload))
                staged_inputs.sort(
                    key=lambda item: (
                        str(item["asset_id"]).encode("utf-8"),
                        str(item["role"]).encode("utf-8"),
                        str(item["source_locator"]).encode("utf-8"),
                    )
                )
                staged_by_locator = {locator: payload for locator, payload in staged_payloads}
                if len(staged_by_locator) != len(staged_payloads):
                    raise conflict("Asset release staged locators are not unique")
                staged_payloads = [
                    (str(item["source_locator"]), staged_by_locator[str(item["source_locator"])])
                    for item in staged_inputs
                ]
                current_root, current_identity = self.workspaces._verified_root(  # noqa: SLF001
                    self.workspaces._row(workspace_id)  # noqa: SLF001
                )
                if current_root != root or current_identity != root_identity:
                    raise conflict("Asset release workspace root identity changed")
        except StudioError:
            raise
        except (OSError, ValueError) as exc:
            raise conflict("Asset release processed bytes are not integral") from exc
        request = build_private_asset_release_seal_request(
            job_id=job_id,
            workspace_id=workspace_id,
            authority=copy.deepcopy(dict(authority)),
            project=snapshot["project"],
            lineage_documents=lineage,
            manifest_id=str(operation_params["manifest_id"]),
            target_grant_id=str(operation_params["target_grant_id"]),
            target_grant_generation=int(operation_params["target_grant_generation"]),
            staged_inputs=staged_inputs,
        )
        try:
            result = execute_private_creation_request(request, artifact_root=root)
            if len(result.outputs) != 2 or result.analysis_status != "passed":
                raise ValueError("asset release preflight did not produce exact outputs")
            release_manifest = decode_json_object(
                result.outputs[0].payload,
                source="asset release preflight manifest",
            )
            assetpack_manifest = decode_json_object(
                result.outputs[1].payload,
                source="asset release preflight assetpack",
            )
        except (RuntimeIOError, TypeError, ValueError) as exc:
            raise conflict("Asset release preflight is not integral") from exc
        return (
            request,
            lineage,
            tuple(staged_payloads),
            release_manifest,
            assetpack_manifest,
        )

    def create_asset_release_seal(self, params: object) -> dict[str, Any]:
        self._asset_release_seal_params(params)
        raise invalid_state(
            "asset_release_authority_required: new asset releases require retained "
            "v10 QA reviews and asset.release.authorize v11"
        )

    @staticmethod
    def _runtime_compose_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("runtime.compose params must be an object")
        common = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
        }
        operation_fields = {
            "gamepack_artifact_id",
            "asset_inventory_artifact_id",
            "assetpack_artifact_id",
            "target_grant_id",
            "expected_target_grant_generation",
        }
        allowed = common | operation_fields
        required = allowed - {"job_id"}
        invalid = (required - set(value)) | (set(value) - allowed)
        if invalid:
            raise invalid_request(
                "runtime.compose params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "runtime.compose":
            raise invalid_request("runtime.compose operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        operation_params = {
            "gamepack_artifact_id": value["gamepack_artifact_id"],
            "asset_inventory_artifact_id": value["asset_inventory_artifact_id"],
            "assetpack_artifact_id": value["assetpack_artifact_id"],
            "target_grant_id": value["target_grant_id"],
            "target_grant_generation": value["expected_target_grant_generation"],
        }
        try:
            _validate_runtime_compose_operation_params(
                operation_params,
                "runtime.compose params",
            )
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        return value

    def _runtime_compose_documents(
        self,
        *,
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], sqlite3.Row]:
        expected = (
            (
                str(operation_params["gamepack_artifact_id"]),
                "world-forge.gamepack",
            ),
            (
                str(operation_params["asset_inventory_artifact_id"]),
                "world-forge.asset_inventory",
            ),
            (
                str(operation_params["assetpack_artifact_id"]),
                "world-forge.assetpack",
            ),
        )
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        selected: list[dict[str, Any]] = []
        for artifact_id, expected_format in expected:
            record = records.get(artifact_id)
            if (
                record is None
                or record["lifecycle"] not in {"active", "candidate"}
                or record["subject"]["format"] != expected_format
            ):
                raise conflict("Runtime composition input artifact is not current")
            subject = record["subject"]
            key = (
                subject["format"],
                subject["format_version"],
                subject["id"],
                subject["content_hash"],
            )
            document = snapshot["documents"].get(key)
            if document is None:
                raise invalid_state("Runtime composition input document is unavailable")
            selected.append(copy.deepcopy(document))

        assetpack_row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (
                snapshot["authority"]["workspace_id"],
                operation_params["assetpack_artifact_id"],
            ),
        ).fetchone()
        if assetpack_row is None:
            raise conflict("Runtime composition assetpack candidate is unavailable")
        producer_job_id = str(assetpack_row["producer_job_id"] or "")
        if not producer_job_id:
            raise conflict("Runtime composition assetpack has no authorized publication")
        producer_operation = str(assetpack_row["producer_operation"] or "")
        if producer_operation != "asset.release.authorize":
            raise conflict(
                "Runtime composition assetpack requires asset.release.authorize v11 authority"
            )
        publication_identity = _assetpack_candidate_publication(
            self.store,
            assetpack_row,
            workspace_id=str(snapshot["authority"]["workspace_id"]),
            job_id=producer_job_id,
            artifact_id=str(operation_params["assetpack_artifact_id"]),
            producer_operation=producer_operation,
        )
        release_job = self.get(producer_job_id)
        if (
            release_job["format_version"] != 11
            or release_job["state"] != "succeeded"
            or release_job["progress"] != "committed"
            or release_job["operation"] != "asset.release.authorize"
            or release_job["result"]["release_status"] != "authorized"
            or release_job["result"]["publication"] is None
        ):
            raise conflict("Runtime composition assetpack authority is incomplete")
        publication = release_job["result"]["publication"]
        grant = self.output_grants.get(operation_params["target_grant_id"])
        if (
            publication["grant_generation"] != operation_params["target_grant_generation"]
            or grant["generation"] != operation_params["target_grant_generation"]
        ):
            raise conflict("Runtime composition output grant generation changed")
        if (
            publication["grant_id"] != operation_params["target_grant_id"]
            or publication["assetpack"] != publication_identity
            or grant["workspace_id"] != snapshot["authority"]["workspace_id"]
            or grant["state"] != "published"
            or grant["publication"] != publication_identity
        ):
            raise conflict("Runtime composition assetpack publication authority changed")
        return selected[0], selected[1], selected[2], assetpack_row

    def _verified_runtime_compose_request(
        self,
        *,
        job_id: str,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], tuple[tuple[str, bytes], ...]]:
        gamepack, inventory, assetpack, _assetpack_row = self._runtime_compose_documents(
            snapshot=snapshot,
            operation_params=operation_params,
        )
        direct_lineage = (gamepack, inventory, assetpack)
        source_keys = {
            self._document_key(document)
            for document in (
                snapshot["project"].project,
                snapshot["project"].profile,
                snapshot["project"].manifest,
                *snapshot["project"].world_modules,
                *snapshot["project"].activity_modules,
                *snapshot["project"].narrative_modules,
                *snapshot["project"].system_modules,
                *snapshot["project"].logic_modules,
            )
        }
        records_by_key = {
            (
                record["subject"]["format"],
                record["subject"]["format_version"],
                record["subject"]["id"],
                record["subject"]["content_hash"],
            ): record
            for record in snapshot["records"]
        }
        validation_documents = {
            self._document_key(document): copy.deepcopy(document) for document in direct_lineage
        }
        pending = [
            identity
            for document in direct_lineage
            for identity in artifact_dependency_identities(document)
        ]
        while pending:
            identity = pending.pop()
            key = (
                str(identity["format"]),
                int(identity["format_version"]),
                str(identity["id"]),
                str(identity["content_hash"]),
            )
            if key in source_keys or key in validation_documents:
                continue
            record = records_by_key.get(key)
            if record is None or record["lifecycle"] not in {"active", "candidate"}:
                raise conflict("Runtime composition lineage is incomplete or stale")
            document = snapshot["documents"].get(key)
            if document is None:
                raise invalid_state("Runtime composition lineage document is unavailable")
            validation_documents[key] = copy.deepcopy(document)
            pending.extend(artifact_dependency_identities(document))
        validation_lineage = tuple(
            validation_documents[key]
            for key in sorted(
                validation_documents,
                key=lambda item: (
                    item[0].encode("utf-8"),
                    item[1],
                    item[2].encode("utf-8"),
                    item[3],
                ),
            )
        )
        binding = self.output_grants.published_binding(
            grant_id=str(operation_params["target_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["target_grant_generation"]),
        )
        staged_inputs: list[dict[str, Any]] = []
        staged_payloads: list[tuple[str, bytes]] = []
        try:
            with verify_generic_assetpack(
                binding["path"],
                expected_content_hash=str(assetpack["content_hash"]),
                expected_parent_identity=tuple(binding["parent_identity"]),
            ) as verified:
                if (
                    verified.manifest != assetpack
                    or tuple(verified.root_identity) != tuple(binding["published_identity"])
                    or assetpack["asset_inventory"]["content_hash"] != inventory["content_hash"]
                ):
                    raise conflict("Runtime composition published assetpack bytes changed")
                for locator, payload in sorted(
                    verified.files.items(),
                    key=lambda item: item[0].encode("utf-8"),
                ):
                    staged_inputs.append(
                        {
                            "source_locator": locator,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "size_bytes": len(payload),
                        }
                    )
                    staged_payloads.append((locator, payload))
        except StudioError:
            raise
        except (GenericAssetpackError, OSError, TypeError, ValueError) as exc:
            raise conflict("Runtime composition published assetpack is not integral") from exc

        current_binding = self.output_grants.published_binding(
            grant_id=str(operation_params["target_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["target_grant_generation"]),
        )
        if (
            current_binding["path"] != binding["path"]
            or current_binding["parent_identity"] != binding["parent_identity"]
            or current_binding["published_identity"] != binding["published_identity"]
        ):
            raise conflict("Runtime composition published assetpack authority changed")
        request = build_private_runtime_compose_request(
            job_id=job_id,
            workspace_id=workspace_id,
            authority=copy.deepcopy(dict(authority)),
            project=snapshot["project"],
            lineage_documents=direct_lineage,
            target_grant_id=str(operation_params["target_grant_id"]),
            target_grant_generation=int(operation_params["target_grant_generation"]),
            staged_inputs=staged_inputs,
        )
        try:
            result = execute_private_creation_request(
                request,
                artifact_root=Path(binding["path"]),
            )
        except (RuntimeIOError, TypeError, ValueError) as exc:
            raise conflict("Runtime composition preflight is not integral") from exc
        if len(result.outputs) != 4 or result.analysis_status != "passed":
            raise conflict("Runtime composition preflight did not produce exact outputs")
        return request, validation_lineage, tuple(staged_payloads)

    def create_runtime_compose(self, params: object) -> dict[str, Any]:
        parsed = self._runtime_compose_params(params)
        snapshot = self.evidence._snapshot(  # noqa: SLF001
            {
                "workspace_id": parsed["workspace_id"],
                "expected_root_generation": parsed["expected_root_generation"],
                "expected_source_revision": parsed["expected_source_revision"],
                "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
                "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
            }
        )
        job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
        authority = {
            "root_generation": parsed["expected_root_generation"],
            "source_revision": parsed["expected_source_revision"],
            "workflow_status_hash": parsed["expected_workflow_status_hash"],
            "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        operation_params = {
            "gamepack_artifact_id": parsed["gamepack_artifact_id"],
            "asset_inventory_artifact_id": parsed["asset_inventory_artifact_id"],
            "assetpack_artifact_id": parsed["assetpack_artifact_id"],
            "target_grant_id": parsed["target_grant_id"],
            "target_grant_generation": parsed["expected_target_grant_generation"],
        }
        request, _lineage, _payloads = self._verified_runtime_compose_request(
            job_id=job_id,
            workspace_id=parsed["workspace_id"],
            authority=authority,
            snapshot=snapshot,
            operation_params=operation_params,
        )
        timestamp = utc_now()
        record: dict[str, Any] = {
            "format": CREATION_JOB_FORMAT,
            "format_version": 4,
            "job_id": job_id,
            "workspace_id": parsed["workspace_id"],
            "operation": "runtime.compose",
            "operation_params": operation_params,
            "state": "queued",
            "generation": 0,
            "authority": authority,
            "inputs": copy.deepcopy(request["inputs"]),
            "progress": "queued",
            "result": None,
            "error": None,
            "created_at": timestamp,
            "started_at": None,
            "finished_at": None,
            "updated_at": timestamp,
            "record_hash": "",
        }
        record["record_hash"] = creation_job_record_hash(record)
        try:
            validate_studio_creation_job(record)
        except StudioContractError as exc:
            raise StudioError(
                "internal_error",
                "Runtime composition job record is invalid",
            ) from exc
        try:
            with self.store.connection:
                self.store.connection.execute(
                    "INSERT INTO creation_jobs "
                    "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                    "VALUES (?, ?, 'runtime.compose', 'queued', 'queued', 0, ?)",
                    (job_id, parsed["workspace_id"], encode_json(record)),
                )
                for position, item in enumerate(record["inputs"]):
                    subject = item["subject"]
                    self.store.connection.execute(
                        "INSERT INTO creation_job_inputs "
                        "(job_id, position, artifact_id, subject_format, subject_version, "
                        "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            job_id,
                            position,
                            item["artifact_id"],
                            subject["format"],
                            subject["format_version"],
                            subject["id"],
                            subject["content_hash"],
                        ),
                    )
                self.store.record_creation_event(
                    workspace_id=record["workspace_id"],
                    topic="creation_job.queued",
                    entity_type="creation_job",
                    entity_id=job_id,
                    payload={"operation": "runtime.compose", "generation": 0},
                    created_at=timestamp,
                )
        except sqlite3.IntegrityError as exc:
            raise conflict(f"Creation job {job_id} already exists") from exc
        return record

    @staticmethod
    def _runtime_bundle_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("runtime.bundle.build params must be an object")
        common = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
        }
        operation_fields = {
            "gamepack_artifact_id",
            "asset_inventory_artifact_id",
            "assetpack_artifact_id",
            "runtime_snapshot_artifact_id",
            "runtime_adapter_registry_artifact_id",
            "runtime_composition_artifact_id",
            "runtime_support_report_artifact_id",
            "source_grant_id",
            "expected_source_grant_generation",
            "target_grant_id",
            "expected_target_grant_generation",
        }
        allowed = common | operation_fields
        required = allowed - {"job_id"}
        invalid = (required - set(value)) | (set(value) - allowed)
        if invalid:
            raise invalid_request(
                "runtime.bundle.build params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "runtime.bundle.build":
            raise invalid_request("runtime.bundle.build operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        operation_params = {
            field: value[field] for field in operation_fields if not field.startswith("expected_")
        }
        operation_params["source_grant_generation"] = value["expected_source_grant_generation"]
        operation_params["target_grant_generation"] = value["expected_target_grant_generation"]
        try:
            _validate_runtime_bundle_operation_params(
                operation_params,
                "runtime.bundle.build params",
            )
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        return value

    def _runtime_bundle_documents(
        self,
        *,
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        source_params = {
            "gamepack_artifact_id": operation_params["gamepack_artifact_id"],
            "asset_inventory_artifact_id": operation_params["asset_inventory_artifact_id"],
            "assetpack_artifact_id": operation_params["assetpack_artifact_id"],
            "target_grant_id": operation_params["source_grant_id"],
            "target_grant_generation": operation_params["source_grant_generation"],
        }
        gamepack, inventory, assetpack, _assetpack_row = self._runtime_compose_documents(
            snapshot=snapshot,
            operation_params=source_params,
        )
        runtime_fields = (
            ("runtime_snapshot_artifact_id", "world-forge.game_runtime_snapshot", 0),
            (
                "runtime_adapter_registry_artifact_id",
                "world-forge.runtime_adapter_registry",
                1,
            ),
            ("runtime_composition_artifact_id", "world-forge.game_runtime_composition", 2),
            ("runtime_support_report_artifact_id", "world-forge.runtime_support_report", 3),
        )
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        runtime_documents: list[dict[str, Any]] = []
        producer_job_id: str | None = None
        for field, expected_format, expected_position in runtime_fields:
            artifact_id = str(operation_params[field])
            record = records.get(artifact_id)
            if (
                record is None
                or record["lifecycle"] != "candidate"
                or record["subject"]["format"] != expected_format
            ):
                raise conflict("Runtime bundle composition candidate is not current")
            row = self.store.connection.execute(
                "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
                (snapshot["authority"]["workspace_id"], artifact_id),
            ).fetchone()
            if (
                row is None
                or row["producer_operation"] != "runtime.compose"
                or int(row["producer_output_position"]) != expected_position
                or not row["producer_job_id"]
            ):
                raise conflict("Runtime bundle composition producer changed")
            current_producer = str(row["producer_job_id"])
            if producer_job_id is None:
                producer_job_id = current_producer
            elif producer_job_id != current_producer:
                raise conflict("Runtime bundle composition outputs cross producer jobs")
            subject = record["subject"]
            key = (
                subject["format"],
                subject["format_version"],
                subject["id"],
                subject["content_hash"],
            )
            document = snapshot["documents"].get(key)
            if document is None:
                raise invalid_state("Runtime bundle composition document is unavailable")
            runtime_documents.append(copy.deepcopy(document))
        if producer_job_id is None:
            raise conflict("Runtime bundle composition producer is unavailable")
        compose_job = self.get(producer_job_id)
        expected_runtime_ids = [
            str(operation_params[field]) for field, _format, _pos in runtime_fields
        ]
        if (
            compose_job["workspace_id"] != snapshot["authority"]["workspace_id"]
            or compose_job["operation"] != "runtime.compose"
            or compose_job["state"] != "succeeded"
            or compose_job["progress"] != "committed"
            or compose_job["result"]["output_artifact_ids"] != expected_runtime_ids
            or compose_job["operation_params"] != source_params
        ):
            raise conflict("Runtime bundle composition job authority changed")
        return (gamepack, inventory, assetpack, *runtime_documents)

    def _verified_runtime_bundle_request(
        self,
        *,
        job_id: str,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any],
        tuple[dict[str, Any], ...],
        tuple[tuple[str, bytes], ...],
        dict[str, Any],
    ]:
        lineage = self._runtime_bundle_documents(
            snapshot=snapshot,
            operation_params=operation_params,
        )
        assetpack = lineage[2]
        binding = self.output_grants.published_binding(
            grant_id=str(operation_params["source_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["source_grant_generation"]),
        )
        staged_inputs: list[dict[str, Any]] = []
        staged_payloads: list[tuple[str, bytes]] = []
        try:
            with verify_generic_assetpack(
                binding["path"],
                expected_content_hash=str(assetpack["content_hash"]),
                expected_parent_identity=tuple(binding["parent_identity"]),
            ) as verified:
                if verified.manifest != assetpack or tuple(verified.root_identity) != tuple(
                    binding["published_identity"]
                ):
                    raise conflict("Runtime bundle source assetpack bytes changed")
                for locator, payload in sorted(
                    verified.files.items(), key=lambda item: item[0].encode("utf-8")
                ):
                    staged_inputs.append(
                        {
                            "source_locator": locator,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "size_bytes": len(payload),
                        }
                    )
                    staged_payloads.append((locator, payload))
        except StudioError:
            raise
        except (GenericAssetpackError, OSError, TypeError, ValueError) as exc:
            raise conflict("Runtime bundle source assetpack is not integral") from exc
        request = build_private_runtime_bundle_request(
            job_id=job_id,
            workspace_id=workspace_id,
            authority=copy.deepcopy(dict(authority)),
            project=snapshot["project"],
            lineage_documents=lineage,
            source_grant_id=str(operation_params["source_grant_id"]),
            source_grant_generation=int(operation_params["source_grant_generation"]),
            target_grant_id=str(operation_params["target_grant_id"]),
            target_grant_generation=int(operation_params["target_grant_generation"]),
            staged_inputs=staged_inputs,
        )
        try:
            result = execute_private_creation_request(request, artifact_root=Path(binding["path"]))
            if len(result.outputs) != 1 or result.analysis_status != "passed":
                raise ValueError("runtime bundle preflight did not produce one manifest")
            manifest = decode_json_object(
                result.outputs[0].payload,
                source="runtime bundle preflight manifest",
            )
        except (RuntimeIOError, TypeError, ValueError) as exc:
            raise conflict("Runtime bundle preflight is not integral") from exc
        confirmed = self.output_grants.published_binding(
            grant_id=str(operation_params["source_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["source_grant_generation"]),
        )
        if (
            confirmed["path"] != binding["path"]
            or confirmed["parent_identity"] != binding["parent_identity"]
            or confirmed["published_identity"] != binding["published_identity"]
        ):
            raise conflict("Runtime bundle source assetpack authority changed")
        return request, lineage, tuple(staged_payloads), manifest

    def create_runtime_bundle(self, params: object) -> dict[str, Any]:
        parsed = self._runtime_bundle_params(params)
        snapshot = self.evidence._snapshot(  # noqa: SLF001
            {
                "workspace_id": parsed["workspace_id"],
                "expected_root_generation": parsed["expected_root_generation"],
                "expected_source_revision": parsed["expected_source_revision"],
                "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
                "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
            }
        )
        job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
        authority = {
            "root_generation": parsed["expected_root_generation"],
            "source_revision": parsed["expected_source_revision"],
            "workflow_status_hash": parsed["expected_workflow_status_hash"],
            "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        requested_target_generation = int(parsed["expected_target_grant_generation"])
        operation_params = {
            field: parsed[field]
            for field in (
                "gamepack_artifact_id",
                "asset_inventory_artifact_id",
                "assetpack_artifact_id",
                "runtime_snapshot_artifact_id",
                "runtime_adapter_registry_artifact_id",
                "runtime_composition_artifact_id",
                "runtime_support_report_artifact_id",
                "source_grant_id",
                "target_grant_id",
            )
        }
        operation_params["source_grant_generation"] = parsed["expected_source_grant_generation"]
        operation_params["target_grant_generation"] = requested_target_generation + 1
        target_grant = self.output_grants.get(parsed["target_grant_id"])
        if (
            target_grant["format_version"] != 2
            or target_grant["kind"] != "game_runtime_bundle_directory"
            or target_grant["state"] != "ready"
            or target_grant["generation"] != requested_target_generation
        ):
            raise conflict("Runtime bundle target grant is not ready")
        request, _lineage, _payloads, manifest = self._verified_runtime_bundle_request(
            job_id=job_id,
            workspace_id=parsed["workspace_id"],
            authority=authority,
            snapshot=snapshot,
            operation_params=operation_params,
        )
        timestamp = utc_now()
        record: dict[str, Any] = {
            "format": CREATION_JOB_FORMAT,
            "format_version": 5,
            "job_id": job_id,
            "workspace_id": parsed["workspace_id"],
            "operation": "runtime.bundle.build",
            "operation_params": operation_params,
            "state": "queued",
            "generation": 0,
            "authority": authority,
            "inputs": copy.deepcopy(request["inputs"]),
            "progress": "queued",
            "result": None,
            "error": None,
            "created_at": timestamp,
            "started_at": None,
            "finished_at": None,
            "updated_at": timestamp,
            "record_hash": "",
        }
        record["record_hash"] = creation_job_record_hash(record)
        try:
            validate_studio_creation_job(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Runtime bundle job record is invalid") from exc
        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            current = self.evidence._snapshot(  # noqa: SLF001
                {
                    "workspace_id": parsed["workspace_id"],
                    "expected_root_generation": parsed["expected_root_generation"],
                    "expected_source_revision": parsed["expected_source_revision"],
                    "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
                    "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
                }
            )
            if current["artifact_snapshot_hash"] != snapshot["artifact_snapshot_hash"]:
                raise conflict("Runtime bundle artifact authority changed before reservation")
            self.output_grants.published_binding(
                grant_id=str(operation_params["source_grant_id"]),
                workspace_id=parsed["workspace_id"],
                expected_generation=int(operation_params["source_grant_generation"]),
            )
            reserved, _binding = self.output_grants.reserve_for_job(
                grant_id=parsed["target_grant_id"],
                job_id=job_id,
                workspace_id=parsed["workspace_id"],
                expected_generation=requested_target_generation,
                expected_manifest_hash=str(manifest["content_hash"]),
                expected_tree_hash=str(manifest["tree_hash"]),
            )
            if reserved["generation"] != operation_params["target_grant_generation"]:
                raise conflict("Runtime bundle target reservation generation changed")
            self.store.connection.execute(
                "INSERT INTO creation_jobs "
                "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                "VALUES (?, ?, 'runtime.bundle.build', 'queued', 'queued', 0, ?)",
                (job_id, parsed["workspace_id"], encode_json(record)),
            )
            for position, item in enumerate(record["inputs"]):
                subject = item["subject"]
                self.store.connection.execute(
                    "INSERT INTO creation_job_inputs "
                    "(job_id, position, artifact_id, subject_format, subject_version, "
                    "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        position,
                        item["artifact_id"],
                        subject["format"],
                        subject["format_version"],
                        subject["id"],
                        subject["content_hash"],
                    ),
                )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_job.queued",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"operation": "runtime.bundle.build", "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation job {job_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _runtime_headless_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("runtime.headless.verify params must be an object")
        common = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
        }
        operation_fields = {
            "gamepack_artifact_id",
            "asset_inventory_artifact_id",
            "assetpack_artifact_id",
            "asset_release_authority_artifact_id",
            "runtime_snapshot_artifact_id",
            "runtime_adapter_registry_artifact_id",
            "runtime_composition_artifact_id",
            "runtime_bundle_artifact_id",
            "source_grant_id",
            "expected_source_grant_generation",
            "platform_id",
            "headless_script_artifact_id",
            "target_grant_id",
            "expected_target_grant_generation",
        }
        allowed = common | operation_fields
        required = allowed - {"job_id"}
        invalid = (required - set(value)) | (set(value) - allowed)
        if invalid:
            raise invalid_request(
                "runtime.headless.verify params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "runtime.headless.verify":
            raise invalid_request("runtime.headless.verify operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        try:
            _validate_runtime_headless_verify_operation_params(
                {field: value[field] for field in operation_fields},
                "runtime.headless.verify params",
            )
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        return value

    def _runtime_headless_documents(
        self,
        *,
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        bindings = (
            ("gamepack_artifact_id", "world-forge.gamepack"),
            ("asset_inventory_artifact_id", "world-forge.asset_inventory"),
            ("assetpack_artifact_id", "world-forge.assetpack"),
            (
                "asset_release_authority_artifact_id",
                "world-forge.asset_release_authority",
            ),
            ("runtime_snapshot_artifact_id", "world-forge.game_runtime_snapshot"),
            (
                "runtime_adapter_registry_artifact_id",
                "world-forge.runtime_adapter_registry",
            ),
            (
                "runtime_composition_artifact_id",
                "world-forge.game_runtime_composition",
            ),
            ("runtime_bundle_artifact_id", "world-forge.game_runtime_bundle"),
            ("headless_script_artifact_id", "world-forge.game_execution_script"),
        )
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        documents = snapshot["documents"]
        selected: list[dict[str, Any]] = []
        for field, expected_format in bindings:
            artifact_id = str(operation_params[field])
            record = records.get(artifact_id)
            if (
                record is None
                or record["lifecycle"] not in {"active", "candidate"}
                or record["subject"]["format"] != expected_format
                or int(record["subject"]["format_version"]) != 1
            ):
                raise conflict(f"Runtime headless {field} is not current")
            subject = record["subject"]
            key = (
                subject["format"],
                subject["format_version"],
                subject["id"],
                subject["content_hash"],
            )
            document = documents.get(key)
            if (
                document is None
                or artifact_id_for_identity(document_identity(document)) != artifact_id
            ):
                raise invalid_state(f"Runtime headless {field} document is unavailable")
            selected.append(copy.deepcopy(document))
        return tuple(selected)

    def _verified_runtime_headless_request(
        self,
        *,
        job_id: str,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any],
        tuple[dict[str, Any], ...],
        tuple[tuple[str, bytes], ...],
        tuple[dict[str, Any], ...],
        dict[str, Any],
    ]:
        documents = self._runtime_headless_documents(
            snapshot=snapshot,
            operation_params=operation_params,
        )
        assetpack = documents[2]
        release = documents[3]
        runtime_bundle = documents[7]
        script = documents[8]

        release_row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, operation_params["asset_release_authority_artifact_id"]),
        ).fetchone()
        assetpack_row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, operation_params["assetpack_artifact_id"]),
        ).fetchone()
        if release_row is None or assetpack_row is None:
            raise conflict("Runtime headless asset authority artifacts are unavailable")
        release_producer = self.get(str(release_row["producer_job_id"]))
        if (
            release_producer["format_version"] != 11
            or release_producer["operation"] != "asset.release.authorize"
            or release_producer["state"] != "succeeded"
            or release_row["producer_operation"] != "asset.release.authorize"
            or int(release_row["producer_output_position"]) != 2
            or assetpack_row["producer_job_id"] != release_row["producer_job_id"]
            or assetpack_row["producer_operation"] != "asset.release.authorize"
            or int(assetpack_row["producer_output_position"]) != 1
        ):
            raise conflict("Runtime headless asset release is not exact v11 authority")
        release_result = release_producer["result"]
        if (
            release_result is None
            or release_result["release_status"] != "authorized"
            or release_result["publication"] is None
            or release_result["output_artifact_ids"][1] != operation_params["assetpack_artifact_id"]
            or release_result["output_artifact_ids"][2]
            != operation_params["asset_release_authority_artifact_id"]
        ):
            raise conflict("Runtime headless requires an authorized v11 release")
        (
            release_request,
            release_dependencies,
            release_payloads,
            expected_manifest,
            expected_assetpack,
            expected_release,
        ) = self._verified_asset_release_authorize_request(
            job_id=str(release_producer["job_id"]),
            workspace_id=workspace_id,
            authority=release_producer["authority"],
            snapshot=snapshot,
            operation_params=release_producer["operation_params"],
        )
        retained_manifest = self.artifacts.get_document(
            workspace_id,
            str(release_result["output_artifact_ids"][0]),
        )
        if (
            retained_manifest != expected_manifest
            or assetpack != expected_assetpack
            or release != expected_release
        ):
            raise conflict("Runtime headless retained v11 release authority changed")
        publication = release_result["publication"]
        assetpack_grant = self.output_grants.get(str(publication["grant_id"]))
        if (
            assetpack_grant["workspace_id"] != workspace_id
            or assetpack_grant["kind"] != "generic_assetpack_directory"
            or assetpack_grant["state"] != "published"
            or assetpack_grant["generation"] != publication["grant_generation"]
        ):
            raise conflict("Runtime headless v11 assetpack publication changed")
        assetpack_binding = self.output_grants.published_binding(
            grant_id=str(publication["grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(publication["grant_generation"]),
        )
        assetpack_handle = verify_generic_assetpack(
            assetpack_binding["path"],
            expected_content_hash=str(assetpack["content_hash"]),
        )
        try:
            if (
                assetpack_handle.manifest != assetpack
                or assetpack_handle.root_identity != assetpack_binding["published_identity"]
            ):
                raise conflict("Runtime headless assetpack publication identity changed")
            assetpack_files = dict(assetpack_handle.files)
        finally:
            assetpack_handle.close()

        runtime_row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, operation_params["runtime_bundle_artifact_id"]),
        ).fetchone()
        if runtime_row is None:
            raise conflict("Runtime headless runtime bundle artifact is unavailable")
        runtime_producer = self.get(str(runtime_row["producer_job_id"]))
        runtime_publication = (
            None
            if runtime_producer["result"] is None
            else runtime_producer["result"]["publication"]
        )
        if (
            runtime_producer["format_version"] != 5
            or runtime_producer["operation"] != "runtime.bundle.build"
            or runtime_producer["state"] != "succeeded"
            or runtime_row["producer_operation"] != "runtime.bundle.build"
            or int(runtime_row["producer_output_position"]) != 0
            or runtime_publication is None
            or runtime_publication["grant_id"] != operation_params["source_grant_id"]
            or runtime_publication["grant_generation"]
            != operation_params["expected_source_grant_generation"]
        ):
            raise conflict("Runtime headless runtime bundle is not exact v5 authority")
        source_grant = self.output_grants.get(str(operation_params["source_grant_id"]))
        if (
            source_grant["workspace_id"] != workspace_id
            or source_grant["kind"] != "game_runtime_bundle_directory"
            or source_grant["state"] != "published"
            or source_grant["generation"] != operation_params["expected_source_grant_generation"]
        ):
            raise conflict("Runtime headless source grant changed")
        runtime_binding = self.output_grants.published_binding(
            grant_id=str(operation_params["source_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["expected_source_grant_generation"]),
        )
        runtime_handle = verify_game_runtime_bundle(
            runtime_binding["path"],
            expected_content_hash=str(runtime_bundle["content_hash"]),
        )
        try:
            if (
                runtime_handle.manifest != runtime_bundle
                or runtime_handle.root_identity != runtime_binding["published_identity"]
            ):
                raise conflict("Runtime headless runtime bundle publication changed")
            runtime_files = dict(runtime_handle.files)
        finally:
            runtime_handle.close()

        payload_by_locator = {locator: payload for locator, payload in release_payloads}
        for relative, payload in assetpack_files.items():
            payload_by_locator[f"assetpack/{relative}"] = payload
        for relative, payload in runtime_files.items():
            payload_by_locator[f"runtime-bundle/{relative}"] = payload
        payload_by_locator["execution/script.json"] = serialize_game_execution_script(script)
        staged_payloads = tuple(
            sorted(payload_by_locator.items(), key=lambda item: item[0].encode("utf-8"))
        )
        staged_inputs = [
            {
                "source_locator": locator,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
            for locator, payload in staged_payloads
        ]
        request = build_private_runtime_headless_request(
            job_id=job_id,
            workspace_id=workspace_id,
            authority=copy.deepcopy(dict(authority)),
            project=snapshot["project"],
            artifact_documents=documents,
            asset_release_request=release_request,
            platform_id=str(operation_params["platform_id"]),
            source_grant_id=str(operation_params["source_grant_id"]),
            source_grant_generation=int(operation_params["expected_source_grant_generation"]),
            target_grant_id=str(operation_params["target_grant_id"]),
            target_grant_generation=int(operation_params["expected_target_grant_generation"]) + 1,
            staged_inputs=staged_inputs,
        )
        try:
            with tempfile.TemporaryDirectory(prefix="world-forge-headless-preflight-") as raw:
                stage = Path(raw)
                stage_private_asset_inputs(stage, request, staged_payloads)
                result = execute_private_creation_request(
                    request,
                    artifact_root=stage / "artifact_root",
                )
                if len(result.outputs) != 3:
                    raise ValueError("runtime headless output count differs")
                output_documents = tuple(
                    decode_json_object(
                        output.payload,
                        source=f"runtime headless preflight output {index}",
                    )
                    for index, output in enumerate(result.outputs)
                )
                evidence_set = verify_headless_evidence_set(
                    stage / "artifact_root" / "headless-evidence",
                    bundle_root=stage / "artifact_root" / "runtime-bundle",
                )
                try:
                    evidence_manifest = evidence_set.manifest
                finally:
                    evidence_set.close()
            if (
                result.analysis_status != "passed"
                or output_documents[0]["supported"] is not False
                or output_documents[0]["release_status"] != "blocked"
                or output_documents[1]["execution_status"] != "headless_verified"
                or output_documents[1]["platform"]["platform_id"] != operation_params["platform_id"]
                or output_documents[2]["supported"] is not False
                or output_documents[2]["dimensions"]["release"] != "blocked"
            ):
                raise ValueError("runtime headless preflight authority overclaims support")
        except (
            GenericHeadlessError,
            RuntimeIOError,
            RuntimeSupportAuthorityError,
            ValueError,
        ) as exc:
            raise conflict("Runtime headless preflight is not integral") from exc
        dependency_closure: list[dict[str, Any]] = []
        dependency_keys: set[tuple[str, int, str, str]] = set()
        source_keys = {
            (
                str(identity["format"]),
                int(identity["format_version"]),
                str(identity["id"]),
                str(identity["content_hash"]),
            )
            for identity in (
                document_identity(document)
                for document in (
                    snapshot["project"].project,
                    snapshot["project"].profile,
                    snapshot["project"].manifest,
                    *snapshot["project"].world_modules,
                    *snapshot["project"].activity_modules,
                    *snapshot["project"].narrative_modules,
                    *snapshot["project"].system_modules,
                    *snapshot["project"].logic_modules,
                )
            )
        }
        pending_dependencies = list((*documents, retained_manifest, *release_dependencies))
        while pending_dependencies:
            document = pending_dependencies.pop(0)
            identity = document_identity(document)
            key = (
                str(identity["format"]),
                int(identity["format_version"]),
                str(identity["id"]),
                str(identity["content_hash"]),
            )
            if key in dependency_keys:
                continue
            dependency_keys.add(key)
            dependency_closure.append(copy.deepcopy(dict(document)))
            for dependency in artifact_dependency_identities(document):
                dependency_key = (
                    str(dependency["format"]),
                    int(dependency["format_version"]),
                    str(dependency["id"]),
                    str(dependency["content_hash"]),
                )
                if dependency_key in dependency_keys or dependency_key in source_keys:
                    continue
                retained_dependency = snapshot["documents"].get(dependency_key)
                if retained_dependency is None:
                    raise conflict("Runtime headless retained dependency closure is incomplete")
                pending_dependencies.append(retained_dependency)
        return (
            request,
            tuple(dependency_closure),
            staged_payloads,
            output_documents,
            evidence_manifest,
        )

    def _verify_pending_runtime_headless_grants(
        self,
        job: Mapping[str, Any],
        *,
        evidence_manifest: Mapping[str, Any],
    ) -> None:
        params = job["operation_params"]
        source = self.output_grants.get(str(params["source_grant_id"]))
        if (
            source["workspace_id"] != job["workspace_id"]
            or source["kind"] != "game_runtime_bundle_directory"
            or source["state"] != "published"
            or source["generation"] != params["expected_source_grant_generation"]
        ):
            raise conflict("Pending runtime headless source grant changed")
        row = self.store.connection.execute(
            "SELECT * FROM creation_output_grants WHERE grant_id = ?",
            (params["target_grant_id"],),
        ).fetchone()
        if (
            row is None
            or row["workspace_id"] != job["workspace_id"]
            or row["kind"] != "headless_evidence_directory"
            or row["reserved_job_id"] != job["job_id"]
            or row["state"] not in {"reserved", "recovery_required", "published"}
            or int(row["generation"]) != params["expected_target_grant_generation"] + 1
            or row["expected_manifest_hash"] != evidence_manifest["content_hash"]
            or row["expected_tree_hash"] != evidence_manifest["tree_hash"]
        ):
            raise conflict("Pending runtime headless target grant changed")

    def create_runtime_headless(self, params: object) -> dict[str, Any]:
        parsed = self._runtime_headless_params(params)
        snapshot_params = {
            "workspace_id": parsed["workspace_id"],
            "expected_root_generation": parsed["expected_root_generation"],
            "expected_source_revision": parsed["expected_source_revision"],
            "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
            "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        snapshot = self.evidence._snapshot(snapshot_params)  # noqa: SLF001
        job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
        authority = {
            "root_generation": parsed["expected_root_generation"],
            "source_revision": parsed["expected_source_revision"],
            "workflow_status_hash": parsed["expected_workflow_status_hash"],
            "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        operation_fields = (
            "gamepack_artifact_id",
            "asset_inventory_artifact_id",
            "assetpack_artifact_id",
            "asset_release_authority_artifact_id",
            "runtime_snapshot_artifact_id",
            "runtime_adapter_registry_artifact_id",
            "runtime_composition_artifact_id",
            "runtime_bundle_artifact_id",
            "source_grant_id",
            "expected_source_grant_generation",
            "platform_id",
            "headless_script_artifact_id",
            "target_grant_id",
            "expected_target_grant_generation",
        )
        operation_params = {field: copy.deepcopy(parsed[field]) for field in operation_fields}
        self.store.connection.execute(f"PRAGMA busy_timeout = {_ATOMIC_SUBMISSION_BUSY_TIMEOUT_MS}")
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.evidence._snapshot(snapshot_params)  # noqa: SLF001
            if current["artifact_snapshot_hash"] != snapshot["artifact_snapshot_hash"]:
                raise conflict("Runtime headless authority changed before persistence")
            request, _dependencies, _payloads, _outputs, evidence_manifest = (
                self._verified_runtime_headless_request(
                    job_id=job_id,
                    workspace_id=parsed["workspace_id"],
                    authority=authority,
                    snapshot=current,
                    operation_params=operation_params,
                )
            )
            inputs = copy.deepcopy(request["inputs"])
            matches = self._pending_exact_jobs(
                workspace_id=parsed["workspace_id"],
                operation="runtime.headless.verify",
                authority=authority,
                inputs=inputs,
                operation_params=operation_params,
            )
            if matches:
                existing = matches[0]
                self._verify_pending_runtime_headless_grants(
                    existing,
                    evidence_manifest=evidence_manifest,
                )
                self.store.connection.commit()
                return existing
            reserved, _binding = self.output_grants.reserve_for_job(
                grant_id=str(parsed["target_grant_id"]),
                job_id=job_id,
                workspace_id=str(parsed["workspace_id"]),
                expected_generation=int(parsed["expected_target_grant_generation"]),
                expected_manifest_hash=str(evidence_manifest["content_hash"]),
                expected_tree_hash=str(evidence_manifest["tree_hash"]),
            )
            if (
                reserved["kind"] != "headless_evidence_directory"
                or reserved["generation"] != int(parsed["expected_target_grant_generation"]) + 1
            ):
                raise invalid_request("Runtime headless requires a headless evidence grant")
            timestamp = utc_now()
            record: dict[str, Any] = {
                "format": CREATION_JOB_FORMAT,
                "format_version": 12,
                "job_id": job_id,
                "workspace_id": parsed["workspace_id"],
                "operation": "runtime.headless.verify",
                "operation_params": operation_params,
                "state": "queued",
                "generation": 0,
                "authority": authority,
                "inputs": inputs,
                "progress": "queued",
                "result": None,
                "error": None,
                "created_at": timestamp,
                "started_at": None,
                "finished_at": None,
                "updated_at": timestamp,
                "record_hash": "",
            }
            record["record_hash"] = creation_job_record_hash(record)
            try:
                validate_studio_creation_job(record)
            except StudioContractError as exc:
                raise StudioError(
                    "internal_error",
                    "Runtime headless job record is invalid",
                ) from exc
            self.store.connection.execute(
                "INSERT INTO creation_jobs "
                "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                "VALUES (?, ?, 'runtime.headless.verify', 'queued', 'queued', 0, ?)",
                (job_id, parsed["workspace_id"], encode_json(record)),
            )
            for position, item in enumerate(inputs):
                subject = item["subject"]
                self.store.connection.execute(
                    "INSERT INTO creation_job_inputs "
                    "(job_id, position, artifact_id, subject_format, subject_version, "
                    "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        position,
                        item["artifact_id"],
                        subject["format"],
                        subject["format_version"],
                        subject["id"],
                        subject["content_hash"],
                    ),
                )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_job.queued",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"operation": "runtime.headless.verify", "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation job {job_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _materialization_bundle_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("game.materialization.bundle.build params must be an object")
        fields = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
            "runtime_bundle_artifact_id",
            "source_grant_id",
            "expected_source_grant_generation",
            "target_grant_id",
            "expected_target_grant_generation",
        }
        required = fields - {"job_id"}
        invalid = (required - set(value)) | (set(value) - fields)
        if invalid:
            raise invalid_request(
                "game.materialization.bundle.build params have invalid fields: "
                + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "game.materialization.bundle.build":
            raise invalid_request("game.materialization.bundle.build operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        params = {
            "runtime_bundle_artifact_id": value["runtime_bundle_artifact_id"],
            "source_grant_id": value["source_grant_id"],
            "source_grant_generation": value["expected_source_grant_generation"],
            "target_grant_id": value["target_grant_id"],
            "target_grant_generation": value["expected_target_grant_generation"],
        }
        try:
            _validate_materialization_bundle_operation_params(
                params,
                "game.materialization.bundle.build params",
            )
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        return value

    def _verified_materialization_bundle_request(
        self,
        *,
        job_id: str,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any],
        tuple[dict[str, Any], ...],
        tuple[tuple[str, bytes], ...],
        dict[str, Any],
    ]:
        artifact_id = str(operation_params["runtime_bundle_artifact_id"])
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        record = records.get(artifact_id)
        if (
            record is None
            or record["lifecycle"] != "candidate"
            or record["subject"]["format"] != "world-forge.game_runtime_bundle"
        ):
            raise conflict("Materialization runtime bundle candidate is not current")
        subject = record["subject"]
        key = (
            subject["format"],
            subject["format_version"],
            subject["id"],
            subject["content_hash"],
        )
        manifest = snapshot["documents"].get(key)
        if manifest is None:
            raise invalid_state("Materialization runtime bundle document is unavailable")
        row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, artifact_id),
        ).fetchone()
        if (
            row is None
            or row["producer_operation"] != "runtime.bundle.build"
            or int(row["producer_output_position"]) != 0
            or not row["producer_job_id"]
        ):
            raise conflict("Materialization runtime bundle producer changed")
        producer = self.get(str(row["producer_job_id"]))
        if (
            producer["workspace_id"] != workspace_id
            or producer["operation"] != "runtime.bundle.build"
            or producer["state"] != "succeeded"
            or producer["progress"] != "committed"
            or producer["result"]["output_artifact_ids"] != [artifact_id]
            or producer["result"]["publication"]["grant_id"] != operation_params["source_grant_id"]
            or producer["result"]["publication"]["grant_generation"]
            != operation_params["source_grant_generation"]
            or producer["result"]["publication"]["runtime_bundle"]
            != {
                **subject,
                "tree_hash": manifest["tree_hash"],
            }
        ):
            raise conflict("Materialization runtime bundle producer authority changed")
        binding = self.output_grants.published_binding(
            grant_id=str(operation_params["source_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["source_grant_generation"]),
        )
        if (
            binding["expected_manifest_hash"] != manifest["content_hash"]
            or binding["expected_tree_hash"] != manifest["tree_hash"]
        ):
            raise conflict("Materialization runtime bundle grant hashes changed")
        staged_inputs: list[dict[str, Any]] = []
        staged_payloads: list[tuple[str, bytes]] = []
        try:
            with verify_game_runtime_bundle(
                binding["path"],
                expected_content_hash=str(manifest["content_hash"]),
            ) as verified:
                if verified.manifest != manifest or tuple(verified.root_identity) != tuple(
                    binding["published_identity"]
                ):
                    raise conflict("Materialization runtime bundle bytes changed")
                for locator, payload in sorted(
                    verified.files.items(),
                    key=lambda item: item[0].encode("utf-8"),
                ):
                    staged_inputs.append(
                        {
                            "source_locator": locator,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "size_bytes": len(payload),
                        }
                    )
                    staged_payloads.append((locator, payload))
        except StudioError:
            raise
        except (GameRuntimeBundleError, OSError, TypeError, ValueError) as exc:
            raise conflict("Materialization runtime bundle source is not integral") from exc
        request = build_private_materialization_bundle_request(
            job_id=job_id,
            workspace_id=workspace_id,
            authority=copy.deepcopy(dict(authority)),
            project=snapshot["project"],
            runtime_bundle_manifest=copy.deepcopy(manifest),
            source_grant_id=str(operation_params["source_grant_id"]),
            source_grant_generation=int(operation_params["source_grant_generation"]),
            target_grant_id=str(operation_params["target_grant_id"]),
            target_grant_generation=int(operation_params["target_grant_generation"]),
            staged_inputs=staged_inputs,
        )
        try:
            result = execute_private_creation_request(request, artifact_root=Path(binding["path"]))
            if len(result.outputs) != 1 or result.analysis_status != "passed":
                raise ValueError("materialization preflight did not produce one manifest")
            output_manifest = decode_json_object(
                result.outputs[0].payload,
                source="materialization preflight manifest",
            )
        except (RuntimeIOError, TypeError, ValueError) as exc:
            raise conflict("Materialization bundle preflight is not integral") from exc
        confirmed = self.output_grants.published_binding(
            grant_id=str(operation_params["source_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["source_grant_generation"]),
        )
        if any(
            confirmed[field] != binding[field]
            for field in (
                "path",
                "parent_identity",
                "published_identity",
                "generation",
                "expected_manifest_hash",
                "expected_tree_hash",
                "leaf",
            )
        ):
            raise conflict("Materialization runtime bundle authority changed")
        return request, (copy.deepcopy(manifest),), tuple(staged_payloads), output_manifest

    def create_materialization_bundle(self, params: object) -> dict[str, Any]:
        parsed = self._materialization_bundle_params(params)
        snapshot = self.evidence._snapshot(  # noqa: SLF001
            {
                "workspace_id": parsed["workspace_id"],
                "expected_root_generation": parsed["expected_root_generation"],
                "expected_source_revision": parsed["expected_source_revision"],
                "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
                "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
            }
        )
        job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
        authority = {
            "root_generation": parsed["expected_root_generation"],
            "source_revision": parsed["expected_source_revision"],
            "workflow_status_hash": parsed["expected_workflow_status_hash"],
            "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        requested_target_generation = int(parsed["expected_target_grant_generation"])
        operation_params = {
            "runtime_bundle_artifact_id": parsed["runtime_bundle_artifact_id"],
            "source_grant_id": parsed["source_grant_id"],
            "source_grant_generation": parsed["expected_source_grant_generation"],
            "target_grant_id": parsed["target_grant_id"],
            "target_grant_generation": requested_target_generation + 1,
        }
        target_grant = self.output_grants.get(parsed["target_grant_id"])
        if (
            target_grant["format_version"] != 3
            or target_grant["kind"] != "game_materialization_bundle_directory"
            or target_grant["state"] != "ready"
            or target_grant["generation"] != requested_target_generation
        ):
            raise conflict("Materialization target grant is not ready")
        request, _lineage, _payloads, manifest = self._verified_materialization_bundle_request(
            job_id=job_id,
            workspace_id=parsed["workspace_id"],
            authority=authority,
            snapshot=snapshot,
            operation_params=operation_params,
        )
        timestamp = utc_now()
        record: dict[str, Any] = {
            "format": CREATION_JOB_FORMAT,
            "format_version": 6,
            "job_id": job_id,
            "workspace_id": parsed["workspace_id"],
            "operation": "game.materialization.bundle.build",
            "operation_params": operation_params,
            "state": "queued",
            "generation": 0,
            "authority": authority,
            "inputs": copy.deepcopy(request["inputs"]),
            "progress": "queued",
            "result": None,
            "error": None,
            "created_at": timestamp,
            "started_at": None,
            "finished_at": None,
            "updated_at": timestamp,
            "record_hash": "",
        }
        record["record_hash"] = creation_job_record_hash(record)
        try:
            validate_studio_creation_job(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Materialization job record is invalid") from exc
        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            current = self.evidence._snapshot(  # noqa: SLF001
                {
                    "workspace_id": parsed["workspace_id"],
                    "expected_root_generation": parsed["expected_root_generation"],
                    "expected_source_revision": parsed["expected_source_revision"],
                    "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
                    "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
                }
            )
            if current["artifact_snapshot_hash"] != snapshot["artifact_snapshot_hash"]:
                raise conflict("Materialization artifact authority changed before reservation")
            self.output_grants.published_binding(
                grant_id=str(operation_params["source_grant_id"]),
                workspace_id=parsed["workspace_id"],
                expected_generation=int(operation_params["source_grant_generation"]),
            )
            reserved, _binding = self.output_grants.reserve_for_job(
                grant_id=parsed["target_grant_id"],
                job_id=job_id,
                workspace_id=parsed["workspace_id"],
                expected_generation=requested_target_generation,
                expected_manifest_hash=str(manifest["content_hash"]),
                expected_tree_hash=str(manifest["tree_hash"]),
            )
            if reserved["generation"] != operation_params["target_grant_generation"]:
                raise conflict("Materialization target reservation generation changed")
            self.store.connection.execute(
                "INSERT INTO creation_jobs "
                "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                "VALUES (?, ?, 'game.materialization.bundle.build', 'queued', 'queued', 0, ?)",
                (job_id, parsed["workspace_id"], encode_json(record)),
            )
            for position, item in enumerate(record["inputs"]):
                subject = item["subject"]
                self.store.connection.execute(
                    "INSERT INTO creation_job_inputs "
                    "(job_id, position, artifact_id, subject_format, subject_version, "
                    "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        position,
                        item["artifact_id"],
                        subject["format"],
                        subject["format_version"],
                        subject["id"],
                        subject["content_hash"],
                    ),
                )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_job.queued",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"operation": "game.materialization.bundle.build", "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation job {job_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _game_materialize_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("game.materialize params must be an object")
        fields = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
            "materialization_bundle_artifact_id",
            "source_grant_id",
            "expected_source_grant_generation",
            "target_grant_id",
            "expected_target_grant_generation",
        }
        required = fields - {"job_id"}
        invalid = (required - set(value)) | (set(value) - fields)
        if invalid:
            raise invalid_request(
                "game.materialize params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "game.materialize":
            raise invalid_request("game.materialize operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        try:
            _validate_game_materialize_operation_params(
                {
                    "materialization_bundle_artifact_id": value[
                        "materialization_bundle_artifact_id"
                    ],
                    "source_grant_id": value["source_grant_id"],
                    "source_grant_generation": value["expected_source_grant_generation"],
                    "target_grant_id": value["target_grant_id"],
                    "target_grant_generation": value["expected_target_grant_generation"],
                },
                "game.materialize params",
            )
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        return value

    def _verified_game_materialize_request(
        self,
        *,
        job_id: str,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any],
        tuple[dict[str, Any], ...],
        tuple[tuple[str, bytes], ...],
        dict[str, Any],
        dict[str, Any],
    ]:
        artifact_id = str(operation_params["materialization_bundle_artifact_id"])
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        record = records.get(artifact_id)
        if (
            record is None
            or record["lifecycle"] != "candidate"
            or record["subject"]["format"] != "world-forge.game_materialization_bundle"
        ):
            raise conflict("Standalone materialization bundle candidate is not current")
        subject = record["subject"]
        key = (
            subject["format"],
            subject["format_version"],
            subject["id"],
            subject["content_hash"],
        )
        manifest = snapshot["documents"].get(key)
        if manifest is None:
            raise invalid_state("Standalone materialization bundle document is unavailable")
        row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, artifact_id),
        ).fetchone()
        if (
            row is None
            or row["producer_operation"] != "game.materialization.bundle.build"
            or int(row["producer_output_position"]) != 0
            or not row["producer_job_id"]
        ):
            raise conflict("Standalone materialization bundle producer changed")
        producer = self.get(str(row["producer_job_id"]))
        if (
            producer["workspace_id"] != workspace_id
            or producer["operation"] != "game.materialization.bundle.build"
            or producer["state"] != "succeeded"
            or producer["progress"] != "committed"
            or producer["result"]["output_artifact_ids"] != [artifact_id]
            or producer["result"]["publication"]["grant_id"] != operation_params["source_grant_id"]
            or producer["result"]["publication"]["grant_generation"]
            != operation_params["source_grant_generation"]
            or producer["result"]["publication"]["materialization_bundle"]
            != {
                **subject,
                "tree_hash": manifest["tree_hash"],
            }
        ):
            raise conflict("Standalone materialization bundle producer authority changed")
        binding = self.output_grants.published_binding(
            grant_id=str(operation_params["source_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["source_grant_generation"]),
        )
        if (
            binding["expected_manifest_hash"] != manifest["content_hash"]
            or binding["expected_tree_hash"] != manifest["tree_hash"]
        ):
            raise conflict("Standalone materialization bundle grant hashes changed")
        staged_inputs: list[dict[str, Any]] = []
        staged_payloads: list[tuple[str, bytes]] = []
        try:
            with verify_game_materialization_bundle(
                binding["path"],
                expected_content_hash=str(manifest["content_hash"]),
                expected_parent_identity=tuple(binding["parent_identity"]),
            ) as verified:
                if verified.manifest != manifest or tuple(verified.root_identity) != tuple(
                    binding["published_identity"]
                ):
                    raise conflict("Standalone materialization bundle bytes changed")
                expected_manifest, expected_lock, _platform = build_standalone_game_documents(
                    verified
                )
                for locator, payload in sorted(
                    verified.files.items(),
                    key=lambda item: item[0].encode("utf-8"),
                ):
                    staged_inputs.append(
                        {
                            "source_locator": locator,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "size_bytes": len(payload),
                        }
                    )
                    staged_payloads.append((locator, payload))
        except StudioError:
            raise
        except (
            GameMaterializationBundleError,
            StandaloneGameError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            raise conflict("Standalone materialization source is not integral") from exc
        request = build_private_game_materialize_request(
            job_id=job_id,
            workspace_id=workspace_id,
            authority=copy.deepcopy(dict(authority)),
            project=snapshot["project"],
            materialization_bundle_manifest=copy.deepcopy(manifest),
            source_grant_id=str(operation_params["source_grant_id"]),
            source_grant_generation=int(operation_params["source_grant_generation"]),
            target_grant_id=str(operation_params["target_grant_id"]),
            target_grant_generation=int(operation_params["target_grant_generation"]),
            staged_inputs=staged_inputs,
        )
        try:
            result = execute_private_creation_request(
                request,
                artifact_root=Path(binding["path"]),
            )
            if len(result.outputs) != 1 or result.analysis_status != "passed":
                raise ValueError("standalone preflight did not produce one manifest")
            output_manifest = decode_json_object(
                result.outputs[0].payload,
                source="standalone preflight manifest",
            )
            if output_manifest != expected_manifest:
                raise ValueError("standalone preflight manifest changed")
        except (RuntimeIOError, TypeError, ValueError) as exc:
            raise conflict("Standalone materialization preflight is not integral") from exc
        confirmed = self.output_grants.published_binding(
            grant_id=str(operation_params["source_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["source_grant_generation"]),
        )
        if any(
            confirmed[field] != binding[field]
            for field in (
                "path",
                "parent_identity",
                "published_identity",
                "generation",
                "expected_manifest_hash",
                "expected_tree_hash",
                "leaf",
            )
        ):
            raise conflict("Standalone materialization source authority changed")
        return (
            request,
            (copy.deepcopy(manifest),),
            tuple(staged_payloads),
            output_manifest,
            expected_lock,
        )

    def create_game_materialize(self, params: object) -> dict[str, Any]:
        parsed = self._game_materialize_params(params)
        snapshot = self.evidence._snapshot(  # noqa: SLF001
            {
                "workspace_id": parsed["workspace_id"],
                "expected_root_generation": parsed["expected_root_generation"],
                "expected_source_revision": parsed["expected_source_revision"],
                "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
                "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
            }
        )
        job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
        authority = {
            "root_generation": parsed["expected_root_generation"],
            "source_revision": parsed["expected_source_revision"],
            "workflow_status_hash": parsed["expected_workflow_status_hash"],
            "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        requested_target_generation = int(parsed["expected_target_grant_generation"])
        operation_params = {
            "materialization_bundle_artifact_id": parsed["materialization_bundle_artifact_id"],
            "source_grant_id": parsed["source_grant_id"],
            "source_grant_generation": parsed["expected_source_grant_generation"],
            "target_grant_id": parsed["target_grant_id"],
            "target_grant_generation": requested_target_generation + 1,
        }
        target_grant = self.output_grants.get(parsed["target_grant_id"])
        if (
            target_grant["format_version"] != 4
            or target_grant["kind"] != "standalone_game_directory"
            or target_grant["state"] != "ready"
            or target_grant["generation"] != requested_target_generation
        ):
            raise conflict("Standalone target grant is not ready")
        request, _lineage, _payloads, manifest, lock = self._verified_game_materialize_request(
            job_id=job_id,
            workspace_id=parsed["workspace_id"],
            authority=authority,
            snapshot=snapshot,
            operation_params=operation_params,
        )
        timestamp = utc_now()
        record: dict[str, Any] = {
            "format": CREATION_JOB_FORMAT,
            "format_version": 7,
            "job_id": job_id,
            "workspace_id": parsed["workspace_id"],
            "operation": "game.materialize",
            "operation_params": operation_params,
            "state": "queued",
            "generation": 0,
            "authority": authority,
            "inputs": copy.deepcopy(request["inputs"]),
            "progress": "queued",
            "result": None,
            "error": None,
            "created_at": timestamp,
            "started_at": None,
            "finished_at": None,
            "updated_at": timestamp,
            "record_hash": "",
        }
        record["record_hash"] = creation_job_record_hash(record)
        try:
            validate_studio_creation_job(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Standalone job record is invalid") from exc
        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            current = self.evidence._snapshot(  # noqa: SLF001
                {
                    "workspace_id": parsed["workspace_id"],
                    "expected_root_generation": parsed["expected_root_generation"],
                    "expected_source_revision": parsed["expected_source_revision"],
                    "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
                    "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
                }
            )
            if current["artifact_snapshot_hash"] != snapshot["artifact_snapshot_hash"]:
                raise conflict("Standalone artifact authority changed before reservation")
            self.output_grants.published_binding(
                grant_id=str(operation_params["source_grant_id"]),
                workspace_id=parsed["workspace_id"],
                expected_generation=int(operation_params["source_grant_generation"]),
            )
            reserved, _binding = self.output_grants.reserve_for_job(
                grant_id=parsed["target_grant_id"],
                job_id=job_id,
                workspace_id=parsed["workspace_id"],
                expected_generation=requested_target_generation,
                expected_manifest_hash=str(manifest["content_hash"]),
                expected_tree_hash=str(lock["tree_hash"]),
            )
            if reserved["generation"] != operation_params["target_grant_generation"]:
                raise conflict("Standalone target reservation generation changed")
            self.store.connection.execute(
                "INSERT INTO creation_jobs "
                "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                "VALUES (?, ?, 'game.materialize', 'queued', 'queued', 0, ?)",
                (job_id, parsed["workspace_id"], encode_json(record)),
            )
            for position, item in enumerate(record["inputs"]):
                subject = item["subject"]
                self.store.connection.execute(
                    "INSERT INTO creation_job_inputs "
                    "(job_id, position, artifact_id, subject_format, subject_version, "
                    "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        position,
                        item["artifact_id"],
                        subject["format"],
                        subject["format_version"],
                        subject["id"],
                        subject["content_hash"],
                    ),
                )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_job.queued",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"operation": "game.materialize", "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation job {job_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _game_package_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("game.package params must be an object")
        fields = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
            "standalone_game_artifact_id",
            "source_grant_id",
            "expected_source_grant_generation",
            "target_grant_id",
            "expected_target_grant_generation",
        }
        required = fields - {"job_id"}
        invalid = (required - set(value)) | (set(value) - fields)
        if invalid:
            raise invalid_request(
                "game.package params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "game.package":
            raise invalid_request("game.package operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        try:
            _validate_game_package_operation_params(
                {
                    "standalone_game_artifact_id": value["standalone_game_artifact_id"],
                    "source_grant_id": value["source_grant_id"],
                    "source_grant_generation": value["expected_source_grant_generation"],
                    "target_grant_id": value["target_grant_id"],
                    "target_grant_generation": value["expected_target_grant_generation"],
                },
                "game.package params",
            )
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        return value

    def _verify_game_package_lineage(
        self,
        *,
        snapshot: Mapping[str, Any],
        standalone: Mapping[str, Any],
    ) -> None:
        expected = {
            "world-forge.gamepack": standalone["lineage"]["gamepack_hash"],
            "world-forge.assetpack": standalone["lineage"]["assetpack_hash"],
            "world-forge.game_runtime_snapshot": standalone["lineage"]["runtime_snapshot_hash"],
            "world-forge.game_runtime_composition": standalone["lineage"][
                "runtime_composition_hash"
            ],
            "world-forge.game_runtime_bundle": standalone["lineage"]["runtime_bundle_hash"],
            "world-forge.game_materialization_bundle": standalone["materialization_bundle"][
                "content_hash"
            ],
        }
        project = snapshot["project"]
        source_keys = {
            self._document_key(document)
            for document in (
                project.project,
                project.profile,
                project.manifest,
                *project.world_modules,
                *project.activity_modules,
                *project.narrative_modules,
                *project.system_modules,
                *project.logic_modules,
            )
        }
        records = snapshot["records"]
        records_by_key = {
            (
                record["subject"]["format"],
                record["subject"]["format_version"],
                record["subject"]["id"],
                record["subject"]["content_hash"],
            ): record
            for record in records
        }
        documents = snapshot["documents"]
        selected: dict[tuple[str, int, str, str], dict[str, Any]] = {}
        pending: list[dict[str, Any]] = []
        for format_name, content_hash in expected.items():
            matches = [
                record
                for record in records
                if record["lifecycle"] in {"active", "candidate"}
                and record["subject"]["format"] == format_name
                and record["subject"]["content_hash"] == content_hash
            ]
            if len(matches) != 1:
                raise conflict("Game package lineage is incomplete or ambiguous")
            subject = matches[0]["subject"]
            key = (
                subject["format"],
                subject["format_version"],
                subject["id"],
                subject["content_hash"],
            )
            document = documents.get(key)
            if document is None:
                raise invalid_state("Game package lineage document is unavailable")
            selected[key] = copy.deepcopy(document)
            pending.extend(artifact_dependency_identities(document))
        while pending:
            identity = pending.pop()
            key = (
                str(identity["format"]),
                int(identity["format_version"]),
                str(identity["id"]),
                str(identity["content_hash"]),
            )
            if key in source_keys or key in selected:
                continue
            record = records_by_key.get(key)
            if record is None or record["lifecycle"] not in {"active", "candidate"}:
                raise conflict("Game package lineage is incomplete or stale")
            document = documents.get(key)
            if document is None:
                raise invalid_state("Game package lineage document is unavailable")
            selected[key] = copy.deepcopy(document)
            pending.extend(artifact_dependency_identities(document))
        try:
            validate_artifact_documents(
                project,
                tuple(selected.values()),
            )
        except (PhaseReportV3Error, TypeError, ValueError) as exc:
            raise conflict("Game package lineage is not one exact integral closure") from exc

    def _verified_game_package_request(
        self,
        *,
        job_id: str,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any],
        tuple[dict[str, Any], ...],
        tuple[tuple[str, bytes], ...],
        dict[str, Any],
        str,
        int,
    ]:
        artifact_id = str(operation_params["standalone_game_artifact_id"])
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        record = records.get(artifact_id)
        if (
            record is None
            or record["lifecycle"] != "candidate"
            or record["subject"]["format"] != "world-forge.standalone_game"
        ):
            raise conflict("Game package standalone candidate is not current")
        subject = record["subject"]
        key = (
            subject["format"],
            subject["format_version"],
            subject["id"],
            subject["content_hash"],
        )
        manifest = snapshot["documents"].get(key)
        if manifest is None:
            raise invalid_state("Game package standalone document is unavailable")
        row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, artifact_id),
        ).fetchone()
        if (
            row is None
            or row["producer_operation"] != "game.materialize"
            or int(row["producer_output_position"]) != 0
            or not row["producer_job_id"]
        ):
            raise conflict("Game package standalone producer changed")
        producer = self.get(str(row["producer_job_id"]))
        if (
            producer["workspace_id"] != workspace_id
            or producer["operation"] != "game.materialize"
            or producer["state"] != "succeeded"
            or producer["progress"] != "committed"
            or producer["result"]["output_artifact_ids"] != [artifact_id]
            or producer["result"]["publication"]["grant_id"] != operation_params["source_grant_id"]
            or producer["result"]["publication"]["grant_generation"]
            != operation_params["source_grant_generation"]
            or producer["result"]["publication"]["standalone_game"]["content_hash"]
            != manifest["content_hash"]
        ):
            raise conflict("Game package standalone producer authority changed")
        binding = self.output_grants.published_binding(
            grant_id=str(operation_params["source_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["source_grant_generation"]),
        )
        if binding["expected_manifest_hash"] != manifest["content_hash"]:
            raise conflict("Game package standalone grant manifest changed")
        self._verify_game_package_lineage(snapshot=snapshot, standalone=manifest)
        staged_inputs: list[dict[str, Any]] = []
        staged_payloads: list[tuple[str, bytes]] = []
        try:
            with verify_standalone_game(
                binding["path"],
                expected_content_hash=str(manifest["content_hash"]),
                expected_root_identity=tuple(binding["published_identity"]),
            ) as verified:
                if (
                    verified.manifest != manifest
                    or verified.lock["tree_hash"] != binding["expected_tree_hash"]
                    or tuple(verified.root_identity) != tuple(binding["published_identity"])
                ):
                    raise conflict("Game package standalone bytes changed")
                built = build_game_package_from_files(verified.files)
                for locator, payload in sorted(
                    verified.files.items(), key=lambda item: item[0].encode("utf-8")
                ):
                    staged_inputs.append(
                        {
                            "source_locator": locator,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "size_bytes": len(payload),
                        }
                    )
                    staged_payloads.append((locator, payload))
                lock = verified.lock
        except StudioError:
            raise
        except (GamePackageError, StandaloneGameError, OSError, TypeError, ValueError) as exc:
            raise conflict("Game package standalone source is not integral") from exc
        request = build_private_game_package_request(
            job_id=job_id,
            workspace_id=workspace_id,
            authority=copy.deepcopy(dict(authority)),
            project=snapshot["project"],
            standalone_game_manifest=copy.deepcopy(manifest),
            standalone_game_lock=copy.deepcopy(lock),
            game_package_manifest=copy.deepcopy(built.manifest),
            archive_sha256=built.archive_sha256,
            archive_size_bytes=len(built.archive_bytes),
            source_grant_id=str(operation_params["source_grant_id"]),
            source_grant_generation=int(operation_params["source_grant_generation"]),
            target_grant_id=str(operation_params["target_grant_id"]),
            target_grant_generation=int(operation_params["target_grant_generation"]),
            staged_inputs=staged_inputs,
        )
        try:
            result = execute_private_creation_request(request, artifact_root=Path(binding["path"]))
            if (
                len(result.outputs) != 1
                or len(result.binary_outputs) != 1
                or result.analysis_status != "passed"
                or decode_json_object(
                    result.outputs[0].payload,
                    source="game package preflight manifest",
                )
                != built.manifest
                or result.binary_outputs[0].payload != built.archive_bytes
            ):
                raise ValueError("game package preflight output changed")
        except (RuntimeIOError, TypeError, ValueError) as exc:
            raise conflict("Game package preflight is not integral") from exc
        confirmed = self.output_grants.published_binding(
            grant_id=str(operation_params["source_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["source_grant_generation"]),
        )
        if any(
            confirmed[field] != binding[field]
            for field in (
                "path",
                "parent_identity",
                "published_identity",
                "generation",
                "expected_manifest_hash",
                "expected_tree_hash",
                "leaf",
            )
        ):
            raise conflict("Game package standalone source authority changed")
        return (
            request,
            (copy.deepcopy(manifest),),
            tuple(staged_payloads),
            copy.deepcopy(built.manifest),
            built.archive_sha256,
            len(built.archive_bytes),
        )

    def create_game_package(self, params: object) -> dict[str, Any]:
        parsed = self._game_package_params(params)
        snapshot = self.evidence._snapshot(  # noqa: SLF001
            {
                "workspace_id": parsed["workspace_id"],
                "expected_root_generation": parsed["expected_root_generation"],
                "expected_source_revision": parsed["expected_source_revision"],
                "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
                "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
            }
        )
        job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
        authority = {
            "root_generation": parsed["expected_root_generation"],
            "source_revision": parsed["expected_source_revision"],
            "workflow_status_hash": parsed["expected_workflow_status_hash"],
            "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        requested_target_generation = int(parsed["expected_target_grant_generation"])
        operation_params = {
            "standalone_game_artifact_id": parsed["standalone_game_artifact_id"],
            "source_grant_id": parsed["source_grant_id"],
            "source_grant_generation": parsed["expected_source_grant_generation"],
            "target_grant_id": parsed["target_grant_id"],
            "target_grant_generation": requested_target_generation + 1,
        }
        target_grant = self.output_grants.get(parsed["target_grant_id"])
        if (
            target_grant["format_version"] != 5
            or target_grant["kind"] != "game_package_file"
            or target_grant["state"] != "ready"
            or target_grant["generation"] != requested_target_generation
        ):
            raise conflict("Game package target grant is not ready")
        request, _dependencies, _payloads, manifest, archive_hash, archive_size = (
            self._verified_game_package_request(
                job_id=job_id,
                workspace_id=parsed["workspace_id"],
                authority=authority,
                snapshot=snapshot,
                operation_params=operation_params,
            )
        )
        timestamp = utc_now()
        record: dict[str, Any] = {
            "format": CREATION_JOB_FORMAT,
            "format_version": 8,
            "job_id": job_id,
            "workspace_id": parsed["workspace_id"],
            "operation": "game.package",
            "operation_params": operation_params,
            "state": "queued",
            "generation": 0,
            "authority": authority,
            "inputs": copy.deepcopy(request["inputs"]),
            "progress": "queued",
            "result": None,
            "error": None,
            "created_at": timestamp,
            "started_at": None,
            "finished_at": None,
            "updated_at": timestamp,
            "record_hash": "",
        }
        record["record_hash"] = creation_job_record_hash(record)
        try:
            validate_studio_creation_job(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Game package job record is invalid") from exc
        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            current = self.evidence._snapshot(  # noqa: SLF001
                {
                    "workspace_id": parsed["workspace_id"],
                    "expected_root_generation": parsed["expected_root_generation"],
                    "expected_source_revision": parsed["expected_source_revision"],
                    "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
                    "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
                }
            )
            if current["artifact_snapshot_hash"] != snapshot["artifact_snapshot_hash"]:
                raise conflict("Game package artifact authority changed before reservation")
            self.output_grants.published_binding(
                grant_id=str(operation_params["source_grant_id"]),
                workspace_id=parsed["workspace_id"],
                expected_generation=int(operation_params["source_grant_generation"]),
            )
            reserved, _binding = self.output_grants.reserve_for_job(
                grant_id=parsed["target_grant_id"],
                job_id=job_id,
                workspace_id=parsed["workspace_id"],
                expected_generation=requested_target_generation,
                expected_manifest_hash=str(manifest["content_hash"]),
                expected_archive_sha256=archive_hash,
                expected_size_bytes=archive_size,
            )
            if reserved["generation"] != operation_params["target_grant_generation"]:
                raise conflict("Game package target reservation generation changed")
            self.store.connection.execute(
                "INSERT INTO creation_jobs "
                "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                "VALUES (?, ?, 'game.package', 'queued', 'queued', 0, ?)",
                (job_id, parsed["workspace_id"], encode_json(record)),
            )
            for position, item in enumerate(record["inputs"]):
                subject = item["subject"]
                self.store.connection.execute(
                    "INSERT INTO creation_job_inputs "
                    "(job_id, position, artifact_id, subject_format, subject_version, "
                    "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        position,
                        item["artifact_id"],
                        subject["format"],
                        subject["format_version"],
                        subject["id"],
                        subject["content_hash"],
                    ),
                )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_job.queued",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"operation": "game.package", "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation job {job_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _game_package_extract_params(value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise invalid_request("game.package.extract params must be an object")
        fields = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
            "game_package_artifact_id",
            "source_grant_id",
            "expected_source_grant_generation",
            "target_grant_id",
            "expected_target_grant_generation",
        }
        required = fields - {"job_id"}
        invalid = (required - set(value)) | (set(value) - fields)
        if invalid:
            raise invalid_request(
                "game.package.extract params have invalid fields: " + ", ".join(sorted(invalid))
            )
        if "job_id" in value:
            _identifier(value["job_id"], field="job_id")
        _identifier(value["workspace_id"], field="workspace_id", workspace=True)
        if value["operation"] != "game.package.extract":
            raise invalid_request("game.package.extract operation is invalid")
        _generation(value["expected_root_generation"], field="expected_root_generation")
        _digest(value["expected_source_revision"], field="expected_source_revision")
        _digest(
            value["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        _digest(
            value["expected_artifact_snapshot_hash"],
            field="expected_artifact_snapshot_hash",
        )
        try:
            _validate_game_package_extract_operation_params(
                {
                    "game_package_artifact_id": value["game_package_artifact_id"],
                    "source_grant_id": value["source_grant_id"],
                    "source_grant_generation": value["expected_source_grant_generation"],
                    "target_grant_id": value["target_grant_id"],
                    "target_grant_generation": value["expected_target_grant_generation"],
                },
                "game.package.extract params",
            )
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        return value

    def _verified_game_package_extract_request(
        self,
        *,
        job_id: str,
        workspace_id: str,
        authority: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        operation_params: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any],
        tuple[dict[str, Any], ...],
        tuple[tuple[str, bytes], ...],
        dict[str, Any],
    ]:
        artifact_id = str(operation_params["game_package_artifact_id"])
        records = {record["artifact_id"]: record for record in snapshot["records"]}
        record = records.get(artifact_id)
        if (
            record is None
            or record["lifecycle"] != "candidate"
            or record["subject"]["format"] != "world-forge.game_package"
        ):
            raise conflict("Game package extraction candidate is not current")
        subject = record["subject"]
        key = (
            subject["format"],
            subject["format_version"],
            subject["id"],
            subject["content_hash"],
        )
        manifest = snapshot["documents"].get(key)
        if manifest is None:
            raise invalid_state("Game package extraction manifest is unavailable")
        row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, artifact_id),
        ).fetchone()
        if (
            row is None
            or row["producer_operation"] != "game.package"
            or int(row["producer_output_position"]) != 0
            or not row["producer_job_id"]
        ):
            raise conflict("Game package extraction producer changed")
        producer = self.get(str(row["producer_job_id"]))
        if (
            producer["workspace_id"] != workspace_id
            or producer["operation"] != "game.package"
            or producer["state"] != "succeeded"
            or producer["progress"] != "committed"
            or producer["result"]["output_artifact_ids"] != [artifact_id]
            or producer["result"]["publication"]["grant_id"] != operation_params["source_grant_id"]
            or producer["result"]["publication"]["grant_generation"]
            != operation_params["source_grant_generation"]
            or producer["result"]["publication"]["game_package"]["content_hash"]
            != manifest["content_hash"]
        ):
            raise conflict("Game package extraction producer authority changed")
        binding = self.output_grants.published_binding(
            grant_id=str(operation_params["source_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["source_grant_generation"]),
        )
        if binding["expected_manifest_hash"] != manifest["content_hash"]:
            raise conflict("Game package extraction source manifest changed")
        try:
            verified = verify_game_package(
                binding["path"],
                expected_file_identity=tuple(binding["published_identity"]),
            )
            try:
                if (
                    verified.manifest != manifest
                    or verified.archive_sha256 != binding["expected_archive_sha256"]
                    or len(verified.archive_bytes) != binding["expected_size_bytes"]
                ):
                    raise conflict("Game package extraction source bytes changed")
                archive_bytes = verified.archive_bytes
                evidence = build_game_package_extraction_evidence(
                    verified.manifest,
                    archive_sha256=verified.archive_sha256,
                    archive_size_bytes=len(verified.archive_bytes),
                )
            finally:
                verified.close()
        except StudioError:
            raise
        except (
            GamePackageExtractionEvidenceError,
            WorldForgeGamePackageError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            raise conflict("Game package extraction source is not integral") from exc
        staged_inputs = [
            {
                "source_locator": "game_package_archive.wfgame",
                "sha256": str(binding["expected_archive_sha256"]),
                "size_bytes": int(binding["expected_size_bytes"]),
            }
        ]
        request = build_private_game_package_extract_request(
            job_id=job_id,
            workspace_id=workspace_id,
            authority=copy.deepcopy(dict(authority)),
            project=snapshot["project"],
            game_package_manifest=copy.deepcopy(manifest),
            archive_sha256=str(binding["expected_archive_sha256"]),
            archive_size_bytes=int(binding["expected_size_bytes"]),
            source_grant_id=str(operation_params["source_grant_id"]),
            source_grant_generation=int(operation_params["source_grant_generation"]),
            target_grant_id=str(operation_params["target_grant_id"]),
            target_grant_generation=int(operation_params["target_grant_generation"]),
            staged_inputs=staged_inputs,
        )
        confirmed = self.output_grants.published_binding(
            grant_id=str(operation_params["source_grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(operation_params["source_grant_generation"]),
        )
        if any(
            confirmed[field] != binding[field]
            for field in (
                "path",
                "parent_identity",
                "published_identity",
                "generation",
                "expected_manifest_hash",
                "expected_archive_sha256",
                "expected_size_bytes",
                "leaf",
            )
        ):
            raise conflict("Game package extraction source authority changed")
        return (
            request,
            (copy.deepcopy(manifest),),
            (("game_package_archive.wfgame", archive_bytes),),
            evidence,
        )

    def create_game_package_extract(self, params: object) -> dict[str, Any]:
        parsed = self._game_package_extract_params(params)
        snapshot = self.evidence._snapshot(  # noqa: SLF001
            {
                "workspace_id": parsed["workspace_id"],
                "expected_root_generation": parsed["expected_root_generation"],
                "expected_source_revision": parsed["expected_source_revision"],
                "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
                "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
            }
        )
        job_id = parsed.get("job_id") or f"job_{uuid.uuid4().hex}"
        authority = {
            "root_generation": parsed["expected_root_generation"],
            "source_revision": parsed["expected_source_revision"],
            "workflow_status_hash": parsed["expected_workflow_status_hash"],
            "artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
        }
        requested_target_generation = int(parsed["expected_target_grant_generation"])
        operation_params = {
            "game_package_artifact_id": parsed["game_package_artifact_id"],
            "source_grant_id": parsed["source_grant_id"],
            "source_grant_generation": parsed["expected_source_grant_generation"],
            "target_grant_id": parsed["target_grant_id"],
            "target_grant_generation": requested_target_generation + 1,
        }
        target_grant = self.output_grants.get(parsed["target_grant_id"])
        if (
            target_grant["format_version"] != 4
            or target_grant["kind"] != "standalone_game_directory"
            or target_grant["state"] != "ready"
            or target_grant["generation"] != requested_target_generation
        ):
            raise conflict("Game package extraction target grant is not ready")
        request, _dependencies, _payloads, evidence = self._verified_game_package_extract_request(
            job_id=job_id,
            workspace_id=parsed["workspace_id"],
            authority=authority,
            snapshot=snapshot,
            operation_params=operation_params,
        )
        timestamp = utc_now()
        record: dict[str, Any] = {
            "format": CREATION_JOB_FORMAT,
            "format_version": 9,
            "job_id": job_id,
            "workspace_id": parsed["workspace_id"],
            "operation": "game.package.extract",
            "operation_params": operation_params,
            "state": "queued",
            "generation": 0,
            "authority": authority,
            "inputs": copy.deepcopy(request["inputs"]),
            "progress": "queued",
            "result": None,
            "error": None,
            "created_at": timestamp,
            "started_at": None,
            "finished_at": None,
            "updated_at": timestamp,
            "record_hash": "",
        }
        record["record_hash"] = creation_job_record_hash(record)
        try:
            validate_studio_creation_job(record)
        except StudioContractError as exc:
            raise StudioError(
                "internal_error", "Game package extraction job record is invalid"
            ) from exc
        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            current = self.evidence._snapshot(  # noqa: SLF001
                {
                    "workspace_id": parsed["workspace_id"],
                    "expected_root_generation": parsed["expected_root_generation"],
                    "expected_source_revision": parsed["expected_source_revision"],
                    "expected_workflow_status_hash": parsed["expected_workflow_status_hash"],
                    "expected_artifact_snapshot_hash": parsed["expected_artifact_snapshot_hash"],
                }
            )
            if current["artifact_snapshot_hash"] != snapshot["artifact_snapshot_hash"]:
                raise conflict(
                    "Game package extraction artifact authority changed before reservation"
                )
            self.output_grants.published_binding(
                grant_id=str(operation_params["source_grant_id"]),
                workspace_id=parsed["workspace_id"],
                expected_generation=int(operation_params["source_grant_generation"]),
            )
            reserved, _binding = self.output_grants.reserve_for_job(
                grant_id=parsed["target_grant_id"],
                job_id=job_id,
                workspace_id=parsed["workspace_id"],
                expected_generation=requested_target_generation,
                expected_manifest_hash=str(evidence["standalone_game"]["content_hash"]),
                expected_tree_hash=str(evidence["extracted_tree_hash"]),
            )
            if reserved["generation"] != operation_params["target_grant_generation"]:
                raise conflict("Game package extraction target reservation generation changed")
            self.store.connection.execute(
                "INSERT INTO creation_jobs "
                "(job_id, workspace_id, operation, state, progress, generation, record_json) "
                "VALUES (?, ?, 'game.package.extract', 'queued', 'queued', 0, ?)",
                (job_id, parsed["workspace_id"], encode_json(record)),
            )
            for position, item in enumerate(record["inputs"]):
                subject = item["subject"]
                self.store.connection.execute(
                    "INSERT INTO creation_job_inputs "
                    "(job_id, position, artifact_id, subject_format, subject_version, "
                    "subject_id, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        job_id,
                        position,
                        item["artifact_id"],
                        subject["format"],
                        subject["format_version"],
                        subject["id"],
                        subject["content_hash"],
                    ),
                )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_job.queued",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"operation": "game.package.extract", "generation": 0},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return record
        except sqlite3.IntegrityError as exc:
            self.store.connection.rollback()
            raise conflict(f"Creation job {job_id} already exists") from exc
        except BaseException:
            self.store.connection.rollback()
            raise

    def get(self, job_id: object) -> dict[str, Any]:
        identifier = _identifier(job_id, field="job_id")
        row = self.store.connection.execute(
            "SELECT * FROM creation_jobs WHERE job_id = ?", (identifier,)
        ).fetchone()
        if row is None:
            raise not_found(f"Creation job {identifier} was not found")
        return self._validated_row(row)

    def list(
        self,
        *,
        workspace_id: object,
        state: str | None = None,
        after_sequence: int = 0,
        limit: int = MAX_CREATION_JOB_PAGE,
    ) -> tuple[list[dict[str, Any]], int | None]:
        identifier = _identifier(workspace_id, field="workspace_id", workspace=True)
        if state is not None and state not in {
            "queued",
            "running",
            "succeeded",
            "failed",
            "canceled",
            "orphaned",
        }:
            raise invalid_request("creation job state filter is invalid")
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise invalid_request("creation job cursor is invalid")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_CREATION_JOB_PAGE
        ):
            raise invalid_request("creation job list limit is invalid")
        query = (
            "SELECT * FROM creation_jobs WHERE workspace_id = ? AND sequence > ? "
            + ("AND state = ? " if state is not None else "")
            + "ORDER BY sequence LIMIT ?"
        )
        values: tuple[object, ...] = (
            (identifier, after_sequence, state, limit + 1)
            if state is not None
            else (identifier, after_sequence, limit + 1)
        )
        rows = self.store.connection.execute(query, values).fetchall()
        next_sequence = int(rows[limit - 1]["sequence"]) if len(rows) > limit else None
        return [self._validated_row(row) for row in rows[:limit]], next_sequence

    def claim_next(self) -> dict[str, Any] | None:
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.store.connection.execute(
                "SELECT * FROM creation_jobs WHERE state = 'queued' ORDER BY sequence LIMIT 1"
            ).fetchone()
            if row is None:
                self.store.connection.commit()
                return None
            record = self._validated_row(row)
            timestamp = utc_now()
            updated = self._updated_record(
                record,
                state="running",
                progress="reserved",
                started_at=timestamp,
                updated_at=timestamp,
            )
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET state = 'running', progress = 'reserved', "
                "generation = ?, cancel_requested = 0, record_json = ? "
                "WHERE job_id = ? AND state = 'queued' AND generation = ?",
                (
                    updated["generation"],
                    encode_json(updated),
                    updated["job_id"],
                    record["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job claim lost its CAS")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic="creation_job.running",
                entity_type="creation_job",
                entity_id=updated["job_id"],
                payload={"progress": "reserved", "generation": updated["generation"]},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return updated
        except BaseException:
            self.store.connection.rollback()
            raise

    def progress(self, job_id: str, progress: str) -> dict[str, Any]:
        record = self.get(job_id)
        if record["state"] != "running":
            raise conflict("Creation job is no longer running")
        timestamp = utc_now()
        updated = self._updated_record(record, progress=progress, updated_at=timestamp)
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET progress = ?, generation = ?, record_json = ? "
                "WHERE job_id = ? AND state = 'running' AND generation = ? "
                "AND cancel_requested = 0",
                (
                    progress,
                    updated["generation"],
                    encode_json(updated),
                    job_id,
                    record["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job progress lost its CAS")
        return updated

    def cancellation_requested(self, job_id: str) -> bool:
        row = self.store.connection.execute(
            "SELECT state, cancel_requested FROM creation_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return row is None or row["state"] != "running" or bool(row["cancel_requested"])

    def cancel(
        self,
        job_id: object,
        *,
        expected_generation: object,
        expected_record_hash: object,
    ) -> dict[str, Any]:
        identifier = _identifier(job_id, field="job_id")
        generation = _generation(expected_generation, field="expected_generation")
        digest = _digest(expected_record_hash, field="expected_record_hash")
        record = self.get(identifier)
        if record["generation"] != generation or not hmac.compare_digest(
            record["record_hash"], digest
        ):
            raise conflict("Creation job changed before cancellation")
        if record["state"] == "queued":
            timestamp = utc_now()
            updated = self._updated_record(
                record,
                state="canceled",
                progress="canceled",
                finished_at=timestamp,
                updated_at=timestamp,
            )
            with self.store.connection:
                has_release_authorize_reservation = (
                    record["operation"] == "asset.release.authorize"
                    and self.store.connection.execute(
                        "SELECT 1 FROM creation_output_grants WHERE reserved_job_id = ?",
                        (identifier,),
                    ).fetchone()
                    is not None
                )
                if has_release_authorize_reservation or record["operation"] in {
                    "asset.release.seal",
                    "runtime.bundle.build",
                    "runtime.headless.verify",
                    "game.materialization.bundle.build",
                    "game.materialize",
                    "game.package",
                    "game.package.extract",
                }:
                    self.output_grants.release_for_job(identifier)
                cursor = self.store.connection.execute(
                    "UPDATE creation_jobs SET state = 'canceled', progress = 'canceled', "
                    "generation = ?, record_json = ? WHERE job_id = ? AND state = 'queued' "
                    "AND generation = ?",
                    (
                        updated["generation"],
                        encode_json(updated),
                        identifier,
                        record["generation"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise conflict("Creation job cancellation lost its CAS")
                self.store.record_creation_event(
                    workspace_id=updated["workspace_id"],
                    topic="creation_job.canceled",
                    entity_type="creation_job",
                    entity_id=identifier,
                    payload={"generation": updated["generation"]},
                    created_at=timestamp,
                )
            return updated
        if record["state"] == "running":
            timestamp = utc_now()
            updated = self._updated_record(record, updated_at=timestamp)
            with self.store.connection:
                cursor = self.store.connection.execute(
                    "UPDATE creation_jobs SET cancel_requested = 1, generation = ?, "
                    "record_json = ? WHERE job_id = ? AND state = 'running' AND generation = ?",
                    (
                        updated["generation"],
                        encode_json(updated),
                        identifier,
                        record["generation"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise conflict("Creation job cancellation lost its CAS")
                self.store.record_creation_event(
                    workspace_id=updated["workspace_id"],
                    topic="creation_job.cancel_requested",
                    entity_type="creation_job",
                    entity_id=identifier,
                    payload={"generation": updated["generation"]},
                    created_at=timestamp,
                )
            return updated
        raise conflict("Creation job cannot be canceled in its current state")

    def finish_terminal(
        self,
        job_id: str,
        *,
        state: str,
        error: Mapping[str, Any] | None = None,
        cleanup_attempt: bool = False,
    ) -> dict[str, Any]:
        record = self.get(job_id)
        if record["state"] != "running":
            raise conflict("Creation job is no longer running")
        timestamp = utc_now()
        updated = self._updated_record(
            record,
            state=state,
            progress=state,
            error=None if error is None else dict(error),
            finished_at=timestamp,
            updated_at=timestamp,
        )
        with self.store.connection:
            if cleanup_attempt:
                deleted = self.store.connection.execute(
                    "DELETE FROM creation_job_attempts WHERE job_id = ?", (job_id,)
                )
                if deleted.rowcount != 1:
                    raise conflict("Creation job cleanup attempt changed")
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET state = ?, progress = ?, generation = ?, "
                "cancel_requested = 0, record_json = ? WHERE job_id = ? AND state = 'running' "
                "AND generation = ?",
                (
                    state,
                    state,
                    updated["generation"],
                    encode_json(updated),
                    job_id,
                    record["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job terminal transition lost its CAS")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic=f"creation_job.{state}",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"generation": updated["generation"]},
                created_at=timestamp,
            )
        return updated

    def snapshot_for_job(self, job: Mapping[str, Any]) -> dict[str, Any]:
        snapshot, _request, _dependencies, _staged_payloads = self.private_request_for_job(job)
        return snapshot

    def private_request_for_job(
        self,
        job: Mapping[str, Any],
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        tuple[dict[str, Any], ...],
        tuple[tuple[str, bytes], ...],
    ]:
        snapshot = self.evidence._snapshot(  # noqa: SLF001
            {
                "workspace_id": job["workspace_id"],
                "expected_root_generation": job["authority"]["root_generation"],
                "expected_source_revision": job["authority"]["source_revision"],
                "expected_workflow_status_hash": job["authority"]["workflow_status_hash"],
                "expected_artifact_snapshot_hash": job["authority"]["artifact_snapshot_hash"],
            }
        )
        if job["operation"] == "creation.compile":
            rebuilt = build_private_compile_request(
                job_id=job["job_id"],
                workspace_id=job["workspace_id"],
                authority=copy.deepcopy(job["authority"]),
                project=snapshot["project"],
            )
            dependencies: tuple[dict[str, Any], ...] = ()
            staged_payloads: tuple[tuple[str, bytes], ...] = ()
        elif job["operation"] == "artifact.admit":
            payload_row = self.store.connection.execute(
                "SELECT * FROM creation_job_payloads WHERE job_id = ?", (job["job_id"],)
            ).fetchone()
            if payload_row is None:
                raise invalid_state("Artifact admission payload is unavailable")
            document = self.artifacts.load_job_payload(payload_row)
            records = {record["artifact_id"]: record for record in snapshot["records"]}
            dependency_documents: list[dict[str, Any]] = []
            for item in job["inputs"]:
                record = records.get(item["artifact_id"])
                if (
                    record is None
                    or record["subject"] != item["subject"]
                    or record["lifecycle"] not in {"active", "candidate"}
                ):
                    raise conflict("Artifact admission dependency changed")
                subject = item["subject"]
                key = (
                    subject["format"],
                    subject["format_version"],
                    subject["id"],
                    subject["content_hash"],
                )
                dependency = snapshot["documents"].get(key)
                if dependency is None:
                    raise invalid_state("Artifact admission dependency is unavailable")
                dependency_documents.append(dependency)
            dependencies = tuple(dependency_documents)
            rebuilt = build_private_admission_request(
                job_id=job["job_id"],
                workspace_id=job["workspace_id"],
                authority=copy.deepcopy(job["authority"]),
                project=snapshot["project"],
                document=document,
                dependency_documents=dependencies,
            )
            staged_payloads = ()
        elif job["operation"] == "asset.process":
            rebuilt, dependencies, staged_payloads = self._verified_asset_process_request(
                job_id=job["job_id"],
                workspace_id=job["workspace_id"],
                authority=job["authority"],
                snapshot=snapshot,
                operation_params=job["operation_params"],
            )
        elif job["operation"] == "asset.qa.review":
            rebuilt, dependencies, staged_payloads = self._verified_asset_qa_review_request(
                job_id=job["job_id"],
                workspace_id=job["workspace_id"],
                authority=job["authority"],
                snapshot=snapshot,
                operation_params=job["operation_params"],
            )
        elif job["operation"] == "asset.release.authorize":
            (
                rebuilt,
                dependencies,
                staged_payloads,
                release_manifest,
                assetpack_manifest,
                release_authority,
            ) = self._verified_asset_release_authorize_request(
                job_id=job["job_id"],
                workspace_id=job["workspace_id"],
                authority=job["authority"],
                snapshot=snapshot,
                operation_params=job["operation_params"],
            )
            self._verify_pending_asset_release_authorize_grant(
                job,
                release_status=str(release_authority["status"]),
                expected_generation=int(job["operation_params"]["target_grant_generation"]),
                manifest_hash=str(release_manifest["content_hash"]),
                assetpack_hash=str(assetpack_manifest["content_hash"]),
            )
        elif job["operation"] == "asset.release.seal":
            grant = self.output_grants.get(job["operation_params"]["target_grant_id"])
            if (
                grant["workspace_id"] != job["workspace_id"]
                or grant["generation"] != job["operation_params"]["target_grant_generation"]
                or grant["state"] not in {"reserved", "recovery_required", "published"}
            ):
                raise conflict("Asset release output grant authority changed")
            initial_binding = self.output_grants.binding_for_job(
                str(job["job_id"]),
                allow_visible=None,
            )
            allow_visible = (
                None
                if grant["state"] in {"recovery_required", "published"}
                or initial_binding["recovery"] is not None
                else False
            )
            self.output_grants.binding_for_job(
                str(job["job_id"]),
                allow_visible=allow_visible,
            )
            (
                rebuilt,
                dependencies,
                staged_payloads,
                release_manifest,
                assetpack_manifest,
            ) = self._verified_asset_release_request(
                job_id=job["job_id"],
                workspace_id=job["workspace_id"],
                authority=job["authority"],
                snapshot=snapshot,
                operation_params=job["operation_params"],
            )
            binding = self.output_grants.binding_for_job(
                str(job["job_id"]),
                allow_visible=allow_visible,
            )
            if (
                binding["expected_manifest_hash"] != release_manifest["content_hash"]
                or binding["expected_tree_hash"] != assetpack_manifest["content_hash"]
            ):
                raise conflict("Asset release output grant hashes changed")
        elif job["operation"] == "runtime.compose":
            rebuilt, dependencies, staged_payloads = self._verified_runtime_compose_request(
                job_id=job["job_id"],
                workspace_id=job["workspace_id"],
                authority=job["authority"],
                snapshot=snapshot,
                operation_params=job["operation_params"],
            )
        elif job["operation"] == "runtime.bundle.build":
            target_grant = self.output_grants.get(job["operation_params"]["target_grant_id"])
            if (
                target_grant["workspace_id"] != job["workspace_id"]
                or target_grant["generation"] != job["operation_params"]["target_grant_generation"]
                or target_grant["state"] not in {"reserved", "recovery_required", "published"}
            ):
                raise conflict("Runtime bundle target grant authority changed")
            target_binding = self.output_grants.binding_for_job(
                str(job["job_id"]), allow_visible=None
            )
            allow_visible = (
                None
                if target_grant["state"] in {"recovery_required", "published"}
                or target_binding["recovery"] is not None
                else False
            )
            self.output_grants.binding_for_job(str(job["job_id"]), allow_visible=allow_visible)
            rebuilt, dependencies, staged_payloads, manifest = (
                self._verified_runtime_bundle_request(
                    job_id=job["job_id"],
                    workspace_id=job["workspace_id"],
                    authority=job["authority"],
                    snapshot=snapshot,
                    operation_params=job["operation_params"],
                )
            )
            target_binding = self.output_grants.binding_for_job(
                str(job["job_id"]), allow_visible=allow_visible
            )
            if (
                target_binding["expected_manifest_hash"] != manifest["content_hash"]
                or target_binding["expected_tree_hash"] != manifest["tree_hash"]
            ):
                raise conflict("Runtime bundle target grant hashes changed")
        elif job["operation"] == "runtime.headless.verify":
            target_grant = self.output_grants.get(job["operation_params"]["target_grant_id"])
            if (
                target_grant["workspace_id"] != job["workspace_id"]
                or target_grant["format_version"] != 6
                or target_grant["kind"] != "headless_evidence_directory"
                or target_grant["generation"]
                != job["operation_params"]["expected_target_grant_generation"] + 1
                or target_grant["state"] not in {"reserved", "recovery_required", "published"}
            ):
                raise conflict("Runtime headless target grant authority changed")
            target_binding = self.output_grants.binding_for_job(
                str(job["job_id"]),
                allow_visible=None,
            )
            allow_visible = (
                None
                if target_grant["state"] in {"recovery_required", "published"}
                or target_binding["recovery"] is not None
                else False
            )
            self.output_grants.binding_for_job(
                str(job["job_id"]),
                allow_visible=allow_visible,
            )
            rebuilt, dependencies, staged_payloads, _outputs, evidence_manifest = (
                self._verified_runtime_headless_request(
                    job_id=job["job_id"],
                    workspace_id=job["workspace_id"],
                    authority=job["authority"],
                    snapshot=snapshot,
                    operation_params=job["operation_params"],
                )
            )
            self._verify_pending_runtime_headless_grants(
                job,
                evidence_manifest=evidence_manifest,
            )
        elif job["operation"] == "game.materialization.bundle.build":
            target_grant = self.output_grants.get(job["operation_params"]["target_grant_id"])
            if (
                target_grant["workspace_id"] != job["workspace_id"]
                or target_grant["generation"] != job["operation_params"]["target_grant_generation"]
                or target_grant["state"] not in {"reserved", "recovery_required", "published"}
            ):
                raise conflict("Materialization target grant authority changed")
            target_binding = self.output_grants.binding_for_job(
                str(job["job_id"]), allow_visible=None
            )
            allow_visible = (
                None
                if target_grant["state"] in {"recovery_required", "published"}
                or target_binding["recovery"] is not None
                else False
            )
            self.output_grants.binding_for_job(str(job["job_id"]), allow_visible=allow_visible)
            rebuilt, dependencies, staged_payloads, manifest = (
                self._verified_materialization_bundle_request(
                    job_id=job["job_id"],
                    workspace_id=job["workspace_id"],
                    authority=job["authority"],
                    snapshot=snapshot,
                    operation_params=job["operation_params"],
                )
            )
            target_binding = self.output_grants.binding_for_job(
                str(job["job_id"]), allow_visible=allow_visible
            )
            if (
                target_binding["expected_manifest_hash"] != manifest["content_hash"]
                or target_binding["expected_tree_hash"] != manifest["tree_hash"]
            ):
                raise conflict("Materialization target grant hashes changed")
        elif job["operation"] == "game.materialize":
            target_grant = self.output_grants.get(job["operation_params"]["target_grant_id"])
            if (
                target_grant["workspace_id"] != job["workspace_id"]
                or target_grant["generation"] != job["operation_params"]["target_grant_generation"]
                or target_grant["state"] not in {"reserved", "recovery_required", "published"}
            ):
                raise conflict("Standalone target grant authority changed")
            target_binding = self.output_grants.binding_for_job(
                str(job["job_id"]), allow_visible=None
            )
            allow_visible = (
                None
                if target_grant["state"] in {"recovery_required", "published"}
                or target_binding["recovery"] is not None
                else False
            )
            self.output_grants.binding_for_job(str(job["job_id"]), allow_visible=allow_visible)
            rebuilt, dependencies, staged_payloads, manifest, lock = (
                self._verified_game_materialize_request(
                    job_id=job["job_id"],
                    workspace_id=job["workspace_id"],
                    authority=job["authority"],
                    snapshot=snapshot,
                    operation_params=job["operation_params"],
                )
            )
            target_binding = self.output_grants.binding_for_job(
                str(job["job_id"]), allow_visible=allow_visible
            )
            if (
                target_binding["expected_manifest_hash"] != manifest["content_hash"]
                or target_binding["expected_tree_hash"] != lock["tree_hash"]
            ):
                raise conflict("Standalone target grant hashes changed")
        elif job["operation"] == "game.package":
            target_grant = self.output_grants.get(job["operation_params"]["target_grant_id"])
            if (
                target_grant["workspace_id"] != job["workspace_id"]
                or target_grant["format_version"] != 5
                or target_grant["kind"] != "game_package_file"
                or target_grant["generation"] != job["operation_params"]["target_grant_generation"]
                or target_grant["state"] not in {"reserved", "recovery_required", "published"}
            ):
                raise conflict("Game package target grant authority changed")
            target_binding = self.output_grants.binding_for_job(
                str(job["job_id"]), allow_visible=None
            )
            allow_visible = (
                None
                if target_grant["state"] in {"recovery_required", "published"}
                or target_binding["recovery"] is not None
                else False
            )
            self.output_grants.binding_for_job(str(job["job_id"]), allow_visible=allow_visible)
            (
                rebuilt,
                dependencies,
                staged_payloads,
                manifest,
                archive_sha256,
                archive_size,
            ) = self._verified_game_package_request(
                job_id=job["job_id"],
                workspace_id=job["workspace_id"],
                authority=job["authority"],
                snapshot=snapshot,
                operation_params=job["operation_params"],
            )
            target_binding = self.output_grants.binding_for_job(
                str(job["job_id"]), allow_visible=allow_visible
            )
            if (
                target_binding["expected_manifest_hash"] != manifest["content_hash"]
                or target_binding["expected_archive_sha256"] != archive_sha256
                or target_binding["expected_size_bytes"] != archive_size
            ):
                raise conflict("Game package target grant hashes changed")
        elif job["operation"] == "game.package.extract":
            target_grant = self.output_grants.get(job["operation_params"]["target_grant_id"])
            if (
                target_grant["workspace_id"] != job["workspace_id"]
                or target_grant["format_version"] != 4
                or target_grant["kind"] != "standalone_game_directory"
                or target_grant["generation"] != job["operation_params"]["target_grant_generation"]
                or target_grant["state"] not in {"reserved", "recovery_required", "published"}
            ):
                raise conflict("Game package extraction target grant authority changed")
            target_binding = self.output_grants.binding_for_job(
                str(job["job_id"]), allow_visible=None
            )
            allow_visible = (
                None
                if target_grant["state"] in {"recovery_required", "published"}
                or target_binding["recovery"] is not None
                else False
            )
            self.output_grants.binding_for_job(str(job["job_id"]), allow_visible=allow_visible)
            rebuilt, dependencies, staged_payloads, evidence = (
                self._verified_game_package_extract_request(
                    job_id=job["job_id"],
                    workspace_id=job["workspace_id"],
                    authority=job["authority"],
                    snapshot=snapshot,
                    operation_params=job["operation_params"],
                )
            )
            target_binding = self.output_grants.binding_for_job(
                str(job["job_id"]), allow_visible=allow_visible
            )
            if (
                target_binding["expected_manifest_hash"]
                != evidence["standalone_game"]["content_hash"]
                or target_binding["expected_tree_hash"] != evidence["extracted_tree_hash"]
            ):
                raise conflict("Game package extraction target grant hashes changed")
        else:
            raise invalid_state("Creation job operation is unsupported")
        if rebuilt["inputs"] != job["inputs"]:
            raise conflict("Creation job immutable input references changed")
        return snapshot, rebuilt, dependencies, staged_payloads

    def recover(
        self,
        job_id: object,
        *,
        mode: object,
        expected_generation: object,
        expected_record_hash: object,
    ) -> dict[str, Any]:
        identifier = _identifier(job_id, field="job_id")
        generation = _generation(expected_generation, field="expected_generation")
        digest = _digest(expected_record_hash, field="expected_record_hash")
        if mode not in {"resume", "rollback", "cleanup"}:
            raise invalid_request("Creation job recovery mode is invalid")
        return CreationJobCoordinator(self).recover(
            identifier,
            mode=mode,
            expected_generation=generation,
            expected_record_hash=digest,
        )

    @staticmethod
    def _updated_record(record: Mapping[str, Any], **changes: object) -> dict[str, Any]:
        updated = copy.deepcopy(dict(record))
        updated.update(changes)
        updated["generation"] = record["generation"] + 1
        updated["record_hash"] = creation_job_record_hash(updated)
        try:
            return validate_studio_creation_job(updated)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Creation job transition is invalid") from exc

    def _validated_row(self, row: sqlite3.Row) -> dict[str, Any]:
        record = decode_object(row["record_json"], context="creation job")
        try:
            validate_studio_creation_job(record)
        except StudioContractError as exc:
            raise invalid_state("Stored creation job record is invalid") from exc
        if (
            record["job_id"] != row["job_id"]
            or record["workspace_id"] != row["workspace_id"]
            or record["operation"] != row["operation"]
            or record["state"] != row["state"]
            or record["progress"] != row["progress"]
            or record["generation"] != row["generation"]
        ):
            raise invalid_state("Stored creation job DB projection diverged")
        input_rows = self.store.connection.execute(
            "SELECT * FROM creation_job_inputs WHERE job_id = ? ORDER BY position",
            (row["job_id"],),
        ).fetchall()
        projected = [
            {
                "artifact_id": item["artifact_id"],
                "subject": {
                    "format": item["subject_format"],
                    "format_version": item["subject_version"],
                    "id": item["subject_id"],
                    "content_hash": item["content_hash"],
                },
            }
            for position, item in enumerate(input_rows)
            if int(item["position"]) == position
        ]
        if len(projected) != len(input_rows) or projected != record["inputs"]:
            raise invalid_state("Stored creation job input projection diverged")
        payload_row = self.store.connection.execute(
            "SELECT * FROM creation_job_payloads WHERE job_id = ?", (row["job_id"],)
        ).fetchone()
        if record["operation"] == "artifact.admit":
            if payload_row is None:
                raise invalid_state("Stored artifact admission payload is unavailable")
            self.artifacts.load_job_payload(payload_row)
        elif record["operation"] == "asset.process" and payload_row is not None:
            if record["state"] != "succeeded":
                raise invalid_state("Pending asset process has unexpected retained bytes")
            self.artifacts.load_asset_process_retention(
                workspace_id=record["workspace_id"],
                producer_job_id=record["job_id"],
            )
        elif (
            record["operation"] == "asset.process"
            and record["state"] == "succeeded"
            and record["result"]["analysis_status"] == "passed"
        ):
            raise invalid_state("Completed asset process retained bytes are unavailable")
        elif payload_row is not None:
            raise invalid_state("Stored creation job has an unexpected private payload")
        _validate_creation_job_result_projection(
            self.store,
            row,
            record,
            artifacts=self.artifacts,
        )
        return record


class CreationJobCoordinator:
    """Synchronous durable FIFO coordinator; a scheduler may call run_once repeatedly."""

    def __init__(
        self,
        jobs: CreationJobManager,
        *,
        timeout_seconds: float = 60.0,
        shutdown_requested: Callable[[], bool] | None = None,
    ) -> None:
        if not 0.05 <= float(timeout_seconds) <= 3600.0:
            raise ValueError("creation job timeout is outside its fixed bounds")
        self.jobs = jobs
        self.store = jobs.store
        self.timeout_seconds = float(timeout_seconds)
        self.shutdown_requested = shutdown_requested or (lambda: False)

    def run_once(self) -> str | None:
        job = self.jobs.claim_next()
        if job is None:
            return None
        try:
            self._execute(job)
        except CreationWorkerExecutionError as exc:
            if self.shutdown_requested():
                return str(job["job_id"])
            self._finish_after_error(
                job["job_id"],
                exc.code,
                recovery_evidence=exc.recovery_evidence,
            )
        except StudioError:
            if self.shutdown_requested():
                return str(job["job_id"])
            self._finish_after_error(job["job_id"], "authority_changed")
        except Exception:
            if self.shutdown_requested():
                return str(job["job_id"])
            self._finish_after_error(job["job_id"], "internal_error")
        return str(job["job_id"])

    def recover(
        self,
        job_id: str,
        *,
        mode: str,
        expected_generation: int,
        expected_record_hash: str | None,
    ) -> dict[str, Any]:
        if expected_record_hash is None:
            raise invalid_request("expected_record_hash is required")
        record = self.jobs.get(job_id)
        if record["generation"] != expected_generation or not hmac.compare_digest(
            record["record_hash"], expected_record_hash
        ):
            raise conflict("Creation job changed before recovery")
        if mode == "cleanup":
            if record["state"] != "succeeded" or record["progress"] != "cleanup_pending":
                raise conflict("Creation job does not require cleanup recovery")
            self._recover_cleanup_with_evidence(record)
            return self._complete_cleanup(job_id)
        if record["state"] != "orphaned":
            raise conflict("Creation job is not recoverable in its current state")
        attempt = self.store.connection.execute(
            "SELECT * FROM creation_job_attempts WHERE job_id = ?", (job_id,)
        ).fetchone()
        has_release_authorize_reservation = (
            record["operation"] == "asset.release.authorize"
            and self.store.connection.execute(
                "SELECT 1 FROM creation_output_grants WHERE reserved_job_id = ?",
                (job_id,),
            ).fetchone()
            is not None
        )
        if record["operation"] == "asset.release.seal" or (
            record["operation"] == "asset.release.authorize" and has_release_authorize_reservation
        ):
            return self._recover_asset_release(
                record,
                mode=mode,
                attempt_exists=attempt is not None,
            )
        if record["operation"] == "runtime.bundle.build":
            return self._recover_runtime_bundle(
                record,
                mode=mode,
                attempt_exists=attempt is not None,
            )
        if record["operation"] == "runtime.headless.verify":
            return self._recover_runtime_headless(
                record,
                mode=mode,
                attempt_exists=attempt is not None,
            )
        if record["operation"] == "game.materialization.bundle.build":
            return self._recover_materialization_bundle(
                record,
                mode=mode,
                attempt_exists=attempt is not None,
            )
        if record["operation"] == "game.materialize":
            return self._recover_game_materialize(
                record,
                mode=mode,
                attempt_exists=attempt is not None,
            )
        if record["operation"] == "game.package":
            return self._recover_game_package(
                record,
                mode=mode,
                attempt_exists=attempt is not None,
            )
        if record["operation"] == "game.package.extract":
            return self._recover_game_package_extract(
                record,
                mode=mode,
                attempt_exists=attempt is not None,
            )
        if (
            record["operation"] == "asset.process"
            and attempt is not None
            and attempt["phase"] in {"output_published", "registry_committing"}
            and self._asset_process_project_publication_may_exist(record, attempt)
        ):
            raise conflict(
                "Processed asset project publication authority is retained and recovery_required",
                recovery_evidence=self._attempt_recovery_evidence(job_id),
            )
        if attempt is not None:
            self._recover_cleanup_with_evidence(record)
        timestamp = utc_now()
        if mode == "resume":
            self.jobs.snapshot_for_job(record)
            updated = self.jobs._updated_record(  # noqa: SLF001
                record,
                state="queued",
                progress="queued",
                result=None,
                error=None,
                started_at=None,
                finished_at=None,
                updated_at=timestamp,
            )
            state = "queued"
            topic = "creation_job.requeued"
        else:
            updated = self.jobs._updated_record(  # noqa: SLF001
                record,
                state="failed",
                progress="failed",
                error={
                    "code": "service_restart",
                    "message": "Creation job was rolled back after service restart",
                    "retryable": False,
                },
                updated_at=timestamp,
            )
            state = "failed"
            topic = "creation_job.rolled_back"
        with self.store.connection:
            if attempt is not None:
                deleted = self.store.connection.execute(
                    "DELETE FROM creation_job_attempts WHERE job_id = ?", (job_id,)
                )
                if deleted.rowcount != 1:
                    raise conflict("Creation job recovery attempt changed")
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET state = ?, progress = ?, generation = ?, "
                "cancel_requested = 0, record_json = ? WHERE job_id = ? "
                "AND state = 'orphaned' AND generation = ?",
                (
                    state,
                    updated["progress"],
                    updated["generation"],
                    encode_json(updated),
                    job_id,
                    record["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job recovery lost its CAS")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic=topic,
                entity_type="creation_job",
                entity_id=job_id,
                payload={"generation": updated["generation"]},
                created_at=timestamp,
            )
        return updated

    @staticmethod
    def _asset_release_publication_identity(binding: Mapping[str, Any]) -> tuple[int, int] | None:
        destination = Path(binding["path"])
        parent_identity = tuple(binding["parent_identity"])
        expected_tree_hash = str(binding["expected_tree_hash"])
        expected_manifest_hash = str(binding["expected_manifest_hash"])
        verified = recover_generic_assetpack(
            destination,
            expected_parent_identity=parent_identity,
        )
        if verified is None:
            try:
                visible = destination.exists() or destination.is_symlink()
            except OSError as exc:
                raise conflict("Asset release output cannot be inspected") from exc
            if not visible:
                return None
            verified = verify_generic_assetpack(
                destination,
                expected_content_hash=expected_tree_hash,
                expected_parent_identity=parent_identity,
            )
        with verified:
            manifest = verified.manifest
            if (
                manifest.get("content_hash") != expected_tree_hash
                or manifest.get("release_ready_manifest", {}).get("content_hash")
                != expected_manifest_hash
            ):
                raise conflict("Asset release recovery hashes changed")
            return tuple(verified.root_identity)

    @classmethod
    def _rollback_asset_release_publication(cls, binding: Mapping[str, Any]) -> None:
        destination = Path(binding["path"])
        parent_identity = tuple(binding["parent_identity"])
        try:
            rollback_generic_assetpack(
                destination,
                expected_parent_identity=parent_identity,
            )
        except GenericAssetpackError as exc:
            if exc.reason_code == "assetpack_rollback_recovery_required":
                raise conflict(
                    "Asset release rollback requires retained-output recovery",
                    reason_code=exc.reason_code,
                    recovery_evidence=exc.recovery_evidence,
                ) from exc
            if exc.reason_code != "assetpack_rollback_committed":
                raise conflict(
                    "Asset release rollback is ambiguous",
                    reason_code=exc.reason_code,
                    recovery_evidence=exc.recovery_evidence,
                ) from exc
        identity = cls._asset_release_publication_identity(binding)
        if identity is None:
            return
        stored_identity = binding["published_identity"]
        if stored_identity is not None and tuple(stored_identity) != identity:
            raise conflict("Asset release rollback identity changed")
        expected_tree_hash = str(binding["expected_tree_hash"])
        expected_manifest_hash = str(binding["expected_manifest_hash"])

        def verify_owned(path: Path, retained_root_fd: int | None) -> None:
            with verify_generic_assetpack(
                path,
                expected_content_hash=expected_tree_hash,
                expected_parent_identity=(parent_identity if retained_root_fd is None else None),
                _retained_root_fd=retained_root_fd,
            ) as verified:
                if (
                    verified.manifest.get("release_ready_manifest", {}).get("content_hash")
                    != expected_manifest_hash
                ):
                    raise conflict("Asset release rollback manifest changed")

        try:
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise conflict("Asset release rollback parent identity changed")
                quarantine_and_remove_verified_directory(
                    destination,
                    identity,
                    verify_retained=verify_owned,
                    retained_parent=parent,
                )
        except DirectoryPublishRecoveryRequiredError as exc:
            raise conflict(
                "Asset release rollback requires retained-output recovery",
                reason_code="asset_release_rollback_recovery_required",
                recovery_evidence=retained_recovery_evidence(
                    stage_path=destination,
                    stage_identity=identity,
                ),
            ) from exc
        except (
            AssetContractError,
            DirectoryPublishError,
            DirectoryPublishIndeterminateError,
        ) as exc:
            raise conflict("Asset release rollback could not remove the exact output") from exc

    def _recover_asset_release(
        self,
        record: Mapping[str, Any],
        *,
        mode: str,
        attempt_exists: bool,
    ) -> dict[str, Any]:
        job_id = str(record["job_id"])
        binding = self.jobs.output_grants.binding_for_job(job_id, allow_visible=None)
        try:
            if mode == "resume":
                published_identity = self._asset_release_publication_identity(binding)
                if published_identity is not None:
                    with self.store.connection:
                        self.jobs.output_grants.note_publication_verified(
                            job_id,
                            published_identity=published_identity,
                        )
            else:
                self._rollback_asset_release_publication(binding)
        except GenericAssetpackError as exc:
            raise conflict(
                "Asset release publication recovery is ambiguous",
                reason_code=exc.reason_code,
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        except StudioError:
            raise
        except (OSError, ValueError) as exc:
            raise conflict(
                "Asset release publication recovery is ambiguous",
                reason_code=type(exc).__name__,
            ) from exc

        timestamp = utc_now()
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            if mode == "resume":
                grant = self.jobs.output_grants.resume_for_job(job_id)
                operation_params = copy.deepcopy(dict(record["operation_params"]))
                operation_params["target_grant_generation"] = grant["generation"]
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    operation_params=operation_params,
                    state="queued",
                    progress="queued",
                    result=None,
                    error=None,
                    started_at=None,
                    finished_at=None,
                    updated_at=timestamp,
                )
                self.jobs.snapshot_for_job(updated)
                state = "queued"
                topic = "creation_job.requeued"
            else:
                self.jobs.output_grants.rollback_for_job(job_id)
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    state="failed",
                    progress="failed",
                    error={
                        "code": "service_restart",
                        "message": "Creation job asset release was rolled back explicitly",
                        "retryable": False,
                    },
                    updated_at=timestamp,
                )
                state = "failed"
                topic = "creation_job.rolled_back"
            if attempt_exists:
                self._recover_cleanup_with_evidence(record)
                deleted = self.store.connection.execute(
                    "DELETE FROM creation_job_attempts WHERE job_id = ?", (job_id,)
                )
                if deleted.rowcount != 1:
                    raise conflict("Creation job recovery attempt changed")
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET state = ?, progress = ?, generation = ?, "
                "cancel_requested = 0, record_json = ? WHERE job_id = ? "
                "AND state = 'orphaned' AND generation = ?",
                (
                    state,
                    updated["progress"],
                    updated["generation"],
                    encode_json(updated),
                    job_id,
                    record["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job asset release recovery lost its CAS")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic=topic,
                entity_type="creation_job",
                entity_id=job_id,
                payload={"generation": updated["generation"]},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return updated
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _runtime_bundle_publication_identity(
        binding: Mapping[str, Any],
    ) -> tuple[int, int] | None:
        destination = Path(binding["path"])
        parent_identity = tuple(binding["parent_identity"])
        expected_content_hash = str(binding["expected_manifest_hash"])
        expected_tree_hash = str(binding["expected_tree_hash"])
        verified = recover_game_runtime_bundle(
            destination,
            expected_parent_identity=parent_identity,
        )
        if verified is None:
            try:
                visible = destination.exists() or destination.is_symlink()
            except OSError as exc:
                raise conflict("Runtime bundle output cannot be inspected") from exc
            if not visible:
                return None
            verified = verify_game_runtime_bundle(
                destination,
                expected_content_hash=expected_content_hash,
            )
        with verified:
            manifest = verified.manifest
            if (
                manifest.get("content_hash") != expected_content_hash
                or manifest.get("tree_hash") != expected_tree_hash
            ):
                raise conflict("Runtime bundle recovery hashes changed")
            return tuple(verified.root_identity)

    @classmethod
    def _rollback_runtime_bundle_publication(cls, binding: Mapping[str, Any]) -> None:
        destination = Path(binding["path"])
        parent_identity = tuple(binding["parent_identity"])
        try:
            rollback_game_runtime_bundle(
                destination,
                expected_parent_identity=parent_identity,
            )
        except GameRuntimeBundleError as exc:
            if exc.reason_code != "game_runtime_bundle_rollback_committed":
                raise conflict(
                    "Runtime bundle rollback is ambiguous",
                    reason_code=exc.reason_code,
                    recovery_evidence=exc.recovery_evidence,
                ) from exc
        try:
            identity = cls._runtime_bundle_publication_identity(binding)
        except GameRuntimeBundleError as exc:
            raise conflict(
                "Runtime bundle rollback is ambiguous",
                reason_code=exc.reason_code,
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        if identity is None:
            return
        stored_identity = binding["published_identity"]
        if stored_identity is not None and tuple(stored_identity) != identity:
            raise conflict("Runtime bundle rollback identity changed")
        if sys.platform.startswith("linux") and os.name == "posix":
            raise conflict(
                "Runtime bundle rollback retains visible exact bytes under the active threat model",
                reason_code="runtime_bundle_rollback_recovery_required",
                recovery_evidence=retained_recovery_evidence(
                    stage_path=destination,
                    stage_identity=identity,
                ),
            )
        expected_content_hash = str(binding["expected_manifest_hash"])
        expected_tree_hash = str(binding["expected_tree_hash"])

        def verify_owned(path: Path) -> None:
            with verify_game_runtime_bundle(
                path,
                expected_content_hash=expected_content_hash,
            ) as verified:
                if verified.manifest.get("tree_hash") != expected_tree_hash:
                    raise conflict("Runtime bundle rollback tree hash changed")

        try:
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise conflict("Runtime bundle rollback parent identity changed")
                quarantine_and_remove_verified_directory(
                    destination,
                    identity,
                    verify=verify_owned,
                )
        except (AssetContractError, DirectoryPublishError) as exc:
            raise conflict("Runtime bundle rollback could not remove the exact output") from exc

    def _recover_runtime_bundle(
        self,
        record: Mapping[str, Any],
        *,
        mode: str,
        attempt_exists: bool,
    ) -> dict[str, Any]:
        job_id = str(record["job_id"])
        binding = self.jobs.output_grants.binding_for_job(job_id, allow_visible=None)
        try:
            if mode == "resume":
                published_identity = self._runtime_bundle_publication_identity(binding)
                if published_identity is not None:
                    with self.store.connection:
                        self.jobs.output_grants.note_publication_verified(
                            job_id,
                            published_identity=published_identity,
                        )
            else:
                self._rollback_runtime_bundle_publication(binding)
        except StudioError:
            raise
        except (GameRuntimeBundleError, OSError, ValueError) as exc:
            raise conflict(
                "Runtime bundle publication recovery is ambiguous",
                reason_code=getattr(exc, "reason_code", type(exc).__name__),
                recovery_evidence=getattr(exc, "recovery_evidence", None),
            ) from exc

        timestamp = utc_now()
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            if mode == "resume":
                grant = self.jobs.output_grants.resume_for_job(job_id)
                operation_params = copy.deepcopy(dict(record["operation_params"]))
                operation_params["target_grant_generation"] = grant["generation"]
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    operation_params=operation_params,
                    state="queued",
                    progress="queued",
                    result=None,
                    error=None,
                    started_at=None,
                    finished_at=None,
                    updated_at=timestamp,
                )
                self.jobs.snapshot_for_job(updated)
                state = "queued"
                topic = "creation_job.requeued"
            else:
                self.jobs.output_grants.rollback_for_job(job_id)
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    state="failed",
                    progress="failed",
                    error={
                        "code": "service_restart",
                        "message": "Creation job runtime bundle was rolled back explicitly",
                        "retryable": False,
                    },
                    updated_at=timestamp,
                )
                state = "failed"
                topic = "creation_job.rolled_back"
            if attempt_exists:
                self._recover_cleanup_with_evidence(record)
                deleted = self.store.connection.execute(
                    "DELETE FROM creation_job_attempts WHERE job_id = ?", (job_id,)
                )
                if deleted.rowcount != 1:
                    raise conflict("Creation job recovery attempt changed")
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET state = ?, progress = ?, generation = ?, "
                "cancel_requested = 0, record_json = ? WHERE job_id = ? "
                "AND state = 'orphaned' AND generation = ?",
                (
                    state,
                    updated["progress"],
                    updated["generation"],
                    encode_json(updated),
                    job_id,
                    record["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job runtime bundle recovery lost its CAS")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic=topic,
                entity_type="creation_job",
                entity_id=job_id,
                payload={"generation": updated["generation"]},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return updated
        except BaseException:
            self.store.connection.rollback()
            raise

    def _runtime_headless_publication_identity(
        self,
        record: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> tuple[int, int] | None:
        params = record["operation_params"]
        source = self.jobs.output_grants.published_binding(
            grant_id=str(params["source_grant_id"]),
            workspace_id=str(record["workspace_id"]),
            expected_generation=int(params["expected_source_grant_generation"]),
        )
        destination = Path(binding["path"])
        parent_identity = tuple(binding["parent_identity"])
        expected_content_hash = str(binding["expected_manifest_hash"])
        expected_tree_hash = str(binding["expected_tree_hash"])
        with open_verified_output_parent(destination.parent, create=False) as parent:
            if parent.identities[-1] != parent_identity:
                raise conflict("Runtime headless output parent identity changed")
            verified = recover_headless_evidence_set(
                destination,
                bundle_root=source["path"],
            )
            if verified is None:
                try:
                    visible = destination.exists() or destination.is_symlink()
                except OSError as exc:
                    raise conflict("Runtime headless output cannot be inspected") from exc
                if not visible:
                    parent.assert_current()
                    return None
                verified = verify_headless_evidence_set(
                    destination,
                    bundle_root=source["path"],
                    expected_content_hash=expected_content_hash,
                )
            try:
                manifest = verified.manifest
                if (
                    manifest["content_hash"] != expected_content_hash
                    or manifest["tree_hash"] != expected_tree_hash
                ):
                    raise conflict("Runtime headless recovery hashes changed")
                identity = tuple(verified.root_identity)
            finally:
                verified.close()
            parent.assert_current()
            return identity

    def _verify_runtime_headless_recovery_binding(
        self,
        record: Mapping[str, Any],
        binding: Mapping[str, Any],
        *,
        published_identity: tuple[int, int] | None,
        require_verified: bool,
    ) -> None:
        if record.get("operation") != "runtime.headless.verify":
            raise conflict("Runtime headless recovery operation changed")
        params = record["operation_params"]
        expected_generation = int(params["expected_target_grant_generation"]) + 1
        grant = self.jobs.output_grants.get(str(params["target_grant_id"]))
        if (
            grant["format_version"] != 6
            or grant["workspace_id"] != record["workspace_id"]
            or grant["kind"] != "headless_evidence_directory"
            or grant["state"] != "reserved"
            or grant["generation"] != expected_generation
            or binding["generation"] != expected_generation
        ):
            raise conflict("Runtime headless recovery grant generation changed")

        def verify_job_binding() -> None:
            current = self.jobs.output_grants.binding_for_job(
                str(record["job_id"]),
                allow_visible=True if require_verified else None,
            )
            if dict(binding) != current:
                raise conflict("Runtime headless recovery job binding changed")

        recovery = binding["recovery"]
        retained_identity = binding["published_identity"]
        if recovery is None:
            if require_verified or published_identity is not None or retained_identity is not None:
                raise conflict("Runtime headless recovery metadata is unavailable")
            verify_job_binding()
            return
        if (
            recovery["expected_manifest_hash"] != binding["expected_manifest_hash"]
            or recovery["expected_tree_hash"] != binding["expected_tree_hash"]
        ):
            raise conflict("Runtime headless recovery metadata changed")
        if recovery["phase"] != "publication_verified":
            if require_verified:
                raise conflict("Runtime headless recovery metadata is not verified")
            if retained_identity is not None:
                raise conflict("Runtime headless recovery identity is ambiguous")
            verify_job_binding()
            return
        recovery_identity = tuple(recovery["published_identity"])
        if (
            retained_identity is None
            or recovery_identity != tuple(retained_identity)
            or (published_identity is not None and recovery_identity != tuple(published_identity))
        ):
            raise conflict("Runtime headless recovery identity changed")
        if "journal_identity" in recovery and (
            recovery.get("journal_payload_state") != "ready"
            or tuple(recovery.get("stage_identity", ())) != recovery_identity
        ):
            raise conflict("Runtime headless recovery journal metadata changed")
        verify_job_binding()

    def _rollback_runtime_headless_publication(
        self,
        record: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> None:
        identity = self._runtime_headless_publication_identity(record, binding)
        if identity is None:
            return
        stored_identity = binding["published_identity"]
        if stored_identity is not None and tuple(stored_identity) != identity:
            raise conflict("Runtime headless rollback identity changed")
        destination = Path(binding["path"])
        if sys.platform.startswith("linux") and os.name == "posix":
            raise conflict(
                "Runtime headless rollback retains visible exact bytes under the "
                "active threat model",
                reason_code="runtime_headless_rollback_recovery_required",
                recovery_evidence=retained_recovery_evidence(
                    stage_path=destination,
                    stage_identity=identity,
                ),
            )
        params = record["operation_params"]
        source = self.jobs.output_grants.published_binding(
            grant_id=str(params["source_grant_id"]),
            workspace_id=str(record["workspace_id"]),
            expected_generation=int(params["expected_source_grant_generation"]),
        )
        expected_content_hash = str(binding["expected_manifest_hash"])
        expected_tree_hash = str(binding["expected_tree_hash"])

        def verify_owned(path: Path) -> None:
            verified = verify_headless_evidence_set(
                path,
                bundle_root=source["path"],
                expected_content_hash=expected_content_hash,
            )
            try:
                if verified.manifest["tree_hash"] != expected_tree_hash:
                    raise conflict("Runtime headless rollback tree hash changed")
            finally:
                verified.close()

        try:
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != tuple(binding["parent_identity"]):
                    raise conflict("Runtime headless rollback parent identity changed")
                quarantine_and_remove_verified_directory(
                    destination,
                    identity,
                    verify=verify_owned,
                )
        except (AssetContractError, DirectoryPublishError) as exc:
            raise conflict("Runtime headless rollback could not remove the exact output") from exc

    def _recover_runtime_headless(
        self,
        record: Mapping[str, Any],
        *,
        mode: str,
        attempt_exists: bool,
    ) -> dict[str, Any]:
        job_id = str(record["job_id"])
        binding = self.jobs.output_grants.binding_for_job(job_id, allow_visible=None)
        try:
            if mode == "resume":
                published_identity = self._runtime_headless_publication_identity(record, binding)
                self._verify_runtime_headless_recovery_binding(
                    record,
                    binding,
                    published_identity=published_identity,
                    require_verified=False,
                )
                if published_identity is not None:
                    with self.store.connection:
                        self.jobs.output_grants.note_publication_verified(
                            job_id,
                            published_identity=published_identity,
                        )
                    binding = self.jobs.output_grants.binding_for_job(
                        job_id,
                        allow_visible=True,
                    )
                    self._verify_runtime_headless_recovery_binding(
                        record,
                        binding,
                        published_identity=published_identity,
                        require_verified=True,
                    )
            else:
                self._rollback_runtime_headless_publication(record, binding)
        except StudioError:
            raise
        except (GenericHeadlessError, OSError, ValueError) as exc:
            raise conflict(
                "Runtime headless publication recovery is ambiguous",
                reason_code=getattr(exc, "reason_code", type(exc).__name__),
                recovery_evidence=getattr(exc, "recovery_evidence", None),
            ) from exc

        timestamp = utc_now()
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            if mode == "resume":
                grant = self.jobs.output_grants.resume_for_job(job_id)
                prior_target_generation = int(
                    record["operation_params"]["expected_target_grant_generation"]
                )
                if (
                    grant["format_version"] != 6
                    or grant["grant_id"] != record["operation_params"]["target_grant_id"]
                    or grant["workspace_id"] != record["workspace_id"]
                    or grant["kind"] != "headless_evidence_directory"
                    or grant["state"] != "reserved"
                    or grant["generation"] != prior_target_generation + 2
                ):
                    raise conflict("Runtime headless recovery grant generation changed")
                operation_params = copy.deepcopy(dict(record["operation_params"]))
                operation_params["expected_target_grant_generation"] = grant["generation"] - 1
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    operation_params=operation_params,
                    state="queued",
                    progress="queued",
                    result=None,
                    error=None,
                    started_at=None,
                    finished_at=None,
                    updated_at=timestamp,
                )
                self.jobs.snapshot_for_job(updated)
                state = "queued"
                topic = "creation_job.requeued"
            else:
                self.jobs.output_grants.rollback_for_job(job_id)
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    state="failed",
                    progress="failed",
                    error={
                        "code": "service_restart",
                        "message": (
                            "Creation job runtime headless output was rolled back explicitly"
                        ),
                        "retryable": False,
                    },
                    updated_at=timestamp,
                )
                state = "failed"
                topic = "creation_job.rolled_back"
            if attempt_exists:
                self._recover_cleanup_with_evidence(
                    record,
                    allow_requeue_retirement=mode == "resume",
                )
                deleted = self.store.connection.execute(
                    "DELETE FROM creation_job_attempts WHERE job_id = ?", (job_id,)
                )
                if deleted.rowcount != 1:
                    raise conflict("Creation job recovery attempt changed")
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET state = ?, progress = ?, generation = ?, "
                "cancel_requested = 0, record_json = ? WHERE job_id = ? "
                "AND state = 'orphaned' AND generation = ?",
                (
                    state,
                    updated["progress"],
                    updated["generation"],
                    encode_json(updated),
                    job_id,
                    record["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job runtime headless recovery lost its CAS")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic=topic,
                entity_type="creation_job",
                entity_id=job_id,
                payload={"generation": updated["generation"]},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return updated
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _materialization_bundle_publication_identity(
        binding: Mapping[str, Any],
    ) -> tuple[int, int] | None:
        destination = Path(binding["path"])
        parent_identity = tuple(binding["parent_identity"])
        expected_content_hash = str(binding["expected_manifest_hash"])
        expected_tree_hash = str(binding["expected_tree_hash"])
        verified = recover_game_materialization_bundle(
            destination,
            expected_parent_identity=parent_identity,
        )
        if verified is None:
            try:
                visible = destination.exists() or destination.is_symlink()
            except OSError as exc:
                raise conflict("Materialization output cannot be inspected") from exc
            if not visible:
                return None
            verified = verify_game_materialization_bundle(
                destination,
                expected_content_hash=expected_content_hash,
                expected_parent_identity=parent_identity,
            )
        with verified:
            manifest = verified.manifest
            if (
                manifest.get("content_hash") != expected_content_hash
                or manifest.get("tree_hash") != expected_tree_hash
            ):
                raise conflict("Materialization recovery hashes changed")
            return tuple(verified.root_identity)

    @classmethod
    def _rollback_materialization_bundle_publication(
        cls,
        binding: Mapping[str, Any],
    ) -> None:
        destination = Path(binding["path"])
        parent_identity = tuple(binding["parent_identity"])
        try:
            rollback_game_materialization_bundle(
                destination,
                expected_parent_identity=parent_identity,
            )
        except GameMaterializationBundleError as exc:
            if exc.reason_code != "game_materialization_bundle_rollback_committed":
                raise conflict(
                    "Materialization rollback is ambiguous",
                    reason_code=exc.reason_code,
                    recovery_evidence=exc.recovery_evidence,
                ) from exc
        try:
            identity = cls._materialization_bundle_publication_identity(binding)
        except GameMaterializationBundleError as exc:
            raise conflict(
                "Materialization rollback is ambiguous",
                reason_code=exc.reason_code,
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        if identity is None:
            return
        stored_identity = binding["published_identity"]
        if stored_identity is not None and tuple(stored_identity) != identity:
            raise conflict("Materialization rollback identity changed")
        if sys.platform.startswith("linux") and os.name == "posix":
            raise conflict(
                "Materialization rollback retains visible exact bytes under the "
                "active threat model",
                reason_code="materialization_bundle_rollback_recovery_required",
                recovery_evidence=retained_recovery_evidence(
                    stage_path=destination,
                    stage_identity=identity,
                ),
            )
        expected_content_hash = str(binding["expected_manifest_hash"])
        expected_tree_hash = str(binding["expected_tree_hash"])

        def verify_owned(path: Path) -> None:
            with verify_game_materialization_bundle(
                path,
                expected_content_hash=expected_content_hash,
            ) as verified:
                if verified.manifest.get("tree_hash") != expected_tree_hash:
                    raise conflict("Materialization rollback tree hash changed")

        try:
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise conflict("Materialization rollback parent identity changed")
                quarantine_and_remove_verified_directory(
                    destination,
                    identity,
                    verify=verify_owned,
                )
        except (AssetContractError, DirectoryPublishError) as exc:
            raise conflict("Materialization rollback could not remove the exact output") from exc

    def _recover_materialization_bundle(
        self,
        record: Mapping[str, Any],
        *,
        mode: str,
        attempt_exists: bool,
    ) -> dict[str, Any]:
        job_id = str(record["job_id"])
        binding = self.jobs.output_grants.binding_for_job(job_id, allow_visible=None)
        try:
            if mode == "resume":
                published_identity = self._materialization_bundle_publication_identity(binding)
                if published_identity is not None:
                    with self.store.connection:
                        self.jobs.output_grants.note_publication_verified(
                            job_id,
                            published_identity=published_identity,
                        )
            else:
                self._rollback_materialization_bundle_publication(binding)
        except StudioError:
            raise
        except (GameMaterializationBundleError, OSError, ValueError) as exc:
            raise conflict(
                "Materialization publication recovery is ambiguous",
                reason_code=getattr(exc, "reason_code", type(exc).__name__),
                recovery_evidence=getattr(exc, "recovery_evidence", None),
            ) from exc

        timestamp = utc_now()
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            if mode == "resume":
                grant = self.jobs.output_grants.resume_for_job(job_id)
                operation_params = copy.deepcopy(dict(record["operation_params"]))
                operation_params["target_grant_generation"] = grant["generation"]
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    operation_params=operation_params,
                    state="queued",
                    progress="queued",
                    result=None,
                    error=None,
                    started_at=None,
                    finished_at=None,
                    updated_at=timestamp,
                )
                self.jobs.snapshot_for_job(updated)
                state = "queued"
                topic = "creation_job.requeued"
            else:
                self.jobs.output_grants.rollback_for_job(job_id)
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    state="failed",
                    progress="failed",
                    error={
                        "code": "service_restart",
                        "message": "Creation job materialization was rolled back explicitly",
                        "retryable": False,
                    },
                    updated_at=timestamp,
                )
                state = "failed"
                topic = "creation_job.rolled_back"
            if attempt_exists:
                self._recover_cleanup_with_evidence(record)
                deleted = self.store.connection.execute(
                    "DELETE FROM creation_job_attempts WHERE job_id = ?", (job_id,)
                )
                if deleted.rowcount != 1:
                    raise conflict("Creation job recovery attempt changed")
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET state = ?, progress = ?, generation = ?, "
                "cancel_requested = 0, record_json = ? WHERE job_id = ? "
                "AND state = 'orphaned' AND generation = ?",
                (
                    state,
                    updated["progress"],
                    updated["generation"],
                    encode_json(updated),
                    job_id,
                    record["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job materialization recovery lost its CAS")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic=topic,
                entity_type="creation_job",
                entity_id=job_id,
                payload={"generation": updated["generation"]},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return updated
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _standalone_publication_identity(
        binding: Mapping[str, Any],
        *,
        authority_hook: Callable[[str, Mapping[str, object]], None] | None = None,
    ) -> tuple[int, int] | None:
        destination = Path(binding["path"])
        parent_identity = tuple(binding["parent_identity"])
        expected_content_hash = str(binding["expected_manifest_hash"])
        expected_tree_hash = str(binding["expected_tree_hash"])
        recovery = binding["recovery"]
        retained_identity = binding["published_identity"]
        if recovery is None:
            try:
                path_file_stat(destination)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise conflict("Standalone output cannot be inspected") from exc
            raise conflict("Unbound standalone output appeared during recovery")
        if (
            recovery["expected_manifest_hash"] != expected_content_hash
            or recovery["expected_tree_hash"] != expected_tree_hash
        ):
            raise conflict("Standalone recovery hashes changed")
        phase = recovery["phase"]
        verified_identity: tuple[int, int] | None = None
        journal_identity: tuple[int, int] | None = None
        operation_id: str | None = None
        stage_identity: tuple[int, int] | None = None
        journal_payload_sha256: str | None = None
        journal_payload_state: str | None = None
        require_journal = phase in {
            "publication_reserved",
            "publication_started",
            "publication_stage_allocated",
            "publication_staged",
        }
        reject_unbound_journal = phase == "publication_reserved"
        if phase == "publication_reserved":
            if retained_identity is not None:
                raise conflict("Standalone recovery identity appeared before verification")
        elif phase == "publication_started":
            if retained_identity is not None:
                raise conflict("Standalone recovery identity appeared before verification")
            try:
                journal_identity = tuple(recovery["journal_identity"])
                operation_id = str(recovery["operation_id"])
                journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                journal_payload_state = str(recovery["journal_payload_state"])
            except KeyError as exc:
                raise conflict("Standalone recovery journal authority is unavailable") from exc
            if journal_payload_state != "intent":
                raise conflict("Standalone recovery journal phase changed")
        elif phase in {
            "publication_stage_allocated",
            "publication_staged",
            "publication_resetting",
        }:
            if retained_identity is not None:
                raise conflict("Standalone recovery identity appeared before verification")
            try:
                journal_identity = tuple(recovery["journal_identity"])
                operation_id = str(recovery["operation_id"])
                journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                journal_payload_state = str(recovery["journal_payload_state"])
                if "stage_identity" in recovery:
                    stage_identity = tuple(recovery["stage_identity"])
            except KeyError as exc:
                raise conflict("Standalone recovery journal authority is unavailable") from exc
            if phase in {"publication_stage_allocated", "publication_staged"} and (
                stage_identity is None
            ):
                raise conflict("Standalone recovery stage authority is unavailable")
            if phase == "publication_stage_allocated" and journal_payload_state != "intent":
                raise conflict("Standalone allocated journal phase changed")
            if phase == "publication_staged" and journal_payload_state != "copying":
                raise conflict("Standalone staged journal phase changed")
        elif phase == "publication_verified":
            verified_identity = tuple(recovery["published_identity"])
            if retained_identity is None or tuple(retained_identity) != verified_identity:
                raise conflict("Standalone retained recovery identity changed")
            try:
                journal_identity = tuple(recovery["journal_identity"])
                operation_id = str(recovery["operation_id"])
                stage_identity = tuple(recovery["stage_identity"])
                journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                journal_payload_state = str(recovery["journal_payload_state"])
            except KeyError as exc:
                raise conflict("Standalone verified journal authority is unavailable") from exc
            if stage_identity != verified_identity:
                raise conflict("Standalone verified stage authority changed")
            if journal_payload_state != "ready":
                raise conflict("Standalone verified journal phase changed")
        else:
            raise conflict("Standalone recovery phase changed")
        verified = recover_standalone_game(
            destination,
            expected_parent_identity=parent_identity,
            expected_root_identity=verified_identity,
            expected_journal_identity=journal_identity,
            expected_operation_id=operation_id,
            expected_content_hash=expected_content_hash,
            expected_tree_hash=expected_tree_hash,
            expected_stage_identity=stage_identity,
            expected_journal_payload_sha256=journal_payload_sha256,
            expected_journal_payload_state=journal_payload_state,
            allow_missing_expected_journal=phase
            in {"publication_verified", "publication_resetting"},
            require_journal_for_visible=require_journal,
            require_intent_journal=phase == "publication_started",
            stage_allocated=phase == "publication_stage_allocated",
            reset_pending=phase == "publication_resetting",
            reject_unbound_journal=reject_unbound_journal,
            _authority_hook=authority_hook,
        )
        if verified is None:
            if require_journal or phase == "publication_resetting":
                return None
            raise conflict("Verified standalone output disappeared during recovery")
        with verified:
            if (
                verified.manifest.get("content_hash") != expected_content_hash
                or verified.lock.get("tree_hash") != expected_tree_hash
                or (
                    verified_identity is not None
                    and tuple(verified.root_identity) != verified_identity
                )
            ):
                raise conflict("Standalone recovery hashes changed")
            return tuple(verified.root_identity)

    @staticmethod
    def _game_package_extraction_publication_identity(
        binding: Mapping[str, Any],
        *,
        authority_hook: Callable[[str, Mapping[str, object]], None] | None = None,
    ) -> tuple[int, int] | None:
        destination = Path(binding["path"])
        parent_identity = tuple(binding["parent_identity"])
        expected_content_hash = str(binding["expected_manifest_hash"])
        expected_tree_hash = str(binding["expected_tree_hash"])
        recovery = binding["recovery"]
        retained_identity = binding["published_identity"]
        if recovery is None:
            try:
                path_file_stat(destination)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise conflict("Game package extraction output cannot be inspected") from exc
            raise conflict("Unbound game package extraction output appeared during recovery")
        if (
            recovery["expected_manifest_hash"] != expected_content_hash
            or recovery["expected_tree_hash"] != expected_tree_hash
        ):
            raise conflict("Game package extraction recovery hashes changed")
        phase = recovery["phase"]
        verified_identity: tuple[int, int] | None = None
        journal_identity: tuple[int, int] | None = None
        operation_id: str | None = None
        stage_identity: tuple[int, int] | None = None
        journal_payload_sha256: str | None = None
        journal_payload_state: str | None = None
        require_journal = phase in {
            "publication_reserved",
            "publication_started",
            "publication_stage_allocated",
            "publication_staged",
        }
        reject_unbound_journal = phase == "publication_reserved"
        if phase == "publication_reserved":
            if retained_identity is not None:
                raise conflict("Game package extraction identity appeared before verification")
        elif phase == "publication_started":
            if retained_identity is not None:
                raise conflict("Game package extraction identity appeared before verification")
            try:
                journal_identity = tuple(recovery["journal_identity"])
                operation_id = str(recovery["operation_id"])
                journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                journal_payload_state = str(recovery["journal_payload_state"])
            except KeyError as exc:
                raise conflict("Game package extraction journal authority is unavailable") from exc
            if journal_payload_state != "intent":
                raise conflict("Game package extraction journal phase changed")
        elif phase in {
            "publication_stage_allocated",
            "publication_staged",
            "publication_resetting",
        }:
            if retained_identity is not None:
                raise conflict("Game package extraction identity appeared before verification")
            try:
                journal_identity = tuple(recovery["journal_identity"])
                operation_id = str(recovery["operation_id"])
                journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                journal_payload_state = str(recovery["journal_payload_state"])
                if "stage_identity" in recovery:
                    stage_identity = tuple(recovery["stage_identity"])
            except KeyError as exc:
                raise conflict("Game package extraction journal authority is unavailable") from exc
            if phase in {"publication_stage_allocated", "publication_staged"} and (
                stage_identity is None
            ):
                raise conflict("Game package extraction stage authority is unavailable")
            if phase == "publication_stage_allocated" and journal_payload_state != "intent":
                raise conflict("Game package extraction allocated journal phase changed")
            if phase == "publication_staged" and journal_payload_state != "copying":
                raise conflict("Game package extraction staged journal phase changed")
        elif phase == "publication_verified":
            verified_identity = tuple(recovery["published_identity"])
            if retained_identity is None or tuple(retained_identity) != verified_identity:
                raise conflict("Game package extraction retained identity changed")
            try:
                journal_identity = tuple(recovery["journal_identity"])
                operation_id = str(recovery["operation_id"])
                stage_identity = tuple(recovery["stage_identity"])
                journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                journal_payload_state = str(recovery["journal_payload_state"])
            except KeyError as exc:
                raise conflict(
                    "Game package extraction verified journal authority is unavailable"
                ) from exc
            if stage_identity != verified_identity:
                raise conflict("Game package extraction verified stage authority changed")
            if journal_payload_state != "ready":
                raise conflict("Game package extraction verified journal phase changed")
        else:
            raise conflict("Game package extraction recovery phase changed")
        verified = recover_game_package_extraction(
            destination,
            expected_parent_identity=parent_identity,
            expected_root_identity=verified_identity,
            expected_journal_identity=journal_identity,
            expected_operation_id=operation_id,
            expected_content_hash=expected_content_hash,
            expected_tree_hash=expected_tree_hash,
            expected_stage_identity=stage_identity,
            expected_journal_payload_sha256=journal_payload_sha256,
            expected_journal_payload_state=journal_payload_state,
            allow_missing_expected_journal=phase
            in {"publication_verified", "publication_resetting"},
            require_journal_for_visible=require_journal,
            require_intent_journal=phase == "publication_started",
            stage_allocated=phase == "publication_stage_allocated",
            reset_pending=phase == "publication_resetting",
            reject_unbound_journal=reject_unbound_journal,
            _authority_hook=authority_hook,
        )
        if verified is None:
            if require_journal or phase == "publication_resetting":
                return None
            raise conflict("Verified game package extraction output disappeared during recovery")
        with verified:
            if (
                verified.manifest.get("content_hash") != expected_content_hash
                or verified.lock.get("tree_hash") != expected_tree_hash
                or (
                    verified_identity is not None
                    and tuple(verified.root_identity) != verified_identity
                )
            ):
                raise conflict("Game package extraction recovery hashes changed")
            return tuple(verified.root_identity)

    def _persist_standalone_authority(
        self,
        job_id: str,
        phase: str,
        evidence: Mapping[str, object],
    ) -> None:
        try:
            journal_identity = tuple(evidence["journal_identity"])
            operation_id = str(evidence["operation_id"])
        except (KeyError, TypeError) as exc:
            raise conflict("Standalone publication authority evidence is invalid") from exc
        journal_payload_sha256: str | None = None
        journal_payload_state: str | None = None
        if phase in {
            "publication_started",
            "publication_stage_allocated",
            "publication_staged",
            "publication_verified",
        }:
            try:
                journal_payload_sha256 = str(evidence["journal_payload_sha256"])
                journal_payload_state = str(evidence["journal_payload_state"])
            except (KeyError, TypeError) as exc:
                raise conflict("Standalone journal payload authority is invalid") from exc
        with self.store.connection:
            if phase == "publication_started":
                self.jobs.output_grants.note_publication_started(
                    job_id,
                    journal_identity=journal_identity,
                    operation_id=operation_id,
                    journal_payload_sha256=journal_payload_sha256,
                    journal_payload_state=journal_payload_state,
                )
                return
            if phase in {"publication_stage_allocated", "publication_staged"}:
                try:
                    stage_identity = tuple(evidence["stage_identity"])
                except (KeyError, TypeError) as exc:
                    raise conflict("Standalone publication stage is invalid") from exc
                if phase == "publication_stage_allocated":
                    self.jobs.output_grants.note_publication_stage_allocated(
                        job_id,
                        journal_identity=journal_identity,
                        operation_id=operation_id,
                        stage_identity=stage_identity,
                        journal_payload_sha256=journal_payload_sha256,
                        journal_payload_state=journal_payload_state,
                    )
                else:
                    self.jobs.output_grants.note_publication_staged(
                        job_id,
                        journal_identity=journal_identity,
                        operation_id=operation_id,
                        stage_identity=stage_identity,
                        journal_payload_sha256=journal_payload_sha256,
                        journal_payload_state=journal_payload_state,
                    )
                return
            if phase == "publication_resetting":
                self.jobs.output_grants.note_publication_resetting(
                    job_id,
                    journal_identity=journal_identity,
                    operation_id=operation_id,
                )
                return
            if phase == "publication_reset":
                self.jobs.output_grants.reset_publication_started(
                    job_id,
                    journal_identity=journal_identity,
                    operation_id=operation_id,
                )
                return
            if phase == "publication_verified":
                try:
                    published_identity = tuple(evidence["published_identity"])
                    stage_identity = tuple(evidence["stage_identity"])
                except (KeyError, TypeError) as exc:
                    raise conflict("Standalone publication identity is invalid") from exc
                self.jobs.output_grants.note_publication_verified(
                    job_id,
                    published_identity=published_identity,
                    journal_identity=journal_identity,
                    operation_id=operation_id,
                    stage_identity=stage_identity,
                    journal_payload_sha256=journal_payload_sha256,
                    journal_payload_state=journal_payload_state,
                )
                return
        raise conflict("Standalone publication authority phase is invalid")

    def _rollback_standalone_publication(
        self,
        job_id: str,
        binding: Mapping[str, Any],
    ) -> None:
        destination = Path(binding["path"])
        parent_identity = tuple(binding["parent_identity"])
        recovery = binding["recovery"]
        expected_journal_identity: tuple[int, int] | None = None
        expected_operation_id: str | None = None
        expected_stage_identity: tuple[int, int] | None = None
        expected_journal_payload_sha256: str | None = None
        expected_journal_payload_state: str | None = None
        allow_missing_expected_journal = False
        require_intent_journal = False
        stage_allocated = False
        reset_pending = False
        reject_unbound_journal = False
        if recovery is None:
            reject_unbound_journal = True
        else:
            if (
                recovery["expected_manifest_hash"] != binding["expected_manifest_hash"]
                or recovery["expected_tree_hash"] != binding["expected_tree_hash"]
            ):
                raise conflict("Standalone recovery hashes changed")
            phase = recovery["phase"]
            if phase == "publication_reserved":
                if binding["published_identity"] is not None:
                    raise conflict("Standalone recovery identity appeared before verification")
                reject_unbound_journal = True
            elif phase == "publication_started":
                if binding["published_identity"] is not None:
                    raise conflict("Standalone recovery identity appeared before verification")
                try:
                    expected_journal_identity = tuple(recovery["journal_identity"])
                    expected_operation_id = str(recovery["operation_id"])
                    expected_journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                    expected_journal_payload_state = str(recovery["journal_payload_state"])
                except KeyError as exc:
                    raise conflict("Standalone recovery journal authority is unavailable") from exc
                require_intent_journal = True
            elif phase in {
                "publication_stage_allocated",
                "publication_staged",
                "publication_resetting",
            }:
                if binding["published_identity"] is not None:
                    raise conflict("Standalone recovery identity appeared before verification")
                try:
                    expected_journal_identity = tuple(recovery["journal_identity"])
                    expected_operation_id = str(recovery["operation_id"])
                    expected_journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                    expected_journal_payload_state = str(recovery["journal_payload_state"])
                    if "stage_identity" in recovery:
                        expected_stage_identity = tuple(recovery["stage_identity"])
                except KeyError as exc:
                    raise conflict("Standalone recovery journal authority is unavailable") from exc
                if phase in {"publication_stage_allocated", "publication_staged"} and (
                    expected_stage_identity is None
                ):
                    raise conflict("Standalone recovery stage authority is unavailable")
                stage_allocated = phase == "publication_stage_allocated" or (
                    phase == "publication_resetting"
                    and expected_stage_identity is not None
                    and expected_journal_payload_state == "intent"
                )
                if phase == "publication_resetting":
                    allow_missing_expected_journal = True
                    reset_pending = True
            elif phase == "publication_verified":
                try:
                    expected_journal_identity = tuple(recovery["journal_identity"])
                    expected_operation_id = str(recovery["operation_id"])
                    recovered_identity = tuple(recovery["published_identity"])
                    expected_stage_identity = tuple(recovery["stage_identity"])
                    expected_journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                    expected_journal_payload_state = str(recovery["journal_payload_state"])
                except KeyError as exc:
                    raise conflict("Standalone verified journal authority is unavailable") from exc
                if (
                    binding["published_identity"] is None
                    or tuple(binding["published_identity"]) != recovered_identity
                ):
                    raise conflict("Standalone retained recovery identity changed")
                if expected_stage_identity != recovered_identity:
                    raise conflict("Standalone verified stage authority changed")
                allow_missing_expected_journal = True
            else:
                raise conflict("Standalone recovery phase changed")
        rollback_result: dict[str, object] | None = None
        try:
            rollback_result = rollback_standalone_game(
                destination,
                expected_parent_identity=parent_identity,
                expected_journal_identity=expected_journal_identity,
                expected_operation_id=expected_operation_id,
                expected_content_hash=str(binding["expected_manifest_hash"]),
                expected_tree_hash=str(binding["expected_tree_hash"]),
                expected_stage_identity=expected_stage_identity,
                expected_journal_payload_sha256=expected_journal_payload_sha256,
                expected_journal_payload_state=expected_journal_payload_state,
                allow_missing_expected_journal=allow_missing_expected_journal,
                require_intent_journal=require_intent_journal,
                stage_allocated=stage_allocated,
                reset_pending=reset_pending,
                reject_unbound_journal=reject_unbound_journal,
                _authority_hook=lambda phase, evidence: self._persist_standalone_authority(
                    job_id,
                    phase,
                    evidence,
                ),
            )
        except StandaloneGameError as exc:
            if exc.reason_code != "standalone_game_rollback_committed":
                raise conflict(
                    "Standalone rollback is ambiguous",
                    reason_code=exc.reason_code,
                    recovery_evidence=exc.recovery_evidence,
                ) from exc
        if rollback_result is not None and rollback_result["status"] == "rolled_back":
            return
        try:
            identity = self._standalone_publication_identity(
                binding,
                authority_hook=lambda phase, evidence: self._persist_standalone_authority(
                    job_id,
                    phase,
                    evidence,
                ),
            )
        except StandaloneGameError as exc:
            raise conflict(
                "Standalone rollback is ambiguous",
                reason_code=exc.reason_code,
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        if identity is None:
            return
        stored_identity = binding["published_identity"]
        if stored_identity is not None and tuple(stored_identity) != identity:
            raise conflict("Standalone rollback identity changed")
        if sys.platform.startswith("linux") and os.name == "posix":
            raise conflict(
                "Standalone rollback retains visible exact bytes under the active threat model",
                reason_code="standalone_game_rollback_recovery_required",
                recovery_evidence=retained_recovery_evidence(
                    stage_path=destination,
                    stage_identity=identity,
                ),
            )
        expected_content_hash = str(binding["expected_manifest_hash"])
        expected_tree_hash = str(binding["expected_tree_hash"])

        def verify_owned(path: Path) -> None:
            with verify_standalone_game(
                path,
                expected_content_hash=expected_content_hash,
            ) as verified:
                if verified.lock.get("tree_hash") != expected_tree_hash:
                    raise conflict("Standalone rollback tree hash changed")

        try:
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise conflict("Standalone rollback parent identity changed")
                quarantine_and_remove_verified_directory(
                    destination,
                    identity,
                    verify=verify_owned,
                )
        except (
            AssetContractError,
            DirectoryPublishError,
            DirectoryPublishIndeterminateError,
            DirectoryPublishRecoveryRequiredError,
        ) as exc:
            raise conflict(
                "Standalone rollback could not remove the exact output",
                reason_code="standalone_game_rollback_recovery_required",
                recovery_evidence=retained_recovery_evidence(
                    stage_path=destination,
                    stage_identity=identity,
                ),
            ) from exc

    def _rollback_game_package_extraction_publication(
        self,
        job_id: str,
        binding: Mapping[str, Any],
    ) -> None:
        destination = Path(binding["path"])
        parent_identity = tuple(binding["parent_identity"])
        recovery = binding["recovery"]
        expected_journal_identity: tuple[int, int] | None = None
        expected_operation_id: str | None = None
        expected_stage_identity: tuple[int, int] | None = None
        expected_journal_payload_sha256: str | None = None
        expected_journal_payload_state: str | None = None
        allow_missing_expected_journal = False
        require_intent_journal = False
        stage_allocated = False
        reset_pending = False
        reject_unbound_journal = False
        if recovery is None:
            reject_unbound_journal = True
        else:
            if (
                recovery["expected_manifest_hash"] != binding["expected_manifest_hash"]
                or recovery["expected_tree_hash"] != binding["expected_tree_hash"]
            ):
                raise conflict("Game package extraction recovery hashes changed")
            phase = recovery["phase"]
            if phase == "publication_reserved":
                if binding["published_identity"] is not None:
                    raise conflict("Game package extraction identity appeared before verification")
                reject_unbound_journal = True
            elif phase == "publication_started":
                if binding["published_identity"] is not None:
                    raise conflict("Game package extraction identity appeared before verification")
                try:
                    expected_journal_identity = tuple(recovery["journal_identity"])
                    expected_operation_id = str(recovery["operation_id"])
                    expected_journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                    expected_journal_payload_state = str(recovery["journal_payload_state"])
                except KeyError as exc:
                    raise conflict(
                        "Game package extraction journal authority is unavailable"
                    ) from exc
                require_intent_journal = True
            elif phase in {
                "publication_stage_allocated",
                "publication_staged",
                "publication_resetting",
            }:
                if binding["published_identity"] is not None:
                    raise conflict("Game package extraction identity appeared before verification")
                try:
                    expected_journal_identity = tuple(recovery["journal_identity"])
                    expected_operation_id = str(recovery["operation_id"])
                    expected_journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                    expected_journal_payload_state = str(recovery["journal_payload_state"])
                    if "stage_identity" in recovery:
                        expected_stage_identity = tuple(recovery["stage_identity"])
                except KeyError as exc:
                    raise conflict(
                        "Game package extraction journal authority is unavailable"
                    ) from exc
                if phase in {"publication_stage_allocated", "publication_staged"} and (
                    expected_stage_identity is None
                ):
                    raise conflict("Game package extraction stage authority is unavailable")
                stage_allocated = phase == "publication_stage_allocated" or (
                    phase == "publication_resetting"
                    and expected_stage_identity is not None
                    and expected_journal_payload_state == "intent"
                )
                if phase == "publication_resetting":
                    allow_missing_expected_journal = True
                    reset_pending = True
            elif phase == "publication_verified":
                try:
                    expected_journal_identity = tuple(recovery["journal_identity"])
                    expected_operation_id = str(recovery["operation_id"])
                    recovered_identity = tuple(recovery["published_identity"])
                    expected_stage_identity = tuple(recovery["stage_identity"])
                    expected_journal_payload_sha256 = str(recovery["journal_payload_sha256"])
                    expected_journal_payload_state = str(recovery["journal_payload_state"])
                except KeyError as exc:
                    raise conflict(
                        "Game package extraction verified journal authority is unavailable"
                    ) from exc
                if (
                    binding["published_identity"] is None
                    or tuple(binding["published_identity"]) != recovered_identity
                ):
                    raise conflict("Game package extraction retained identity changed")
                if expected_stage_identity != recovered_identity:
                    raise conflict("Game package extraction verified stage authority changed")
                allow_missing_expected_journal = True
            else:
                raise conflict("Game package extraction recovery phase changed")
        rollback_result: dict[str, object] | None = None
        try:
            rollback_result = rollback_game_package_extraction(
                destination,
                expected_parent_identity=parent_identity,
                expected_journal_identity=expected_journal_identity,
                expected_operation_id=expected_operation_id,
                expected_content_hash=str(binding["expected_manifest_hash"]),
                expected_tree_hash=str(binding["expected_tree_hash"]),
                expected_stage_identity=expected_stage_identity,
                expected_journal_payload_sha256=expected_journal_payload_sha256,
                expected_journal_payload_state=expected_journal_payload_state,
                allow_missing_expected_journal=allow_missing_expected_journal,
                require_intent_journal=require_intent_journal,
                stage_allocated=stage_allocated,
                reset_pending=reset_pending,
                reject_unbound_journal=reject_unbound_journal,
                _authority_hook=lambda phase, evidence: self._persist_standalone_authority(
                    job_id,
                    phase,
                    evidence,
                ),
            )
        except WorldForgeGamePackageError as exc:
            if exc.reason_code != "game_package_rollback_committed":
                raise conflict(
                    "Game package extraction rollback is ambiguous",
                    reason_code=exc.reason_code,
                    recovery_evidence=exc.recovery_evidence,
                ) from exc
        if rollback_result is not None and rollback_result["status"] == "rolled_back":
            return
        try:
            identity = self._game_package_extraction_publication_identity(
                binding,
                authority_hook=lambda phase, evidence: self._persist_standalone_authority(
                    job_id,
                    phase,
                    evidence,
                ),
            )
        except WorldForgeGamePackageError as exc:
            raise conflict(
                "Game package extraction rollback is ambiguous",
                reason_code=exc.reason_code,
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        if identity is None:
            return
        stored_identity = binding["published_identity"]
        if stored_identity is not None and tuple(stored_identity) != identity:
            raise conflict("Game package extraction rollback identity changed")
        if sys.platform.startswith("linux") and os.name == "posix":
            raise conflict(
                "Game package extraction rollback retains visible exact bytes",
                reason_code="game_package_rollback_recovery_required",
                recovery_evidence=retained_recovery_evidence(
                    stage_path=destination,
                    stage_identity=identity,
                ),
            )
        expected_content_hash = str(binding["expected_manifest_hash"])
        expected_tree_hash = str(binding["expected_tree_hash"])

        def verify_owned(path: Path) -> None:
            with verify_standalone_game(
                path,
                expected_content_hash=expected_content_hash,
            ) as verified:
                if verified.lock.get("tree_hash") != expected_tree_hash:
                    raise conflict("Game package extraction rollback tree hash changed")

        try:
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise conflict("Game package extraction rollback parent changed")
                quarantine_and_remove_verified_directory(
                    destination,
                    identity,
                    verify=verify_owned,
                )
        except (
            AssetContractError,
            DirectoryPublishError,
            DirectoryPublishIndeterminateError,
            DirectoryPublishRecoveryRequiredError,
        ) as exc:
            raise conflict(
                "Game package extraction rollback could not remove the exact output",
                reason_code="game_package_rollback_recovery_required",
                recovery_evidence=retained_recovery_evidence(
                    stage_path=destination,
                    stage_identity=identity,
                ),
            ) from exc

    def _recover_game_materialize(
        self,
        record: Mapping[str, Any],
        *,
        mode: str,
        attempt_exists: bool,
    ) -> dict[str, Any]:
        job_id = str(record["job_id"])
        binding = self.jobs.output_grants.binding_for_job(job_id, allow_visible=None)
        try:
            if mode == "resume":
                published_identity = self._standalone_publication_identity(
                    binding,
                    authority_hook=lambda phase, evidence: self._persist_standalone_authority(
                        job_id,
                        phase,
                        evidence,
                    ),
                )
                if published_identity is not None:
                    verified_binding = self.jobs.output_grants.binding_for_job(
                        job_id,
                        allow_visible=True,
                    )
                    retained = verified_binding["recovery"]
                    if retained is None or retained["phase"] != "publication_verified":
                        raise conflict("Standalone verified recovery authority disappeared")
                    with self.store.connection:
                        self.jobs.output_grants.note_publication_verified(
                            job_id,
                            published_identity=published_identity,
                            journal_identity=tuple(retained["journal_identity"]),
                            operation_id=str(retained["operation_id"]),
                            stage_identity=tuple(retained["stage_identity"]),
                            journal_payload_sha256=str(retained["journal_payload_sha256"]),
                            journal_payload_state=str(retained["journal_payload_state"]),
                        )
            else:
                self._rollback_standalone_publication(job_id, binding)
        except StudioError:
            raise
        except (StandaloneGameError, OSError, ValueError) as exc:
            raise conflict(
                "Standalone publication recovery is ambiguous",
                reason_code=getattr(exc, "reason_code", type(exc).__name__),
                recovery_evidence=getattr(exc, "recovery_evidence", None),
            ) from exc

        timestamp = utc_now()
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            if mode == "resume":
                grant = self.jobs.output_grants.resume_for_job(job_id)
                operation_params = copy.deepcopy(dict(record["operation_params"]))
                operation_params["target_grant_generation"] = grant["generation"]
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    operation_params=operation_params,
                    state="queued",
                    progress="queued",
                    result=None,
                    error=None,
                    started_at=None,
                    finished_at=None,
                    updated_at=timestamp,
                )
                self.jobs.snapshot_for_job(updated)
                state = "queued"
                topic = "creation_job.requeued"
            else:
                self.jobs.output_grants.rollback_for_job(job_id)
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    state="failed",
                    progress="failed",
                    error={
                        "code": "service_restart",
                        "message": "Creation job standalone publication was rolled back explicitly",
                        "retryable": False,
                    },
                    updated_at=timestamp,
                )
                state = "failed"
                topic = "creation_job.rolled_back"
            if attempt_exists:
                self._recover_cleanup_with_evidence(record)
                deleted = self.store.connection.execute(
                    "DELETE FROM creation_job_attempts WHERE job_id = ?", (job_id,)
                )
                if deleted.rowcount != 1:
                    raise conflict("Creation job recovery attempt changed")
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET state = ?, progress = ?, generation = ?, "
                "cancel_requested = 0, record_json = ? WHERE job_id = ? "
                "AND state = 'orphaned' AND generation = ?",
                (
                    state,
                    updated["progress"],
                    updated["generation"],
                    encode_json(updated),
                    job_id,
                    record["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job standalone recovery lost its CAS")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic=topic,
                entity_type="creation_job",
                entity_id=job_id,
                payload={"generation": updated["generation"]},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return updated
        except BaseException:
            self.store.connection.rollback()
            raise

    def _recover_game_package(
        self,
        record: Mapping[str, Any],
        *,
        mode: str,
        attempt_exists: bool,
    ) -> dict[str, Any]:
        job_id = str(record["job_id"])
        binding = self.jobs.output_grants.binding_for_job(job_id, allow_visible=None)
        recovery = binding["recovery"]
        if recovery is None or recovery["phase"] not in {
            "file_publication_reserved",
            "file_publication_started",
            "file_publication_verified",
        }:
            raise conflict("Game package recovery authority is unavailable")
        try:
            info = path_file_stat(binding["path"])
        except FileNotFoundError:
            visible = False
        except OSError as exc:
            raise conflict("Game package recovery target cannot be inspected") from exc
        else:
            visible = True
            visible_identity = (int(info.st_dev), int(info.st_ino))
        if recovery["phase"] == "file_publication_verified":
            retained_identity = binding["published_identity"]
            if (
                retained_identity is None
                or tuple(recovery["published_identity"]) != tuple(retained_identity)
                or not visible
                or visible_identity != tuple(retained_identity)
            ):
                raise conflict("Game package verified recovery identity changed")
            verified = verify_game_package(
                binding["path"],
                expected_file_identity=tuple(retained_identity),
            )
            try:
                if (
                    verified.manifest["content_hash"] != binding["expected_manifest_hash"]
                    or verified.archive_sha256 != binding["expected_archive_sha256"]
                    or len(verified.archive_bytes) != binding["expected_size_bytes"]
                ):
                    raise conflict("Game package verified recovery bytes changed")
            finally:
                verified.close()
        elif visible:
            raise conflict(
                "Game package visible output lacks a retained publication identity",
                reason_code="game_package_publication_recovery_required",
            )

        timestamp = utc_now()
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            if mode == "resume":
                grant = self.jobs.output_grants.resume_for_job(job_id)
                operation_params = copy.deepcopy(dict(record["operation_params"]))
                operation_params["target_grant_generation"] = grant["generation"]
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    operation_params=operation_params,
                    state="queued",
                    progress="queued",
                    result=None,
                    error=None,
                    started_at=None,
                    finished_at=None,
                    updated_at=timestamp,
                )
                self.jobs.snapshot_for_job(updated)
                state = "queued"
                topic = "creation_job.requeued"
            else:
                if visible:
                    raise conflict(
                        "Game package rollback retains visible bytes fail-closed",
                        reason_code="game_package_rollback_recovery_required",
                    )
                self.jobs.output_grants.rollback_for_job(job_id)
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    state="failed",
                    progress="failed",
                    error={
                        "code": "service_restart",
                        "message": "Creation job game package publication was rolled back",
                        "retryable": False,
                    },
                    updated_at=timestamp,
                )
                state = "failed"
                topic = "creation_job.rolled_back"
            if attempt_exists:
                self._recover_cleanup_with_evidence(record)
                deleted = self.store.connection.execute(
                    "DELETE FROM creation_job_attempts WHERE job_id = ?", (job_id,)
                )
                if deleted.rowcount != 1:
                    raise conflict("Creation job recovery attempt changed")
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET state = ?, progress = ?, generation = ?, "
                "cancel_requested = 0, record_json = ? WHERE job_id = ? "
                "AND state = 'orphaned' AND generation = ?",
                (
                    state,
                    updated["progress"],
                    updated["generation"],
                    encode_json(updated),
                    job_id,
                    record["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job game package recovery lost its CAS")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic=topic,
                entity_type="creation_job",
                entity_id=job_id,
                payload={"generation": updated["generation"]},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return updated
        except BaseException:
            self.store.connection.rollback()
            raise

    def _recover_game_package_extract(
        self,
        record: Mapping[str, Any],
        *,
        mode: str,
        attempt_exists: bool,
    ) -> dict[str, Any]:
        job_id = str(record["job_id"])
        binding = self.jobs.output_grants.binding_for_job(job_id, allow_visible=None)
        try:
            if mode == "resume":
                published_identity = self._game_package_extraction_publication_identity(
                    binding,
                    authority_hook=lambda phase, evidence: self._persist_standalone_authority(
                        job_id,
                        phase,
                        evidence,
                    ),
                )
                if published_identity is not None:
                    verified_binding = self.jobs.output_grants.binding_for_job(
                        job_id,
                        allow_visible=True,
                    )
                    retained = verified_binding["recovery"]
                    if retained is None or retained["phase"] != "publication_verified":
                        raise conflict("Game package extraction verified authority disappeared")
                    with self.store.connection:
                        self.jobs.output_grants.note_publication_verified(
                            job_id,
                            published_identity=published_identity,
                            journal_identity=tuple(retained["journal_identity"]),
                            operation_id=str(retained["operation_id"]),
                            stage_identity=tuple(retained["stage_identity"]),
                            journal_payload_sha256=str(retained["journal_payload_sha256"]),
                            journal_payload_state=str(retained["journal_payload_state"]),
                        )
            else:
                self._rollback_game_package_extraction_publication(job_id, binding)
        except StudioError:
            raise
        except (WorldForgeGamePackageError, OSError, ValueError) as exc:
            raise conflict(
                "Game package extraction publication recovery is ambiguous",
                reason_code=getattr(exc, "reason_code", type(exc).__name__),
                recovery_evidence=getattr(exc, "recovery_evidence", None),
            ) from exc

        timestamp = utc_now()
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            if mode == "resume":
                grant = self.jobs.output_grants.resume_for_job(job_id)
                operation_params = copy.deepcopy(dict(record["operation_params"]))
                operation_params["target_grant_generation"] = grant["generation"]
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    operation_params=operation_params,
                    state="queued",
                    progress="queued",
                    result=None,
                    error=None,
                    started_at=None,
                    finished_at=None,
                    updated_at=timestamp,
                )
                self.jobs.snapshot_for_job(updated)
                state = "queued"
                topic = "creation_job.requeued"
            else:
                self.jobs.output_grants.rollback_for_job(job_id)
                updated = self.jobs._updated_record(  # noqa: SLF001
                    record,
                    state="failed",
                    progress="failed",
                    error={
                        "code": "service_restart",
                        "message": (
                            "Creation job game package extraction publication was "
                            "rolled back explicitly"
                        ),
                        "retryable": False,
                    },
                    updated_at=timestamp,
                )
                state = "failed"
                topic = "creation_job.rolled_back"
            if attempt_exists:
                self._recover_cleanup_with_evidence(record)
                deleted = self.store.connection.execute(
                    "DELETE FROM creation_job_attempts WHERE job_id = ?",
                    (job_id,),
                )
                if deleted.rowcount != 1:
                    raise conflict("Creation job recovery attempt changed")
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET state = ?, progress = ?, generation = ?, "
                "cancel_requested = 0, record_json = ? WHERE job_id = ? "
                "AND state = 'orphaned' AND generation = ?",
                (
                    state,
                    updated["progress"],
                    updated["generation"],
                    encode_json(updated),
                    job_id,
                    record["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job game package extraction recovery lost its CAS")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic=topic,
                entity_type="creation_job",
                entity_id=job_id,
                payload={"generation": updated["generation"]},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return updated
        except BaseException:
            self.store.connection.rollback()
            raise

    def _attempt_recovery_evidence(self, job_id: str) -> dict[str, object]:
        row = self.store.connection.execute(
            "SELECT stage_locator, stage_dev, stage_ino, journal_name, journal_dev, journal_ino "
            "FROM creation_job_attempts WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return {}

        def exact_named_entry(path: Path, identity: tuple[int, int] | None) -> bool:
            if identity is None:
                return True
            try:
                info = path_file_stat(path)
            except OSError:
                return False
            return (int(info.st_dev), int(info.st_ino)) == identity

        stage = self.store.creation_jobs_dir / row["stage_locator"]
        journal = self.store.creation_job_journals_dir / row["journal_name"]
        stage_identity = (
            None
            if row["stage_dev"] is None or row["stage_ino"] is None
            else (int(row["stage_dev"]), int(row["stage_ino"]))
        )
        journal_identity = (
            None
            if row["journal_dev"] is None or row["journal_ino"] is None
            else (int(row["journal_dev"]), int(row["journal_ino"]))
        )
        if sys.platform.startswith("linux") and os.name == "posix":
            if stage_identity is not None and not exact_named_entry(stage, stage_identity):
                stage = self._retained_stage_evidence_path(stage, stage_identity)
            if journal_identity is not None and not exact_named_entry(journal, journal_identity):
                journal = retained_journal_evidence_path(journal, journal_identity)
        return retained_recovery_evidence(
            stage_path=stage,
            stage_identity=stage_identity,
            journal_path=journal,
            journal_identity=journal_identity,
        )

    def _recover_cleanup_with_evidence(
        self,
        job: Mapping[str, Any],
        *,
        allow_requeue_retirement: bool = False,
    ) -> None:
        try:
            self._recover_cleanup(
                job,
                allow_requeue_retirement=allow_requeue_retirement,
            )
        except StudioError as exc:
            details = dict(exc.details)
            evidence = self._attempt_recovery_evidence(str(job["job_id"]))
            if evidence:
                details.setdefault("recovery_evidence", evidence)
            raise StudioError(exc.code, exc.message, details=details) from exc
        except Exception as exc:
            details: dict[str, object] = {"reason_code": "cleanup_recovery_failed"}
            evidence = self._attempt_recovery_evidence(str(job["job_id"]))
            if evidence:
                details["recovery_evidence"] = evidence
            raise conflict(
                "Creation job cleanup requires explicit recovery",
                **details,
            ) from exc

    def _recover_cleanup(
        self,
        job: Mapping[str, Any],
        *,
        allow_requeue_retirement: bool = False,
    ) -> None:
        row = self.store.connection.execute(
            "SELECT * FROM creation_job_attempts WHERE job_id = ?", (job["job_id"],)
        ).fetchone()
        if row is None:
            raise conflict("Creation job recovery attempt is unavailable")
        if row["phase"] == "reserving":
            self._recover_reserving_attempt(row)
            return
        if (row["binary_output_dev"] is None) != (row["binary_output_ino"] is None):
            raise conflict("Creation job binary output identity is incomplete")
        binary_identity = (
            None
            if row["binary_output_dev"] is None
            else (int(row["binary_output_dev"]), int(row["binary_output_ino"]))
        )
        if job["operation"] != "game.package" and binary_identity is not None:
            raise conflict("Creation job has an unexpected binary output identity")
        if (
            job["operation"] == "game.package"
            and _JOURNAL_PHASES.index(str(row["phase"]))
            >= _JOURNAL_PHASES.index("output_published")
            and binary_identity is None
        ):
            raise conflict("Game package binary output identity is unavailable")
        if (
            row["journal_dev"] is None
            or row["journal_ino"] is None
            or row["stage_dev"] is None
            or row["stage_ino"] is None
        ):
            raise conflict("Creation job recovery attempt identity is unavailable")
        journal_path = self.store.creation_job_journals_dir / row["journal_name"]
        stage = self.store.creation_jobs_dir / row["stage_locator"]
        journal_identity = (int(row["journal_dev"]), int(row["journal_ino"]))
        stage_identity = (int(row["stage_dev"]), int(row["stage_ino"]))
        completed_attempt = (
            job["state"] == "succeeded"
            and job["progress"] == "cleanup_pending"
            and row["phase"] == "cleanup_pending"
        )
        requeue_retirement = (
            allow_requeue_retirement
            and job["operation"] == "runtime.headless.verify"
            and job["state"] == "orphaned"
            and row["phase"] == "registry_committing"
        )
        if allow_requeue_retirement and not requeue_retirement:
            raise conflict("Runtime headless requeue recovery attempt changed")
        try:
            loaded = read_append_only_journal_history_state(
                journal_path,
                max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
            )
        except Exception as exc:
            raise conflict("Creation job recovery journal is invalid") from exc
        if loaded is None:
            if not completed_attempt and not requeue_retirement:
                raise conflict("Creation job recovery journal is unavailable")
            try:
                path_file_stat(stage)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise conflict("Creation job recovery stage cannot be inspected") from exc
            else:
                raise conflict("Creation job active stage reappeared after journal retirement")
            if os.name == "nt":
                return
            if not (sys.platform.startswith("linux") and os.name == "posix"):
                raise conflict("Creation job retained cleanup evidence is unsupported")
            retained_journal = retained_journal_evidence_path(
                journal_path,
                journal_identity,
            )
            try:
                retained_loaded = read_append_only_journal_history_state(
                    retained_journal,
                    max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                    max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
                )
            except Exception as exc:
                raise conflict("Creation job retained journal evidence is invalid") from exc
            if retained_loaded is None:
                raise conflict("Creation job retained journal evidence is unavailable")
            history, retained_identity, partial_tail = retained_loaded
            if retained_identity != journal_identity or partial_tail:
                raise conflict("Creation job retained journal evidence changed")
            documents = self._validated_recovery_history(job, row, history)
            last = documents[-1]
            expected_retained_phase = (
                "registry_committing" if requeue_retirement else "cleanup_pending"
            )
            if last["phase"] != expected_retained_phase:
                raise conflict("Creation job retained journal phase changed")
            retained_stage = self._retained_stage_evidence_path(stage, stage_identity)
            try:
                path_file_stat(retained_stage)
            except FileNotFoundError as exc:
                raise conflict("Creation job retained stage evidence is unavailable") from exc
            except OSError as exc:
                raise conflict("Creation job retained stage evidence cannot be inspected") from exc
            outputs = self._recovery_outputs(
                retained_stage,
                job,
                last,
                allow_missing=False,
            )
            if completed_attempt:
                self.jobs.artifacts.validate_cleanup_outputs(job, outputs)
            self._verify_cleanup_stage(
                retained_stage,
                stage_identity,
                row["request_locator"],
                row["request_sha256"],
                outputs,
                allow_missing=False,
                binary_identity=binary_identity,
            )
            return
        history, active_journal_identity, partial_tail = loaded
        if active_journal_identity != journal_identity:
            raise conflict("Creation job recovery journal identity changed")
        if partial_tail:
            self._validated_recovery_history(job, row, history)
            try:
                truncate_append_only_journal_partial_tail(
                    journal_path,
                    expected_identity=active_journal_identity,
                    expected_payload=history[-1],
                    expected_history=history,
                    max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                    max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
                )
            except Exception as exc:
                raise conflict("Creation job recovery journal tail cannot be repaired") from exc
            loaded = read_append_only_journal_history_state(
                journal_path,
                max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
            )
            if loaded is None:
                raise conflict("Creation job recovery journal disappeared during repair")
            history, repaired_identity, repaired_tail = loaded
            if repaired_identity != active_journal_identity or repaired_tail:
                raise conflict("Creation job recovery journal repair diverged")
        documents = self._validated_recovery_history(job, row, history)
        last = documents[-1]
        if row["phase"] == "cleanup_pending":
            if last["phase"] not in {
                "registry_committing",
                "committed",
                "cleanup_pending",
            }:
                raise conflict("Creation job cleanup journal phase diverged")
        elif last["phase"] != row["phase"]:
            row_phase_index = _JOURNAL_PHASES.index(row["phase"])
            journal_phase_index = _JOURNAL_PHASES.index(last["phase"])
            if journal_phase_index != row_phase_index + 1:
                raise conflict("Creation job recovery phase diverged")
        try:
            path_file_stat(stage)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError):
                stage_exists = False
            else:
                raise conflict("Creation job recovery stage cannot be inspected") from exc
        else:
            stage_exists = True
        cleanup_root = stage
        retained_terminal = False
        if (
            not stage_exists
            and (completed_attempt or requeue_retirement)
            and sys.platform.startswith("linux")
            and os.name == "posix"
        ):
            cleanup_root = self._retained_stage_evidence_path(stage, stage_identity)
            try:
                path_file_stat(cleanup_root)
                retained_terminal = True
            except FileNotFoundError:
                retained_terminal = False
            except OSError as exc:
                raise conflict("Creation job retained stage evidence cannot be inspected") from exc
            if not retained_terminal:
                raise conflict("Creation job retained stage evidence is unavailable")
        if stage_exists or retained_terminal:
            outputs = self._recovery_outputs(
                cleanup_root,
                job,
                last,
                allow_missing=not completed_attempt and not requeue_retirement,
            )
            if completed_attempt:
                self.jobs.artifacts.validate_cleanup_outputs(job, outputs)
            if completed_attempt and last["phase"] != "cleanup_pending":
                if retained_terminal:
                    raise conflict("Creation job retained stage preceded terminal journal state")
                for phase in _JOURNAL_PHASES[_JOURNAL_PHASES.index(last["phase"]) + 1 :]:
                    if phase not in {"committed", "cleanup_pending"}:
                        raise conflict("Creation job cleanup journal cannot be completed safely")
                    updated_payload = _journal_payload(
                        job=job,
                        phase=phase,
                        stage_locator=stage.name,
                        stage_identity=stage_identity,
                        request_locator=row["request_locator"],
                        request_sha256=row["request_sha256"],
                        outputs=outputs,
                    )
                    try:
                        append_append_only_journal(
                            journal_path,
                            expected_identity=journal_identity,
                            expected_payload=history[-1],
                            expected_history=history,
                            updated_payload=updated_payload,
                            max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                            max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
                        )
                    except Exception as exc:
                        raise conflict(
                            "Creation job cleanup journal cannot reach terminal state"
                        ) from exc
                    history = (*history, updated_payload)
                documents = self._validated_recovery_history(job, row, history)
                last = documents[-1]
            completed_cleanup = completed_attempt and last["phase"] == "cleanup_pending"
            if retained_terminal:
                self._verify_cleanup_stage(
                    cleanup_root,
                    stage_identity,
                    row["request_locator"],
                    row["request_sha256"],
                    outputs,
                    allow_missing=False,
                    binary_identity=binary_identity,
                )
            else:
                self._cleanup_stage(
                    stage,
                    stage_identity,
                    row["request_locator"],
                    row["request_sha256"],
                    outputs,
                    allow_missing=not completed_cleanup and not requeue_retirement,
                    allow_retained_terminal=completed_cleanup or requeue_retirement,
                    binary_identity=binary_identity,
                )
        elif not completed_attempt and not requeue_retirement:
            raise conflict("Creation job recovery stage is unavailable")
        elif not (os.name == "nt" and last["phase"] == "cleanup_pending"):
            raise conflict("Creation job recovery stage is unavailable")
        if (
            sys.platform.startswith("linux")
            and os.name == "posix"
            and not requeue_retirement
            and (not completed_attempt or last["phase"] != "cleanup_pending")
        ):
            raise conflict(
                "Creation job recovery_required: the exact partial journal was retained "
                "without pathname deletion",
                recovery_evidence=retained_recovery_evidence(
                    stage_path=stage,
                    stage_identity=stage_identity,
                    journal_path=journal_path,
                    journal_identity=journal_identity,
                ),
            )
        remove_append_only_journal(
            journal_path,
            expected_identity=journal_identity,
            expected_payload=history[-1],
            expected_history=history,
            max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
            max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
        )

    def _recovery_outputs(
        self,
        root: Path,
        job: Mapping[str, Any],
        last: Mapping[str, Any],
        *,
        allow_missing: bool,
    ) -> list[VerifiedCreationOutput]:
        outputs: list[VerifiedCreationOutput] = []
        for raw in last["outputs"]:
            try:
                payload, identity = _read_bound_file(
                    root / f"{raw['locator']}.json",
                    limit=int(raw["size"]),
                )
            except FileNotFoundError as exc:
                if not allow_missing:
                    raise conflict("Creation job recovery output is unavailable") from exc
                outputs.append(
                    VerifiedCreationOutput(
                        locator=raw["locator"],
                        subject=copy.deepcopy(raw["subject"]),
                        payload=b"",
                        size=int(raw["size"]),
                        sha256=raw["sha256"],
                        file_identity=tuple(raw["file_identity"]),
                    )
                )
                continue
            if (
                identity != tuple(raw["file_identity"])
                or len(payload) != raw["size"]
                or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), raw["sha256"])
            ):
                raise conflict("Creation job recovery output identity changed")
            outputs.append(
                VerifiedCreationOutput(
                    locator=raw["locator"],
                    subject=copy.deepcopy(raw["subject"]),
                    payload=payload,
                    size=len(payload),
                    sha256=raw["sha256"],
                    file_identity=identity,
                )
            )
        if not last["outputs"] and last["phase"] == "worker_started":
            outputs.extend(self._unjournaled_outputs_for_cleanup(root, job["operation"]))
        return outputs

    def _recover_reserving_attempt(self, row: sqlite3.Row) -> None:
        if (
            isinstance(row["generation"], bool)
            or not isinstance(row["generation"], int)
            or row["generation"] != 0
        ):
            raise conflict("Creation job recovery attempt generation is invalid")
        if row["worker_pid"] is not None or row["worker_identity_json"] is not None:
            raise conflict("Creation job reserving attempt has an invalid worker binding")
        stage = self.store.creation_jobs_dir / row["stage_locator"]
        try:
            stage_info = path_file_stat(stage)
        except FileNotFoundError:
            stage_info = None
        except OSError as exc:
            raise conflict("Creation job reserved stage cannot be inspected") from exc
        if stage_info is not None:
            if not stat.S_ISDIR(stage_info.st_mode):
                raise conflict("Creation job reserved stage is unsafe")
            stage_identity = (int(stage_info.st_dev), int(stage_info.st_ino))
            stored_identity = (
                None
                if row["stage_dev"] is None or row["stage_ino"] is None
                else (int(row["stage_dev"]), int(row["stage_ino"]))
            )
            if stored_identity is None:
                if any(stage.iterdir()):
                    raise conflict("Creation job unbound reserved stage is not empty")
                self._cleanup_empty_stage(
                    stage,
                    stage_identity,
                    allow_retained_terminal=False,
                )
            else:
                if stage_identity != stored_identity:
                    raise conflict("Creation job reserved stage identity changed")
                request_path = stage / f"{row['request_locator']}.json"
                try:
                    request_payload, _request_identity = _read_bound_file(
                        request_path,
                        limit=64 * 1024 * 1024,
                    )
                except FileNotFoundError:
                    request_digest = row["request_sha256"]
                else:
                    request_digest = hashlib.sha256(request_payload).hexdigest()
                self._cleanup_stage(
                    stage,
                    stored_identity,
                    row["request_locator"],
                    request_digest,
                    (),
                    allow_missing=True,
                    allow_retained_terminal=False,
                )

        journal_path = self.store.creation_job_journals_dir / row["journal_name"]
        try:
            journal_payload, journal_identity = _read_bound_file(
                journal_path,
                limit=_MAX_JOURNAL_FILE_BYTES,
            )
        except FileNotFoundError:
            return
        stored_journal_identity = (
            None
            if row["journal_dev"] is None or row["journal_ino"] is None
            else (int(row["journal_dev"]), int(row["journal_ino"]))
        )
        if stored_journal_identity is not None and journal_identity != stored_journal_identity:
            raise conflict("Creation job reserved journal identity changed")
        confirmed_payload, confirmed_identity = _read_bound_file(
            journal_path,
            limit=_MAX_JOURNAL_FILE_BYTES,
        )
        confirmed = path_file_stat(journal_path)
        if (
            not stat.S_ISREG(confirmed.st_mode)
            or confirmed.st_nlink != 1
            or (int(confirmed.st_dev), int(confirmed.st_ino)) != journal_identity
            or confirmed_identity != journal_identity
            or confirmed_payload != journal_payload
        ):
            raise conflict("Creation job reserved journal changed during cleanup")
        if sys.platform.startswith("linux") and os.name == "posix":
            raise conflict(
                "Creation job recovery_required: the exact reserving journal was "
                "retained without pathname deletion"
            )
        try:
            remove_append_only_journal(
                journal_path,
                expected_identity=journal_identity,
                expected_payload=journal_payload,
                max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
            )
        except DirectoryPublishError as exc:
            raise conflict("Creation job reserving journal cleanup failed") from exc

    @staticmethod
    def _unjournaled_outputs_for_cleanup(
        stage: Path,
        operation: object,
    ) -> tuple[VerifiedCreationOutput, ...]:
        count = (
            4
            if operation == "runtime.compose"
            else 3
            if operation
            in {
                "creation.compile",
                "asset.process",
                "asset.release.authorize",
                "runtime.headless.verify",
            }
            else 2
            if operation == "asset.release.seal"
            else 1
            if operation
            in {
                "artifact.admit",
                "runtime.bundle.build",
                "game.materialization.bundle.build",
                "game.materialize",
                "game.package",
                "game.package.extract",
                "asset.qa.review",
            }
            else 0
        )
        if count == 0:
            raise conflict("Creation job recovery operation is invalid")
        outputs: list[VerifiedCreationOutput] = []
        for index in range(1, count + 1):
            locator = f"output_{index:04d}"
            path = stage / f"{locator}.json"
            try:
                payload, identity = _read_bound_file(path, limit=64 * 1024 * 1024)
            except FileNotFoundError:
                continue
            outputs.append(
                VerifiedCreationOutput(
                    locator=locator,
                    subject={},
                    payload=payload,
                    size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest(),
                    file_identity=identity,
                )
            )
        return tuple(outputs)

    @staticmethod
    def _validated_recovery_history(
        job: Mapping[str, Any],
        row: sqlite3.Row,
        history: Sequence[bytes],
    ) -> list[dict[str, Any]]:
        expected_attempt_generation = {
            "reserved": 1,
            "worker_started": 2,
            "output_published": 3,
            "registry_committing": 4,
            "cleanup_pending": 5,
        }.get(row["phase"])
        if (
            expected_attempt_generation is None
            or isinstance(row["generation"], bool)
            or not isinstance(row["generation"], int)
            or row["generation"] != expected_attempt_generation
        ):
            raise conflict("Creation job recovery attempt generation is invalid")
        documents: list[dict[str, Any]] = []
        previous_phase = -1
        base_generation: int | None = None
        binding: tuple[object, ...] | None = None
        prior_outputs: object = None
        for index, payload in enumerate(history):
            try:
                document = decode_json_object(payload, source="creation job recovery journal")
            except RuntimeIOError as exc:
                raise conflict("Creation job recovery journal payload is invalid") from exc
            if set(document) != {
                "format",
                "format_version",
                "job_id",
                "job_generation",
                "phase",
                "stage_locator",
                "stage_identity",
                "request",
                "outputs",
            }:
                raise conflict("Creation job recovery journal fields changed")
            if (
                document["format"] != _JOURNAL_FORMAT
                or document["format_version"] != _JOURNAL_VERSION
                or document["job_id"] != job["job_id"]
                or not isinstance(document["job_generation"], int)
                or isinstance(document["job_generation"], bool)
                or document["job_generation"] < 0
                or document["phase"] not in _JOURNAL_PHASES
                or document["stage_locator"] != row["stage_locator"]
                or document["stage_identity"] != [int(row["stage_dev"]), int(row["stage_ino"])]
            ):
                raise conflict("Creation job recovery journal binding changed")
            request = document["request"]
            outputs = document["outputs"]
            if (
                not isinstance(request, dict)
                or set(request) != {"locator", "sha256"}
                or request["locator"] != row["request_locator"]
                or request["sha256"] != row["request_sha256"]
                or not isinstance(outputs, list)
                or len(outputs) > 16
            ):
                raise conflict("Creation job recovery request binding changed")
            phase_index = _JOURNAL_PHASES.index(document["phase"])
            if phase_index != previous_phase + 1:
                raise conflict("Creation job recovery journal order is invalid")
            previous_phase = phase_index
            if base_generation is None:
                base_generation = document["job_generation"]
            expected_generation = base_generation + min(phase_index, 4)
            if document["job_generation"] != expected_generation:
                raise conflict("Creation job recovery journal generation is invalid")
            current_binding = (
                document["stage_locator"],
                tuple(document["stage_identity"]),
                request["locator"],
                request["sha256"],
            )
            if binding is not None and current_binding != binding:
                raise conflict("Creation job recovery journal binding changed")
            binding = current_binding
            if index < 2 and outputs:
                raise conflict("Creation job recovery outputs appeared too early")
            if outputs:
                if prior_outputs is not None and outputs != prior_outputs:
                    raise conflict("Creation job recovery output binding changed")
                prior_outputs = outputs
            for output in outputs:
                if not isinstance(output, dict) or set(output) != {
                    "locator",
                    "subject",
                    "size",
                    "sha256",
                    "file_identity",
                }:
                    raise conflict("Creation job recovery output fields changed")
                if (
                    not isinstance(output["locator"], str)
                    or not isinstance(output["subject"], dict)
                    or isinstance(output["size"], bool)
                    or not isinstance(output["size"], int)
                    or output["size"] < 0
                    or not isinstance(output["sha256"], str)
                    or SHA256_PATTERN.fullmatch(output["sha256"]) is None
                    or not isinstance(output["file_identity"], list)
                    or len(output["file_identity"]) != 2
                    or any(
                        isinstance(item, bool) or not isinstance(item, int) or item < 0
                        for item in output["file_identity"]
                    )
                ):
                    raise conflict("Creation job recovery output identity is invalid")
            documents.append(document)
        if not documents or documents[0]["phase"] != "reserved":
            raise conflict("Creation job recovery journal is incomplete")
        last_generation = documents[-1]["job_generation"]
        delta = int(job["generation"]) - int(last_generation)
        allowed_delta = (
            {0, 1}
            if job["state"] == "succeeded"
            else {1, 2}
            if job["state"] == "orphaned"
            else {0, 1, 2}
            if job["state"] == "running"
            else set()
        )
        if delta not in allowed_delta:
            raise conflict("Creation job recovery journal generation diverged")
        return documents

    def _execute(self, claimed: Mapping[str, Any]) -> None:
        snapshot, request, dependency_documents, staged_payloads = (
            self.jobs.private_request_for_job(claimed)
        )
        stage_locator = f"stage_{uuid.uuid4().hex}"
        request_locator = f"request_{uuid.uuid4().hex}"
        journal_name = f"creation_job_{uuid.uuid4().hex}.journal"
        journal_path = self.store.creation_job_journals_dir / journal_name
        request_sha256 = hashlib.sha256(canonical_json_bytes(request)).hexdigest()
        with self.store.connection:
            self.store.connection.execute(
                "INSERT INTO creation_job_attempts "
                "(job_id, phase, journal_name, journal_dev, journal_ino, stage_locator, "
                "stage_dev, stage_ino, request_locator, request_sha256, generation, "
                "created_at, updated_at) VALUES (?, 'reserving', ?, NULL, NULL, ?, NULL, "
                "NULL, ?, ?, 0, ?, ?)",
                (
                    claimed["job_id"],
                    journal_name,
                    stage_locator,
                    request_locator,
                    request_sha256,
                    claimed["updated_at"],
                    claimed["updated_at"],
                ),
            )
        stage, stage_identity = create_creation_stage(
            self.store.creation_jobs_dir,
            claimed["job_id"],
            locator=stage_locator,
        )
        with self.store.connection:
            updated_attempt = self.store.connection.execute(
                "UPDATE creation_job_attempts SET stage_dev = ?, stage_ino = ?, "
                "updated_at = ? WHERE job_id = ? AND phase = 'reserving' "
                "AND stage_dev IS NULL AND stage_ino IS NULL",
                (
                    str(stage_identity[0]),
                    str(stage_identity[1]),
                    claimed["updated_at"],
                    claimed["job_id"],
                ),
            )
            if updated_attempt.rowcount != 1:
                raise conflict("Creation job stage reservation changed")
        published_locator, published_sha256 = write_private_request(
            stage,
            request,
            locator=request_locator,
        )
        if published_locator != request_locator or not hmac.compare_digest(
            published_sha256, request_sha256
        ):
            raise invalid_state("Creation job private request publication changed")
        stage_private_asset_inputs(stage, request, staged_payloads)
        initial = _journal_payload(
            job=claimed,
            phase="reserved",
            stage_locator=stage.name,
            stage_identity=stage_identity,
            request_locator=request_locator,
            request_sha256=request_sha256,
            outputs=(),
        )
        journal_identity = create_append_only_journal(
            journal_path,
            initial,
            max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
        )
        fsync_directory(
            self.store.creation_job_journals_dir,
            context="creation job journal parent",
        )
        with self.store.connection:
            updated_attempt = self.store.connection.execute(
                "UPDATE creation_job_attempts SET phase = 'reserved', journal_dev = ?, "
                "journal_ino = ?, generation = generation + 1, updated_at = ? "
                "WHERE job_id = ? AND phase = 'reserving'",
                (
                    str(journal_identity[0]),
                    str(journal_identity[1]),
                    claimed["updated_at"],
                    claimed["job_id"],
                ),
            )
            if updated_attempt.rowcount != 1:
                raise conflict("Creation job journal reservation changed")

        history = (initial,)
        job = self.jobs.progress(claimed["job_id"], "worker_started")
        history = self._advance_journal(
            job,
            current=history,
            identity=journal_identity,
            path=journal_path,
            phase="worker_started",
            stage=stage,
            stage_identity=stage_identity,
            request_locator=request_locator,
            request_sha256=request_sha256,
            outputs=(),
        )
        envelope = {
            "format": "world-forge.studio_creation_worker",
            "format_version": job["format_version"],
            "kind": "request",
            "job_id": job["job_id"],
            "operation": job["operation"],
            "request_locator": request_locator,
            "request_sha256": request_sha256,
        }
        execution = run_isolated_creation_worker(
            stage,
            stage_identity,
            envelope,
            timeout_seconds=self.timeout_seconds,
            cancel_requested=lambda: (
                self.shutdown_requested() or self.jobs.cancellation_requested(job["job_id"])
            ),
            process_started=lambda pid, proof: self._record_worker_process(
                job["job_id"], pid, proof
            ),
            process_stopped=lambda pid, proof: self._clear_worker_process(
                job["job_id"], pid, proof
            ),
        )
        if not execution.response["ok"]:
            error = execution.response["error"]
            raise CreationWorkerExecutionError(
                error["code"],
                error["message"],
                recovery_evidence=error.get("recovery_evidence"),
            )
        verify_creation_stage_outputs(
            stage,
            stage_identity,
            request_locator,
            request_sha256,
            execution.outputs,
            execution.binary_outputs,
        )
        self._validate_asset_release_authorize_execution(
            job,
            outputs=execution.outputs,
            metadata=execution.response["metadata"],
            dependency_documents=dependency_documents,
            artifact_root=stage / "artifact_root",
        )
        self._validate_runtime_headless_execution(
            job,
            request=request,
            outputs=execution.outputs,
            metadata=execution.response["metadata"],
            artifact_root=stage / "artifact_root",
        )
        if job["operation"] == "asset.process":
            metadata = execution.response["metadata"]
            reason_codes = set(metadata.get("reason_codes", ()))
            if "processing_partial_publication" in reason_codes or (
                metadata["analysis_status"] == "failed" and execution.binary_outputs
            ):
                raise CreationWorkerExecutionError(
                    "recovery_required",
                    "Asset processing produced partial or indeterminate publication evidence",
                )
        self._record_binary_output(job, execution.binary_outputs)
        # Published here is the versioned private-worker-output phase.  Project-root
        # asset visibility is established separately by _publish_asset_outputs();
        # recovery must never infer it from this phase name alone.
        job = self.jobs.progress(job["job_id"], "output_published")
        history = self._advance_journal(
            job,
            current=history,
            identity=journal_identity,
            path=journal_path,
            phase="output_published",
            stage=stage,
            stage_identity=stage_identity,
            request_locator=request_locator,
            request_sha256=request_sha256,
            outputs=execution.outputs,
        )
        snapshot = self.jobs.snapshot_for_job(job)
        output_ids = {artifact_id_for_identity(item.subject) for item in execution.outputs}
        existing_ids = {record["artifact_id"] for record in snapshot["records"]}
        if output_ids & existing_ids:
            raise CreationWorkerExecutionError(
                "invalid_artifact",
                "Creation worker output duplicates an existing exact artifact",
            )
        try:
            prepared = self.jobs.artifacts.prepare_outputs(
                job=job,
                outputs=execution.outputs,
                project=snapshot["project"],
                dependency_documents=dependency_documents,
                artifact_root=(
                    stage / "artifact_root"
                    if job["operation"]
                    in {
                        "asset.process",
                        "asset.release.authorize",
                        "asset.release.seal",
                        "runtime.compose",
                        "runtime.bundle.build",
                        "game.materialization.bundle.build",
                        "game.materialize",
                        "game.package",
                        "game.package.extract",
                        "asset.qa.review",
                        "runtime.headless.verify",
                    }
                    else None
                ),
            )
        except StudioError as exc:
            raise CreationWorkerExecutionError(
                "invalid_artifact",
                "Creation worker output is not an integral artifact closure",
            ) from exc
        asset_process_retention: PreparedAssetProcessRetention | None = None
        if job["operation"] == "asset.process" and execution.binary_outputs:
            receipts = [
                artifact.document
                for artifact in prepared
                if artifact.document.get("format") == "world-forge.asset_processing_receipt"
            ]
            if len(receipts) != 1 or receipts[0].get("status") != "completed":
                raise CreationWorkerExecutionError(
                    "invalid_artifact",
                    "Asset process retained output receipt is not exact",
                )
            asset_process_retention = self.jobs.artifacts.prepare_asset_process_retention(
                job=job,
                outputs=execution.binary_outputs,
                processing_receipt=receipts[0],
            )
        with self._publish_asset_outputs(
            job,
            execution.binary_outputs,
        ) as asset_publication_guard:
            publication = (
                self._publish_asset_release(
                    job,
                    request=request,
                    outputs=execution.outputs,
                    dependency_documents=dependency_documents,
                    artifact_root=stage / "artifact_root",
                )
                if job["operation"] in {"asset.release.seal", "asset.release.authorize"}
                else self._publish_runtime_bundle(
                    job,
                    request=request,
                    outputs=execution.outputs,
                    dependency_documents=dependency_documents,
                    artifact_root=stage / "artifact_root",
                )
                if job["operation"] == "runtime.bundle.build"
                else self._publish_runtime_headless(
                    job,
                    request=request,
                    outputs=execution.outputs,
                    artifact_root=stage / "artifact_root",
                )
                if job["operation"] == "runtime.headless.verify"
                else self._publish_materialization_bundle(
                    job,
                    request=request,
                    outputs=execution.outputs,
                    dependency_documents=dependency_documents,
                    artifact_root=stage / "artifact_root",
                )
                if job["operation"] == "game.materialization.bundle.build"
                else self._publish_standalone_game(
                    job,
                    request=request,
                    outputs=execution.outputs,
                    dependency_documents=dependency_documents,
                )
                if job["operation"] == "game.materialize"
                else self._publish_game_package(
                    job,
                    request=request,
                    outputs=execution.outputs,
                    binary_outputs=execution.binary_outputs,
                    dependency_documents=dependency_documents,
                )
                if job["operation"] == "game.package"
                else self._publish_game_package_extraction(
                    job,
                    request=request,
                    outputs=execution.outputs,
                    dependency_documents=dependency_documents,
                )
                if job["operation"] == "game.package.extract"
                else None
            )
            job = self.jobs.progress(job["job_id"], "registry_committing")
            history = self._advance_journal(
                job,
                current=history,
                identity=journal_identity,
                path=journal_path,
                phase="registry_committing",
                stage=stage,
                stage_identity=stage_identity,
                request_locator=request_locator,
                request_sha256=request_sha256,
                outputs=execution.outputs,
            )
            pending = self._commit_registry(
                job,
                prepared,
                execution.response["metadata"],
                request=request,
                publication=publication,
                dependency_documents=dependency_documents,
                artifact_root=stage / "artifact_root",
                asset_publication_guard=asset_publication_guard,
                asset_process_retention=asset_process_retention,
            )
        committed_payload = _journal_payload(
            job=pending,
            phase="committed",
            stage_locator=stage.name,
            stage_identity=stage_identity,
            request_locator=request_locator,
            request_sha256=request_sha256,
            outputs=execution.outputs,
        )
        append_append_only_journal(
            journal_path,
            expected_identity=journal_identity,
            expected_payload=history[-1],
            expected_history=history,
            updated_payload=committed_payload,
            max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
            max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
        )
        history = (*history, committed_payload)
        cleanup_payload = _journal_payload(
            job=pending,
            phase="cleanup_pending",
            stage_locator=stage.name,
            stage_identity=stage_identity,
            request_locator=request_locator,
            request_sha256=request_sha256,
            outputs=execution.outputs,
        )
        append_append_only_journal(
            journal_path,
            expected_identity=journal_identity,
            expected_payload=history[-1],
            expected_history=history,
            updated_payload=cleanup_payload,
            max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
            max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
        )
        history = (*history, cleanup_payload)
        try:
            self.jobs.artifacts.validate_cleanup_outputs(pending, execution.outputs)
            self._cleanup_stage(
                stage,
                stage_identity,
                request_locator,
                request_sha256,
                execution.outputs,
                allow_retained_terminal=True,
                binary_identity=(
                    execution.binary_outputs[0].file_identity
                    if job["operation"] == "game.package"
                    else None
                ),
            )
            remove_append_only_journal(
                journal_path,
                expected_identity=journal_identity,
                expected_payload=history[-1],
                expected_history=history,
                max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
            )
        except Exception:
            return
        self._complete_cleanup(pending["job_id"])

    def _record_worker_process(
        self,
        job_id: str,
        pid: int,
        proof: Mapping[str, Any],
    ) -> None:
        encoded = encode_json(dict(proof))
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE creation_job_attempts SET worker_pid = ?, worker_identity_json = ?, "
                "updated_at = ? WHERE job_id = ? AND phase = 'worker_started' "
                "AND worker_pid IS NULL AND worker_identity_json IS NULL",
                (pid, encoded, utc_now(), job_id),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation worker process registration changed")

    def _clear_worker_process(
        self,
        job_id: str,
        pid: int,
        proof: Mapping[str, Any],
    ) -> None:
        encoded = encode_json(dict(proof))
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE creation_job_attempts SET worker_pid = NULL, "
                "worker_identity_json = NULL, updated_at = ? WHERE job_id = ? "
                "AND worker_pid = ? AND worker_identity_json = ?",
                (utc_now(), job_id, pid, encoded),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation worker process completion changed")

    def _record_binary_output(
        self,
        job: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationBinaryOutput],
    ) -> None:
        if job["operation"] == "game.package":
            if len(outputs) != 1 or outputs[0].locator != "game_package_archive":
                raise conflict("Game package binary output binding changed")
            identity = outputs[0].file_identity
            with self.store.connection:
                cursor = self.store.connection.execute(
                    "UPDATE creation_job_attempts SET binary_output_dev = ?, "
                    "binary_output_ino = ?, updated_at = ? WHERE job_id = ? "
                    "AND phase = 'worker_started' AND binary_output_dev IS NULL "
                    "AND binary_output_ino IS NULL",
                    (
                        str(identity[0]),
                        str(identity[1]),
                        utc_now(),
                        job["job_id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise conflict("Game package binary output identity lost its CAS")
            return
        if outputs and job["operation"] != "asset.process":
            raise conflict("Creation job received an unexpected binary output binding")

    @contextmanager
    def _publish_asset_outputs(
        self,
        job: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationBinaryOutput],
    ) -> Iterator[Callable[[], AbstractContextManager[None]] | None]:
        if job["operation"] == "game.package":
            if (
                len(outputs) != 1
                or outputs[0].locator != "game_package_archive"
                or outputs[0].size < 1
            ):
                raise CreationWorkerExecutionError(
                    "worker_protocol", "Game package private archive output changed"
                )
            yield None
            return
        if job["operation"] != "asset.process":
            if outputs:
                raise CreationWorkerExecutionError(
                    "worker_protocol", "Non-asset creation job produced binary outputs"
                )
            yield None
            return
        if not outputs:
            yield None
            return
        row = self.jobs.workspaces._row(job["workspace_id"])  # noqa: SLF001
        root, root_identity = self.jobs.workspaces._verified_root(row)  # noqa: SLF001

        def census() -> tuple[int, int, int]:
            exact = 0
            absent = 0
            ambiguous = 0
            for output in outputs:
                destination = root.joinpath(*Path(output.locator).parts)
                try:
                    info = path_file_stat(destination)
                except FileNotFoundError:
                    absent += 1
                    continue
                except OSError:
                    ambiguous += 1
                    continue
                if (
                    stat.S_ISLNK(info.st_mode)
                    or bool(
                        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    )
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                ):
                    ambiguous += 1
                    continue
                try:
                    retained = read_verified_artifact_bytes(
                        root,
                        output.locator,
                        expected_sha256=output.sha256,
                        expected_size_bytes=output.size,
                        limit=16 * 1024 * 1024,
                    )
                except (GenericAssetProductionError, OSError, ValueError):
                    ambiguous += 1
                    continue
                if retained == output.payload:
                    exact += 1
                else:
                    ambiguous += 1
            return exact, absent, ambiguous

        try:
            with exclusive_world_lifecycle(root, error_type=ValueError):

                def verify_publication() -> None:
                    exact, absent, ambiguous = census()
                    current_root, current_identity = self.jobs.workspaces._verified_root(  # noqa: SLF001
                        self.jobs.workspaces._row(job["workspace_id"])  # noqa: SLF001
                    )
                    if current_root != root or current_identity != root_identity:
                        ambiguous = len(outputs) - exact
                        absent = 0
                    if exact != len(outputs) or absent or ambiguous:
                        raise CreationWorkerExecutionError(
                            "recovery_required",
                            "Processed asset project publication requires explicit recovery "
                            f"(exact={exact}, absent={absent}, ambiguous={ambiguous})",
                        )

                publication_error: Exception | None = None
                for output in outputs:
                    destination = root.joinpath(*Path(output.locator).parts)
                    try:
                        write_bytes_atomic(destination, output.payload, durable_parent=True)
                    except AssetContractError as exc:
                        if not str(exc).startswith("Refusing to overwrite "):
                            publication_error = exc
                            break
                        try:
                            existing = read_verified_artifact_bytes(
                                root,
                                output.locator,
                                expected_sha256=output.sha256,
                                expected_size_bytes=output.size,
                                limit=16 * 1024 * 1024,
                            )
                        except (GenericAssetProductionError, OSError, ValueError) as read_error:
                            publication_error = read_error
                            break
                        if existing != output.payload:
                            publication_error = ValueError("published asset output bytes differ")
                            break
                    except (OSError, ValueError) as exc:
                        publication_error = exc
                        break
                if publication_error is not None:
                    exact, absent, ambiguous = census()
                    raise CreationWorkerExecutionError(
                        "recovery_required",
                        "Processed asset project publication requires explicit recovery "
                        f"(exact={exact}, absent={absent}, ambiguous={ambiguous})",
                    ) from publication_error
                verify_publication()
        except CreationWorkerExecutionError:
            raise
        except Exception as exc:
            raise CreationWorkerExecutionError(
                "recovery_required",
                "Processed asset project publication authority is indeterminate",
            ) from exc

        @contextmanager
        def commit_guard() -> Iterator[None]:
            yielded = False
            try:
                with exclusive_world_lifecycle(root, error_type=ValueError):
                    verify_publication()
                    yielded = True
                    yield
            except CreationWorkerExecutionError:
                raise
            except Exception as exc:
                if yielded:
                    raise
                raise CreationWorkerExecutionError(
                    "recovery_required",
                    "Processed asset project commit authority is indeterminate",
                ) from exc

        # The first lock publishes and verifies.  The commit guard reacquires the
        # same cooperative authority, revalidates after any intervening work, and
        # remains held until SQLite commits the bound candidate records.
        yield commit_guard

    def _asset_process_project_publication_may_exist(
        self,
        job: Mapping[str, Any],
        attempt: sqlite3.Row,
    ) -> bool:
        """Fail closed when retained evidence cannot disprove project publication."""
        try:
            if (
                attempt["stage_dev"] is None
                or attempt["stage_ino"] is None
                or attempt["journal_dev"] is None
                or attempt["journal_ino"] is None
            ):
                return True
            stage = self.store.creation_jobs_dir / attempt["stage_locator"]
            journal = self.store.creation_job_journals_dir / attempt["journal_name"]
            stage_info = path_file_stat(stage)
            if not stat.S_ISDIR(stage_info.st_mode) or (
                int(stage_info.st_dev),
                int(stage_info.st_ino),
            ) != (int(attempt["stage_dev"]), int(attempt["stage_ino"])):
                return True
            loaded = read_append_only_journal_history_state(
                journal,
                max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
            )
            if loaded is None:
                return True
            history, journal_identity, partial_tail = loaded
            if partial_tail or journal_identity != (
                int(attempt["journal_dev"]),
                int(attempt["journal_ino"]),
            ):
                return True
            documents = self._validated_recovery_history(job, attempt, history)
            outputs = self._recovery_outputs(
                stage,
                job,
                documents[-1],
                allow_missing=False,
            )
            receipts = [
                output
                for output in outputs
                if output.subject.get("format") == "world-forge.asset_processing_receipt"
            ]
            if len(receipts) != 1 or not receipts[0].payload:
                return True
            receipt = validate_asset_processing_receipt_document(
                decode_json_object(
                    receipts[0].payload,
                    source="creation job retained processing receipt",
                )
            )
            records = (
                receipt["outputs"]
                if receipt["status"] == "completed"
                else receipt["recovery"]["retained_artifacts"]
            )
            confirmed_stage_info = path_file_stat(stage)
            if not stat.S_ISDIR(confirmed_stage_info.st_mode) or (
                int(confirmed_stage_info.st_dev),
                int(confirmed_stage_info.st_ino),
            ) != (int(attempt["stage_dev"]), int(attempt["stage_ino"])):
                return True
            return bool(records)
        except (
            KeyError,
            RuntimeIOError,
            GenericAssetProcessingError,
            StudioError,
            OSError,
            TypeError,
            ValueError,
        ):
            return True

    def _trusted_asset_release_authorize_outputs(
        self,
        job: Mapping[str, Any],
        *,
        dependency_documents: Sequence[Mapping[str, Any]],
        artifact_root: Path,
        candidate_documents: tuple[Mapping[str, Any], Mapping[str, Any]] | None = None,
    ) -> tuple[tuple[dict[str, Any], dict[str, Any], dict[str, Any]], list[Any]]:
        if job["operation"] != "asset.release.authorize":
            raise invalid_state("Asset release authority expectation operation changed")
        review_documents = [
            dict(item)
            for item in dependency_documents
            if item.get("format") == "world-forge.asset_qa_review_receipt"
        ]
        lineage_documents = [
            dict(item)
            for item in dependency_documents
            if item.get("format") != "world-forge.asset_qa_review_receipt"
        ]
        if tuple(document_identity(document) for document in review_documents) != tuple(
            item["subject"] for item in job["inputs"]
        ):
            raise invalid_state("Asset release authority retained reviews changed")
        resolver = StudioAssetAuthorityResolver(
            self.store,
            artifacts=self.jobs.artifacts,
        )
        try:
            review_handles = [
                verify_asset_qa_review(document, resolver=resolver) for document in review_documents
            ]
            expected_blockers = derive_asset_release_blockers(
                review_handles,
                job["operation_params"]["blockers"],
            )
            authority_fields = {
                "workspace_id": job["workspace_id"],
                **copy.deepcopy(dict(job["authority"])),
                "producer_job_id": job["job_id"],
                "producer_operation": "asset.release.authorize",
                "producer_output_position": 2,
            }
            if candidate_documents is None:
                manifest, assetpack, authority, rebuilt_blockers = (
                    _build_asset_release_authorize_outputs(
                        project=self.jobs.snapshot_for_job(job)["project"],
                        lineage_documents=lineage_documents,
                        reviews=review_handles,
                        manifest_id=str(job["operation_params"]["manifest_id"]),
                        assetpack_id=str(job["operation_params"]["assetpack_id"]),
                        release_authority_id=str(job["operation_params"]["release_authority_id"]),
                        blockers=expected_blockers,
                        authority=authority_fields,
                        artifact_root=artifact_root,
                    )
                )
                if rebuilt_blockers != expected_blockers:
                    raise ValueError("asset release rebuilt blockers changed")
            else:
                manifest = copy.deepcopy(dict(candidate_documents[0]))
                assetpack = copy.deepcopy(dict(candidate_documents[1]))
                authority = build_asset_release_authority(
                    manifest,
                    assetpack,
                    review_handles,
                    release_authority_id=str(job["operation_params"]["release_authority_id"]),
                    blockers=expected_blockers,
                    authority=authority_fields,
                )
        except (
            GenericAssetAuthorityError,
            GenericAssetProcessingError,
            StudioError,
            ValueError,
        ) as exc:
            raise invalid_state("Asset release authority expectation is not integral") from exc
        if expected_blockers != list(job["operation_params"]["blockers"]):
            raise invalid_state("Asset release authority trusted blocker union changed")
        return (manifest, assetpack, authority), review_handles

    def _validate_asset_release_authorize_execution(
        self,
        job: Mapping[str, Any],
        *,
        outputs: Sequence[VerifiedCreationOutput],
        metadata: Mapping[str, Any],
        dependency_documents: Sequence[Mapping[str, Any]],
        artifact_root: Path,
    ) -> None:
        if job["operation"] != "asset.release.authorize":
            return
        try:
            documents = tuple(
                decode_json_object(
                    output.payload,
                    source=f"asset release authority worker output {index}",
                )
                for index, output in enumerate(outputs)
            )
            if len(documents) != 3:
                raise ValueError("worker release output count differs")
            expected, _reviews = self._trusted_asset_release_authorize_outputs(
                job,
                dependency_documents=dependency_documents,
                artifact_root=artifact_root,
                candidate_documents=(documents[0], documents[1]),
            )
            authority = expected[2]
            expected_analysis = "passed" if authority["status"] == "authorized" else "failed"
            if (
                documents != expected
                or metadata.get("analysis_status") != expected_analysis
                or list(metadata.get("reason_codes", ())) != authority["blockers"]
            ):
                raise ValueError("worker release decision differs from trusted expectation")
        except (RuntimeIOError, StudioError, TypeError, ValueError) as exc:
            raise CreationWorkerExecutionError(
                "worker_protocol",
                "Asset release worker changed the trusted release decision",
            ) from exc

    def _trusted_runtime_headless_outputs(
        self,
        job: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        artifact_root: Path,
    ) -> tuple[tuple[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]:
        if (
            job["operation"] != "runtime.headless.verify"
            or request["operation"] != job["operation"]
        ):
            raise invalid_state("Runtime headless expectation operation changed")
        documents = request["artifact_documents"]
        assetpack_handle = None
        evidence_set = None
        try:
            assetpack_handle, release_handle = _private_verified_asset_release(
                request,
                documents,
                artifact_root=artifact_root,
            )
            initial = initialize_runtime_support_authority(
                gamepack=documents[0],
                inventory=documents[1],
                composition=documents[6],
                registry=documents[5],
                snapshot=documents[4],
                verified_assetpack=assetpack_handle,
                asset_release_authority=release_handle,
            )
            evidence_set = verify_headless_evidence_set(
                artifact_root / "headless-evidence",
                bundle_root=artifact_root / "runtime-bundle",
            )
            if (
                evidence_set.manifest["runtime_evidence"]["platform"]["platform_id"]
                != request["platform_id"]
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
                or authority.document["supported"] is not False
                or authority.document["release_status"] != "blocked"
                or support["supported"] is not False
                or support["dimensions"]["release"] != "blocked"
            ):
                raise ValueError("runtime headless authority overclaims support")
            return (authority.document, evidence[0], support), evidence_set.manifest
        except (
            GenericAssetAuthorityError,
            GenericAssetpackError,
            GenericHeadlessError,
            RuntimeSupportAuthorityError,
            StudioError,
            TypeError,
            ValueError,
        ) as exc:
            raise invalid_state("Runtime headless expectation is not integral") from exc
        finally:
            if evidence_set is not None:
                evidence_set.close()
            if assetpack_handle is not None:
                assetpack_handle.close()

    def _validate_runtime_headless_execution(
        self,
        job: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationOutput],
        metadata: Mapping[str, Any],
        artifact_root: Path,
    ) -> None:
        if job["operation"] != "runtime.headless.verify":
            return
        try:
            documents = tuple(
                decode_json_object(
                    output.payload,
                    source=f"runtime headless worker output {index}",
                )
                for index, output in enumerate(outputs)
            )
            if len(documents) != 3:
                raise ValueError("runtime headless worker output count differs")
            expected, _manifest = self._trusted_runtime_headless_outputs(
                job,
                request=request,
                artifact_root=artifact_root,
            )
            if (
                documents != expected
                or metadata.get("analysis_status") != "passed"
                or list(metadata.get("reason_codes", ())) != expected[2]["reason_codes"]
            ):
                raise ValueError("runtime headless worker authority differs")
        except (RuntimeIOError, StudioError, TypeError, ValueError) as exc:
            raise CreationWorkerExecutionError(
                "worker_protocol",
                "Runtime headless worker changed the trusted authority",
            ) from exc

    def _publish_asset_release(
        self,
        job: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationOutput],
        dependency_documents: Sequence[Mapping[str, Any]],
        artifact_root: Path,
    ) -> dict[str, Any] | None:
        if (
            job["operation"]
            not in {
                "asset.release.seal",
                "asset.release.authorize",
            }
            or request["operation"] != job["operation"]
        ):
            raise CreationWorkerExecutionError(
                "worker_protocol", "Asset release publication authority changed"
            )
        expected_count = 3 if job["operation"] == "asset.release.authorize" else 2
        if len(outputs) != expected_count:
            raise CreationWorkerExecutionError(
                "worker_protocol", "Asset release publication output set changed"
            )
        try:
            release_manifest = decode_json_object(
                outputs[0].payload,
                source="asset release worker manifest",
            )
            assetpack_manifest = decode_json_object(
                outputs[1].payload,
                source="asset release worker assetpack",
            )
            if (
                release_manifest.get("format") != "world-forge.asset_manifest"
                or release_manifest.get("state") != "release_ready"
                or assetpack_manifest.get("format") != "world-forge.assetpack"
                or assetpack_manifest.get("release_ready_manifest", {}).get("content_hash")
                != release_manifest.get("content_hash")
            ):
                raise ValueError("asset release output formats changed")
            release_handle = None
            review_handles: list[Any] = []
            release_lineage = tuple(
                dict(item)
                for item in dependency_documents
                if item.get("format") != "world-forge.asset_qa_review_receipt"
            )
            if job["operation"] == "asset.release.authorize":
                release_authority = decode_json_object(
                    outputs[2].payload,
                    source="asset release worker authority",
                )
                retained = StudioAssetAuthorityResolver(
                    self.store,
                    artifacts=self.jobs.artifacts,
                )
                expected_outputs, review_handles = self._trusted_asset_release_authorize_outputs(
                    job,
                    dependency_documents=dependency_documents,
                    artifact_root=artifact_root,
                    candidate_documents=(release_manifest, assetpack_manifest),
                )
                if [release_manifest, assetpack_manifest, release_authority] != list(
                    expected_outputs
                ):
                    raise ValueError("asset release authority worker output differs")
                release_handle = verify_asset_release_authority(
                    release_authority,
                    manifest=release_manifest,
                    assetpack=assetpack_manifest,
                    reviews=review_handles,
                    resolver=_PendingAssetReleaseResolver(
                        retained=retained,
                        release=release_authority,
                        job=job,
                    ),
                )
                if (
                    release_authority["status"]
                    != ("authorized" if release_handle.authorized else "blocked")
                    or request["release_authority_id"] != release_authority["release_authority_id"]
                ):
                    raise ValueError("asset release authority status changed")
                if expected_outputs[2]["status"] == "blocked":
                    return None
            _lineage, roots, records = _asset_release_lineage(
                self.jobs.snapshot_for_job(job)["project"],
                release_lineage,
            )
            with self.store.connection:
                binding = self.jobs.output_grants.begin_publication(str(job["job_id"]))
            if (
                binding["expected_manifest_hash"] != release_manifest["content_hash"]
                or binding["expected_tree_hash"] != assetpack_manifest["content_hash"]
            ):
                raise ValueError("asset release output hashes changed")
            destination = Path(binding["path"])
            parent_identity = tuple(binding["parent_identity"])
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise ValueError("asset release output parent identity changed")
                verified = recover_generic_assetpack(
                    destination,
                    expected_parent_identity=parent_identity,
                )
                if verified is None:
                    try:
                        exists = destination.exists() or destination.is_symlink()
                    except OSError as exc:
                        raise ValueError("asset release destination cannot be inspected") from exc
                    if exists:
                        verified = verify_generic_assetpack(
                            destination,
                            expected_content_hash=assetpack_manifest["content_hash"],
                            expected_parent_identity=parent_identity,
                        )
                    else:
                        verified = seal_generic_assetpack(
                            destination,
                            release_manifest,
                            gamepack=roots["world-forge.gamepack"],
                            subject=roots["world-forge.asset_subject"],
                            target=roots["world-forge.asset_target"],
                            style=roots["world-forge.asset_style"],
                            inventory=roots["world-forge.asset_inventory"],
                            asset_records=records,
                            artifact_root=artifact_root,
                            qa_reviews=(
                                review_handles
                                if job["operation"] == "asset.release.authorize"
                                else None
                            ),
                            release_authority=release_handle,
                            expected_parent_identity=parent_identity,
                        )
                with verified:
                    manifest = verified.manifest
                    if manifest != assetpack_manifest:
                        raise ValueError("published assetpack manifest changed")
                    published_identity = verified.root_identity
                    publication = {
                        "format": "world-forge.assetpack",
                        "format_version": 1,
                        "id": manifest["assetpack_id"],
                        "content_hash": manifest["content_hash"],
                        "inventory_hash": manifest["inventory"]["content_hash"],
                    }
                parent.assert_current()
                with self.store.connection:
                    self.jobs.output_grants.note_publication_verified(
                        str(job["job_id"]),
                        published_identity=published_identity,
                    )
            return publication
        except CreationWorkerExecutionError:
            raise
        except GenericAssetpackError as exc:
            raise CreationWorkerExecutionError(
                (
                    "recovery_required"
                    if exc.reason_code.endswith("_recovery_required")
                    else "invalid_artifact"
                ),
                "Asset release could not be published safely",
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        except (RuntimeIOError, StudioError, OSError, ValueError) as exc:
            raise CreationWorkerExecutionError(
                "invalid_artifact", "Asset release could not be published safely"
            ) from exc

    def _publish_runtime_bundle(
        self,
        job: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationOutput],
        dependency_documents: Sequence[Mapping[str, Any]],
        artifact_root: Path,
    ) -> dict[str, Any]:
        if job["operation"] != "runtime.bundle.build" or request["operation"] != job["operation"]:
            raise CreationWorkerExecutionError(
                "worker_protocol", "Runtime bundle publication authority changed"
            )
        if len(outputs) != 1 or len(dependency_documents) != 7:
            raise CreationWorkerExecutionError(
                "worker_protocol", "Runtime bundle publication output set changed"
            )
        try:
            manifest = decode_json_object(
                outputs[0].payload,
                source="runtime bundle worker manifest",
            )
            by_format = {str(document["format"]): document for document in dependency_documents}
            required_formats = {
                "world-forge.gamepack",
                "world-forge.asset_inventory",
                "world-forge.assetpack",
                "world-forge.game_runtime_snapshot",
                "world-forge.runtime_adapter_registry",
                "world-forge.game_runtime_composition",
                "world-forge.runtime_support_report",
            }
            if set(by_format) != required_formats:
                raise ValueError("runtime bundle lineage formats changed")
            with self.store.connection:
                binding = self.jobs.output_grants.begin_publication(str(job["job_id"]))
            if binding["expected_manifest_hash"] != manifest.get("content_hash") or binding[
                "expected_tree_hash"
            ] != manifest.get("tree_hash"):
                raise ValueError("runtime bundle output hashes changed")
            destination = Path(binding["path"])
            parent_identity = tuple(binding["parent_identity"])
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise ValueError("runtime bundle output parent identity changed")
                verified = recover_game_runtime_bundle(
                    destination,
                    expected_parent_identity=parent_identity,
                )
                if verified is None:
                    try:
                        exists = destination.exists() or destination.is_symlink()
                    except OSError as exc:
                        raise ValueError("runtime bundle destination cannot be inspected") from exc
                    if exists:
                        verified = verify_game_runtime_bundle(
                            destination,
                            expected_content_hash=str(manifest["content_hash"]),
                        )
                    else:
                        verified = build_game_runtime_bundle_from_objects(
                            destination,
                            gamepack=by_format["world-forge.gamepack"],
                            inventory=by_format["world-forge.asset_inventory"],
                            assetpack=by_format["world-forge.assetpack"],
                            assetpack_root=artifact_root,
                            snapshot=by_format["world-forge.game_runtime_snapshot"],
                            registry=by_format["world-forge.runtime_adapter_registry"],
                            composition=by_format["world-forge.game_runtime_composition"],
                            support_report=by_format["world-forge.runtime_support_report"],
                            expected_parent_identity=parent_identity,
                        )
                with verified:
                    published_manifest = verified.manifest
                    if published_manifest != manifest:
                        raise ValueError("published runtime bundle manifest changed")
                    published_identity = verified.root_identity
                    publication = {
                        "format": published_manifest["format"],
                        "format_version": published_manifest["format_version"],
                        "id": published_manifest["bundle_id"],
                        "content_hash": published_manifest["content_hash"],
                        "tree_hash": published_manifest["tree_hash"],
                    }
                parent.assert_current()
                with self.store.connection:
                    self.jobs.output_grants.note_publication_verified(
                        str(job["job_id"]),
                        published_identity=published_identity,
                    )
            return publication
        except CreationWorkerExecutionError:
            raise
        except GameRuntimeBundleError as exc:
            raise CreationWorkerExecutionError(
                (
                    "recovery_required"
                    if exc.reason_code.endswith(("_recovery_required", "_indeterminate"))
                    else "invalid_artifact"
                ),
                "Runtime bundle could not be published safely",
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        except (RuntimeIOError, StudioError, OSError, ValueError) as exc:
            raise CreationWorkerExecutionError(
                "invalid_artifact", "Runtime bundle could not be published safely"
            ) from exc

    def _publish_runtime_headless(
        self,
        job: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationOutput],
        artifact_root: Path,
    ) -> dict[str, Any]:
        if (
            job["operation"] != "runtime.headless.verify"
            or request["operation"] != job["operation"]
            or len(outputs) != 3
        ):
            raise CreationWorkerExecutionError(
                "worker_protocol",
                "Runtime headless publication authority changed",
            )
        try:
            expected_outputs, manifest = self._trusted_runtime_headless_outputs(
                job,
                request=request,
                artifact_root=artifact_root,
            )
            actual_outputs = tuple(
                decode_json_object(
                    output.payload,
                    source=f"runtime headless publication output {index}",
                )
                for index, output in enumerate(outputs)
            )
            if actual_outputs != expected_outputs:
                raise ValueError("runtime headless publication outputs changed")
            with self.store.connection:
                binding = self.jobs.output_grants.begin_publication(str(job["job_id"]))
            if (
                binding["expected_manifest_hash"] != manifest["content_hash"]
                or binding["expected_tree_hash"] != manifest["tree_hash"]
            ):
                raise ValueError("runtime headless output hashes changed")
            source = self.jobs.output_grants.published_binding(
                grant_id=str(request["source_grant_id"]),
                workspace_id=str(job["workspace_id"]),
                expected_generation=int(request["source_grant_generation"]),
            )
            destination = Path(binding["path"])
            parent_identity = tuple(binding["parent_identity"])
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise ValueError("runtime headless output parent identity changed")
                verified = recover_headless_evidence_set(
                    destination,
                    bundle_root=source["path"],
                )
                if verified is None:
                    verified = publish_headless_evidence_tree(
                        artifact_root / "headless-evidence",
                        destination,
                        bundle_root=source["path"],
                        expected_content_hash=str(manifest["content_hash"]),
                        expected_tree_hash=str(manifest["tree_hash"]),
                        expected_source_identity=directory_identity(
                            artifact_root / "headless-evidence",
                            context="runtime headless private evidence",
                        ),
                    )
                try:
                    if (
                        verified.manifest != manifest
                        or verified.manifest["runtime_evidence"]["platform"]["platform_id"]
                        != request["platform_id"]
                    ):
                        raise ValueError("published runtime headless evidence changed")
                    published_identity = tuple(verified.root_identity)
                    publication = {
                        "format": verified.manifest["format"],
                        "format_version": verified.manifest["format_version"],
                        "id": verified.manifest["evidence_set_id"],
                        "content_hash": verified.manifest["content_hash"],
                        "tree_hash": verified.manifest["tree_hash"],
                    }
                finally:
                    verified.close()
                parent.assert_current()
                with self.store.connection:
                    self.jobs.output_grants.note_publication_verified(
                        str(job["job_id"]),
                        published_identity=published_identity,
                    )
            final = verify_headless_evidence_set(
                destination,
                bundle_root=source["path"],
                expected_content_hash=str(manifest["content_hash"]),
            )
            try:
                if final.root_identity != published_identity or final.manifest != manifest:
                    raise ValueError("runtime headless final publication changed")
            finally:
                final.close()
            return publication
        except CreationWorkerExecutionError:
            raise
        except GenericHeadlessError as exc:
            raise CreationWorkerExecutionError(
                ("recovery_required" if "recovery" in exc.reason_code else "invalid_artifact"),
                "Runtime headless evidence could not be published safely",
            ) from exc
        except (RuntimeIOError, StudioError, OSError, ValueError) as exc:
            raise CreationWorkerExecutionError(
                "invalid_artifact",
                "Runtime headless evidence could not be published safely",
            ) from exc

    def _publish_materialization_bundle(
        self,
        job: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationOutput],
        dependency_documents: Sequence[Mapping[str, Any]],
        artifact_root: Path,
    ) -> dict[str, Any]:
        if (
            job["operation"] != "game.materialization.bundle.build"
            or request["operation"] != job["operation"]
        ):
            raise CreationWorkerExecutionError(
                "worker_protocol",
                "Materialization bundle publication authority changed",
            )
        if len(outputs) != 1 or len(dependency_documents) != 1:
            raise CreationWorkerExecutionError(
                "worker_protocol",
                "Materialization bundle publication output set changed",
            )
        try:
            manifest = decode_json_object(
                outputs[0].payload,
                source="materialization bundle worker manifest",
            )
            runtime_bundle = dependency_documents[0]
            if runtime_bundle.get("format") != "world-forge.game_runtime_bundle":
                raise ValueError("materialization bundle lineage format changed")
            if runtime_bundle != request["runtime_bundle_manifest"]:
                raise ValueError("materialization bundle runtime lineage changed")
            with self.store.connection:
                binding = self.jobs.output_grants.begin_publication(str(job["job_id"]))
            if binding["expected_manifest_hash"] != manifest.get("content_hash") or binding[
                "expected_tree_hash"
            ] != manifest.get("tree_hash"):
                raise ValueError("materialization bundle output hashes changed")
            destination = Path(binding["path"])
            parent_identity = tuple(binding["parent_identity"])
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise ValueError("materialization bundle output parent identity changed")
                verified = recover_game_materialization_bundle(
                    destination,
                    expected_parent_identity=parent_identity,
                )
                if verified is None:
                    try:
                        exists = destination.exists() or destination.is_symlink()
                    except OSError as exc:
                        raise ValueError(
                            "materialization bundle destination cannot be inspected"
                        ) from exc
                    if exists:
                        verified = verify_game_materialization_bundle(
                            destination,
                            expected_content_hash=str(manifest["content_hash"]),
                            expected_parent_identity=parent_identity,
                        )
                    else:
                        verified = build_game_materialization_bundle(
                            destination,
                            runtime_bundle_root=artifact_root,
                            expected_parent_identity=parent_identity,
                        )
                with verified:
                    published_manifest = verified.manifest
                    if published_manifest != manifest:
                        raise ValueError("published materialization bundle manifest changed")
                    if (
                        published_manifest["lineage"]["runtime_bundle_hash"]
                        != runtime_bundle["content_hash"]
                    ):
                        raise ValueError("materialization bundle runtime lineage crossed")
                    published_identity = verified.root_identity
                    publication = {
                        "format": published_manifest["format"],
                        "format_version": published_manifest["format_version"],
                        "id": published_manifest["materialization_bundle_id"],
                        "content_hash": published_manifest["content_hash"],
                        "tree_hash": published_manifest["tree_hash"],
                    }
                parent.assert_current()
                with self.store.connection:
                    self.jobs.output_grants.note_publication_verified(
                        str(job["job_id"]),
                        published_identity=published_identity,
                    )
            return publication
        except CreationWorkerExecutionError:
            raise
        except GameMaterializationBundleError as exc:
            raise CreationWorkerExecutionError(
                (
                    "recovery_required"
                    if exc.reason_code.endswith(("_recovery_required", "_indeterminate"))
                    else "invalid_artifact"
                ),
                "Materialization bundle could not be published safely",
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        except (RuntimeIOError, StudioError, OSError, ValueError) as exc:
            raise CreationWorkerExecutionError(
                "invalid_artifact",
                "Materialization bundle could not be published safely",
            ) from exc

    def _publish_standalone_game(
        self,
        job: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationOutput],
        dependency_documents: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if job["operation"] != "game.materialize" or request["operation"] != job["operation"]:
            raise CreationWorkerExecutionError(
                "worker_protocol",
                "Standalone publication authority changed",
            )
        if len(outputs) != 1 or len(dependency_documents) != 1:
            raise CreationWorkerExecutionError(
                "worker_protocol",
                "Standalone publication output set changed",
            )
        try:
            manifest = decode_json_object(
                outputs[0].payload,
                source="standalone game worker manifest",
            )
            materialization = dependency_documents[0]
            if materialization.get("format") != "world-forge.game_materialization_bundle":
                raise ValueError("standalone materialization lineage format changed")
            if materialization != request["materialization_bundle_manifest"]:
                raise ValueError("standalone materialization lineage changed")
            source_binding = self.jobs.output_grants.published_binding(
                grant_id=str(job["operation_params"]["source_grant_id"]),
                workspace_id=str(job["workspace_id"]),
                expected_generation=int(job["operation_params"]["source_grant_generation"]),
            )
            if (
                source_binding["expected_manifest_hash"] != materialization["content_hash"]
                or source_binding["expected_tree_hash"] != materialization["tree_hash"]
            ):
                raise ValueError("standalone source grant hashes changed")
            with self.store.connection:
                binding = self.jobs.output_grants.begin_publication(str(job["job_id"]))
            destination = Path(binding["path"])
            parent_identity = tuple(binding["parent_identity"])
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise ValueError("standalone output parent identity changed")
                with verify_game_materialization_bundle(
                    source_binding["path"],
                    expected_content_hash=str(materialization["content_hash"]),
                    expected_parent_identity=tuple(source_binding["parent_identity"]),
                ) as source:
                    if source.manifest != materialization or tuple(source.root_identity) != tuple(
                        source_binding["published_identity"]
                    ):
                        raise ValueError("standalone source bytes changed")
                    expected_manifest, expected_lock, _platform = build_standalone_game_documents(
                        source
                    )
                    if manifest != expected_manifest:
                        raise ValueError("standalone worker manifest changed")
                    if (
                        binding["expected_manifest_hash"] != manifest["content_hash"]
                        or binding["expected_tree_hash"] != expected_lock["tree_hash"]
                    ):
                        raise ValueError("standalone output hashes changed")

                    def exact_publication(verified):
                        published_manifest = verified.manifest
                        published_lock = verified.lock
                        if (
                            published_manifest != manifest
                            or published_lock != expected_lock
                            or published_manifest["materialization_bundle"]["content_hash"]
                            != materialization["content_hash"]
                        ):
                            raise ValueError("published standalone game changed")
                        return verified.root_identity, {
                            "format": published_manifest["format"],
                            "format_version": published_manifest["format_version"],
                            "id": published_manifest["game_id"],
                            "content_hash": published_manifest["content_hash"],
                            "tree_hash": published_lock["tree_hash"],
                        }

                    recovery = binding["recovery"]
                    retained_identity = binding["published_identity"]
                    if recovery is not None and recovery["phase"] == "publication_verified":
                        recovery_identity = tuple(recovery["published_identity"])
                        recovery_stage_identity = tuple(recovery["stage_identity"])
                        if (
                            recovery["expected_manifest_hash"] != binding["expected_manifest_hash"]
                            or recovery["expected_tree_hash"] != binding["expected_tree_hash"]
                            or retained_identity is None
                            or recovery_identity != tuple(retained_identity)
                            or recovery_stage_identity != recovery_identity
                        ):
                            raise ValueError("standalone recovery authority changed")
                        with verify_standalone_game(
                            destination,
                            expected_content_hash=str(binding["expected_manifest_hash"]),
                            expected_root_identity=recovery_identity,
                        ) as verified:
                            published_identity, publication = exact_publication(verified)
                    else:
                        if retained_identity is not None:
                            raise ValueError("standalone publication identity is ambiguous")
                        recovery_phase = None if recovery is None else recovery["phase"]
                        bound_phase = recovery_phase in {
                            "publication_started",
                            "publication_stage_allocated",
                            "publication_staged",
                            "publication_resetting",
                        }
                        expected_journal_identity = (
                            tuple(recovery["journal_identity"]) if bound_phase else None
                        )
                        expected_operation_id = (
                            str(recovery["operation_id"]) if bound_phase else None
                        )
                        expected_stage_identity = (
                            tuple(recovery["stage_identity"])
                            if bound_phase and "stage_identity" in recovery
                            else None
                        )
                        expected_journal_payload_sha256 = (
                            str(recovery["journal_payload_sha256"]) if bound_phase else None
                        )
                        expected_journal_payload_state = (
                            str(recovery["journal_payload_state"]) if bound_phase else None
                        )
                        with materialize_game(
                            source_binding["path"],
                            destination,
                            expected_content_hash=str(materialization["content_hash"]),
                            expected_source_identity=tuple(source_binding["published_identity"]),
                            expected_parent_identity=parent_identity,
                            _verified_source=source,
                            _authority_hook=lambda phase, evidence: (
                                self._persist_standalone_authority(
                                    str(job["job_id"]),
                                    phase,
                                    evidence,
                                )
                            ),
                            _expected_journal_identity=expected_journal_identity,
                            _expected_operation_id=expected_operation_id,
                            _expected_stage_identity=expected_stage_identity,
                            _expected_journal_payload_sha256=(expected_journal_payload_sha256),
                            _expected_journal_payload_state=expected_journal_payload_state,
                            _require_intent_journal=recovery_phase == "publication_started",
                            _stage_allocated=(recovery_phase == "publication_stage_allocated"),
                            _reset_pending=recovery_phase == "publication_resetting",
                            _reject_unbound_journal=(recovery_phase == "publication_reserved"),
                        ) as verified:
                            published_identity, publication = exact_publication(verified)
                parent.assert_current()
                confirmed = self.jobs.output_grants.published_binding(
                    grant_id=str(job["operation_params"]["source_grant_id"]),
                    workspace_id=str(job["workspace_id"]),
                    expected_generation=int(job["operation_params"]["source_grant_generation"]),
                )
                if any(
                    confirmed[field] != source_binding[field]
                    for field in (
                        "path",
                        "parent_identity",
                        "published_identity",
                        "generation",
                        "expected_manifest_hash",
                        "expected_tree_hash",
                        "leaf",
                    )
                ):
                    raise ValueError("standalone source authority changed")
                retained = self.jobs.output_grants.binding_for_job(
                    str(job["job_id"]),
                    allow_visible=True,
                )["recovery"]
                if retained is None or retained["phase"] != "publication_verified":
                    raise ValueError("standalone verified authority disappeared")
                with self.store.connection:
                    self.jobs.output_grants.note_publication_verified(
                        str(job["job_id"]),
                        published_identity=published_identity,
                        journal_identity=tuple(retained["journal_identity"]),
                        operation_id=str(retained["operation_id"]),
                        stage_identity=tuple(retained["stage_identity"]),
                        journal_payload_sha256=str(retained["journal_payload_sha256"]),
                        journal_payload_state=str(retained["journal_payload_state"]),
                    )
            return publication
        except CreationWorkerExecutionError:
            raise
        except StandaloneGameError as exc:
            raise CreationWorkerExecutionError(
                (
                    "recovery_required"
                    if exc.reason_code.endswith(("_recovery_required", "_indeterminate"))
                    else "invalid_artifact"
                ),
                "Standalone game could not be published safely",
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        except GameMaterializationBundleError as exc:
            raise CreationWorkerExecutionError(
                "invalid_artifact",
                "Standalone source could not be verified safely",
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        except (RuntimeIOError, StudioError, OSError, ValueError) as exc:
            raise CreationWorkerExecutionError(
                "invalid_artifact",
                "Standalone game could not be published safely",
            ) from exc

    def _publish_game_package(
        self,
        job: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationOutput],
        binary_outputs: Sequence[VerifiedCreationBinaryOutput],
        dependency_documents: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if job["operation"] != "game.package" or request["operation"] != job["operation"]:
            raise CreationWorkerExecutionError(
                "worker_protocol",
                "Game package publication authority changed",
            )
        if (
            len(outputs) != 1
            or len(binary_outputs) != 1
            or len(dependency_documents) != 1
            or binary_outputs[0].locator != "game_package_archive"
        ):
            raise CreationWorkerExecutionError(
                "worker_protocol",
                "Game package publication output set changed",
            )
        package = None
        visible = None
        retained = None
        try:
            manifest = decode_json_object(
                outputs[0].payload,
                source="game package worker manifest",
            )
            standalone_manifest = dependency_documents[0]
            archive = binary_outputs[0]
            package = verify_game_package_bytes(archive.payload)
            if (
                standalone_manifest.get("format") != "world-forge.standalone_game"
                or standalone_manifest != request["standalone_game_manifest"]
                or package.manifest != manifest
                or manifest != request["game_package_manifest"]
                or archive.sha256 != request["archive_output"]["sha256"]
                or archive.size != request["archive_output"]["size_bytes"]
                or package.archive_sha256 != archive.sha256
            ):
                raise ValueError("game package worker authority changed")
            source_binding = self.jobs.output_grants.published_binding(
                grant_id=str(job["operation_params"]["source_grant_id"]),
                workspace_id=str(job["workspace_id"]),
                expected_generation=int(job["operation_params"]["source_grant_generation"]),
            )
            if (
                source_binding["expected_manifest_hash"] != standalone_manifest["content_hash"]
                or source_binding["expected_tree_hash"]
                != request["standalone_game_lock"]["tree_hash"]
            ):
                raise ValueError("game package source grant hashes changed")
            with self.store.connection:
                binding = self.jobs.output_grants.begin_publication(str(job["job_id"]))
            if (
                binding["expected_manifest_hash"] != manifest["content_hash"]
                or binding["expected_archive_sha256"] != archive.sha256
                or binding["expected_size_bytes"] != archive.size
            ):
                raise ValueError("game package output hashes changed")
            destination = Path(binding["path"])
            parent_identity = tuple(binding["parent_identity"])
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise ValueError("game package output parent identity changed")
                with verify_standalone_game(
                    source_binding["path"],
                    expected_content_hash=str(standalone_manifest["content_hash"]),
                    expected_root_identity=tuple(source_binding["published_identity"]),
                ) as source:
                    if (
                        source.manifest != standalone_manifest
                        or source.lock != request["standalone_game_lock"]
                    ):
                        raise ValueError("game package standalone source bytes changed")
                    recovery = binding["recovery"]
                    if recovery is None:
                        raise ValueError("game package publication reservation disappeared")
                    if recovery["phase"] == "file_publication_verified":
                        retained_identity = binding["published_identity"]
                        if retained_identity is None or tuple(
                            recovery["published_identity"]
                        ) != tuple(retained_identity):
                            raise ValueError("game package retained identity changed")
                        visible = verify_game_package(
                            destination,
                            expected_file_identity=tuple(retained_identity),
                        )
                        published_identity = tuple(retained_identity)
                    else:
                        if recovery["phase"] == "file_publication_reserved":
                            with self.store.connection:
                                binding = self.jobs.output_grants.note_file_publication_started(
                                    str(job["job_id"])
                                )
                            recovery = binding["recovery"]
                        if recovery["phase"] != "file_publication_started":
                            raise ValueError("game package publication phase changed")
                        if binding["published_identity"] is not None:
                            raise ValueError("game package publication identity is ambiguous")
                        visible, published_identity = publish_verified_game_package(
                            source,
                            package,
                            destination,
                            expected_parent_identity=parent_identity,
                            expected_archive_sha256=archive.sha256,
                            expected_size_bytes=archive.size,
                        )
                        with self.store.connection:
                            self.jobs.output_grants.note_file_publication_verified(
                                str(job["job_id"]),
                                published_identity=published_identity,
                            )
                    if (
                        visible.manifest != manifest
                        or visible.archive_bytes != archive.payload
                        or visible.archive_sha256 != archive.sha256
                    ):
                        raise ValueError("published game package bytes changed")
                parent.assert_current()
                confirmed_source = self.jobs.output_grants.published_binding(
                    grant_id=str(job["operation_params"]["source_grant_id"]),
                    workspace_id=str(job["workspace_id"]),
                    expected_generation=int(job["operation_params"]["source_grant_generation"]),
                )
                if any(
                    confirmed_source[field] != source_binding[field]
                    for field in (
                        "path",
                        "parent_identity",
                        "published_identity",
                        "generation",
                        "expected_manifest_hash",
                        "expected_tree_hash",
                        "leaf",
                    )
                ):
                    raise ValueError("game package source authority changed")
                retained = self.jobs.output_grants.binding_for_job(
                    str(job["job_id"]),
                    allow_visible=True,
                )
                if (
                    retained["recovery"] is None
                    or retained["recovery"]["phase"] != "file_publication_verified"
                    or tuple(retained["published_identity"]) != tuple(published_identity)
                ):
                    raise ValueError("game package verified authority disappeared")
                confirmed_package = verify_game_package(
                    destination,
                    expected_file_identity=tuple(published_identity),
                )
                try:
                    if (
                        confirmed_package.manifest != manifest
                        or confirmed_package.archive_bytes != archive.payload
                    ):
                        raise ValueError("game package final bytes changed")
                finally:
                    confirmed_package.close()
            return {
                "format": manifest["format"],
                "format_version": manifest["format_version"],
                "id": manifest["package_id"],
                "content_hash": manifest["content_hash"],
                "archive_sha256": archive.sha256,
                "size_bytes": archive.size,
            }
        except CreationWorkerExecutionError:
            raise
        except WorldForgeGamePackageError as exc:
            raise CreationWorkerExecutionError(
                (
                    "recovery_required"
                    if exc.reason_code.endswith(
                        ("_recovery_required", "_indeterminate", "_destination_exists")
                    )
                    else "invalid_artifact"
                ),
                "Game package could not be published safely",
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        except (GamePackageError, RuntimeIOError, StudioError, OSError, ValueError) as exc:
            raise CreationWorkerExecutionError(
                "invalid_artifact",
                "Game package could not be published safely",
            ) from exc
        finally:
            if package is not None:
                package.close()
            if visible is not None:
                visible.close()

    def _publish_game_package_extraction(
        self,
        job: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationOutput],
        dependency_documents: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if (
            job["operation"] != "game.package.extract"
            or request["operation"] != job["operation"]
            or len(outputs) != 1
            or len(dependency_documents) != 1
        ):
            raise CreationWorkerExecutionError(
                "worker_protocol",
                "Game package extraction publication authority changed",
            )
        package = None
        visible = None
        try:
            package_manifest = dependency_documents[0]
            evidence = validate_game_package_extraction_evidence(
                decode_json_object(
                    outputs[0].payload,
                    source="game package extraction worker evidence",
                ),
                package_manifest=package_manifest,
                archive_sha256=str(request["archive_input"]["sha256"]),
                archive_size_bytes=int(request["archive_input"]["size_bytes"]),
            )
            if (
                package_manifest != request["game_package_manifest"]
                or evidence["package"]["content_hash"] != package_manifest["content_hash"]
            ):
                raise ValueError("game package extraction worker authority changed")
            source_binding = self.jobs.output_grants.published_binding(
                grant_id=str(job["operation_params"]["source_grant_id"]),
                workspace_id=str(job["workspace_id"]),
                expected_generation=int(job["operation_params"]["source_grant_generation"]),
            )
            if (
                source_binding["expected_manifest_hash"] != package_manifest["content_hash"]
                or source_binding["expected_archive_sha256"] != request["archive_input"]["sha256"]
                or source_binding["expected_size_bytes"] != request["archive_input"]["size_bytes"]
            ):
                raise ValueError("game package extraction source grant hashes changed")
            package = verify_game_package(
                source_binding["path"],
                expected_file_identity=tuple(source_binding["published_identity"]),
            )
            if (
                package.manifest != package_manifest
                or package.archive_sha256 != evidence["package"]["archive_sha256"]
                or len(package.archive_bytes) != evidence["package"]["size_bytes"]
            ):
                raise ValueError("game package extraction source bytes changed")
            with self.store.connection:
                binding = self.jobs.output_grants.begin_publication(str(job["job_id"]))
            if (
                binding["expected_manifest_hash"] != evidence["standalone_game"]["content_hash"]
                or binding["expected_tree_hash"] != evidence["extracted_tree_hash"]
            ):
                raise ValueError("game package extraction output hashes changed")
            destination = Path(binding["path"])
            parent_identity = tuple(binding["parent_identity"])
            with open_verified_output_parent(destination.parent, create=False) as parent:
                if parent.identities[-1] != parent_identity:
                    raise ValueError("game package extraction output parent changed")

                def exact_publication(verified):
                    if (
                        verified.manifest["content_hash"]
                        != evidence["standalone_game"]["content_hash"]
                        or verified.lock["content_hash"] != evidence["payload_lock"]["content_hash"]
                        or verified.lock["tree_hash"] != evidence["extracted_tree_hash"]
                        or verified.manifest["lineage"] != evidence["lineage"]
                        or verified.root_identity is None
                    ):
                        raise ValueError("game package extraction published bytes changed")
                    return tuple(verified.root_identity)

                recovery = binding["recovery"]
                retained_identity = binding["published_identity"]
                if recovery is not None and recovery["phase"] == "publication_verified":
                    recovery_identity = tuple(recovery["published_identity"])
                    recovery_stage_identity = tuple(recovery["stage_identity"])
                    if (
                        recovery["expected_manifest_hash"] != binding["expected_manifest_hash"]
                        or recovery["expected_tree_hash"] != binding["expected_tree_hash"]
                        or retained_identity is None
                        or recovery_identity != tuple(retained_identity)
                        or recovery_stage_identity != recovery_identity
                    ):
                        raise ValueError("game package extraction recovery authority changed")
                    with verify_standalone_game(
                        destination,
                        expected_content_hash=str(binding["expected_manifest_hash"]),
                        expected_root_identity=recovery_identity,
                    ) as verified:
                        published_identity = exact_publication(verified)
                else:
                    if retained_identity is not None:
                        raise ValueError(
                            "game package extraction publication identity is ambiguous"
                        )
                    visible = extract_game_package(
                        source_binding["path"],
                        destination,
                        expected_source_identity=tuple(source_binding["published_identity"]),
                        expected_parent_identity=parent_identity,
                        _verified_package=package,
                        _authority_hook=lambda phase, authority: self._persist_standalone_authority(
                            str(job["job_id"]),
                            phase,
                            authority,
                        ),
                    )
                    published_identity = exact_publication(visible)
                parent.assert_current()
                retained = self.jobs.output_grants.binding_for_job(
                    str(job["job_id"]),
                    allow_visible=True,
                )
                retained_authority = retained["recovery"]
                if (
                    retained_authority is None
                    or retained_authority["phase"] != "publication_verified"
                    or retained["published_identity"] is None
                    or tuple(retained["published_identity"]) != published_identity
                ):
                    raise ValueError("game package extraction verified authority disappeared")
                confirmed_source = self.jobs.output_grants.published_binding(
                    grant_id=str(job["operation_params"]["source_grant_id"]),
                    workspace_id=str(job["workspace_id"]),
                    expected_generation=int(job["operation_params"]["source_grant_generation"]),
                )
                if any(
                    confirmed_source[field] != source_binding[field]
                    for field in (
                        "path",
                        "parent_identity",
                        "published_identity",
                        "generation",
                        "expected_manifest_hash",
                        "expected_archive_sha256",
                        "expected_size_bytes",
                        "leaf",
                    )
                ):
                    raise ValueError("game package extraction source authority changed")
                with self.store.connection:
                    self.jobs.output_grants.note_publication_verified(
                        str(job["job_id"]),
                        published_identity=published_identity,
                        journal_identity=tuple(retained_authority["journal_identity"]),
                        operation_id=str(retained_authority["operation_id"]),
                        stage_identity=tuple(retained_authority["stage_identity"]),
                        journal_payload_sha256=str(retained_authority["journal_payload_sha256"]),
                        journal_payload_state=str(retained_authority["journal_payload_state"]),
                    )
            standalone = evidence["standalone_game"]
            return {
                "format": standalone["format"],
                "format_version": standalone["format_version"],
                "id": standalone["game_id"],
                "content_hash": standalone["content_hash"],
                "tree_hash": evidence["extracted_tree_hash"],
            }
        except CreationWorkerExecutionError:
            raise
        except WorldForgeGamePackageError as exc:
            raise CreationWorkerExecutionError(
                (
                    "recovery_required"
                    if exc.reason_code.endswith(
                        ("_recovery_required", "_indeterminate", "_destination_exists")
                    )
                    else "invalid_artifact"
                ),
                "Game package extraction could not be published safely",
                recovery_evidence=exc.recovery_evidence,
            ) from exc
        except (
            GamePackageExtractionEvidenceError,
            RuntimeIOError,
            StudioError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            raise CreationWorkerExecutionError(
                "invalid_artifact",
                "Game package extraction could not be published safely",
            ) from exc
        finally:
            if visible is not None:
                visible.close()
            if package is not None:
                package.close()

    def _advance_journal(
        self,
        job: Mapping[str, Any],
        *,
        current: tuple[bytes, ...],
        identity: tuple[int, int],
        path: Path,
        phase: str,
        stage: Path,
        stage_identity: tuple[int, int],
        request_locator: str,
        request_sha256: str,
        outputs: Sequence[VerifiedCreationOutput],
    ) -> tuple[bytes, ...]:
        payload = _journal_payload(
            job=job,
            phase=phase,
            stage_locator=stage.name,
            stage_identity=stage_identity,
            request_locator=request_locator,
            request_sha256=request_sha256,
            outputs=outputs,
        )
        append_append_only_journal(
            path,
            expected_identity=identity,
            expected_payload=current[-1],
            expected_history=current,
            updated_payload=payload,
            max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
            max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
        )
        with self.store.connection:
            self.store.connection.execute(
                "UPDATE creation_job_attempts SET phase = ?, generation = generation + 1, "
                "updated_at = ? WHERE job_id = ?",
                (phase, job["updated_at"], job["job_id"]),
            )
        return (*current, payload)

    def _verify_standalone_registry_commit(
        self,
        job: Mapping[str, Any],
        prepared: Sequence[PreparedCreationArtifact],
        publication: Mapping[str, Any],
        target_binding: Mapping[str, Any],
    ) -> None:
        if len(prepared) != 1:
            raise conflict("Standalone registry candidate set changed")
        candidate = prepared[0].document
        if candidate.get("format") != "world-forge.standalone_game":
            raise conflict("Standalone registry candidate format changed")
        source_binding = self.jobs.output_grants.published_binding(
            grant_id=str(job["operation_params"]["source_grant_id"]),
            workspace_id=str(job["workspace_id"]),
            expected_generation=int(job["operation_params"]["source_grant_generation"]),
        )
        source_identity = source_binding["published_identity"]
        target_identity = target_binding["published_identity"]
        if source_identity is None or target_identity is None:
            raise conflict("Standalone registry publication identity is unavailable")
        with verify_game_materialization_bundle(
            source_binding["path"],
            expected_content_hash=str(source_binding["expected_manifest_hash"]),
            expected_parent_identity=tuple(source_binding["parent_identity"]),
        ) as source:
            source_manifest = source.manifest
            if (
                tuple(source.root_identity) != tuple(source_identity)
                or source_manifest["content_hash"] != source_binding["expected_manifest_hash"]
                or source_manifest["tree_hash"] != source_binding["expected_tree_hash"]
                or candidate["materialization_bundle"]
                != {
                    "format": source_manifest["format"],
                    "format_version": source_manifest["format_version"],
                    "id": source_manifest["materialization_bundle_id"],
                    "content_hash": source_manifest["content_hash"],
                }
            ):
                raise conflict("Standalone registry source bytes changed")
            with verify_standalone_game(
                target_binding["path"],
                expected_content_hash=str(target_binding["expected_manifest_hash"]),
                expected_root_identity=tuple(target_identity),
            ) as standalone:
                standalone_manifest = standalone.manifest
                standalone_lock = standalone.lock
                expected_publication = {
                    "format": standalone_manifest["format"],
                    "format_version": standalone_manifest["format_version"],
                    "id": standalone_manifest["game_id"],
                    "content_hash": standalone_manifest["content_hash"],
                    "tree_hash": standalone_lock["tree_hash"],
                }
                if (
                    standalone_manifest != candidate
                    or standalone_lock["tree_hash"] != target_binding["expected_tree_hash"]
                    or standalone_manifest["materialization_bundle"]
                    != candidate["materialization_bundle"]
                    or dict(publication) != expected_publication
                ):
                    raise conflict("Standalone registry target bytes changed")

    def _verify_game_package_registry_commit(
        self,
        job: Mapping[str, Any],
        prepared: Sequence[PreparedCreationArtifact],
        publication: Mapping[str, Any],
        target_binding: Mapping[str, Any],
    ) -> None:
        if len(prepared) != 1:
            raise conflict("Game package registry candidate set changed")
        candidate = prepared[0].document
        if candidate.get("format") != "world-forge.game_package":
            raise conflict("Game package registry candidate format changed")
        source_binding = self.jobs.output_grants.published_binding(
            grant_id=str(job["operation_params"]["source_grant_id"]),
            workspace_id=str(job["workspace_id"]),
            expected_generation=int(job["operation_params"]["source_grant_generation"]),
        )
        source_identity = source_binding["published_identity"]
        target_identity = target_binding["published_identity"]
        if source_identity is None or target_identity is None:
            raise conflict("Game package registry publication identity is unavailable")
        package = None
        expected = None
        try:
            with verify_standalone_game(
                source_binding["path"],
                expected_content_hash=str(source_binding["expected_manifest_hash"]),
                expected_root_identity=tuple(source_identity),
            ) as source:
                if source.lock["tree_hash"] != source_binding["expected_tree_hash"]:
                    raise conflict("Game package registry source bytes changed")
                expected = build_game_package_from_files(source.files)
                package = verify_game_package(
                    target_binding["path"],
                    expected_file_identity=tuple(target_identity),
                )
                expected_publication = {
                    "format": package.manifest["format"],
                    "format_version": package.manifest["format_version"],
                    "id": package.manifest["package_id"],
                    "content_hash": package.manifest["content_hash"],
                    "archive_sha256": package.archive_sha256,
                    "size_bytes": len(package.archive_bytes),
                }
                if (
                    package.manifest != candidate
                    or package.manifest != expected.manifest
                    or package.archive_bytes != expected.archive_bytes
                    or target_binding["expected_manifest_hash"] != candidate["content_hash"]
                    or target_binding["expected_archive_sha256"] != package.archive_sha256
                    or target_binding["expected_size_bytes"] != len(package.archive_bytes)
                    or dict(publication) != expected_publication
                ):
                    raise conflict("Game package registry target bytes changed")
        except (
            GamePackageError,
            StandaloneGameError,
            WorldForgeGamePackageError,
            TypeError,
            ValueError,
        ) as exc:
            raise conflict("Game package registry bytes are not integral") from exc
        finally:
            if package is not None:
                package.close()
            if expected is not None:
                expected.close()

    def _verify_game_package_extraction_registry_commit(
        self,
        job: Mapping[str, Any],
        prepared: Sequence[PreparedCreationArtifact],
        publication: Mapping[str, Any],
        target_binding: Mapping[str, Any],
    ) -> None:
        if len(prepared) != 1:
            raise conflict("Game package extraction registry candidate set changed")
        candidate = prepared[0].document
        if candidate.get("format") != "world-forge.game_package_extraction":
            raise conflict("Game package extraction registry candidate format changed")
        source_binding = self.jobs.output_grants.published_binding(
            grant_id=str(job["operation_params"]["source_grant_id"]),
            workspace_id=str(job["workspace_id"]),
            expected_generation=int(job["operation_params"]["source_grant_generation"]),
        )
        source_identity = source_binding["published_identity"]
        target_identity = target_binding["published_identity"]
        if source_identity is None or target_identity is None:
            raise conflict("Game package extraction registry identity is unavailable")
        package = None
        try:
            package = verify_game_package(
                source_binding["path"],
                expected_file_identity=tuple(source_identity),
            )
            evidence = validate_game_package_extraction_evidence(
                candidate,
                package_manifest=package.manifest,
                archive_sha256=package.archive_sha256,
                archive_size_bytes=len(package.archive_bytes),
            )
            with verify_standalone_game(
                target_binding["path"],
                expected_content_hash=str(target_binding["expected_manifest_hash"]),
                expected_root_identity=tuple(target_identity),
            ) as standalone:
                expected_publication = {
                    "format": standalone.manifest["format"],
                    "format_version": standalone.manifest["format_version"],
                    "id": standalone.manifest["game_id"],
                    "content_hash": standalone.manifest["content_hash"],
                    "tree_hash": standalone.lock["tree_hash"],
                }
                if (
                    source_binding["expected_manifest_hash"] != package.manifest["content_hash"]
                    or source_binding["expected_archive_sha256"] != package.archive_sha256
                    or source_binding["expected_size_bytes"] != len(package.archive_bytes)
                    or standalone.manifest["content_hash"]
                    != evidence["standalone_game"]["content_hash"]
                    or standalone.lock["content_hash"] != evidence["payload_lock"]["content_hash"]
                    or standalone.lock["tree_hash"] != evidence["extracted_tree_hash"]
                    or standalone.manifest["lineage"] != evidence["lineage"]
                    or target_binding["expected_tree_hash"] != evidence["extracted_tree_hash"]
                    or dict(publication) != expected_publication
                ):
                    raise conflict("Game package extraction registry bytes changed")
        except (
            GamePackageExtractionEvidenceError,
            StandaloneGameError,
            WorldForgeGamePackageError,
            TypeError,
            ValueError,
        ) as exc:
            raise conflict("Game package extraction registry bytes are not integral") from exc
        finally:
            if package is not None:
                package.close()

    def _verify_runtime_headless_registry_commit(
        self,
        job: Mapping[str, Any],
        prepared: Sequence[PreparedCreationArtifact],
        publication: Mapping[str, Any],
        binding: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        artifact_root: Path,
    ) -> None:
        if len(prepared) != 3 or job["operation"] != "runtime.headless.verify":
            raise conflict("Runtime headless registry candidate set changed")
        expected, manifest = self._trusted_runtime_headless_outputs(
            job,
            request=request,
            artifact_root=artifact_root,
        )
        if tuple(item.document for item in prepared) != expected:
            raise conflict("Runtime headless registry authority documents changed")
        expected_publication = {
            "format": manifest["format"],
            "format_version": manifest["format_version"],
            "id": manifest["evidence_set_id"],
            "content_hash": manifest["content_hash"],
            "tree_hash": manifest["tree_hash"],
        }
        if (
            dict(publication) != expected_publication
            or binding["expected_manifest_hash"] != manifest["content_hash"]
            or binding["expected_tree_hash"] != manifest["tree_hash"]
            or binding["published_identity"] is None
        ):
            raise conflict("Runtime headless registry publication changed")
        source = self.jobs.output_grants.published_binding(
            grant_id=str(request["source_grant_id"]),
            workspace_id=str(job["workspace_id"]),
            expected_generation=int(request["source_grant_generation"]),
        )
        try:
            verified = verify_headless_evidence_set(
                binding["path"],
                bundle_root=source["path"],
                expected_content_hash=str(manifest["content_hash"]),
            )
            try:
                if (
                    verified.root_identity != tuple(binding["published_identity"])
                    or verified.manifest != manifest
                ):
                    raise conflict("Runtime headless registry visible evidence changed")
            finally:
                verified.close()
        except GenericHeadlessError as exc:
            raise conflict("Runtime headless registry authority changed") from exc

    def _commit_registry(
        self,
        job: Mapping[str, Any],
        prepared: Sequence[PreparedCreationArtifact],
        metadata: Mapping[str, Any],
        *,
        request: Mapping[str, Any],
        publication: Mapping[str, Any] | None,
        dependency_documents: Sequence[Mapping[str, Any]],
        artifact_root: Path,
        asset_publication_guard: Callable[[], AbstractContextManager[None]] | None,
        asset_process_retention: PreparedAssetProcessRetention | None,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            current = self.jobs.get(job["job_id"])
            if (
                current["generation"] != job["generation"]
                or current["progress"] != "registry_committing"
            ):
                raise conflict("Creation job changed before registry commit")
            if asset_publication_guard is not None:
                if job["operation"] != "asset.process":
                    raise conflict("Asset publication guard operation changed")
            trusted_release_outputs: (
                tuple[
                    dict[str, Any],
                    dict[str, Any],
                    dict[str, Any],
                ]
                | None
            ) = None
            if job["operation"] == "asset.release.authorize":
                if len(prepared) != 3:
                    raise conflict("Asset release authority candidate set changed")
                trusted_release_outputs, _trusted_reviews = (
                    self._trusted_asset_release_authorize_outputs(
                        job,
                        dependency_documents=dependency_documents,
                        artifact_root=artifact_root,
                        candidate_documents=(prepared[0].document, prepared[1].document),
                    )
                )
                expected_analysis = (
                    "passed" if trusted_release_outputs[2]["status"] == "authorized" else "failed"
                )
                if (
                    tuple(artifact.document for artifact in prepared) != trusted_release_outputs
                    or metadata.get("analysis_status") != expected_analysis
                    or list(metadata.get("reason_codes", ()))
                    != trusted_release_outputs[2]["blockers"]
                ):
                    raise conflict("Asset release trusted registry expectation changed")
            trusted_headless_outputs: (
                tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None
            ) = None
            trusted_headless_manifest: dict[str, Any] | None = None
            if job["operation"] == "runtime.headless.verify":
                trusted_headless_outputs, trusted_headless_manifest = (
                    self._trusted_runtime_headless_outputs(
                        job,
                        request=request,
                        artifact_root=artifact_root,
                    )
                )
                if (
                    tuple(artifact.document for artifact in prepared) != trusted_headless_outputs
                    or metadata.get("analysis_status") != "passed"
                    or list(metadata.get("reason_codes", ()))
                    != trusted_headless_outputs[2]["reason_codes"]
                    or publication is None
                ):
                    raise conflict("Runtime headless trusted registry expectation changed")
            self.jobs.artifacts.insert_prepared(job, prepared, created_at=timestamp)
            if asset_process_retention is not None:
                self.jobs.artifacts.insert_asset_process_retention(
                    job,
                    asset_process_retention,
                )
            elif job["operation"] == "asset.process" and metadata["analysis_status"] == "passed":
                raise conflict("Asset process retained binary evidence is unavailable")
            verified_review = None
            if job["operation"] == "asset.qa.review":
                if len(prepared) != 1:
                    raise conflict("Asset QA review candidate set changed")
                try:
                    verified_review = verify_asset_qa_review(
                        prepared[0].document,
                        resolver=StudioAssetAuthorityResolver(
                            self.store,
                            artifacts=self.jobs.artifacts,
                        ),
                    )
                except GenericAssetAuthorityError as exc:
                    raise conflict(
                        "Asset QA review retained authority could not be reverified"
                    ) from exc
            verified_release = None
            if job["operation"] == "asset.release.authorize":
                if len(prepared) != 3:
                    raise conflict("Asset release authority candidate set changed")
                try:
                    resolver = StudioAssetAuthorityResolver(
                        self.store,
                        artifacts=self.jobs.artifacts,
                    )
                    review_documents = [
                        self.jobs.artifacts.get_document(
                            str(job["workspace_id"]),
                            str(item["artifact_id"]),
                        )
                        for item in job["inputs"]
                    ]
                    review_handles = [
                        verify_asset_qa_review(document, resolver=resolver)
                        for document in review_documents
                    ]
                    verified_release = verify_asset_release_authority(
                        prepared[2].document,
                        manifest=prepared[0].document,
                        assetpack=prepared[1].document,
                        reviews=review_handles,
                        resolver=resolver,
                    )
                except (GenericAssetAuthorityError, StudioError) as exc:
                    raise conflict(
                        "Asset release retained authority could not be reverified"
                    ) from exc
            published_grant: dict[str, Any] | None = None
            publication_required = job["operation"] in {
                "asset.release.seal",
                "runtime.bundle.build",
                "runtime.headless.verify",
                "game.materialization.bundle.build",
                "game.materialize",
                "game.package",
                "game.package.extract",
            } or (
                job["operation"] == "asset.release.authorize"
                and trusted_release_outputs is not None
                and trusted_release_outputs[2]["status"] == "authorized"
            )
            if publication_required:
                if publication is None:
                    raise conflict("Directory publication evidence is unavailable")
                binding = self.jobs.output_grants.binding_for_job(
                    str(job["job_id"]),
                    allow_visible=True,
                )
                published_identity = binding["published_identity"]
                if published_identity is None:
                    raise conflict("Asset release publication identity is unavailable")
                transition_verifier = None
                if job["operation"] == "runtime.headless.verify":
                    self._verify_runtime_headless_registry_commit(
                        job,
                        prepared,
                        publication,
                        binding,
                        request=request,
                        artifact_root=artifact_root,
                    )

                    def verify_transition(current_binding):
                        self._verify_runtime_headless_registry_commit(
                            job,
                            prepared,
                            publication,
                            current_binding,
                            request=request,
                            artifact_root=artifact_root,
                        )

                    transition_verifier = verify_transition
                elif job["operation"] == "game.materialize":
                    self._verify_standalone_registry_commit(
                        job,
                        prepared,
                        publication,
                        binding,
                    )

                    def verify_transition(current_binding):
                        self._verify_standalone_registry_commit(
                            job,
                            prepared,
                            publication,
                            current_binding,
                        )

                    transition_verifier = verify_transition
                elif job["operation"] == "game.package":
                    self._verify_game_package_registry_commit(
                        job,
                        prepared,
                        publication,
                        binding,
                    )

                    def verify_transition(current_binding):
                        self._verify_game_package_registry_commit(
                            job,
                            prepared,
                            publication,
                            current_binding,
                        )

                    transition_verifier = verify_transition
                elif job["operation"] == "game.package.extract":
                    self._verify_game_package_extraction_registry_commit(
                        job,
                        prepared,
                        publication,
                        binding,
                    )

                    def verify_transition(current_binding):
                        self._verify_game_package_extraction_registry_commit(
                            job,
                            prepared,
                            publication,
                            current_binding,
                        )

                    transition_verifier = verify_transition
                published_grant = self.jobs.output_grants.mark_published(
                    str(job["job_id"]),
                    publication=publication,
                    published_identity=published_identity,
                    _verify_transition=transition_verifier,
                )
            elif publication is not None:
                raise conflict("Non-release creation job has publication evidence")
            after = self.jobs.evidence._snapshot(  # noqa: SLF001
                {
                    "workspace_id": job["workspace_id"],
                    "expected_root_generation": job["authority"]["root_generation"],
                    "expected_source_revision": job["authority"]["source_revision"],
                    "expected_workflow_status_hash": job["authority"]["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": None,
                }
            )
            result = {
                "output_artifact_ids": [artifact.artifact_id for artifact in prepared],
                "artifact_snapshot_hash": after["artifact_snapshot_hash"],
                "analysis_status": metadata["analysis_status"],
                "reason_codes": list(metadata.get("reason_codes", [])),
                "cleanup_pending": True,
            }
            if verified_review is not None:
                review_document = verified_review.document
                expected_analysis = (
                    "passed" if review_document["status"] == "approved" else "failed"
                )
                if metadata["analysis_status"] != expected_analysis or list(
                    metadata.get("reason_codes", [])
                ) != list(review_document["blockers"]):
                    raise conflict("Asset QA review worker metadata changed")
                result["review_receipt"] = {
                    "format": review_document["format"],
                    "format_version": review_document["format_version"],
                    "review_receipt_id": review_document["review_receipt_id"],
                    "content_hash": review_document["content_hash"],
                }
                result["review_status"] = review_document["status"]
            if verified_release is not None:
                release_document = verified_release.document
                if (
                    trusted_release_outputs is None
                    or release_document != trusted_release_outputs[2]
                ):
                    raise conflict("Asset release retained authority differs from expectation")
                expected_analysis = (
                    "passed" if trusted_release_outputs[2]["status"] == "authorized" else "failed"
                )
                if metadata["analysis_status"] != expected_analysis or list(
                    metadata.get("reason_codes", [])
                ) != list(trusted_release_outputs[2]["blockers"]):
                    raise conflict("Asset release authority worker metadata changed")
                result["asset_manifest"] = {
                    "manifest_id": prepared[0].document["manifest_id"],
                    "content_hash": prepared[0].document["content_hash"],
                }
                result["assetpack"] = {
                    "assetpack_id": prepared[1].document["assetpack_id"],
                    "content_hash": prepared[1].document["content_hash"],
                }
                result["asset_release_authority"] = {
                    "format": release_document["format"],
                    "format_version": release_document["format_version"],
                    "release_authority_id": release_document["release_authority_id"],
                    "content_hash": release_document["content_hash"],
                }
                result["release_status"] = trusted_release_outputs[2]["status"]
                result["publication"] = None
            if trusted_headless_outputs is not None:
                if trusted_headless_manifest is None:
                    raise conflict("Runtime headless retained manifest is unavailable")
                identities = [document_identity(document) for document in trusted_headless_outputs]
                result["runtime_support_authority"] = identities[0]
                result["runtime_evidence"] = identities[1]
                result["runtime_support_report"] = identities[2]
                result["release_status"] = "blocked"
                result["native_status"] = "unavailable"
                result["supported"] = False
            if published_grant is not None:
                payload_field = {
                    "asset.release.seal": "assetpack",
                    "asset.release.authorize": "assetpack",
                    "runtime.bundle.build": "runtime_bundle",
                    "runtime.headless.verify": "headless_evidence_set",
                    "game.materialization.bundle.build": "materialization_bundle",
                    "game.materialize": "standalone_game",
                    "game.package": "game_package",
                    "game.package.extract": "standalone_game",
                }[job["operation"]]
                result["publication"] = {
                    "grant_id": published_grant["grant_id"],
                    "grant_generation": published_grant["generation"],
                    "kind": published_grant["kind"],
                    "state": "published",
                    payload_field: copy.deepcopy(dict(publication)),
                }
            updated = self.jobs._updated_record(  # noqa: SLF001
                current,
                state="succeeded",
                progress="cleanup_pending",
                result=result,
                error=None,
                finished_at=timestamp,
                updated_at=timestamp,
            )
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET state = 'succeeded', progress = 'cleanup_pending', "
                "generation = ?, cancel_requested = 0, record_json = ? WHERE job_id = ? "
                "AND state = 'running' AND generation = ? AND cancel_requested = 0",
                (
                    updated["generation"],
                    encode_json(updated),
                    job["job_id"],
                    current["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job registry commit lost its CAS")
            attempt = self.store.connection.execute(
                "UPDATE creation_job_attempts SET phase = 'cleanup_pending', "
                "generation = generation + 1, updated_at = ? WHERE job_id = ? "
                "AND phase = 'registry_committing'",
                (timestamp, job["job_id"]),
            )
            if attempt.rowcount != 1:
                raise conflict("Creation job registry attempt lost its CAS")
            event_payload = {
                "generation": updated["generation"],
                "output_artifact_ids": result["output_artifact_ids"],
                "artifact_snapshot_hash": result["artifact_snapshot_hash"],
                "cleanup_pending": True,
            }
            if "publication" in result:
                event_payload["publication"] = copy.deepcopy(result["publication"])
            if "review_receipt" in result:
                event_payload["review_receipt"] = copy.deepcopy(result["review_receipt"])
                event_payload["review_status"] = result["review_status"]
            if "asset_release_authority" in result:
                for field in (
                    "asset_manifest",
                    "assetpack",
                    "asset_release_authority",
                    "release_status",
                    "publication",
                ):
                    event_payload[field] = copy.deepcopy(result[field])
            if "runtime_support_authority" in result:
                for field in (
                    "runtime_support_authority",
                    "runtime_evidence",
                    "runtime_support_report",
                    "release_status",
                    "native_status",
                    "supported",
                    "publication",
                ):
                    event_payload[field] = copy.deepcopy(result[field])
            self.store.record_creation_event(
                workspace_id=job["workspace_id"],
                topic="creation_job.succeeded",
                entity_type="creation_job",
                entity_id=job["job_id"],
                payload=event_payload,
                created_at=timestamp,
            )
            with nullcontext() if asset_publication_guard is None else asset_publication_guard():
                self.store.connection.commit()
            return updated
        except BaseException:
            self.store.connection.rollback()
            raise

    @staticmethod
    def _asset_stage_bindings(
        request: Mapping[str, Any],
        outputs: Sequence[VerifiedCreationOutput],
        *,
        artifact_root: Path | None = None,
    ) -> dict[str, tuple[str, int]]:
        if request["operation"] in {
            "asset.release.authorize",
            "asset.release.seal",
            "runtime.compose",
            "runtime.bundle.build",
            "game.materialization.bundle.build",
            "game.materialize",
            "game.package",
            "game.package.extract",
            "asset.qa.review",
            "runtime.headless.verify",
        }:
            bindings = {
                str(item["source_locator"]): (str(item["sha256"]), int(item["size_bytes"]))
                for item in request["staged_inputs"]
            }
            if request["operation"] == "runtime.headless.verify":
                if artifact_root is None:
                    raise invalid_state("Runtime headless cleanup root is unavailable")
                evidence_root = artifact_root / "headless-evidence"
                if not evidence_root.exists() and not evidence_root.is_symlink():
                    if outputs:
                        raise invalid_state("Runtime headless cleanup evidence is unavailable")
                    return bindings
                try:
                    verified = verify_headless_evidence_set(
                        evidence_root,
                        bundle_root=artifact_root / "runtime-bundle",
                    )
                    try:
                        for relative, payload in verified.files.items():
                            locator = f"headless-evidence/{relative}"
                            if locator in bindings:
                                raise invalid_state("Runtime headless cleanup locators overlap")
                            bindings[locator] = (
                                hashlib.sha256(payload).hexdigest(),
                                len(payload),
                            )
                    finally:
                        verified.close()
                except GenericHeadlessError as exc:
                    raise invalid_state(
                        "Runtime headless cleanup evidence is not integral"
                    ) from exc
            return bindings
        if request["operation"] != "asset.process":
            return {}
        bindings = {
            str(item["source_locator"]): (str(item["sha256"]), int(item["size_bytes"]))
            for item in request["staged_inputs"]
        }
        receipt_payloads = [
            output.payload
            for output in outputs
            if output.subject.get("format") == "world-forge.asset_processing_receipt"
            and output.payload
        ]
        if len(receipt_payloads) > 1:
            raise invalid_state("Creation job stage has multiple processing receipts")
        if not receipt_payloads:
            return bindings
        try:
            receipt = validate_asset_processing_receipt_document(
                decode_json_object(
                    receipt_payloads[0],
                    source="creation job staged processing receipt",
                )
            )
        except (RuntimeIOError, GenericAssetProcessingError, TypeError, ValueError) as exc:
            raise invalid_state("Creation job staged processing receipt is invalid") from exc
        records = (
            receipt["outputs"]
            if receipt["status"] == "completed"
            else receipt["recovery"]["retained_artifacts"]
        )
        for item in records:
            locator = str(item["locator"])
            binding = (str(item["sha256"]), int(item["size_bytes"]))
            if locator in bindings:
                raise invalid_state("Creation job staged asset locators overlap")
            bindings[locator] = binding
        return bindings

    def _cleanup_asset_root(
        self,
        root: Path,
        bindings: Mapping[str, tuple[str, int]],
        *,
        allow_missing: bool,
    ) -> None:
        try:
            root_info = path_file_stat(root)
        except FileNotFoundError:
            if allow_missing:
                return
            raise invalid_state("Creation job staged asset root is unavailable") from None
        if (
            stat.S_ISLNK(root_info.st_mode)
            or bool(getattr(root_info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
            or not stat.S_ISDIR(root_info.st_mode)
        ):
            raise invalid_state("Creation job staged asset root is unsafe")

        files: dict[str, tuple[Path, tuple[int, int]]] = {}
        pending = [root]
        while pending:
            current = pending.pop()
            try:
                entries = list(current.iterdir())
            except OSError as exc:
                raise invalid_state("Creation job staged asset tree cannot be inspected") from exc
            for entry in entries:
                try:
                    info = path_file_stat(entry)
                except OSError as exc:
                    raise invalid_state(
                        "Creation job staged asset entry cannot be inspected"
                    ) from exc
                if stat.S_ISLNK(info.st_mode) or bool(
                    getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                ):
                    raise invalid_state("Creation job staged asset tree contains a link")
                if stat.S_ISDIR(info.st_mode):
                    pending.append(entry)
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise invalid_state("Creation job staged asset entry is unsafe")
                locator = entry.relative_to(root).as_posix()
                files[locator] = (entry, (int(info.st_dev), int(info.st_ino)))

        actual = set(files)
        expected = set(bindings)
        if (not allow_missing and actual != expected) or not actual <= expected:
            raise invalid_state("Creation job staged asset entries changed before cleanup")
        for locator in sorted(actual, key=lambda item: item.encode("utf-8")):
            source, identity = files[locator]
            digest, size = bindings[locator]
            payload, confirmed_identity = _read_bound_file(source, limit=size)
            if (
                confirmed_identity != identity
                or len(payload) != size
                or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), digest)
            ):
                raise invalid_state("Creation job staged asset bytes changed before cleanup")

    def _verify_cleanup_stage(
        self,
        stage: Path,
        expected_identity: tuple[int, int],
        request_locator: str,
        request_sha256: str,
        outputs: Sequence[VerifiedCreationOutput],
        *,
        allow_missing: bool,
        binary_identity: tuple[int, int] | None = None,
    ) -> None:
        try:
            info = path_file_stat(stage)
        except FileNotFoundError:
            if allow_missing:
                return
            raise
        if (
            not stat.S_ISDIR(info.st_mode)
            or (int(info.st_dev), int(info.st_ino)) != expected_identity
        ):
            raise invalid_state("Creation job stage identity changed before cleanup")
        request_path = stage / f"{request_locator}.json"
        try:
            request_payload, _request_identity = _read_bound_file(
                request_path,
                limit=64 * 1024 * 1024,
            )
        except FileNotFoundError:
            if any(stage.iterdir()):
                raise invalid_state("Creation job request is unavailable before cleanup") from None
            request = None
        else:
            if not hmac.compare_digest(
                hashlib.sha256(request_payload).hexdigest(),
                request_sha256,
            ):
                raise invalid_state("Creation job request bytes changed before cleanup")
            try:
                request = validate_private_creation_request(
                    decode_json_object(
                        request_payload,
                        source="creation job cleanup request",
                    )
                )
            except (RuntimeIOError, TypeError, ValueError) as exc:
                raise invalid_state("Creation job cleanup request is invalid") from exc
        expected = {f"{request_locator}.json": (request_sha256, None)}
        expected.update(
            {f"{output.locator}.json": (output.sha256, output.file_identity) for output in outputs}
        )
        archive_size: int | None = None
        if request is not None and request["operation"] == "game.package":
            archive = request["archive_output"]
            archive_size = int(archive["size_bytes"])
            expected["game_package_archive.wfgame"] = (
                archive["sha256"],
                binary_identity,
            )
        asset_bindings = (
            {}
            if request is None
            else self._asset_stage_bindings(
                request,
                outputs,
                artifact_root=stage / "artifact_root",
            )
        )
        if asset_bindings:
            expected["artifact_root"] = ("", None)
        names = {entry.name for entry in stage.iterdir()}
        if (not allow_missing and names != set(expected)) or not names <= set(expected):
            raise invalid_state("Creation job stage entries changed before cleanup")
        if "artifact_root" in names:
            self._cleanup_asset_root(
                stage / "artifact_root",
                asset_bindings,
                allow_missing=allow_missing,
            )
            names.remove("artifact_root")
        for name in sorted(names, key=lambda item: item.encode("utf-8")):
            payload, identity = _read_bound_file(
                stage / name,
                limit=(
                    archive_size
                    if name == "game_package_archive.wfgame" and archive_size is not None
                    else 64 * 1024 * 1024
                ),
            )
            digest, expected_file_identity = expected[name]
            if (
                (name == "game_package_archive.wfgame" and expected_file_identity is None)
                or (
                    name == "game_package_archive.wfgame"
                    and archive_size is not None
                    and len(payload) != archive_size
                )
                or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), digest)
                or (expected_file_identity is not None and identity != expected_file_identity)
            ):
                raise invalid_state("Creation job stage file changed before cleanup")

    @staticmethod
    def _retained_stage_evidence_path(
        stage: Path,
        expected_identity: tuple[int, int],
    ) -> Path:
        token = hashlib.sha256(
            (f"{stage.name}\0{expected_identity[0]}\0{expected_identity[1]}").encode()
        ).hexdigest()
        return stage.parent / f".worldforge-retained-creation-stage-{token}"

    def _retire_completed_stage(
        self,
        stage: Path,
        expected_identity: tuple[int, int],
        verify: Callable[[Path], None],
    ) -> None:
        evidence = self._retained_stage_evidence_path(stage, expected_identity)
        try:
            parent_identity = directory_identity(
                stage.parent,
                context="creation job stage parent",
            )
            with publish_directory_noreplace(
                stage,
                evidence,
                expected_source_identity=expected_identity,
                expected_parent_identity=parent_identity,
            ) as published_identity:
                if published_identity != expected_identity:
                    raise invalid_state("Creation job retained stage evidence identity changed")
                verify(evidence)
        except (DirectoryPublishError, FileExistsError, OSError) as exc:
            raise invalid_state(
                "Creation job completed stage could not be retained as terminal evidence"
            ) from exc

    def _cleanup_stage(
        self,
        stage: Path,
        expected_identity: tuple[int, int],
        request_locator: str,
        request_sha256: str,
        outputs: Sequence[VerifiedCreationOutput],
        *,
        allow_missing: bool = False,
        allow_retained_terminal: bool = False,
        binary_identity: tuple[int, int] | None = None,
    ) -> None:
        self._verify_cleanup_stage(
            stage,
            expected_identity,
            request_locator,
            request_sha256,
            outputs,
            allow_missing=allow_missing,
            binary_identity=binary_identity,
        )

        def verify_owned(root: Path) -> None:
            self._verify_cleanup_stage(
                root,
                expected_identity,
                request_locator,
                request_sha256,
                outputs,
                allow_missing=allow_missing,
                binary_identity=binary_identity,
            )

        if sys.platform.startswith("linux") and os.name == "posix":
            if not allow_retained_terminal:
                raise conflict(
                    "Creation job recovery_required: the exact partial stage was retained "
                    "without pathname deletion",
                    recovery_evidence=retained_recovery_evidence(
                        stage_path=stage,
                        stage_identity=expected_identity,
                    ),
                )
            self._retire_completed_stage(stage, expected_identity, verify_owned)
            return

        try:
            quarantine_and_remove_verified_directory(
                stage,
                expected_identity,
                verify=verify_owned,
            )
        except DirectoryPublishError as exc:
            raise invalid_state("Creation job exact stage cleanup failed") from exc

    def _cleanup_empty_stage(
        self,
        stage: Path,
        expected_identity: tuple[int, int],
        *,
        allow_retained_terminal: bool,
    ) -> None:
        def verify_empty(root: Path) -> None:
            info = path_file_stat(root)
            if (
                not stat.S_ISDIR(info.st_mode)
                or (int(info.st_dev), int(info.st_ino)) != expected_identity
                or any(root.iterdir())
            ):
                raise invalid_state("Creation job empty stage changed before cleanup")

        verify_empty(stage)
        if sys.platform.startswith("linux") and os.name == "posix":
            if not allow_retained_terminal:
                raise conflict(
                    "Creation job recovery_required: the exact empty stage was retained "
                    "without pathname deletion",
                    recovery_evidence=retained_recovery_evidence(
                        stage_path=stage,
                        stage_identity=expected_identity,
                    ),
                )
            self._retire_completed_stage(stage, expected_identity, verify_empty)
            return
        try:
            remove_verified_empty_directory(stage, expected_identity)
        except DirectoryPublishError as exc:
            raise invalid_state("Creation job exact empty-stage cleanup failed") from exc

    def _complete_cleanup(self, job_id: str) -> dict[str, Any]:
        current = self.jobs.get(job_id)
        if current["state"] != "succeeded" or current["progress"] != "cleanup_pending":
            raise conflict("Creation job cleanup is not pending")
        result = copy.deepcopy(current["result"])
        result["cleanup_pending"] = False
        timestamp = utc_now()
        updated = self.jobs._updated_record(  # noqa: SLF001
            current,
            progress="committed",
            result=result,
            updated_at=timestamp,
        )
        with self.store.connection:
            deleted = self.store.connection.execute(
                "DELETE FROM creation_job_attempts WHERE job_id = ?", (job_id,)
            )
            if deleted.rowcount != 1:
                raise conflict("Creation job cleanup attempt changed")
            cursor = self.store.connection.execute(
                "UPDATE creation_jobs SET progress = 'committed', generation = ?, "
                "record_json = ? WHERE job_id = ? AND state = 'succeeded' "
                "AND progress = 'cleanup_pending' AND generation = ?",
                (
                    updated["generation"],
                    encode_json(updated),
                    job_id,
                    current["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation job cleanup completion lost its CAS")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic="creation_job.cleanup_completed",
                entity_type="creation_job",
                entity_id=job_id,
                payload={"generation": updated["generation"]},
                created_at=timestamp,
            )
        return updated

    def _finish_after_error(
        self,
        job_id: str,
        code: str,
        *,
        recovery_evidence: Mapping[str, object] | None = None,
    ) -> None:
        try:
            record = self.jobs.get(job_id)
        except StudioError:
            return
        if record["state"] != "running":
            return
        canceled = self.jobs.cancellation_requested(job_id)
        attempt = self.store.connection.execute(
            "SELECT * FROM creation_job_attempts WHERE job_id = ?", (job_id,)
        ).fetchone()
        attempt_evidence = self._attempt_recovery_evidence(job_id) if attempt is not None else {}
        combined_recovery_evidence = copy.deepcopy(attempt_evidence)
        if recovery_evidence:
            combined_recovery_evidence.update(copy.deepcopy(dict(recovery_evidence)))
        if combined_recovery_evidence:
            validate_studio_recovery_evidence(
                combined_recovery_evidence,
                "creation job recovery evidence",
            )

        def recovery_error(message: str) -> dict[str, Any]:
            error: dict[str, Any] = {
                "code": "recovery_required",
                "message": message,
                "retryable": True,
            }
            if combined_recovery_evidence:
                error["recovery_evidence"] = copy.deepcopy(combined_recovery_evidence)
            return error

        if (
            record["operation"] == "asset.process"
            and attempt is not None
            and attempt["phase"] in {"output_published", "registry_committing"}
            and self._asset_process_project_publication_may_exist(record, attempt)
        ):
            self.jobs.finish_terminal(
                job_id,
                state="orphaned",
                error=recovery_error(
                    "Processed asset project publication requires explicit recovery"
                ),
            )
            return

        has_release_authorize_reservation = (
            record["operation"] == "asset.release.authorize"
            and self.store.connection.execute(
                "SELECT 1 FROM creation_output_grants WHERE reserved_job_id = ?",
                (job_id,),
            ).fetchone()
            is not None
        )
        if (
            has_release_authorize_reservation
            or record["operation"]
            in {
                "asset.release.seal",
                "runtime.bundle.build",
                "runtime.headless.verify",
                "game.materialization.bundle.build",
                "game.materialize",
                "game.package",
                "game.package.extract",
            }
        ) and attempt is not None:
            try:
                binding = self.jobs.output_grants.binding_for_job(
                    job_id,
                    allow_visible=None,
                )
                recovery = binding["recovery"]
            except StudioError:
                recovery = None
            if recovery is not None:
                try:
                    with self.store.connection:
                        self.jobs.output_grants.mark_recovery_required(
                            job_id,
                            recovery=recovery,
                        )
                        self.jobs.finish_terminal(
                            job_id,
                            state="orphaned",
                            error=recovery_error("Artifact publication requires explicit recovery"),
                        )
                except StudioError as exc:
                    raise invalid_state(
                        "Artifact publication recovery state could not be committed atomically"
                    ) from exc
                return
        if has_release_authorize_reservation:
            try:
                binding = self.jobs.output_grants.binding_for_job(
                    job_id,
                    allow_visible=False,
                )
                if binding["recovery"] is None:
                    with self.store.connection:
                        self.jobs.output_grants.release_for_job(job_id)
                    has_release_authorize_reservation = False
            except StudioError:
                pass
        if code == "recovery_required" or recovery_evidence:
            self.jobs.finish_terminal(
                job_id,
                state="orphaned",
                error=recovery_error("Creation job requires explicit retained-evidence recovery"),
            )
            return
        cleanup_complete = attempt is None
        if attempt is not None:
            try:
                self._recover_cleanup_with_evidence(record)
                cleanup_complete = True
            except Exception:
                cleanup_complete = False
        if not cleanup_complete:
            self.jobs.finish_terminal(
                job_id,
                state="orphaned",
                error=recovery_error("Creation job cleanup requires explicit recovery"),
            )
            return
        if has_release_authorize_reservation or record["operation"] in {
            "asset.release.seal",
            "runtime.bundle.build",
            "runtime.headless.verify",
            "game.materialization.bundle.build",
            "game.materialize",
            "game.package",
            "game.package.extract",
        }:
            try:
                with self.store.connection:
                    self.jobs.output_grants.release_for_job(job_id)
            except StudioError:
                self.jobs.finish_terminal(
                    job_id,
                    state="orphaned",
                    error=recovery_error(
                        "Artifact publication reservation requires explicit recovery"
                    ),
                )
                return
        if canceled:
            state = "canceled"
            error = None
        else:
            state = "failed"
            normalized = (
                code
                if code
                in {
                    "authority_changed",
                    "input_changed",
                    "internal_error",
                    "invalid_artifact",
                    "invalid_project",
                    "timeout",
                    "worker_crashed",
                    "worker_protocol",
                }
                else "internal_error"
            )
            error = {
                "code": normalized,
                "message": "Creation job failed before registry commit",
                "retryable": normalized in {"timeout", "worker_crashed"},
            }
        self.jobs.finish_terminal(
            job_id,
            state=state,
            error=error,
            cleanup_attempt=attempt is not None,
        )


class CreationJobScheduler:
    """One durable FIFO creation-job executor using a thread-owned store."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        timeout_seconds: float = 60.0,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int | float):
            raise ValueError("creation job timeout must be numeric")
        if not 0.05 <= float(timeout_seconds) <= 3600.0:
            raise ValueError("creation job timeout is outside its fixed bounds")
        self.data_dir = Path(data_dir)
        self.timeout_seconds = float(timeout_seconds)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="studio-creation-job-scheduler",
        )
        self._startup_error: BaseException | None = None
        self._lifecycle_lock = threading.Lock()
        self._started = False
        self._shutdown = False

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._shutdown:
                raise invalid_state("Creation job scheduler cannot start after shutdown")
            if self._started:
                raise invalid_state("Creation job scheduler can start only once")
            self._started = True
            try:
                self._thread.start()
            except BaseException as exc:
                self._shutdown = True
                raise StudioError(
                    "internal_error", "Creation job scheduler could not start"
                ) from exc
        startup_error: StudioError | None = None
        if not self._ready.wait(timeout=5.0):
            startup_error = StudioError("internal_error", "Creation job scheduler did not start")
        elif self._startup_error is not None:
            startup_error = StudioError("internal_error", "Creation job scheduler could not start")
        if startup_error is not None:
            try:
                self.shutdown()
            except StudioError as exc:
                raise exc from startup_error
            raise startup_error from self._startup_error

    def notify(self) -> None:
        with self._lifecycle_lock:
            if self._shutdown:
                return
        self._wake.set()

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            self._shutdown = True
            started = self._started
        self._stop.set()
        self._wake.set()
        if started and self._thread.ident is not None:
            self._thread.join(timeout=10.0)
        if self._thread.is_alive():
            raise StudioError("internal_error", "Creation job scheduler did not stop cleanly")

    def _run(self) -> None:
        store: StudioStore | None = None
        try:
            store = StudioStore(self.data_dir, mode="secondary")
            grants = CreationRootGrantManager(store)
            workspaces = CreationWorkspaceManager(store, grants=grants)
            artifacts = CreationArtifactRegistry(store, workspaces=workspaces)
            evidence = CreationEvidenceManager(workspaces, candidates=artifacts)
            output_grants = CreationOutputGrantManager(store)
            jobs = CreationJobManager(
                store,
                workspaces=workspaces,
                evidence=evidence,
                artifacts=artifacts,
                output_grants=output_grants,
            )
            coordinator = CreationJobCoordinator(
                jobs,
                timeout_seconds=self.timeout_seconds,
                shutdown_requested=self._stop.is_set,
            )
        except BaseException as exc:
            self._startup_error = exc
            if store is not None:
                try:
                    store.close()
                except BaseException:
                    pass
            self._ready.set()
            return
        self._ready.set()
        try:
            while not self._stop.is_set():
                claimed = coordinator.run_once()
                if claimed is None:
                    self._wake.wait(timeout=0.1)
                    self._wake.clear()
        finally:
            store.close()
