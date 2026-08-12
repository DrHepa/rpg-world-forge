from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_studio_creation_asset_seal_v4 import _grant_record
from tests.test_studio_creation_runtime_compose_v4 import (
    _prepare_published_runtime_inputs,
)
from worldforge.game_runtime_bundle import verify_game_runtime_bundle
from worldforge.studio.contracts import (
    StudioContractError,
    validate_studio_creation_output_grant,
)

ROOT = Path(__file__).resolve().parents[1]


class StudioRuntimeBundleWorkerContractTests(unittest.TestCase):
    def test_v5_worker_request_is_pathless_closed_and_deterministic(self) -> None:
        import hashlib

        from tests.test_multigenre_game_runtime_bundle import _sealed_fixture
        from worldforge.creation_contracts import load_creation_project, read_creation_object
        from worldforge.gamepack import load_gamepack
        from worldforge.studio.creation_job_protocol import (
            build_private_runtime_bundle_request,
            execute_private_creation_request,
            validate_private_creation_request,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with _sealed_fixture("abstract-puzzle", root) as verified_assetpack:
                lineage = (
                    load_gamepack(
                        ROOT
                        / "examples"
                        / "multigenre-contracts"
                        / "abstract-puzzle"
                        / "artifacts"
                        / "abstract-puzzle.gamepack.json"
                    ),
                    read_creation_object(
                        ROOT / "examples/multigenre-contracts/abstract-puzzle/assets/inventory.json"
                    ),
                    verified_assetpack.manifest,
                    read_creation_object(
                        ROOT / "examples/multigenre-contracts/runtime/snapshot.json"
                    ),
                    read_creation_object(
                        ROOT / "examples/multigenre-contracts/runtime/registry.json"
                    ),
                    read_creation_object(
                        ROOT
                        / "examples/multigenre-contracts/abstract-puzzle/runtime/composition.json"
                    ),
                    read_creation_object(
                        ROOT
                        / "examples"
                        / "multigenre-contracts"
                        / "abstract-puzzle"
                        / "runtime"
                        / "support-report.json"
                    ),
                )
                staged_inputs = [
                    {
                        "source_locator": locator,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size_bytes": len(payload),
                    }
                    for locator, payload in sorted(
                        verified_assetpack.files.items(),
                        key=lambda item: item[0].encode("utf-8"),
                    )
                ]
                request = build_private_runtime_bundle_request(
                    job_id="job_runtime_bundle_worker",
                    workspace_id="workspace_puzzle",
                    authority={
                        "root_generation": 0,
                        "source_revision": "a" * 64,
                        "workflow_status_hash": None,
                        "artifact_snapshot_hash": "b" * 64,
                    },
                    project=load_creation_project(
                        ROOT / "examples/multigenre-contracts/abstract-puzzle/project.json"
                    ),
                    lineage_documents=lineage,
                    source_grant_id="grant_assetpack_source",
                    source_grant_generation=2,
                    target_grant_id="grant_runtime_bundle_target",
                    target_grant_generation=1,
                    staged_inputs=staged_inputs,
                )
                self.assertEqual(5, request["format_version"])
                self.assertEqual("runtime.bundle.build", request["operation"])
                self.assertNotIn(str(verified_assetpack.root), json.dumps(request))
                self.assertEqual(request, validate_private_creation_request(request))
                leaked = {**request, "native_path": str(verified_assetpack.root)}
                with self.assertRaisesRegex(ValueError, "fields"):
                    validate_private_creation_request(leaked)
                first = execute_private_creation_request(
                    request,
                    artifact_root=verified_assetpack.root,
                )
                second = execute_private_creation_request(
                    request,
                    artifact_root=verified_assetpack.root,
                )
                self.assertEqual(1, len(first.outputs))
                self.assertEqual(
                    "world-forge.game_runtime_bundle", first.outputs[0].subject["format"]
                )
                self.assertEqual(first.outputs[0].payload, second.outputs[0].payload)


class StudioRuntimeBundleGrantContractTests(unittest.TestCase):
    def test_output_grant_v2_is_closed_and_v1_remains_exact(self) -> None:
        legacy = _grant_record()
        self.assertEqual(legacy, validate_studio_creation_output_grant(copy.deepcopy(legacy)))

        runtime_bundle = {
            **legacy,
            "format_version": 2,
            "grant_id": "grant_runtime_bundle",
            "kind": "game_runtime_bundle_directory",
        }
        self.assertEqual(
            runtime_bundle,
            validate_studio_creation_output_grant(copy.deepcopy(runtime_bundle)),
        )
        leaked = {**runtime_bundle, "path": "/private/native/path"}
        with self.assertRaisesRegex(StudioContractError, "unknown fields"):
            validate_studio_creation_output_grant(leaked)

    def test_queued_v5_cancellation_releases_only_the_reserved_bundle_grant(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.integrity import canonical_payload_hash
        from worldforge.studio.creation_output_grants import CreationOutputGrantManager
        from worldforge.studio.storage import encode_json, utc_now

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                output_parent = base / "outputs"
                output_parent.mkdir()
                grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_runtime_bundle_cancel",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "game_runtime_bundle_directory",
                        "display_name": "runtime-bundle-cancel",
                        "path": str(output_parent / "runtime-bundle-cancel"),
                    }
                )
                job_id = "job_runtime_bundle_cancel"
                with service.store.connection:
                    reserved, _binding = service.creation_output_grants.reserve_for_job(
                        grant_id=grant["grant_id"],
                        job_id=job_id,
                        workspace_id=workspace["workspace_id"],
                        expected_generation=grant["generation"],
                        expected_manifest_hash="a" * 64,
                        expected_tree_hash="b" * 64,
                    )
                    timestamp = utc_now()
                    record = {
                        "format": "world-forge.studio_creation_job",
                        "format_version": 5,
                        "job_id": job_id,
                        "workspace_id": workspace["workspace_id"],
                        "operation": "runtime.bundle.build",
                        "operation_params": {
                            "gamepack_artifact_id": "artifact_gamepack_cancel",
                            "asset_inventory_artifact_id": "artifact_inventory_cancel",
                            "assetpack_artifact_id": "artifact_assetpack_cancel",
                            "runtime_snapshot_artifact_id": "artifact_snapshot_cancel",
                            "runtime_adapter_registry_artifact_id": "artifact_registry_cancel",
                            "runtime_composition_artifact_id": "artifact_composition_cancel",
                            "runtime_support_report_artifact_id": "artifact_support_cancel",
                            "source_grant_id": "grant_assetpack_source_cancel",
                            "source_grant_generation": 2,
                            "target_grant_id": grant["grant_id"],
                            "target_grant_generation": reserved["generation"],
                        },
                        "state": "queued",
                        "generation": 0,
                        "authority": {
                            "root_generation": workspace["root_generation"],
                            "source_revision": workspace["source_revision"],
                            "workflow_status_hash": workspace["workflow_status_hash"],
                            "artifact_snapshot_hash": "c" * 64,
                        },
                        "inputs": [],
                        "progress": "queued",
                        "result": None,
                        "error": None,
                        "created_at": timestamp,
                        "started_at": None,
                        "finished_at": None,
                        "updated_at": timestamp,
                        "record_hash": "",
                    }
                    record["record_hash"] = canonical_payload_hash(
                        record,
                        hash_field="record_hash",
                    )
                    service.store.connection.execute(
                        "INSERT INTO creation_jobs "
                        "(job_id, workspace_id, operation, state, progress, generation, "
                        "record_json) VALUES (?, ?, ?, 'queued', 'queued', 0, ?)",
                        (
                            job_id,
                            workspace["workspace_id"],
                            "runtime.bundle.build",
                            encode_json(record),
                        ),
                    )
                canceled = service.creation_jobs.cancel(
                    job_id,
                    expected_generation=0,
                    expected_record_hash=record["record_hash"],
                )
                self.assertEqual("canceled", canceled["state"])
                released = service.creation_output_grants.get(grant["grant_id"])
                self.assertEqual("ready", released["state"])
                self.assertEqual(reserved["generation"] + 1, released["generation"])
                self.assertIsNone(released["publication"])
                self.assertIsInstance(
                    service.creation_output_grants,
                    CreationOutputGrantManager,
                )
            finally:
                service.close()


class StudioRuntimeBundleRecoveryTests(unittest.TestCase):
    def test_exact_bundle_rollback_is_platform_truthful_and_never_deletes_foreign_bytes(
        self,
    ) -> None:
        from tests.test_multigenre_game_runtime_bundle import _build_bundle
        from worldforge.studio.creation_jobs import CreationJobCoordinator
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with _build_bundle("abstract-puzzle", base) as verified:
                destination = verified.root
                parent_info = destination.parent.stat()
                binding = {
                    "path": str(destination),
                    "parent_identity": (parent_info.st_dev, parent_info.st_ino),
                    "published_identity": verified.root_identity,
                    "expected_manifest_hash": verified.manifest["content_hash"],
                    "expected_tree_hash": verified.manifest["tree_hash"],
                }
            foreign = destination / "foreign.txt"
            foreign.write_bytes(b"foreign runtime bytes must survive")
            with self.assertRaises(StudioError):
                CreationJobCoordinator._rollback_runtime_bundle_publication(binding)
            self.assertEqual(b"foreign runtime bytes must survive", foreign.read_bytes())
            foreign.unlink()
            if sys.platform.startswith("linux") and os.name == "posix":
                with self.assertRaisesRegex(StudioError, "retains visible exact bytes") as raised:
                    CreationJobCoordinator._rollback_runtime_bundle_publication(binding)
                self.assertEqual(
                    "runtime_bundle_rollback_recovery_required",
                    raised.exception.details["reason_code"],
                )
                self.assertTrue(destination.is_dir())
            else:
                CreationJobCoordinator._rollback_runtime_bundle_publication(binding)
                self.assertFalse(destination.exists())

    def test_bundle_rollback_rejects_a_replaced_parent_without_redirecting_cleanup(self) -> None:
        from tests.test_multigenre_game_runtime_bundle import _build_bundle
        from worldforge.studio.creation_jobs import CreationJobCoordinator
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output_parent = base / "bundle-parent"
            output_parent.mkdir()
            with _build_bundle("abstract-puzzle", output_parent) as verified:
                destination = verified.root
                parent_info = destination.parent.stat()
                binding = {
                    "path": str(destination),
                    "parent_identity": (parent_info.st_dev, parent_info.st_ino),
                    "published_identity": verified.root_identity,
                    "expected_manifest_hash": verified.manifest["content_hash"],
                    "expected_tree_hash": verified.manifest["tree_hash"],
                }
            displaced = base / "bundle-parent-displaced"
            output_parent.rename(displaced)
            output_parent.mkdir()
            with self.assertRaises(StudioError):
                CreationJobCoordinator._rollback_runtime_bundle_publication(binding)
            self.assertEqual([], list(output_parent.iterdir()))
            self.assertTrue((displaced / destination.name).is_dir())


class StudioRuntimeBundleCoordinatorTests(unittest.TestCase):
    def test_runtime_bundle_job_publishes_pathless_candidate_with_exact_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace, before, artifact_ids, source_grant, assetpack_root = (
                _prepare_published_runtime_inputs(base)
            )
            try:
                published_source = service.creation_output_grants.get(source_grant["grant_id"])
                compose = service.creation_jobs.create_runtime_compose(
                    {
                        "job_id": "job_compose_for_runtime_bundle",
                        "workspace_id": workspace["workspace_id"],
                        "operation": "runtime.compose",
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": before["artifact_snapshot_hash"],
                        "gamepack_artifact_id": artifact_ids["world-forge.gamepack"],
                        "asset_inventory_artifact_id": artifact_ids["world-forge.asset_inventory"],
                        "assetpack_artifact_id": artifact_ids["world-forge.assetpack"],
                        "target_grant_id": source_grant["grant_id"],
                        "expected_target_grant_generation": published_source["generation"],
                    }
                )
                self.assertEqual(compose["job_id"], service.creation_job_coordinator.run_once())
                completed_compose = service.creation_jobs.get(compose["job_id"])
                self.assertEqual("succeeded", completed_compose["state"], completed_compose)
                runtime_ids = dict(
                    zip(
                        (
                            "runtime_snapshot_artifact_id",
                            "runtime_adapter_registry_artifact_id",
                            "runtime_composition_artifact_id",
                            "runtime_support_report_artifact_id",
                        ),
                        completed_compose["result"]["output_artifact_ids"],
                        strict=True,
                    )
                )
                evidence = service.creation_evidence.list(
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
                output_root = base / "outputs" / "puzzle-runtime-bundle"
                target_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_puzzle_runtime_bundle",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "game_runtime_bundle_directory",
                        "display_name": "puzzle-runtime-bundle",
                        "path": str(output_root),
                    }
                )
                queued = service.creation_jobs.create_runtime_bundle(
                    {
                        "job_id": "job_build_puzzle_runtime_bundle",
                        "workspace_id": workspace["workspace_id"],
                        "operation": "runtime.bundle.build",
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
                        "gamepack_artifact_id": artifact_ids["world-forge.gamepack"],
                        "asset_inventory_artifact_id": artifact_ids["world-forge.asset_inventory"],
                        "assetpack_artifact_id": artifact_ids["world-forge.assetpack"],
                        **runtime_ids,
                        "source_grant_id": source_grant["grant_id"],
                        "expected_source_grant_generation": published_source["generation"],
                        "target_grant_id": target_grant["grant_id"],
                        "expected_target_grant_generation": target_grant["generation"],
                    }
                )

                self.assertEqual(5, queued["format_version"])
                self.assertNotIn(str(assetpack_root), json.dumps(queued))
                self.assertNotIn(str(output_root), json.dumps(queued))
                self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                completed = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("succeeded", completed["state"], completed)
                self.assertEqual("committed", completed["progress"])
                self.assertEqual(1, len(completed["result"]["output_artifact_ids"]))
                publication = completed["result"]["publication"]
                self.assertEqual("game_runtime_bundle_directory", publication["kind"])
                self.assertEqual("published", publication["state"])
                self.assertNotIn("path", publication)
                with verify_game_runtime_bundle(output_root) as verified:
                    manifest = verified.manifest
                    self.assertEqual(
                        publication["runtime_bundle"],
                        {
                            "format": manifest["format"],
                            "format_version": manifest["format_version"],
                            "id": manifest["bundle_id"],
                            "content_hash": manifest["content_hash"],
                            "tree_hash": manifest["tree_hash"],
                        },
                    )
                    self.assertEqual(
                        artifact_ids["world-forge.gamepack"],
                        queued["operation_params"]["gamepack_artifact_id"],
                    )
                candidate = service.creation_artifacts.get_document(
                    workspace["workspace_id"],
                    completed["result"]["output_artifact_ids"][0],
                )
                self.assertEqual(manifest, candidate)
                self.assertEqual("pre_execution", candidate["state"])
                self.assertFalse(
                    service.creation_output_grants.get(source_grant["grant_id"])["publication"]
                    is None
                )
                from worldforge.studio.errors import StudioError
                from worldforge.studio.storage import encode_json

                candidate_row = service.store.connection.execute(
                    "SELECT record_json FROM creation_artifacts WHERE artifact_id = ?",
                    (completed["result"]["output_artifact_ids"][0],),
                ).fetchone()
                self.assertIsNotNone(candidate_row)
                tampered_candidate = json.loads(candidate_row["record_json"])
                tampered_candidate["authority"]["root_generation"] += 1
                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_artifacts SET record_json = ? WHERE artifact_id = ?",
                        (
                            encode_json(tampered_candidate),
                            completed["result"]["output_artifact_ids"][0],
                        ),
                    )
                with self.assertRaisesRegex(StudioError, "projection"):
                    service.creation_jobs.get(completed["job_id"])
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
