from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class StudioCreationAssetReviewV10ContractTests(unittest.TestCase):
    def test_private_worker_v10_is_closed_and_version_discriminated(self) -> None:
        from worldforge.studio.contracts import validate_studio_creation_worker_envelope

        request = {
            "format": "world-forge.studio_creation_worker",
            "format_version": 10,
            "kind": "request",
            "job_id": "job_review_board",
            "operation": "asset.qa.review",
            "request_locator": "request_" + "a" * 32,
            "request_sha256": "b" * 64,
        }
        self.assertEqual(request, validate_studio_creation_worker_envelope(request))

        mismatched = {**request, "format_version": 9}
        with self.assertRaisesRegex(ValueError, "operation|version"):
            validate_studio_creation_worker_envelope(mismatched)

    def test_published_v10_worker_and_review_params_are_closed(self) -> None:
        root = Path(__file__).resolve().parents[1]
        worker = json.loads(
            (root / "schemas/studio-creation-worker.schema.json").read_text("utf-8")
        )
        jobs = json.loads((root / "schemas/studio-creation-job-v12.schema.json").read_text("utf-8"))
        catalog = json.loads((root / "contracts/catalog.json").read_text("utf-8"))
        self.assertEqual(
            "World Forge Studio isolated creation worker envelope v11",
            worker["title"],
        )
        self.assertEqual(list(range(1, 12)), worker["properties"]["format_version"]["enum"])
        self.assertEqual(33, len(worker["oneOf"]))
        self.assertEqual(
            64,
            jobs["$defs"]["assetQaReviewOperationParams"]["properties"]["blockers"]["maxItems"],
        )
        entry = next(
            item for item in catalog["contracts"] if item["id"] == "studio-creation-worker"
        )
        self.assertEqual(11, entry["version"])
        self.assertIn("tests/test_studio_creation_asset_review_v10.py", entry["tests"])


class StudioCreationAssetReviewV10RetentionTests(unittest.TestCase):
    def test_asset_process_retains_exact_processed_bytes_in_private_blob_cas(self) -> None:
        from tests.test_studio_creation_asset_jobs_v4 import (
            StudioCreationAssetJobCoordinatorTests,
            _processed_project_output,
        )
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                _before, queued = StudioCreationAssetJobCoordinatorTests._queued_asset_process(
                    service,
                    workspace,
                )
                self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                completed = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("succeeded", completed["state"])

                retention = service.creation_artifacts.load_asset_process_retention(
                    workspace_id=workspace["workspace_id"],
                    producer_job_id=queued["job_id"],
                )
                self.assertEqual("world-forge.studio_asset_process_retention", retention["format"])
                self.assertEqual(1, retention["format_version"])
                self.assertEqual(queued["job_id"], retention["producer_job_id"])
                self.assertEqual(1, len(retention["outputs"]))
                output = retention["outputs"][0]
                retained_bytes = service.creation_artifacts.read_retained_asset_output(
                    retention,
                    role=output["role"],
                )
                self.assertEqual(_processed_project_output(base).read_bytes(), retained_bytes)
            finally:
                service.close()
                service.store.close()


class StudioCreationAssetReviewV10JobTests(unittest.TestCase):
    def test_review_job_recovers_across_every_coordinator_crash_boundary(self) -> None:
        from tests.test_studio_creation_asset_jobs_v4 import (
            StudioCreationAssetJobCoordinatorTests,
        )
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio import creation_jobs as creation_jobs_module
        from worldforge.studio.errors import StudioError
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                _before, process = StudioCreationAssetJobCoordinatorTests._queued_asset_process(
                    service,
                    workspace,
                )
                self.assertEqual(process["job_id"], service.creation_job_coordinator.run_once())
                processed = service.creation_jobs.get(process["job_id"])
                qa_artifact_id = processed["result"]["output_artifact_ids"][2]
                qa_report = service.creation_artifacts.get_document(
                    workspace["workspace_id"],
                    qa_artifact_id,
                )
                data_dir = service.store.data_dir

                for index, boundary in enumerate(
                    ("worker_started", "output_published", "registry_committing")
                ):
                    with self.subTest(boundary=boundary):
                        snapshot = service.creation_evidence.list(
                            {
                                "workspace_id": workspace["workspace_id"],
                                "expected_root_generation": workspace["root_generation"],
                                "expected_source_revision": workspace["source_revision"],
                                "expected_workflow_status_hash": workspace["workflow_status_hash"],
                                "expected_artifact_snapshot_hash": None,
                                "lifecycle": None,
                                "cursor": None,
                                "limit": 64,
                            }
                        )
                        queued = service.creation_jobs.create_asset_qa_review(
                            {
                                "job_id": f"job_review_crash_{index}",
                                "workspace_id": workspace["workspace_id"],
                                "operation": "asset.qa.review",
                                "expected_root_generation": workspace["root_generation"],
                                "expected_source_revision": workspace["source_revision"],
                                "expected_workflow_status_hash": workspace["workflow_status_hash"],
                                "expected_artifact_snapshot_hash": snapshot[
                                    "artifact_snapshot_hash"
                                ],
                                "qa_report_artifact_id": qa_artifact_id,
                                "output_role": qa_report["outputs"][0]["role"],
                                "review_receipt_id": f"review_receipt_crash_{index}",
                                "decisions": [
                                    "approved" for _criterion in qa_report["acceptance_criteria"]
                                ],
                                "blockers": [],
                            }
                        )
                        if boundary == "worker_started":
                            interrupted = patch.object(
                                creation_jobs_module,
                                "run_isolated_creation_worker",
                                side_effect=SystemExit("simulated worker-start crash"),
                            )
                        elif boundary == "output_published":
                            interrupted = patch.object(
                                service.creation_artifacts,
                                "prepare_outputs",
                                side_effect=SystemExit("simulated output-prepare crash"),
                            )
                        else:
                            interrupted = patch.object(
                                service.creation_job_coordinator,
                                "_commit_registry",
                                side_effect=SystemExit("simulated registry-commit crash"),
                            )
                        with interrupted, self.assertRaises(SystemExit):
                            service.creation_job_coordinator.run_once()

                        service.close()
                        service.store.close()
                        reopened_store = StudioStore(data_dir)
                        service = StudioService(reopened_store)
                        orphaned = service.creation_jobs.get(queued["job_id"])
                        self.assertEqual("orphaned", orphaned["state"])
                        self.assertEqual("orphaned", orphaned["progress"])
                        attempt = service.store.connection.execute(
                            "SELECT phase FROM creation_job_attempts WHERE job_id = ?",
                            (queued["job_id"],),
                        ).fetchone()
                        self.assertIsNotNone(attempt)
                        self.assertEqual(boundary, attempt["phase"])
                        self.assertEqual(
                            0,
                            service.store.connection.execute(
                                "SELECT COUNT(*) FROM creation_job_outputs WHERE job_id = ?",
                                (orphaned["job_id"],),
                            ).fetchone()[0],
                        )
                        if sys.platform.startswith("linux") and os.name == "posix":
                            with self.assertRaisesRegex(
                                StudioError,
                                "recovery_required",
                            ) as raised:
                                service.creation_jobs.recover(
                                    orphaned["job_id"],
                                    mode="resume",
                                    expected_generation=orphaned["generation"],
                                    expected_record_hash=orphaned["record_hash"],
                                )
                            evidence = raised.exception.details["recovery_evidence"]
                            self.assertEqual({"stage"}, set(evidence))
                            self.assertNotIn("/", evidence["stage"]["locator"])
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
                            completed = service.creation_jobs.get(resumed["job_id"])
                            self.assertEqual("succeeded", completed["state"])
                            self.assertEqual("committed", completed["progress"])
                            self.assertEqual("approved", completed["result"]["review_status"])
            finally:
                service.close()
                service.store.close()

    def test_review_job_builds_and_reverifies_one_exact_retained_authority_receipt(self) -> None:
        from tests.test_studio_creation_asset_jobs_v4 import (
            StudioCreationAssetJobCoordinatorTests,
        )
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.generic_asset_authority import (
            GenericAssetAuthorityError,
            verify_asset_qa_review,
        )
        from worldforge.studio.creation_asset_authority import StudioAssetAuthorityResolver
        from worldforge.studio.errors import StudioError
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                _before, process = StudioCreationAssetJobCoordinatorTests._queued_asset_process(
                    service,
                    workspace,
                )
                self.assertEqual(process["job_id"], service.creation_job_coordinator.run_once())
                processed = service.creation_jobs.get(process["job_id"])
                qa_artifact_id = processed["result"]["output_artifact_ids"][2]
                qa_report = service.creation_artifacts.get_document(
                    workspace["workspace_id"],
                    qa_artifact_id,
                )
                snapshot = service.creation_evidence.list(
                    {
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": None,
                        "lifecycle": None,
                        "cursor": None,
                        "limit": 64,
                    }
                )
                review_params = {
                    "job_id": "job_review_asset_board",
                    "workspace_id": workspace["workspace_id"],
                    "operation": "asset.qa.review",
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": snapshot["artifact_snapshot_hash"],
                    "qa_report_artifact_id": qa_artifact_id,
                    "output_role": qa_report["outputs"][0]["role"],
                    "review_receipt_id": "review_receipt_board_ui",
                    "decisions": ["approved" for _criterion in qa_report["acceptance_criteria"]],
                    "blockers": [],
                }
                for forbidden in (
                    "qa_report",
                    "status",
                    "evidence_hashes",
                    "path",
                    "command",
                    "provider",
                    "env",
                ):
                    with self.subTest(forbidden=forbidden):
                        with self.assertRaisesRegex(StudioError, "invalid fields"):
                            service.creation_jobs.create_asset_qa_review(
                                {**review_params, forbidden: "caller-controlled"}
                            )
                with self.assertRaisesRegex(
                    (StudioError, ValueError),
                    "decisions|criteria|coverage",
                ):
                    service.creation_jobs.create_asset_qa_review(
                        {**review_params, "decisions": review_params["decisions"][:-1]}
                    )

                queued = service.creation_jobs.create_asset_qa_review(review_params)
                self.assertEqual(10, queued["format_version"])
                self.assertEqual("asset.qa.review", queued["operation"])
                self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())

                completed = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("succeeded", completed["state"])
                self.assertEqual("approved", completed["result"]["review_status"])
                self.assertEqual(1, len(completed["result"]["output_artifact_ids"]))
                receipt = service.creation_artifacts.get_document(
                    workspace["workspace_id"],
                    completed["result"]["output_artifact_ids"][0],
                )
                self.assertEqual(
                    completed["result"]["review_receipt"],
                    {
                        "format": receipt["format"],
                        "format_version": receipt["format_version"],
                        "review_receipt_id": receipt["review_receipt_id"],
                        "content_hash": receipt["content_hash"],
                    },
                )
                resolver = StudioAssetAuthorityResolver(
                    service.store,
                    artifacts=service.creation_artifacts,
                )
                verified = verify_asset_qa_review(receipt, resolver=resolver)
                self.assertTrue(verified.approved)
                self.assertNotIn("native_path", json.dumps(completed, sort_keys=True))
                restarted_store = StudioStore(base / "studio", mode="secondary")
                restarted = StudioService(restarted_store)
                try:
                    restarted_receipt = restarted.creation_artifacts.get_document(
                        workspace["workspace_id"],
                        completed["result"]["output_artifact_ids"][0],
                    )
                    restarted_resolver = StudioAssetAuthorityResolver(
                        restarted.store,
                        artifacts=restarted.creation_artifacts,
                    )
                    self.assertTrue(
                        verify_asset_qa_review(
                            restarted_receipt,
                            resolver=restarted_resolver,
                        ).approved
                    )
                finally:
                    restarted.close()
                    restarted_store.close()

                after_approved = service.creation_evidence.list(
                    {
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": None,
                        "lifecycle": None,
                        "cursor": None,
                        "limit": 64,
                    }
                )
                rejected = service.creation_jobs.create_asset_qa_review(
                    {
                        **review_params,
                        "job_id": "job_reject_asset_board",
                        "expected_artifact_snapshot_hash": after_approved["artifact_snapshot_hash"],
                        "review_receipt_id": "review_receipt_board_ui_rejected",
                        "decisions": [
                            "rejected",
                            *review_params["decisions"][1:],
                        ],
                        "blockers": ["criterion_rejected"],
                    }
                )
                with patch.object(
                    service.creation_job_coordinator,
                    "_complete_cleanup",
                    side_effect=OSError("simulated cleanup registry interruption"),
                ):
                    self.assertEqual(
                        rejected["job_id"],
                        service.creation_job_coordinator.run_once(),
                    )
                rejected_job = service.creation_jobs.get(rejected["job_id"])
                self.assertEqual("succeeded", rejected_job["state"])
                self.assertEqual("cleanup_pending", rejected_job["progress"])
                self.assertEqual("failed", rejected_job["result"]["analysis_status"])
                self.assertEqual("rejected", rejected_job["result"]["review_status"])
                rejected_job = service.creation_jobs.recover(
                    rejected_job["job_id"],
                    mode="cleanup",
                    expected_generation=rejected_job["generation"],
                    expected_record_hash=rejected_job["record_hash"],
                )
                self.assertEqual("committed", rejected_job["progress"])
                rejected_receipt = service.creation_artifacts.get_document(
                    workspace["workspace_id"],
                    rejected_job["result"]["output_artifact_ids"][0],
                )
                self.assertFalse(
                    verify_asset_qa_review(rejected_receipt, resolver=resolver).approved
                )

                approved_artifact_id = completed["result"]["output_artifact_ids"][0]
                tampered_bindings = (
                    ("root_generation", workspace["root_generation"] + 1),
                    ("source_revision", "f" * 64),
                    ("workflow_status_hash", "e" * 64),
                    ("input_artifact_snapshot_hash", "d" * 64),
                    ("producer_operation", "asset.process"),
                    ("producer_output_position", 1),
                )
                for column, value in tampered_bindings:
                    with self.subTest(tampered_column=column):
                        service.store.connection.execute("SAVEPOINT tampered_authority")
                        try:
                            service.store.connection.execute(
                                f"UPDATE creation_artifacts SET {column} = ? WHERE artifact_id = ?",
                                (value, approved_artifact_id),
                            )
                            with self.assertRaises(GenericAssetAuthorityError):
                                verify_asset_qa_review(receipt, resolver=resolver)
                        finally:
                            service.store.connection.execute("ROLLBACK TO tampered_authority")
                            service.store.connection.execute("RELEASE tampered_authority")

                service.store.connection.execute("SAVEPOINT cross_job_authority")
                try:
                    service.store.connection.execute(
                        "UPDATE creation_artifacts SET producer_job_id = ?, "
                        "producer_output_position = 15 WHERE artifact_id = ?",
                        (process["job_id"], approved_artifact_id),
                    )
                    with self.assertRaises(GenericAssetAuthorityError):
                        verify_asset_qa_review(receipt, resolver=resolver)
                finally:
                    service.store.connection.execute("ROLLBACK TO cross_job_authority")
                    service.store.connection.execute("RELEASE cross_job_authority")

                retention = service.creation_artifacts.load_asset_process_retention(
                    workspace_id=workspace["workspace_id"],
                    producer_job_id=process["job_id"],
                )
                retained_blob = service.store.blob_path(retention["outputs"][0]["sha256"])
                retained_payload = retained_blob.read_bytes()
                held_blob = retained_blob.with_name(retained_blob.name + ".held")
                retained_blob.rename(held_blob)
                with self.assertRaises(GenericAssetAuthorityError):
                    verify_asset_qa_review(receipt, resolver=resolver)
                retained_blob.write_bytes(retained_payload)
                with self.assertRaises(GenericAssetAuthorityError):
                    verify_asset_qa_review(receipt, resolver=resolver)
                retained_blob.unlink()
                held_blob.rename(retained_blob)
                hardlink = retained_blob.with_name(retained_blob.name + ".hardlink")
                os.link(retained_blob, hardlink)
                try:
                    with self.assertRaises(GenericAssetAuthorityError):
                        verify_asset_qa_review(receipt, resolver=resolver)
                finally:
                    hardlink.unlink()
                self.assertTrue(verify_asset_qa_review(receipt, resolver=resolver).approved)
                retained_blob.write_bytes(b"wrong-size")
                with self.assertRaises(GenericAssetAuthorityError):
                    verify_asset_qa_review(receipt, resolver=resolver)
                retained_blob.write_bytes(retained_payload)
                self.assertTrue(verify_asset_qa_review(receipt, resolver=resolver).approved)
                retained_blob.write_bytes(b"x" * len(retained_payload))
                with self.assertRaises(GenericAssetAuthorityError):
                    verify_asset_qa_review(receipt, resolver=resolver)
            finally:
                service.close()
                service.store.close()


if __name__ == "__main__":
    unittest.main()
