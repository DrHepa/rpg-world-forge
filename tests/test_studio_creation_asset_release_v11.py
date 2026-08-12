from __future__ import annotations

import copy
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worldforge.integrity import canonical_payload_hash

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _v11_job(*, release_status: str) -> dict[str, object]:
    published = release_status == "authorized"
    record: dict[str, object] = {
        "format": "world-forge.studio_creation_job",
        "format_version": 11,
        "job_id": "job_authorize_asset_release",
        "workspace_id": "workspace_01",
        "operation": "asset.release.authorize",
        "operation_params": {
            "review_receipt_artifact_ids": ["artifact_review_01"],
            "manifest_id": "manifest_01",
            "assetpack_id": "assetpack_01",
            "release_authority_id": "release_authority_01",
            "blockers": [] if published else ["release_blocked"],
            "target_grant_id": "grant_assetpack_01",
            "target_grant_generation": 4 if published else 3,
        },
        "state": "succeeded",
        "generation": 5,
        "authority": {
            "root_generation": 3,
            "source_revision": HASH_A,
            "workflow_status_hash": HASH_B,
            "artifact_snapshot_hash": HASH_C,
        },
        "inputs": [],
        "progress": "committed",
        "result": {
            "output_artifact_ids": [
                "artifact_manifest_01",
                "artifact_assetpack_01",
                "artifact_release_authority_01",
            ],
            "artifact_snapshot_hash": HASH_A,
            "analysis_status": "passed" if published else "failed",
            "reason_codes": [] if published else ["release_blocked"],
            "cleanup_pending": False,
            "asset_manifest": {"manifest_id": "manifest_01", "content_hash": HASH_A},
            "assetpack": {"assetpack_id": "assetpack_01", "content_hash": HASH_B},
            "asset_release_authority": {
                "format": "world-forge.asset_release_authority",
                "format_version": 1,
                "release_authority_id": "release_authority_01",
                "content_hash": HASH_C,
            },
            "release_status": release_status,
            "publication": (
                {
                    "grant_id": "grant_assetpack_01",
                    "grant_generation": 4,
                    "kind": "generic_assetpack_directory",
                    "state": "published",
                    "assetpack": {
                        "format": "world-forge.assetpack",
                        "format_version": 1,
                        "id": "assetpack_01",
                        "content_hash": HASH_B,
                        "inventory_hash": HASH_C,
                    },
                }
                if published
                else None
            ),
        },
        "error": None,
        "created_at": "2026-08-08T00:00:00Z",
        "started_at": "2026-08-08T00:00:01Z",
        "finished_at": "2026-08-08T00:00:02Z",
        "updated_at": "2026-08-08T00:00:02Z",
        "record_hash": "",
    }
    record["record_hash"] = canonical_payload_hash(record, hash_field="record_hash")
    return record


class StudioCreationAssetReleaseV11ContractTests(unittest.TestCase):
    def test_v11_requires_target_grant_and_has_conditional_publication(self) -> None:
        from worldforge.studio.contracts import (
            validate_studio_creation_job,
            validate_studio_protocol_envelope,
        )

        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 5,
            "kind": "request",
            "request_id": "authorize_release",
            "method": "creation_job.create",
            "params": {
                "workspace_id": "workspace_01",
                "operation": "asset.release.authorize",
                "expected_root_generation": 3,
                "expected_source_revision": HASH_A,
                "expected_workflow_status_hash": HASH_B,
                "expected_artifact_snapshot_hash": HASH_C,
                "review_receipt_artifact_ids": ["artifact_review_01"],
                "manifest_id": "manifest_01",
                "assetpack_id": "assetpack_01",
                "release_authority_id": "release_authority_01",
                "blockers": [],
                "target_grant_id": "grant_assetpack_01",
                "expected_target_grant_generation": 3,
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        for forbidden in (
            "status",
            "content_hash",
            "path",
            "runtime_path",
            "command",
            "provider",
            "env",
            "script",
        ):
            leaked = copy.deepcopy(request)
            leaked["params"][forbidden] = "caller-controlled"
            with (
                self.subTest(forbidden=forbidden),
                self.assertRaisesRegex(
                    ValueError,
                    "invalid fields",
                ),
            ):
                validate_studio_protocol_envelope(leaked)
        for missing in ("target_grant_id", "expected_target_grant_generation"):
            invalid = copy.deepcopy(request)
            del invalid["params"][missing]
            with (
                self.subTest(missing=missing),
                self.assertRaisesRegex(ValueError, "invalid fields"),
            ):
                validate_studio_protocol_envelope(invalid)

        authorized = _v11_job(release_status="authorized")
        blocked = _v11_job(release_status="blocked")
        self.assertEqual(authorized, validate_studio_creation_job(authorized))
        self.assertEqual(blocked, validate_studio_creation_job(blocked))

        blocked_with_publication = copy.deepcopy(blocked)
        blocked_with_publication["result"]["publication"] = authorized["result"]["publication"]
        blocked_with_publication["record_hash"] = canonical_payload_hash(
            blocked_with_publication,
            hash_field="record_hash",
        )
        with self.assertRaisesRegex(ValueError, "publication|blocked"):
            validate_studio_creation_job(blocked_with_publication)

        authorized_without_publication = copy.deepcopy(authorized)
        authorized_without_publication["result"]["publication"] = None
        authorized_without_publication["record_hash"] = canonical_payload_hash(
            authorized_without_publication,
            hash_field="record_hash",
        )
        with self.assertRaisesRegex(ValueError, "publication|authorized"):
            validate_studio_creation_job(authorized_without_publication)

        wrong_output_count = copy.deepcopy(authorized)
        wrong_output_count["result"]["output_artifact_ids"].pop()
        wrong_output_count["record_hash"] = canonical_payload_hash(
            wrong_output_count,
            hash_field="record_hash",
        )
        with self.assertRaisesRegex(ValueError, "three|3|output"):
            validate_studio_creation_job(wrong_output_count)

        authorized_failed = copy.deepcopy(authorized)
        authorized_failed["result"]["analysis_status"] = "failed"
        authorized_failed["result"]["reason_codes"] = ["release_blocked"]
        authorized_failed["record_hash"] = canonical_payload_hash(
            authorized_failed,
            hash_field="record_hash",
        )
        with self.assertRaisesRegex(ValueError, "analysis|reason|authorized"):
            validate_studio_creation_job(authorized_failed)

        blocked_passed = copy.deepcopy(blocked)
        blocked_passed["result"]["analysis_status"] = "passed"
        blocked_passed["result"]["reason_codes"] = []
        blocked_passed["record_hash"] = canonical_payload_hash(
            blocked_passed,
            hash_field="record_hash",
        )
        with self.assertRaisesRegex(ValueError, "analysis|reason|blocked"):
            validate_studio_creation_job(blocked_passed)

        v4 = copy.deepcopy(request)
        v4["protocol_version"] = 4
        with self.assertRaisesRegex(ValueError, "invalid fields|operation is unknown"):
            validate_studio_protocol_envelope(v4)

    def test_private_worker_v11_is_closed_and_operation_discriminated(self) -> None:
        from worldforge.studio.contracts import validate_studio_creation_worker_envelope

        request = {
            "format": "world-forge.studio_creation_worker",
            "format_version": 11,
            "kind": "request",
            "job_id": "job_authorize_asset_release",
            "operation": "asset.release.authorize",
            "request_locator": "request_" + "a" * 32,
            "request_sha256": HASH_B,
        }
        self.assertEqual(request, validate_studio_creation_worker_envelope(request))
        wrong_version = {**request, "format_version": 10}
        with self.assertRaisesRegex(ValueError, "operation|version"):
            validate_studio_creation_worker_envelope(wrong_version)


class StudioCreationAssetReleaseV11DispatchTests(unittest.TestCase):
    def test_public_manager_routes_v11_to_closed_parameter_validation(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                with self.assertRaisesRegex(StudioError, "invalid fields"):
                    service.creation_jobs.create(
                        {
                            "workspace_id": workspace["workspace_id"],
                            "operation": "asset.release.authorize",
                        }
                    )
            finally:
                service.close()
                service.store.close()


def _snapshot(service: object, workspace: dict[str, object]) -> dict[str, object]:
    return service.creation_evidence.list(
        {
            "workspace_id": workspace["workspace_id"],
            "expected_root_generation": workspace["root_generation"],
            "expected_source_revision": workspace["source_revision"],
            "expected_workflow_status_hash": workspace["workflow_status_hash"],
            "expected_artifact_snapshot_hash": None,
            "lifecycle": None,
            "cursor": None,
            "limit": 128,
        }
    )


def _review_processed_outputs(
    service: object,
    workspace: dict[str, object],
    qa_artifact_ids: list[str],
) -> list[str]:
    review_artifact_ids: list[str] = []
    for index, qa_artifact_id in enumerate(qa_artifact_ids):
        qa_report = service.creation_artifacts.get_document(
            workspace["workspace_id"],
            qa_artifact_id,
        )
        current = _snapshot(service, workspace)
        queued = service.creation_jobs.create_asset_qa_review(
            {
                "job_id": f"job_review_release_{index}",
                "workspace_id": workspace["workspace_id"],
                "operation": "asset.qa.review",
                "expected_root_generation": workspace["root_generation"],
                "expected_source_revision": workspace["source_revision"],
                "expected_workflow_status_hash": workspace["workflow_status_hash"],
                "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                "qa_report_artifact_id": qa_artifact_id,
                "output_role": qa_report["outputs"][0]["role"],
                "review_receipt_id": f"review_release_{index}",
                "decisions": ["approved" for _criterion in qa_report["acceptance_criteria"]],
                "blockers": [],
            }
        )
        if service.creation_job_coordinator.run_once() != queued["job_id"]:
            raise AssertionError("review job was not executed")
        completed = service.creation_jobs.get(queued["job_id"])
        if completed["state"] != "succeeded":
            raise AssertionError(completed)
        review_artifact_ids.extend(completed["result"]["output_artifact_ids"])
    return sorted(review_artifact_ids, key=lambda item: item.encode("utf-8"))


def _release_candidates(
    service: object,
    workspace: dict[str, object],
    review_artifact_ids: list[str],
    *,
    manifest_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    from worldforge.generic_asset_authority import verify_asset_qa_review
    from worldforge.generic_asset_processing import build_asset_manifest
    from worldforge.generic_assetpack import build_generic_assetpack_manifest
    from worldforge.studio.creation_asset_authority import StudioAssetAuthorityResolver
    from worldforge.studio.creation_job_protocol import _asset_release_lineage

    current = service.creation_jobs.evidence._snapshot(  # noqa: SLF001
        {
            "workspace_id": workspace["workspace_id"],
            "expected_root_generation": workspace["root_generation"],
            "expected_source_revision": workspace["source_revision"],
            "expected_workflow_status_hash": workspace["workflow_status_hash"],
            "expected_artifact_snapshot_hash": _snapshot(service, workspace)[
                "artifact_snapshot_hash"
            ],
        }
    )
    records = {record["artifact_id"]: record for record in current["records"]}
    qa_artifact_ids: list[str] = []
    resolver = StudioAssetAuthorityResolver(
        service.store,
        artifacts=service.creation_artifacts,
    )
    handles = []
    for artifact_id in review_artifact_ids:
        review = service.creation_artifacts.get_document(
            workspace["workspace_id"],
            artifact_id,
        )
        handles.append(verify_asset_qa_review(review, resolver=resolver))
        qa_identity = review["lineage"]["qa_report"]
        matches = [
            record["artifact_id"] for record in records.values() if record["subject"] == qa_identity
        ]
        if len(matches) != 1:
            raise AssertionError("QA report identity is not exact")
        qa_artifact_ids.append(matches[0])
    lineage = service.creation_jobs._asset_release_documents(  # noqa: SLF001
        snapshot=current,
        qa_report_artifact_ids=qa_artifact_ids,
    )
    _canonical, roots, asset_records = _asset_release_lineage(current["project"], lineage)
    root, _identity = service.creation_jobs.workspaces._verified_root(  # noqa: SLF001
        service.creation_jobs.workspaces._row(workspace["workspace_id"])  # noqa: SLF001
    )
    manifest = build_asset_manifest(
        roots["world-forge.gamepack"],
        roots["world-forge.asset_subject"],
        roots["world-forge.asset_target"],
        roots["world-forge.asset_style"],
        roots["world-forge.asset_inventory"],
        manifest_id=manifest_id,
        state="release_ready",
        asset_records=asset_records,
        artifact_root=root,
        qa_reviews=handles,
    )
    assetpack = build_generic_assetpack_manifest(
        manifest,
        gamepack=roots["world-forge.gamepack"],
        subject=roots["world-forge.asset_subject"],
        target=roots["world-forge.asset_target"],
        style=roots["world-forge.asset_style"],
        inventory=roots["world-forge.asset_inventory"],
        asset_records=asset_records,
        artifact_root=root,
        qa_reviews=handles,
    )
    return manifest, assetpack


def _mutating_release_worker(
    real_run: object,
    *,
    status: str,
    blockers: list[str],
    metadata_blockers: list[str] | None = None,
):
    from isoworld.runtime_io import decode_json_object
    from worldforge.integrity import canonical_json_bytes
    from worldforge.phase_report_v3 import document_identity
    from worldforge.studio.contracts import validate_studio_creation_worker_envelope
    from worldforge.studio.creation_executor import (
        CreationWorkerExecution,
        _verified_outputs,
    )

    def invoke(stage: Path, stage_identity: tuple[int, int], envelope: object, **kwargs: object):
        execution = real_run(stage, stage_identity, envelope, **kwargs)
        authority_path = stage / "output_0003.json"
        authority = decode_json_object(
            authority_path.read_bytes(),
            source="malicious asset release authority",
        )
        authority["status"] = status
        authority["blockers"] = list(blockers)
        authority["content_hash"] = ""
        authority["content_hash"] = canonical_payload_hash(
            authority,
            hash_field="content_hash",
        )
        payload = canonical_json_bytes(authority)
        authority_path.write_bytes(payload)
        response = copy.deepcopy(execution.response)
        raw_output = response["outputs"][2]
        raw_output["subject"] = document_identity(authority)
        raw_output["size"] = len(payload)
        raw_output["sha256"] = hashlib.sha256(payload).hexdigest()
        response["metadata"]["analysis_status"] = "passed" if status == "authorized" else "failed"
        response["metadata"]["reason_codes"] = list(
            blockers if metadata_blockers is None else metadata_blockers
        )
        validate_studio_creation_worker_envelope(response)
        return CreationWorkerExecution(response, _verified_outputs(stage, response))

    return invoke


class StudioCreationAssetReleaseV11JobTests(unittest.TestCase):
    def test_worker_cannot_change_trusted_release_decision(self) -> None:
        from tests.test_studio_creation_asset_seal_v4 import (
            _prepare_processed_creation_service,
        )
        from worldforge.studio import creation_jobs as creation_jobs_module
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        scenarios = (
            ("authorized_to_blocked", [], "blocked", ["worker_inserted"], None),
            ("blocked_to_authorized", ["policy_blocker"], "authorized", [], None),
        )
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace, _before, qa_ids = _prepare_processed_creation_service(base)
            try:
                review_ids = _review_processed_outputs(service, workspace, list(qa_ids))
                output_parent = base / "adversarial-outputs"
                output_parent.mkdir()
                for index, (
                    name,
                    expected_blockers,
                    worker_status,
                    worker_blockers,
                    metadata_blockers,
                ) in enumerate(scenarios):
                    with self.subTest(name=name):
                        manifest, assetpack = _release_candidates(
                            service,
                            workspace,
                            review_ids,
                            manifest_id=f"adversarial_manifest_{index}",
                        )
                        target = output_parent / name
                        grant = service.creation_output_grants.create(
                            {
                                "grant_id": f"grant_{name}",
                                "workspace_id": workspace["workspace_id"],
                                "kind": "generic_assetpack_directory",
                                "display_name": name,
                                "path": str(target),
                            }
                        )
                        current = _snapshot(service, workspace)
                        queued = service.creation_jobs.create_asset_release_authorize(
                            {
                                "job_id": f"job_{name}",
                                "workspace_id": workspace["workspace_id"],
                                "operation": "asset.release.authorize",
                                "expected_root_generation": workspace["root_generation"],
                                "expected_source_revision": workspace["source_revision"],
                                "expected_workflow_status_hash": workspace["workflow_status_hash"],
                                "expected_artifact_snapshot_hash": current[
                                    "artifact_snapshot_hash"
                                ],
                                "review_receipt_artifact_ids": review_ids,
                                "manifest_id": manifest["manifest_id"],
                                "assetpack_id": assetpack["assetpack_id"],
                                "release_authority_id": f"authority_{name}",
                                "blockers": expected_blockers,
                                "target_grant_id": grant["grant_id"],
                                "expected_target_grant_generation": grant["generation"],
                            }
                        )
                        self.assertEqual(
                            expected_blockers,
                            queued["operation_params"]["blockers"],
                        )
                        real_run = creation_jobs_module.run_isolated_creation_worker
                        with patch.object(
                            creation_jobs_module,
                            "run_isolated_creation_worker",
                            side_effect=_mutating_release_worker(
                                real_run,
                                status=worker_status,
                                blockers=worker_blockers,
                                metadata_blockers=metadata_blockers,
                            ),
                        ):
                            self.assertEqual(
                                queued["job_id"],
                                service.creation_job_coordinator.run_once(),
                            )
                        completed = service.creation_jobs.get(queued["job_id"])
                        self.assertNotEqual("succeeded", completed["state"], completed)
                        current_grant = service.creation_output_grants.get(grant["grant_id"])
                        self.assertEqual("ready", current_grant["state"])
                        self.assertIsNone(current_grant["publication"])
                        if expected_blockers:
                            self.assertEqual(grant, current_grant)
                        else:
                            self.assertEqual(grant["generation"] + 2, current_grant["generation"])
                            reservation = service.store.connection.execute(
                                "SELECT reserved_job_id FROM creation_output_grants "
                                "WHERE grant_id = ?",
                                (grant["grant_id"],),
                            ).fetchone()
                            self.assertIsNone(reservation["reserved_job_id"])
                        self.assertFalse(target.exists())

                from worldforge.generic_asset_authority import (
                    build_asset_release_authority,
                    verify_asset_qa_review,
                )
                from worldforge.integrity import canonical_json_bytes
                from worldforge.phase_report_v3 import document_identity
                from worldforge.studio.creation_asset_authority import (
                    StudioAssetAuthorityResolver,
                )
                from worldforge.studio.creation_executor import (
                    CreationWorkerExecutionError,
                    VerifiedCreationOutput,
                )

                resolver = StudioAssetAuthorityResolver(
                    service.store,
                    artifacts=service.creation_artifacts,
                )
                review_handles = [
                    verify_asset_qa_review(
                        service.creation_artifacts.get_document(
                            workspace["workspace_id"], artifact_id
                        ),
                        resolver=resolver,
                    )
                    for artifact_id in review_ids
                ]
                expected_authority_fields = {
                    "workspace_id": workspace["workspace_id"],
                    **copy.deepcopy(dict(queued["authority"])),
                    "producer_job_id": queued["job_id"],
                    "producer_operation": "asset.release.authorize",
                    "producer_output_position": 2,
                }
                expected_authorized = build_asset_release_authority(
                    manifest,
                    assetpack,
                    review_handles,
                    release_authority_id=str(queued["operation_params"]["release_authority_id"]),
                    blockers=[],
                    authority=expected_authority_fields,
                )
                expected_blocked = build_asset_release_authority(
                    manifest,
                    assetpack,
                    review_handles,
                    release_authority_id=str(queued["operation_params"]["release_authority_id"]),
                    blockers=["a_blocker", "z_blocker"],
                    authority=expected_authority_fields,
                )

                def outputs_for(documents: tuple[dict[str, object], ...]):
                    outputs = []
                    for index, document in enumerate(documents):
                        payload = canonical_json_bytes(document)
                        outputs.append(
                            VerifiedCreationOutput(
                                locator=f"output_{index + 1:04d}",
                                subject=document_identity(document),
                                payload=payload,
                                size=len(payload),
                                sha256=hashlib.sha256(payload).hexdigest(),
                                file_identity=(1, index + 1),
                            )
                        )
                    return tuple(outputs)

                def changed_authority(
                    original: dict[str, object],
                    *,
                    status: str,
                    blockers: list[str],
                ) -> dict[str, object]:
                    changed = copy.deepcopy(original)
                    changed["status"] = status
                    changed["blockers"] = blockers
                    changed["content_hash"] = ""
                    changed["content_hash"] = canonical_payload_hash(
                        changed,
                        hash_field="content_hash",
                    )
                    return changed

                validation_cases = (
                    (
                        "authorized_to_blocked_validation",
                        (manifest, assetpack, expected_authorized),
                        changed_authority(
                            expected_authorized,
                            status="blocked",
                            blockers=["worker_inserted"],
                        ),
                    ),
                    (
                        "blocked_to_authorized",
                        (manifest, assetpack, expected_blocked),
                        changed_authority(expected_blocked, status="authorized", blockers=[]),
                    ),
                    (
                        "added_blocker",
                        (manifest, assetpack, expected_blocked),
                        changed_authority(
                            expected_blocked,
                            status="blocked",
                            blockers=["a_blocker", "worker_inserted", "z_blocker"],
                        ),
                    ),
                    (
                        "removed_blocker",
                        (manifest, assetpack, expected_blocked),
                        changed_authority(
                            expected_blocked,
                            status="blocked",
                            blockers=["a_blocker"],
                        ),
                    ),
                    (
                        "reordered_blockers",
                        (manifest, assetpack, expected_blocked),
                        changed_authority(
                            expected_blocked,
                            status="blocked",
                            blockers=["z_blocker", "a_blocker"],
                        ),
                    ),
                )
                for name, expected, malicious_authority in validation_cases:
                    with (
                        self.subTest(name=name),
                        patch.object(
                            service.creation_job_coordinator,
                            "_trusted_asset_release_authorize_outputs",
                            return_value=(expected, review_handles),
                        ),
                        self.assertRaises(CreationWorkerExecutionError),
                    ):
                        service.creation_job_coordinator._validate_asset_release_authorize_execution(  # noqa: SLF001
                            queued,
                            outputs=outputs_for((manifest, assetpack, malicious_authority)),
                            metadata={
                                "analysis_status": (
                                    "passed"
                                    if malicious_authority["status"] == "authorized"
                                    else "failed"
                                ),
                                "reason_codes": malicious_authority["blockers"],
                            },
                            dependency_documents=(),
                            artifact_root=base,
                        )

                restart_manifest, restart_assetpack = _release_candidates(
                    service,
                    workspace,
                    review_ids,
                    manifest_id="blocked_restart_manifest",
                )
                restart_target = output_parent / "blocked-restart"
                restart_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_blocked_restart",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "generic_assetpack_directory",
                        "display_name": "blocked-restart",
                        "path": str(restart_target),
                    }
                )
                current = _snapshot(service, workspace)
                restart_job = service.creation_jobs.create_asset_release_authorize(
                    {
                        "job_id": "job_blocked_restart",
                        "workspace_id": workspace["workspace_id"],
                        "operation": "asset.release.authorize",
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                        "review_receipt_artifact_ids": review_ids,
                        "manifest_id": restart_manifest["manifest_id"],
                        "assetpack_id": restart_assetpack["assetpack_id"],
                        "release_authority_id": "authority_blocked_restart",
                        "blockers": ["restart_blocker"],
                        "target_grant_id": restart_grant["grant_id"],
                        "expected_target_grant_generation": restart_grant["generation"],
                    }
                )
                self.assertEqual(
                    restart_grant,
                    service.creation_output_grants.get(restart_grant["grant_id"]),
                )
                data_dir = service.store.data_dir
                service.close()
                service.store.close()
                service = StudioService(StudioStore(data_dir))
                self.assertEqual(restart_job, service.creation_jobs.get(restart_job["job_id"]))
                self.assertEqual(
                    restart_grant,
                    service.creation_output_grants.get(restart_grant["grant_id"]),
                )
                self.assertEqual(
                    restart_job["job_id"],
                    service.creation_job_coordinator.run_once(),
                )
                restarted = service.creation_jobs.get(restart_job["job_id"])
                self.assertEqual("succeeded", restarted["state"], restarted)
                self.assertEqual("blocked", restarted["result"]["release_status"])
                self.assertEqual(["restart_blocker"], restarted["result"]["reason_codes"])
                self.assertEqual(
                    restart_grant,
                    service.creation_output_grants.get(restart_grant["grant_id"]),
                )
                self.assertFalse(restart_target.exists())
            finally:
                service.close()
                service.store.close()

    def test_authorized_release_publishes_and_blocked_release_keeps_grant_untouched(self) -> None:
        from tests.test_studio_creation_asset_seal_v4 import (
            _prepare_processed_creation_service,
        )
        from worldforge.generic_asset_authority import (
            GenericAssetAuthorityError,
            verify_asset_release_authority,
        )
        from worldforge.studio.creation_asset_authority import StudioAssetAuthorityResolver
        from worldforge.studio.errors import StudioError
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace, _before, qa_ids = _prepare_processed_creation_service(base)
            try:
                review_ids = _review_processed_outputs(service, workspace, list(qa_ids))
                manifest, assetpack = _release_candidates(
                    service,
                    workspace,
                    review_ids,
                    manifest_id="authorized_release_manifest",
                )
                output_parent = base / "outputs"
                output_parent.mkdir()
                target = output_parent / "authorized-assets"
                grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_authorized_assets",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "generic_assetpack_directory",
                        "display_name": "authorized-assets",
                        "path": str(target),
                    }
                )
                current = _snapshot(service, workspace)
                params = {
                    "job_id": "job_authorize_release",
                    "workspace_id": workspace["workspace_id"],
                    "operation": "asset.release.authorize",
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                    "review_receipt_artifact_ids": review_ids,
                    "manifest_id": manifest["manifest_id"],
                    "assetpack_id": assetpack["assetpack_id"],
                    "release_authority_id": "release_authority_authorized",
                    "blockers": [],
                    "target_grant_id": grant["grant_id"],
                    "expected_target_grant_generation": grant["generation"],
                }
                with self.assertRaisesRegex(StudioError, "generation"):
                    service.creation_jobs.create_asset_release_authorize(
                        {
                            **params,
                            "job_id": "job_authorize_stale_generation",
                            "expected_target_grant_generation": grant["generation"] + 1,
                        }
                    )
                for invalid_reviews in (
                    [],
                    [review_ids[0], review_ids[0]],
                    ["artifact_" + "f" * 64],
                ):
                    with (
                        self.subTest(invalid_reviews=invalid_reviews),
                        self.assertRaisesRegex(
                            StudioError,
                            "review|current|coverage|collision|array",
                        ),
                    ):
                        service.creation_jobs.create_asset_release_authorize(
                            {
                                **params,
                                "job_id": "job_authorize_invalid_review_set",
                                "review_receipt_artifact_ids": invalid_reviews,
                            }
                        )
                with self.assertRaisesRegex(
                    StudioError,
                    "review receipt|review|current|v10",
                ):
                    service.creation_jobs.create_asset_release_authorize(
                        {
                            **params,
                            "job_id": "job_authorize_raw_qa",
                            "review_receipt_artifact_ids": [qa_ids[0]],
                        }
                    )
                with self.assertRaisesRegex(StudioError, "preflight|identity|integral"):
                    service.creation_jobs.create_asset_release_authorize(
                        {
                            **params,
                            "job_id": "job_authorize_pack_drift",
                            "assetpack_id": "assetpack_wrong_identity",
                        }
                    )
                self.assertEqual(
                    grant,
                    service.creation_output_grants.get(grant["grant_id"]),
                )
                queued = service.creation_jobs.create_asset_release_authorize(params)
                self.assertEqual(11, queued["format_version"])
                self.assertEqual(1, len(queued["inputs"]))
                reserved_grant = service.creation_output_grants.get(grant["grant_id"])
                self.assertEqual(
                    "reserved",
                    reserved_grant["state"],
                )
                replayed = service.creation_jobs.create_asset_release_authorize(
                    {**params, "job_id": "job_authorize_release_replay"}
                )
                self.assertEqual(queued, replayed)
                self.assertEqual(
                    reserved_grant,
                    service.creation_output_grants.get(grant["grant_id"]),
                )
                self.assertEqual(
                    1,
                    service.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_jobs WHERE workspace_id = ? "
                        "AND operation = 'asset.release.authorize' "
                        "AND state IN ('queued', 'running')",
                        (workspace["workspace_id"],),
                    ).fetchone()[0],
                )
                data_dir = service.store.data_dir
                service.close()
                service.store.close()
                service = StudioService(StudioStore(data_dir))
                self.assertEqual(
                    queued,
                    service.creation_jobs.get(queued["job_id"]),
                )
                self.assertEqual(
                    reserved_grant,
                    service.creation_output_grants.get(grant["grant_id"]),
                )
                with patch.object(
                    service.creation_job_coordinator,
                    "_cleanup_stage",
                    side_effect=OSError("simulated v11 cleanup interruption"),
                ):
                    self.assertEqual(
                        queued["job_id"],
                        service.creation_job_coordinator.run_once(),
                    )
                cleanup_pending = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("succeeded", cleanup_pending["state"])
                self.assertEqual("cleanup_pending", cleanup_pending["progress"])
                self.assertTrue(cleanup_pending["result"]["cleanup_pending"])
                completed = service.creation_jobs.recover(
                    cleanup_pending["job_id"],
                    mode="cleanup",
                    expected_generation=cleanup_pending["generation"],
                    expected_record_hash=cleanup_pending["record_hash"],
                )
                self.assertEqual("succeeded", completed["state"], completed)
                self.assertEqual("committed", completed["progress"])
                self.assertFalse(completed["result"]["cleanup_pending"])
                self.assertEqual("authorized", completed["result"]["release_status"])
                self.assertEqual("published", completed["result"]["publication"]["state"])
                output_documents = [
                    service.creation_artifacts.get_document(workspace["workspace_id"], artifact_id)
                    for artifact_id in completed["result"]["output_artifact_ids"]
                ]
                self.assertEqual(
                    [
                        "world-forge.asset_manifest",
                        "world-forge.assetpack",
                        "world-forge.asset_release_authority",
                    ],
                    [document["format"] for document in output_documents],
                )
                self.assertEqual(manifest, output_documents[0])
                self.assertEqual(assetpack, output_documents[1])
                resolver = StudioAssetAuthorityResolver(
                    service.store,
                    artifacts=service.creation_artifacts,
                )
                reviews = [
                    service.creation_artifacts.get_document(workspace["workspace_id"], artifact_id)
                    for artifact_id in review_ids
                ]
                from worldforge.generic_asset_authority import verify_asset_qa_review

                review_handles = [
                    verify_asset_qa_review(review, resolver=resolver) for review in reviews
                ]
                verified = verify_asset_release_authority(
                    output_documents[2],
                    manifest=output_documents[0],
                    assetpack=output_documents[1],
                    reviews=review_handles,
                    resolver=resolver,
                )
                self.assertTrue(verified.authorized)
                self.assertTrue(target.is_dir())
                release_artifact_id = completed["result"]["output_artifact_ids"][2]
                service.store.connection.execute("SAVEPOINT release_producer_tamper")
                try:
                    service.store.connection.execute(
                        "UPDATE creation_artifacts SET producer_output_position = 7 "
                        "WHERE artifact_id = ?",
                        (release_artifact_id,),
                    )
                    with self.assertRaises(GenericAssetAuthorityError):
                        verify_asset_release_authority(
                            output_documents[2],
                            manifest=output_documents[0],
                            assetpack=output_documents[1],
                            reviews=review_handles,
                            resolver=resolver,
                        )
                finally:
                    service.store.connection.execute("ROLLBACK TO release_producer_tamper")
                    service.store.connection.execute("RELEASE release_producer_tamper")

                blocked_manifest, blocked_assetpack = _release_candidates(
                    service,
                    workspace,
                    review_ids,
                    manifest_id="blocked_release_manifest",
                )
                blocked_target = output_parent / "blocked-assets"
                blocked_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_blocked_assets",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "generic_assetpack_directory",
                        "display_name": "blocked-assets",
                        "path": str(blocked_target),
                    }
                )
                current = _snapshot(service, workspace)
                blocked = service.creation_jobs.create_asset_release_authorize(
                    {
                        **params,
                        "job_id": "job_block_release",
                        "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                        "manifest_id": blocked_manifest["manifest_id"],
                        "assetpack_id": blocked_assetpack["assetpack_id"],
                        "release_authority_id": "release_authority_blocked",
                        "blockers": ["release_blocked_by_reviewer"],
                        "target_grant_id": blocked_grant["grant_id"],
                        "expected_target_grant_generation": blocked_grant["generation"],
                    }
                )
                self.assertEqual(
                    blocked_grant,
                    service.creation_output_grants.get(blocked_grant["grant_id"]),
                )
                self.assertEqual(blocked["job_id"], service.creation_job_coordinator.run_once())
                blocked_job = service.creation_jobs.get(blocked["job_id"])
                self.assertEqual("succeeded", blocked_job["state"], blocked_job)
                self.assertEqual("blocked", blocked_job["result"]["release_status"])
                self.assertEqual("failed", blocked_job["result"]["analysis_status"])
                self.assertEqual(
                    ["release_blocked_by_reviewer"],
                    blocked_job["result"]["reason_codes"],
                )
                self.assertIsNone(blocked_job["result"]["publication"])
                self.assertEqual(
                    blocked_grant,
                    service.creation_output_grants.get(blocked_grant["grant_id"]),
                )
                self.assertFalse(blocked_target.exists())

                qa_artifact_id = qa_ids[0]
                qa_report = service.creation_artifacts.get_document(
                    workspace["workspace_id"],
                    qa_artifact_id,
                )
                current = _snapshot(service, workspace)
                rejected_review = service.creation_jobs.create_asset_qa_review(
                    {
                        "job_id": "job_review_release_rejected",
                        "workspace_id": workspace["workspace_id"],
                        "operation": "asset.qa.review",
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                        "qa_report_artifact_id": qa_artifact_id,
                        "output_role": qa_report["outputs"][0]["role"],
                        "review_receipt_id": "review_release_rejected",
                        "decisions": [
                            "rejected",
                            *["approved" for _criterion in qa_report["acceptance_criteria"][1:]],
                        ],
                        "blockers": ["criterion_rejected"],
                    }
                )
                self.assertEqual(
                    rejected_review["job_id"],
                    service.creation_job_coordinator.run_once(),
                )
                rejected_review_job = service.creation_jobs.get(rejected_review["job_id"])
                self.assertEqual("succeeded", rejected_review_job["state"])
                rejected_review_ids = rejected_review_job["result"]["output_artifact_ids"]
                rejected_manifest, rejected_assetpack = _release_candidates(
                    service,
                    workspace,
                    rejected_review_ids,
                    manifest_id="rejected_release_manifest",
                )
                rejected_target = output_parent / "rejected-assets"
                rejected_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_rejected_assets",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "generic_assetpack_directory",
                        "display_name": "rejected-assets",
                        "path": str(rejected_target),
                    }
                )
                current = _snapshot(service, workspace)
                with self.assertRaisesRegex(
                    StudioError,
                    "duplicate|duplicated|extra|unique|canonical",
                ):
                    service.creation_jobs.create_asset_release_authorize(
                        {
                            **params,
                            "job_id": "job_reject_release_duplicate_reviews",
                            "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                            "review_receipt_artifact_ids": sorted(
                                [review_ids[0], rejected_review_ids[0]],
                                key=lambda item: item.encode("utf-8"),
                            ),
                            "manifest_id": rejected_manifest["manifest_id"],
                            "assetpack_id": rejected_assetpack["assetpack_id"],
                            "release_authority_id": "release_authority_duplicate_reviews",
                            "blockers": [],
                            "target_grant_id": rejected_grant["grant_id"],
                            "expected_target_grant_generation": rejected_grant["generation"],
                        }
                    )
                rejected_release = service.creation_jobs.create_asset_release_authorize(
                    {
                        **params,
                        "job_id": "job_reject_release_from_review",
                        "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                        "review_receipt_artifact_ids": rejected_review_ids,
                        "manifest_id": rejected_manifest["manifest_id"],
                        "assetpack_id": rejected_assetpack["assetpack_id"],
                        "release_authority_id": "release_authority_rejected_review",
                        "blockers": [],
                        "target_grant_id": rejected_grant["grant_id"],
                        "expected_target_grant_generation": rejected_grant["generation"],
                    }
                )
                self.assertEqual(
                    ["criterion_rejected"],
                    rejected_release["operation_params"]["blockers"],
                )
                self.assertEqual(
                    rejected_grant,
                    service.creation_output_grants.get(rejected_grant["grant_id"]),
                )
                self.assertEqual(
                    rejected_release["job_id"],
                    service.creation_job_coordinator.run_once(),
                )
                rejected_release_job = service.creation_jobs.get(rejected_release["job_id"])
                self.assertEqual("succeeded", rejected_release_job["state"])
                self.assertEqual("blocked", rejected_release_job["result"]["release_status"])
                self.assertEqual(
                    ["criterion_rejected"],
                    rejected_release_job["result"]["reason_codes"],
                )
                self.assertEqual(3, len(rejected_release_job["result"]["output_artifact_ids"]))
                self.assertIsNone(rejected_release_job["result"]["publication"])
                self.assertEqual(
                    rejected_grant,
                    service.creation_output_grants.get(rejected_grant["grant_id"]),
                )
                self.assertFalse(rejected_target.exists())

                crash_manifest, crash_assetpack = _release_candidates(
                    service,
                    workspace,
                    review_ids,
                    manifest_id="registry_crash_release_manifest",
                )
                crash_target = output_parent / "registry-crash-assets"
                crash_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_registry_crash_assets",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "generic_assetpack_directory",
                        "display_name": "registry-crash-assets",
                        "path": str(crash_target),
                    }
                )
                current = _snapshot(service, workspace)
                crash_job = service.creation_jobs.create_asset_release_authorize(
                    {
                        **params,
                        "job_id": "job_authorize_registry_crash",
                        "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                        "manifest_id": crash_manifest["manifest_id"],
                        "assetpack_id": crash_assetpack["assetpack_id"],
                        "release_authority_id": "release_authority_registry_crash",
                        "target_grant_id": crash_grant["grant_id"],
                        "expected_target_grant_generation": crash_grant["generation"],
                    }
                )
                with (
                    patch.object(
                        service.creation_job_coordinator,
                        "_commit_registry",
                        side_effect=SystemExit("simulated v11 registry crash"),
                    ),
                    self.assertRaises(SystemExit),
                ):
                    service.creation_job_coordinator.run_once()
                self.assertTrue(crash_target.is_dir())
                data_dir = service.store.data_dir
                service.close()
                service.store.close()
                service = StudioService(StudioStore(data_dir))
                orphaned = service.creation_jobs.get(crash_job["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                attempt = service.store.connection.execute(
                    "SELECT phase FROM creation_job_attempts WHERE job_id = ?",
                    (orphaned["job_id"],),
                ).fetchone()
                self.assertEqual("registry_committing", attempt["phase"])
                if sys.platform.startswith("linux") and os.name == "posix":
                    with self.assertRaisesRegex(StudioError, "recovery_required") as raised:
                        service.creation_jobs.recover(
                            orphaned["job_id"],
                            mode="resume",
                            expected_generation=orphaned["generation"],
                            expected_record_hash=orphaned["record_hash"],
                        )
                    self.assertIn("recovery_evidence", raised.exception.details)
                else:
                    resumed = service.creation_jobs.recover(
                        orphaned["job_id"],
                        mode="resume",
                        expected_generation=orphaned["generation"],
                        expected_record_hash=orphaned["record_hash"],
                    )
                    self.assertEqual("queued", resumed["state"])
                    self.assertEqual(
                        resumed["job_id"],
                        service.creation_job_coordinator.run_once(),
                    )
                    self.assertEqual(
                        "succeeded",
                        service.creation_jobs.get(resumed["job_id"])["state"],
                    )
            finally:
                service.close()
                service.store.close()


if __name__ == "__main__":
    unittest.main()
