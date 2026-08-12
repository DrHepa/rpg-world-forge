from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from worldforge.game_runtime_bundle import (
    GameRuntimeBundleError,
    verify_game_runtime_bundle,
)
from worldforge.generic_asset_authority import (
    GenericAssetAuthorityError,
    verify_asset_qa_review,
    verify_asset_release_authority,
)
from worldforge.generic_assetpack import GenericAssetpackError, verify_generic_assetpack
from worldforge.generic_headless import GenericHeadlessError, verify_headless_evidence_set
from worldforge.phase_report_v3 import document_identity
from worldforge.runtime_support_authority import (
    RuntimeSupportAuthorityError,
    VerifiedRuntimeSupportAuthority,
    attach_verified_headless_evidence,
    derive_runtime_evidence,
    derive_runtime_support_report,
    initialize_runtime_support_authority,
)
from worldforge.studio.contracts import StudioContractError, validate_studio_creation_job
from worldforge.studio.creation_artifacts import CreationArtifactRegistry
from worldforge.studio.creation_asset_authority import StudioAssetAuthorityResolver
from worldforge.studio.creation_output_grants import CreationOutputGrantManager
from worldforge.studio.errors import StudioError, invalid_state
from worldforge.studio.storage import StudioStore, decode_object


@dataclass(frozen=True, slots=True)
class ReconstructedRuntimeHeadlessAuthority:
    """Exact retained v12 authority rebuilt from CAS and published trees."""

    authority: VerifiedRuntimeSupportAuthority
    documents: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
    evidence_manifest: dict[str, Any]
    publication: dict[str, Any]


class _RetainedRuntimeAssetAuthorityResolver(StudioAssetAuthorityResolver):
    """Verify immutable v11 authority without re-entering workspace snapshots."""

    def __init__(
        self,
        store: StudioStore,
        *,
        artifacts: CreationArtifactRegistry,
        workspace_id: str,
    ) -> None:
        super().__init__(store, artifacts=artifacts)
        self._workspace_id = workspace_id

    def _require_current_workspace(self, row: Any) -> None:
        if str(row["workspace_id"]) != self._workspace_id:
            self._fail(
                "asset_authority_binding_mismatch",
                "retained runtime authority crosses workspaces",
            )


class StudioRuntimeAuthorityResolver:
    """Reconstruct v12 runtime authority without trusting retained claim JSON."""

    def __init__(
        self,
        store: StudioStore,
        *,
        artifacts: CreationArtifactRegistry,
    ) -> None:
        if artifacts.store is not store:
            raise ValueError("Studio runtime authority resolver store binding differs")
        self.store = store
        self.artifacts = artifacts
        self.output_grants = CreationOutputGrantManager(store)

    @staticmethod
    def _fail(detail: str) -> None:
        raise invalid_state(f"Stored runtime headless authority diverged: {detail}")

    @staticmethod
    def _identity_key(value: Mapping[str, Any]) -> tuple[str, int, str, str]:
        return (
            str(value["format"]),
            int(value["format_version"]),
            str(value["id"]),
            str(value["content_hash"]),
        )

    def _artifact(self, workspace_id: str, artifact_id: str) -> tuple[Any, Any]:
        row = self.store.connection.execute(
            "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
            (workspace_id, artifact_id),
        ).fetchone()
        if row is None:
            self._fail("retained input artifact is unavailable")
        return row, self.artifacts._validated_row(row)  # noqa: SLF001

    def _producer(self, producer_job_id: str) -> tuple[Any, dict[str, Any]]:
        row = self.store.connection.execute(
            "SELECT * FROM creation_jobs WHERE job_id = ?",
            (producer_job_id,),
        ).fetchone()
        if row is None:
            self._fail("retained producer is unavailable")
        try:
            record = validate_studio_creation_job(
                decode_object(row["record_json"], context="runtime authority producer")
            )
        except StudioContractError as exc:
            self._fail(f"retained producer is invalid: {exc}")
        if (
            record["job_id"] != row["job_id"]
            or record["workspace_id"] != row["workspace_id"]
            or record["operation"] != row["operation"]
            or record["state"] != row["state"]
            or record["progress"] != row["progress"]
            or int(record["generation"]) != int(row["generation"])
        ):
            self._fail("retained producer projection differs")
        return row, record

    def _input_documents(
        self,
        job: Mapping[str, Any],
    ) -> tuple[tuple[Any, ...], tuple[dict[str, Any], ...]]:
        workspace_id = str(job["workspace_id"])
        projected = self.store.connection.execute(
            "SELECT * FROM creation_job_inputs WHERE job_id = ? ORDER BY position",
            (job["job_id"],),
        ).fetchall()
        if len(projected) != len(job["inputs"]):
            self._fail("retained input projection is incomplete")
        rows: list[Any] = []
        documents: list[dict[str, Any]] = []
        for position, (projection, expected) in enumerate(
            zip(projected, job["inputs"], strict=True)
        ):
            if (
                int(projection["position"]) != position
                or projection["artifact_id"] != expected["artifact_id"]
                or {
                    "format": projection["subject_format"],
                    "format_version": int(projection["subject_version"]),
                    "id": projection["subject_id"],
                    "content_hash": projection["content_hash"],
                }
                != expected["subject"]
            ):
                self._fail("retained input projection differs")
            row, stored = self._artifact(workspace_id, str(expected["artifact_id"]))
            if stored.record["subject"] != expected["subject"]:
                self._fail("retained input subject differs")
            rows.append(row)
            documents.append(stored.document)
        return tuple(rows), tuple(documents)

    def _verified_asset_release(
        self,
        *,
        workspace_id: str,
        assetpack_row: Any,
        release_row: Any,
        assetpack: Mapping[str, Any],
        release: Mapping[str, Any],
    ) -> tuple[Any, Any, dict[str, Any]]:
        if (
            assetpack_row["producer_job_id"] != release_row["producer_job_id"]
            or assetpack_row["producer_operation"] != "asset.release.authorize"
            or release_row["producer_operation"] != "asset.release.authorize"
            or int(assetpack_row["producer_output_position"]) != 1
            or int(release_row["producer_output_position"]) != 2
        ):
            self._fail("assetpack and release authority do not share exact v11 authority")
        _producer_row, producer = self._producer(str(release_row["producer_job_id"]))
        result = producer["result"]
        if (
            producer["format_version"] != 11
            or producer["operation"] != "asset.release.authorize"
            or producer["state"] != "succeeded"
            or producer["progress"] not in {"committed", "cleanup_pending"}
            or result is None
            or result["release_status"] != "authorized"
            or result["publication"] is None
            or result["output_artifact_ids"][1] != assetpack_row["artifact_id"]
            or result["output_artifact_ids"][2] != release_row["artifact_id"]
        ):
            self._fail("asset release producer is not authorized v11 authority")

        stored_release = self.artifacts._validated_row(release_row)  # noqa: SLF001
        dependency_documents = {
            self._identity_key(identity): self._artifact(workspace_id, artifact_id)[1].document
            for artifact_id, identity in stored_release.dependencies
        }
        manifest_identity = release["candidate_manifest"]
        manifest = dependency_documents.get(self._identity_key(manifest_identity))
        if manifest is None:
            self._fail("retained release manifest is unavailable")
        resolver = _RetainedRuntimeAssetAuthorityResolver(
            self.store,
            artifacts=self.artifacts,
            workspace_id=workspace_id,
        )
        reviews = []
        for identity in release["qa_reviews"]:
            review = dependency_documents.get(self._identity_key(identity))
            if review is None:
                self._fail("retained release review is unavailable")
            reviews.append(verify_asset_qa_review(review, resolver=resolver))
        release_handle = verify_asset_release_authority(
            release,
            manifest=manifest,
            assetpack=assetpack,
            reviews=reviews,
            resolver=resolver,
        )

        publication = result["publication"]
        grant = self.output_grants.get(str(publication["grant_id"]))
        binding = self.output_grants.published_binding(
            grant_id=str(publication["grant_id"]),
            workspace_id=workspace_id,
            expected_generation=int(publication["grant_generation"]),
        )
        if grant["kind"] != "generic_assetpack_directory":
            self._fail("retained assetpack publication kind differs")
        assetpack_handle = verify_generic_assetpack(
            binding["path"],
            expected_content_hash=str(assetpack["content_hash"]),
        )
        if assetpack_handle.manifest != assetpack or assetpack_handle.root_identity != tuple(
            binding["published_identity"]
        ):
            assetpack_handle.close()
            self._fail("retained assetpack publication identity differs")
        return assetpack_handle, release_handle, publication

    def reconstruct(
        self,
        *,
        job: Mapping[str, Any],
        retained_documents: Sequence[Mapping[str, Any]],
    ) -> ReconstructedRuntimeHeadlessAuthority:
        """Rebuild one succeeded v12 result from exact inputs and visible grants."""

        try:
            if (
                job.get("format_version") != 12
                or job.get("operation") != "runtime.headless.verify"
                or job.get("state") != "succeeded"
                or job.get("progress") not in {"committed", "cleanup_pending"}
                or job.get("result") is None
                or len(retained_documents) != 3
            ):
                self._fail("retained job is not a completed v12 authority job")
            output_documents = tuple(copy.deepcopy(dict(item)) for item in retained_documents)
            rows, inputs = self._input_documents(job)
            if len(inputs) != 9:
                self._fail("retained v12 input closure is not exact")
            params = job["operation_params"]
            expected_ids = (
                params["gamepack_artifact_id"],
                params["asset_inventory_artifact_id"],
                params["assetpack_artifact_id"],
                params["asset_release_authority_artifact_id"],
                params["runtime_snapshot_artifact_id"],
                params["runtime_adapter_registry_artifact_id"],
                params["runtime_composition_artifact_id"],
                params["runtime_bundle_artifact_id"],
                params["headless_script_artifact_id"],
            )
            if tuple(item["artifact_id"] for item in job["inputs"]) != expected_ids:
                self._fail("retained v12 input order differs")

            workspace_id = str(job["workspace_id"])
            assetpack_handle = None
            runtime_handle = None
            evidence_handle = None
            try:
                assetpack_handle, release_handle, _asset_publication = self._verified_asset_release(
                    workspace_id=workspace_id,
                    assetpack_row=rows[2],
                    release_row=rows[3],
                    assetpack=inputs[2],
                    release=inputs[3],
                )

                runtime_row = rows[7]
                if (
                    runtime_row["producer_operation"] != "runtime.bundle.build"
                    or int(runtime_row["producer_output_position"]) != 0
                ):
                    self._fail("retained runtime bundle is not exact v5 authority")
                _runtime_producer_row, runtime_producer = self._producer(
                    str(runtime_row["producer_job_id"])
                )
                runtime_result = runtime_producer["result"]
                if (
                    runtime_producer["format_version"] != 5
                    or runtime_producer["operation"] != "runtime.bundle.build"
                    or runtime_producer["state"] != "succeeded"
                    or runtime_result is None
                    or runtime_result["publication"] is None
                    or runtime_result["publication"]["grant_id"] != params["source_grant_id"]
                    or runtime_result["publication"]["grant_generation"]
                    != params["expected_source_grant_generation"]
                ):
                    self._fail("retained runtime bundle publication differs")
                source_binding = self.output_grants.published_binding(
                    grant_id=str(params["source_grant_id"]),
                    workspace_id=workspace_id,
                    expected_generation=int(params["expected_source_grant_generation"]),
                )
                source_grant = self.output_grants.get(str(params["source_grant_id"]))
                if source_grant["kind"] != "game_runtime_bundle_directory":
                    self._fail("retained runtime bundle grant kind differs")
                runtime_handle = verify_game_runtime_bundle(
                    source_binding["path"],
                    expected_content_hash=str(inputs[7]["content_hash"]),
                )
                if runtime_handle.manifest != inputs[7] or runtime_handle.root_identity != tuple(
                    source_binding["published_identity"]
                ):
                    self._fail("retained runtime bundle publication identity differs")

                publication = job["result"]["publication"]
                target_binding = self.output_grants.published_binding(
                    grant_id=str(params["target_grant_id"]),
                    workspace_id=workspace_id,
                    expected_generation=int(publication["grant_generation"]),
                )
                target_grant = self.output_grants.get(str(params["target_grant_id"]))
                if (
                    publication["grant_id"] != params["target_grant_id"]
                    or publication["kind"] != "headless_evidence_directory"
                    or target_grant["kind"] != "headless_evidence_directory"
                    or int(publication["grant_generation"])
                    != int(params["expected_target_grant_generation"]) + 2
                ):
                    self._fail("retained headless publication generation differs")
                evidence_handle = verify_headless_evidence_set(
                    target_binding["path"],
                    bundle_root=source_binding["path"],
                    expected_content_hash=str(publication["headless_evidence_set"]["content_hash"]),
                )
                manifest = evidence_handle.manifest
                if (
                    evidence_handle.root_identity != tuple(target_binding["published_identity"])
                    or manifest["runtime_bundle"] != document_identity(inputs[7])
                    or manifest["execution_script"] != document_identity(inputs[8])
                    or manifest["runtime_evidence"]["platform"]["platform_id"]
                    != params["platform_id"]
                    or publication["headless_evidence_set"]
                    != {
                        "format": manifest["format"],
                        "format_version": manifest["format_version"],
                        "id": manifest["evidence_set_id"],
                        "content_hash": manifest["content_hash"],
                        "tree_hash": manifest["tree_hash"],
                    }
                ):
                    self._fail("retained headless evidence lineage differs")

                authority = initialize_runtime_support_authority(
                    gamepack=inputs[0],
                    inventory=inputs[1],
                    composition=inputs[6],
                    registry=inputs[5],
                    snapshot=inputs[4],
                    verified_assetpack=assetpack_handle,
                    asset_release_authority=release_handle,
                )
                authority = attach_verified_headless_evidence(
                    authority,
                    evidence_handle,
                    bundle_root=source_binding["path"],
                )
                evidence = derive_runtime_evidence(authority)
                support = derive_runtime_support_report(authority)
                expected_documents = (authority.document, evidence[0], support)
                if (
                    len(evidence) != 1
                    or output_documents != expected_documents
                    or job["result"]["runtime_support_authority"]
                    != document_identity(expected_documents[0])
                    or job["result"]["runtime_evidence"] != document_identity(expected_documents[1])
                    or job["result"]["runtime_support_report"]
                    != document_identity(expected_documents[2])
                    or job["result"]["release_status"] != "blocked"
                    or job["result"]["native_status"] != "unavailable"
                    or job["result"]["supported"] is not False
                    or expected_documents[0]["release_status"] != "blocked"
                    or expected_documents[0]["native_status"] != "unavailable"
                    or expected_documents[0]["supported"] is not False
                ):
                    self._fail("retained authority documents differ from reconstruction")
                return ReconstructedRuntimeHeadlessAuthority(
                    authority=authority,
                    documents=expected_documents,
                    evidence_manifest=copy.deepcopy(manifest),
                    publication=copy.deepcopy(publication),
                )
            finally:
                if evidence_handle is not None:
                    evidence_handle.close()
                if runtime_handle is not None:
                    runtime_handle.close()
                if assetpack_handle is not None:
                    assetpack_handle.close()
        except StudioError:
            raise
        except (
            GameRuntimeBundleError,
            GenericAssetAuthorityError,
            GenericAssetpackError,
            GenericHeadlessError,
            RuntimeSupportAuthorityError,
            StudioContractError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            self._fail(str(exc))


__all__ = [
    "ReconstructedRuntimeHeadlessAuthority",
    "StudioRuntimeAuthorityResolver",
]
