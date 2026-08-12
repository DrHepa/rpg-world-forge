from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

from worldforge.generic_asset_authority import (
    AssetAuthorityResolver,
    GenericAssetAuthorityError,
    RetainedAssetQaReviewRecord,
    RetainedAssetReleaseAuthorityRecord,
    validate_asset_qa_review_receipt_document,
    validate_asset_release_authority_document,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.phase_report_v3 import artifact_dependency_identities
from worldforge.studio.creation_artifacts import CreationArtifactRegistry
from worldforge.studio.creation_evidence import CreationEvidenceManager
from worldforge.studio.storage import StudioStore


class StudioAssetAuthorityResolver(AssetAuthorityResolver):
    """Resolve retained Studio authority without exposing workspace or CAS paths."""

    def __init__(
        self,
        store: StudioStore,
        *,
        artifacts: CreationArtifactRegistry,
    ) -> None:
        if artifacts.store is not store:
            raise ValueError("Studio asset authority resolver store binding differs")
        self.store = store
        self.artifacts = artifacts
        self.evidence = CreationEvidenceManager(
            artifacts.workspaces,
            candidates=artifacts,
        )

    @staticmethod
    def _fail(code: str, detail: str) -> None:
        raise GenericAssetAuthorityError(code, detail)

    def _unique_artifact_row(
        self,
        *,
        subject_format: str,
        subject_id: str,
        content_hash: str,
    ) -> Any:
        rows = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE subject_format = ? "
            "AND subject_version = 1 AND subject_id = ? AND content_hash = ?",
            (subject_format, subject_id, content_hash),
        ).fetchall()
        if len(rows) != 1:
            self._fail(
                "authority_resolver_ambiguous",
                "retained authority identity is missing or crosses workspaces",
            )
        return rows[0]

    def _dependency_documents(
        self,
        row: Any,
        document: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        stored = self.artifacts._validated_row(row)  # noqa: SLF001
        expected = tuple(artifact_dependency_identities(document))
        if tuple(identity for _artifact_id, identity in stored.dependencies) != expected:
            self._fail(
                "asset_authority_binding_mismatch",
                "retained review dependency projection differs",
            )
        resolved: list[dict[str, Any]] = []
        for artifact_id, identity in stored.dependencies:
            dependency_row = self.store.connection.execute(
                "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
                (row["workspace_id"], artifact_id),
            ).fetchone()
            if dependency_row is None:
                self._fail(
                    "authority_resolver_invalid",
                    "retained review dependency is unavailable",
                )
            dependency = self.artifacts._validated_row(dependency_row)  # noqa: SLF001
            if dependency.record["subject"] != identity:
                self._fail(
                    "asset_authority_binding_mismatch",
                    "retained review dependency identity differs",
                )
            resolved.append(dependency.document)
        return tuple(resolved)

    def _require_current_workspace(self, row: Any) -> None:
        self.evidence._snapshot(  # noqa: SLF001
            {
                "workspace_id": row["workspace_id"],
                "expected_root_generation": int(row["root_generation"]),
                "expected_source_revision": row["source_revision"],
                "expected_workflow_status_hash": row["workflow_status_hash"],
                "expected_artifact_snapshot_hash": None,
            }
        )

    def _require_asset_process_producers(
        self,
        *,
        workspace_id: str,
        processing_receipt_id: str,
        qa_report_id: str,
    ) -> str:
        processing_row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, processing_receipt_id),
        ).fetchone()
        qa_row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, qa_report_id),
        ).fetchone()
        if processing_row is None or qa_row is None:
            self._fail("authority_resolver_invalid", "retained review source is unavailable")
        self.artifacts._validated_row(processing_row)  # noqa: SLF001
        self.artifacts._validated_row(qa_row)  # noqa: SLF001
        if (
            processing_row["producer_job_id"] != qa_row["producer_job_id"]
            or processing_row["producer_operation"] != "asset.process"
            or qa_row["producer_operation"] != "asset.process"
            or int(processing_row["producer_output_position"]) != 1
            or int(qa_row["producer_output_position"]) != 2
        ):
            self._fail(
                "asset_authority_binding_mismatch",
                "retained QA and processing receipt do not share one exact producer",
            )
        return str(qa_row["producer_job_id"])

    def resolve_asset_qa_review(
        self,
        *,
        review_receipt_id: str,
        content_hash: str,
    ) -> RetainedAssetQaReviewRecord:
        row = self._unique_artifact_row(
            subject_format="world-forge.asset_qa_review_receipt",
            subject_id=review_receipt_id,
            content_hash=content_hash,
        )
        try:
            stored = self.artifacts._validated_row(row)  # noqa: SLF001
            review = validate_asset_qa_review_receipt_document(stored.document)
            if (
                review["review_receipt_id"] != review_receipt_id
                or not hmac.compare_digest(review["content_hash"], content_hash)
                or row["producer_operation"] != "asset.qa.review"
                or int(row["producer_output_position"]) != 0
            ):
                self._fail(
                    "asset_authority_binding_mismatch",
                    "retained review producer binding differs",
                )
            dependencies = self._dependency_documents(row, review)
            by_format = {str(document["format"]): document for document in dependencies}
            specification = by_format["world-forge.asset_spec"]
            processing_receipt = by_format["world-forge.asset_processing_receipt"]
            qa_report = by_format["world-forge.asset_qa_report"]
            dependency_ids = {
                str(identity["format"]): artifact_id
                for artifact_id, identity in stored.dependencies
            }
            process_job_id = self._require_asset_process_producers(
                workspace_id=str(row["workspace_id"]),
                processing_receipt_id=dependency_ids["world-forge.asset_processing_receipt"],
                qa_report_id=dependency_ids["world-forge.asset_qa_report"],
            )
            retention = self.artifacts.load_asset_process_retention(
                workspace_id=str(row["workspace_id"]),
                producer_job_id=process_job_id,
            )
            reviewed = review["reviewed_output"]
            retained_matches = [
                output for output in retention["outputs"] if output["role"] == reviewed["role"]
            ]
            if len(retained_matches) != 1:
                self._fail(
                    "asset_authority_binding_mismatch",
                    "retained reviewed output role is unavailable",
                )
            retained_record = retained_matches[0]
            if any(
                retained_record[field] != reviewed[field]
                for field in (
                    "candidate_artifact_id",
                    "role",
                    "media_type",
                    "runtime_path",
                    "locator",
                    "sha256",
                    "size_bytes",
                )
            ):
                self._fail(
                    "asset_authority_binding_mismatch",
                    "retained reviewed output identity differs",
                )
            retained_bytes = self.artifacts.read_retained_asset_output(
                retention,
                role=str(reviewed["role"]),
            )
            self._require_current_workspace(row)
            document_bytes = canonical_json_bytes(review)
            if (
                len(document_bytes) != int(row["document_size"])
                or hashlib.sha256(document_bytes).hexdigest() != row["document_blob_sha256"]
            ):
                self._fail(
                    "asset_authority_cas_mismatch",
                    "retained review document CAS differs",
                )
            return RetainedAssetQaReviewRecord(
                document_bytes=document_bytes,
                document_blob_sha256=str(row["document_blob_sha256"]),
                document_size_bytes=int(row["document_size"]),
                specification_bytes=canonical_json_bytes(specification),
                processing_receipt_bytes=canonical_json_bytes(processing_receipt),
                qa_report_bytes=canonical_json_bytes(qa_report),
                retained_output_bytes=retained_bytes,
                retained_output_sha256=str(retained_record["sha256"]),
                retained_output_size_bytes=int(retained_record["size_bytes"]),
                workspace_id=str(row["workspace_id"]),
                root_generation=int(row["root_generation"]),
                source_revision=str(row["source_revision"]),
                workflow_status_hash=row["workflow_status_hash"],
                artifact_snapshot_hash=str(row["input_artifact_snapshot_hash"]),
                producer_job_id=str(row["producer_job_id"]),
                producer_operation=str(row["producer_operation"]),
                producer_output_position=int(row["producer_output_position"]),
            )
        except GenericAssetAuthorityError:
            raise
        except Exception as exc:
            self._fail("authority_resolver_failed", str(exc))

    def resolve_asset_release_authority(
        self,
        *,
        release_authority_id: str,
        content_hash: str,
    ) -> RetainedAssetReleaseAuthorityRecord:
        row = self._unique_artifact_row(
            subject_format="world-forge.asset_release_authority",
            subject_id=release_authority_id,
            content_hash=content_hash,
        )
        try:
            stored = self.artifacts._validated_row(row)  # noqa: SLF001
            release = validate_asset_release_authority_document(stored.document)
            if (
                release["release_authority_id"] != release_authority_id
                or not hmac.compare_digest(release["content_hash"], content_hash)
                or row["producer_operation"] != "asset.release.authorize"
                or int(row["producer_output_position"]) != 2
            ):
                self._fail(
                    "asset_authority_binding_mismatch",
                    "retained release producer binding differs",
                )
            dependencies = self._dependency_documents(row, release)
            expected_positions = {
                (
                    release["candidate_manifest"]["format"],
                    release["candidate_manifest"]["id"],
                    release["candidate_manifest"]["content_hash"],
                ): 0,
                (
                    release["candidate_assetpack"]["format"],
                    release["candidate_assetpack"]["id"],
                    release["candidate_assetpack"]["content_hash"],
                ): 1,
            }
            dependency_rows: dict[tuple[str, str, str], Any] = {}
            for artifact_id, identity in stored.dependencies:
                dependency_row = self.store.connection.execute(
                    "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
                    (row["workspace_id"], artifact_id),
                ).fetchone()
                if dependency_row is None:
                    self._fail(
                        "authority_resolver_invalid",
                        "retained release dependency is unavailable",
                    )
                dependency_rows[
                    (
                        str(identity["format"]),
                        str(identity["id"]),
                        str(identity["content_hash"]),
                    )
                ] = dependency_row
            for identity_key, position in expected_positions.items():
                candidate_row = dependency_rows.get(identity_key)
                if (
                    candidate_row is None
                    or candidate_row["producer_job_id"] != row["producer_job_id"]
                    or candidate_row["producer_operation"] != "asset.release.authorize"
                    or int(candidate_row["producer_output_position"]) != position
                ):
                    self._fail(
                        "asset_authority_binding_mismatch",
                        "retained release candidates do not share one exact producer",
                    )
            review_keys = {
                (
                    str(identity["format"]),
                    str(identity["id"]),
                    str(identity["content_hash"]),
                )
                for identity in release["qa_reviews"]
            }
            if set(dependency_rows) != set(expected_positions) | review_keys:
                self._fail(
                    "asset_authority_binding_mismatch",
                    "retained release dependency closure is incomplete or extra",
                )
            for review_key in review_keys:
                review_row = dependency_rows.get(review_key)
                if (
                    review_row is None
                    or review_row["producer_operation"] != "asset.qa.review"
                    or int(review_row["producer_output_position"]) != 0
                ):
                    self._fail(
                        "asset_authority_binding_mismatch",
                        "retained release review producer differs",
                    )
            if len(dependencies) != len(dependency_rows):
                self._fail(
                    "asset_authority_binding_mismatch",
                    "retained release dependencies are not unique",
                )
            self._require_current_workspace(row)
            document_bytes = canonical_json_bytes(release)
            if (
                len(document_bytes) != int(row["document_size"])
                or hashlib.sha256(document_bytes).hexdigest() != row["document_blob_sha256"]
            ):
                self._fail(
                    "asset_authority_cas_mismatch",
                    "retained release document CAS differs",
                )
            return RetainedAssetReleaseAuthorityRecord(
                document_bytes=document_bytes,
                document_blob_sha256=str(row["document_blob_sha256"]),
                document_size_bytes=int(row["document_size"]),
                workspace_id=str(row["workspace_id"]),
                root_generation=int(row["root_generation"]),
                source_revision=str(row["source_revision"]),
                workflow_status_hash=row["workflow_status_hash"],
                artifact_snapshot_hash=str(row["input_artifact_snapshot_hash"]),
                producer_job_id=str(row["producer_job_id"]),
                producer_operation=str(row["producer_operation"]),
                producer_output_position=int(row["producer_output_position"]),
            )
        except GenericAssetAuthorityError:
            raise
        except Exception as exc:
            self._fail("authority_resolver_failed", str(exc))
