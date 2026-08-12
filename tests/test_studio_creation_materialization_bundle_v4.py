from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_studio_creation_asset_seal_v4 import _grant_record
from worldforge.studio.contracts import (
    StudioContractError,
    validate_studio_creation_output_grant,
)

ROOT = Path(__file__).resolve().parents[1]


def _prepare_published_runtime_bundle(base: Path):
    from tests.test_studio_creation_runtime_compose_v4 import (
        _prepare_published_runtime_inputs,
    )

    service, workspace, before, artifact_ids, assetpack_grant, _assetpack_root = (
        _prepare_published_runtime_inputs(base)
    )
    published_assetpack = service.creation_output_grants.get(assetpack_grant["grant_id"])
    compose = service.creation_jobs.create_runtime_compose(
        {
            "job_id": "job_compose_for_materialization",
            "workspace_id": workspace["workspace_id"],
            "operation": "runtime.compose",
            "expected_root_generation": workspace["root_generation"],
            "expected_source_revision": workspace["source_revision"],
            "expected_workflow_status_hash": workspace["workflow_status_hash"],
            "expected_artifact_snapshot_hash": before["artifact_snapshot_hash"],
            "gamepack_artifact_id": artifact_ids["world-forge.gamepack"],
            "asset_inventory_artifact_id": artifact_ids["world-forge.asset_inventory"],
            "assetpack_artifact_id": artifact_ids["world-forge.assetpack"],
            "target_grant_id": assetpack_grant["grant_id"],
            "expected_target_grant_generation": published_assetpack["generation"],
        }
    )
    service.creation_job_coordinator.run_once()
    completed_compose = service.creation_jobs.get(compose["job_id"])
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
    runtime_root = base / "outputs" / "runtime-bundle-for-materialization"
    runtime_grant = service.creation_output_grants.create(
        {
            "grant_id": "grant_runtime_for_materialization",
            "workspace_id": workspace["workspace_id"],
            "kind": "game_runtime_bundle_directory",
            "display_name": "runtime-for-materialization",
            "path": str(runtime_root),
        }
    )
    runtime_job = service.creation_jobs.create_runtime_bundle(
        {
            "job_id": "job_runtime_for_materialization",
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
            "source_grant_id": assetpack_grant["grant_id"],
            "expected_source_grant_generation": published_assetpack["generation"],
            "target_grant_id": runtime_grant["grant_id"],
            "expected_target_grant_generation": runtime_grant["generation"],
        }
    )
    service.creation_job_coordinator.run_once()
    completed_runtime = service.creation_jobs.get(runtime_job["job_id"])
    assert completed_runtime["state"] == "succeeded", completed_runtime
    return service, workspace, runtime_root, runtime_grant, completed_runtime


class StudioMaterializationBundleContractTests(unittest.TestCase):
    def test_phase_artifact_identity_accepts_the_published_runtime_bundle_id(self) -> None:
        from worldforge.phase_report_v3 import validate_artifact_identity

        identity = {
            "format": "world-forge.game_runtime_bundle",
            "format_version": 1,
            "id": "game_runtime_bundle_" + "a" * 48,
            "content_hash": "b" * 64,
        }
        self.assertEqual(identity, validate_artifact_identity(identity))

    def test_output_grant_v3_is_closed_and_prior_versions_remain_exact(self) -> None:
        from worldforge.studio.service import StudioService

        initialized = StudioService._initialize({}, protocol_version=4)  # noqa: SLF001
        self.assertTrue(initialized["capabilities"]["creation_materialization_bundle"])
        job_schema = json.loads(
            (ROOT / "schemas/studio-creation-job.schema.json").read_text(encoding="utf-8")
        )
        worker_schema = json.loads(
            (ROOT / "schemas/studio-creation-worker.schema.json").read_text(encoding="utf-8")
        )
        output_schema = json.loads(
            (ROOT / "schemas/studio-creation-output-grant.schema.json").read_text(encoding="utf-8")
        )
        protocol_schema = json.loads(
            (ROOT / "schemas/studio-protocol-v4.schema.json").read_text(encoding="utf-8")
        )
        catalog = json.loads((ROOT / "contracts/catalog.json").read_text(encoding="utf-8"))
        self.assertEqual("World Forge Studio creation job v9", job_schema["title"])
        self.assertEqual(9, len(job_schema["oneOf"]))
        self.assertEqual(
            "game.materialization.bundle.build",
            job_schema["oneOf"][5]["properties"]["operation"]["const"],
        )
        self.assertEqual(
            "World Forge Studio isolated creation worker envelope v11",
            worker_schema["title"],
        )
        self.assertEqual(33, len(worker_schema["oneOf"]))
        self.assertEqual("World Forge Studio creation output grant v5", output_schema["title"])
        self.assertEqual(10, len(protocol_schema["$defs"]["jobCreateParams"]["oneOf"]))
        self.assertIn(
            "game_materialization_bundle_directory",
            protocol_schema["$defs"]["outputGrantCreateParams"]["properties"]["kind"]["enum"],
        )
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        self.assertEqual(9, entries["studio-creation-job"]["version"])
        self.assertEqual(11, entries["studio-creation-worker"]["version"])
        self.assertEqual(5, entries["studio-creation-output-grant"]["version"])
        for contract_id in (
            "studio-creation-job",
            "studio-creation-worker",
            "studio-creation-output-grant",
            "studio-protocol-v4",
        ):
            self.assertIn(
                "tests/test_studio_creation_materialization_bundle_v4.py",
                entries[contract_id]["tests"],
            )
        legacy = _grant_record()
        runtime = {
            **legacy,
            "format_version": 2,
            "grant_id": "grant_runtime",
            "kind": "game_runtime_bundle_directory",
        }
        materialization = {
            **legacy,
            "format_version": 3,
            "grant_id": "grant_materialization",
            "kind": "game_materialization_bundle_directory",
        }
        for document in (legacy, runtime, materialization):
            self.assertEqual(
                document,
                validate_studio_creation_output_grant(copy.deepcopy(document)),
            )
        with self.assertRaisesRegex(StudioContractError, "unknown fields"):
            validate_studio_creation_output_grant(
                {**materialization, "native_path": "/private/native/path"}
            )

    def test_v6_worker_request_is_pathless_closed_and_deterministic(self) -> None:
        from tests.test_multigenre_game_runtime_bundle import _build_bundle
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio.creation_job_protocol import (
            build_private_materialization_bundle_request,
            execute_private_creation_request,
            validate_private_creation_request,
        )

        with tempfile.TemporaryDirectory(prefix="wf-studio-materialization-worker-") as temporary:
            root = Path(temporary)
            with _build_bundle("abstract-puzzle", root) as runtime_bundle:
                staged_inputs = [
                    {
                        "source_locator": locator,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size_bytes": len(payload),
                    }
                    for locator, payload in sorted(
                        runtime_bundle.files.items(),
                        key=lambda item: item[0].encode("utf-8"),
                    )
                ]
                request = build_private_materialization_bundle_request(
                    job_id="job_materialization_worker",
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
                    runtime_bundle_manifest=runtime_bundle.manifest,
                    source_grant_id="grant_runtime_source",
                    source_grant_generation=2,
                    target_grant_id="grant_materialization_target",
                    target_grant_generation=1,
                    staged_inputs=staged_inputs,
                )
                self.assertEqual(6, request["format_version"])
                self.assertEqual("game.materialization.bundle.build", request["operation"])
                self.assertNotIn(str(runtime_bundle.root), json.dumps(request))
                self.assertEqual(request, validate_private_creation_request(request))
                with self.assertRaisesRegex(ValueError, "fields"):
                    validate_private_creation_request(
                        {**request, "native_path": str(runtime_bundle.root)}
                    )
                first = execute_private_creation_request(
                    request,
                    artifact_root=runtime_bundle.root,
                )
                second = execute_private_creation_request(
                    request,
                    artifact_root=runtime_bundle.root,
                )
                self.assertEqual(1, len(first.outputs))
                self.assertEqual(
                    "world-forge.game_materialization_bundle",
                    first.outputs[0].subject["format"],
                )
                self.assertEqual(first.outputs[0].payload, second.outputs[0].payload)

    def test_public_v4_request_is_fixed_pathless_and_generation_bound(self) -> None:
        from worldforge.studio.contracts import validate_studio_protocol_envelope

        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "request",
            "request_id": "materialization-request-01",
            "method": "creation_job.create",
            "params": {
                "job_id": "job_materialization_request",
                "workspace_id": "workspace_puzzle",
                "operation": "game.materialization.bundle.build",
                "expected_root_generation": 1,
                "expected_source_revision": "a" * 64,
                "expected_workflow_status_hash": None,
                "expected_artifact_snapshot_hash": "b" * 64,
                "runtime_bundle_artifact_id": "artifact_runtime_bundle",
                "source_grant_id": "grant_runtime_bundle_source",
                "expected_source_grant_generation": 2,
                "target_grant_id": "grant_materialization_target",
                "expected_target_grant_generation": 0,
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(copy.deepcopy(request)))
        for leaked in (
            {**request["params"], "path": "/renderer/private"},
            {
                **request["params"],
                "kind": "game_materialization_bundle_directory",
            },
        ):
            with self.assertRaises(StudioContractError):
                validate_studio_protocol_envelope({**request, "params": leaked})


class StudioMaterializationBundleCoordinatorTests(unittest.TestCase):
    def test_queued_v6_cancellation_releases_only_the_reserved_target(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.integrity import canonical_payload_hash
        from worldforge.studio.storage import encode_json, utc_now

        with tempfile.TemporaryDirectory(prefix="wf-studio-materialization-cancel-") as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                output_parent = base / "outputs"
                output_parent.mkdir()
                grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_materialization_cancel",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "game_materialization_bundle_directory",
                        "display_name": "materialization-cancel",
                        "path": str(output_parent / "materialization-cancel"),
                    }
                )
                job_id = "job_materialization_cancel"
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
                        "format_version": 6,
                        "job_id": job_id,
                        "workspace_id": workspace["workspace_id"],
                        "operation": "game.materialization.bundle.build",
                        "operation_params": {
                            "runtime_bundle_artifact_id": "artifact_runtime_bundle_cancel",
                            "source_grant_id": "grant_runtime_bundle_source_cancel",
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
                            "game.materialization.bundle.build",
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
            finally:
                service.close()

    def test_restart_marks_v6_publication_and_grant_recovery_required(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.integrity import canonical_payload_hash
        from worldforge.studio.creation_output_grants import CreationOutputGrantManager
        from worldforge.studio.storage import StudioStore, decode_object, encode_json, utc_now

        with tempfile.TemporaryDirectory(prefix="wf-studio-materialization-restart-") as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            data_dir = service.store.data_dir
            output_parent = base / "outputs"
            output_parent.mkdir()
            grant = service.creation_output_grants.create(
                {
                    "grant_id": "grant_materialization_restart",
                    "workspace_id": workspace["workspace_id"],
                    "kind": "game_materialization_bundle_directory",
                    "display_name": "materialization-restart",
                    "path": str(output_parent / "materialization-restart"),
                }
            )
            job_id = "job_materialization_restart"
            with service.store.connection:
                reserved, _binding = service.creation_output_grants.reserve_for_job(
                    grant_id=grant["grant_id"],
                    job_id=job_id,
                    workspace_id=workspace["workspace_id"],
                    expected_generation=grant["generation"],
                    expected_manifest_hash="a" * 64,
                    expected_tree_hash="b" * 64,
                )
                service.creation_output_grants.begin_publication(job_id)
                timestamp = utc_now()
                record = {
                    "format": "world-forge.studio_creation_job",
                    "format_version": 6,
                    "job_id": job_id,
                    "workspace_id": workspace["workspace_id"],
                    "operation": "game.materialization.bundle.build",
                    "operation_params": {
                        "runtime_bundle_artifact_id": "artifact_runtime_bundle_restart",
                        "source_grant_id": "grant_runtime_bundle_source_restart",
                        "source_grant_generation": 2,
                        "target_grant_id": grant["grant_id"],
                        "target_grant_generation": reserved["generation"],
                    },
                    "state": "running",
                    "generation": 1,
                    "authority": {
                        "root_generation": workspace["root_generation"],
                        "source_revision": workspace["source_revision"],
                        "workflow_status_hash": workspace["workflow_status_hash"],
                        "artifact_snapshot_hash": "c" * 64,
                    },
                    "inputs": [],
                    "progress": "reserved",
                    "result": None,
                    "error": None,
                    "created_at": timestamp,
                    "started_at": timestamp,
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
                    "record_json) VALUES (?, ?, ?, 'running', 'reserved', 1, ?)",
                    (
                        job_id,
                        workspace["workspace_id"],
                        "game.materialization.bundle.build",
                        encode_json(record),
                    ),
                )
            service.close()
            service.store.close()
            with StudioStore(data_dir) as reopened:
                row = reopened.connection.execute(
                    "SELECT record_json FROM creation_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                assert row is not None
                orphaned = decode_object(row["record_json"], context="restarted materialization")
                self.assertEqual("orphaned", orphaned["state"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                recovered_grant = CreationOutputGrantManager(reopened).get(grant["grant_id"])
                self.assertEqual("recovery_required", recovered_grant["state"])
                self.assertEqual(reserved["generation"] + 1, recovered_grant["generation"])
            self.assertFalse((output_parent / "materialization-restart").exists())

    def test_exact_rollback_rejects_foreign_bytes_and_parent_replacement(self) -> None:
        from tests.test_multigenre_game_runtime_bundle import _build_bundle
        from worldforge.game_materialization_bundle import build_game_materialization_bundle
        from worldforge.studio.creation_jobs import CreationJobCoordinator
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory(prefix="wf-studio-materialization-rollback-") as temporary:
            base = Path(temporary)
            parent = base / "materialization-parent"
            parent.mkdir()
            with _build_bundle("abstract-puzzle", base) as runtime_bundle:
                with build_game_materialization_bundle(
                    parent / "materialization",
                    runtime_bundle_root=runtime_bundle.root,
                ) as verified:
                    destination = verified.root
                    parent_info = parent.stat()
                    binding = {
                        "path": str(destination),
                        "parent_identity": (parent_info.st_dev, parent_info.st_ino),
                        "published_identity": verified.root_identity,
                        "expected_manifest_hash": verified.manifest["content_hash"],
                        "expected_tree_hash": verified.manifest["tree_hash"],
                    }
                foreign = destination / "foreign.txt"
                foreign.write_bytes(b"foreign materialization bytes must survive")
                with self.assertRaises(StudioError):
                    CreationJobCoordinator._rollback_materialization_bundle_publication(binding)
                self.assertEqual(
                    b"foreign materialization bytes must survive",
                    foreign.read_bytes(),
                )
                foreign.unlink()
                displaced = base / "materialization-parent-displaced"
                parent.rename(displaced)
                parent.mkdir()
                with self.assertRaises(StudioError):
                    CreationJobCoordinator._rollback_materialization_bundle_publication(binding)
                self.assertEqual([], list(parent.iterdir()))
                self.assertTrue((displaced / destination.name).is_dir())
                if sys.platform.startswith("linux") and os.name == "posix":
                    self.assertTrue((displaced / destination.name).is_dir())

    def test_job_publishes_pathless_candidate_with_exact_runtime_lineage(self) -> None:
        from worldforge.game_materialization_bundle import verify_game_materialization_bundle
        from worldforge.studio.creation_jobs import CreationJobManager
        from worldforge.studio.errors import StudioError

        self.assertTrue(hasattr(CreationJobManager, "create_materialization_bundle"))

        with tempfile.TemporaryDirectory(prefix="wf-studio-materialization-job-") as temporary:
            base = Path(temporary)
            service, workspace, runtime_root, runtime_grant, runtime_job = (
                _prepare_published_runtime_bundle(base)
            )
            try:
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
                runtime_artifact_id = runtime_job["result"]["output_artifact_ids"][0]
                published_runtime = service.creation_output_grants.get(runtime_grant["grant_id"])
                target_root = base / "outputs" / "materialization-bundle"
                target_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_materialization_target",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "game_materialization_bundle_directory",
                        "display_name": "materialization-bundle",
                        "path": str(target_root),
                    }
                )
                create_params = {
                    "job_id": "job_materialization_bundle",
                    "workspace_id": workspace["workspace_id"],
                    "operation": "game.materialization.bundle.build",
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
                    "runtime_bundle_artifact_id": runtime_artifact_id,
                    "source_grant_id": runtime_grant["grant_id"],
                    "expected_source_grant_generation": published_runtime["generation"],
                    "target_grant_id": target_grant["grant_id"],
                    "expected_target_grant_generation": target_grant["generation"],
                }
                with self.assertRaises(StudioError):
                    service.creation_jobs.create_materialization_bundle(
                        {
                            **create_params,
                            "job_id": "job_materialization_stale_candidate",
                            "runtime_bundle_artifact_id": "artifact_stale_runtime_bundle",
                        }
                    )
                with self.assertRaises(StudioError):
                    service.creation_jobs.create_materialization_bundle(
                        {
                            **create_params,
                            "job_id": "job_materialization_stale_grant",
                            "expected_source_grant_generation": (
                                published_runtime["generation"] + 1
                            ),
                        }
                    )
                foreign = runtime_root / "foreign-runtime-byte.txt"
                foreign.write_bytes(b"unbound runtime bundle byte")
                try:
                    with self.assertRaises(StudioError):
                        service.creation_jobs.create_materialization_bundle(
                            {**create_params, "job_id": "job_materialization_tampered_source"}
                        )
                finally:
                    foreign.unlink()
                retained_target = service.creation_output_grants.get(target_grant["grant_id"])
                self.assertEqual("ready", retained_target["state"])
                self.assertEqual(target_grant["generation"], retained_target["generation"])
                queued = service.creation_jobs.create_materialization_bundle(create_params)
                self.assertEqual(6, queued["format_version"])
                self.assertNotIn(str(runtime_root), json.dumps(queued))
                self.assertNotIn(str(target_root), json.dumps(queued))
                self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                completed = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("succeeded", completed["state"], completed)
                self.assertEqual("committed", completed["progress"])
                self.assertEqual(1, len(completed["result"]["output_artifact_ids"]))
                publication = completed["result"]["publication"]
                self.assertEqual(
                    "game_materialization_bundle_directory",
                    publication["kind"],
                )
                self.assertEqual("published", publication["state"])
                self.assertNotIn("path", publication)
                with verify_game_materialization_bundle(target_root) as verified:
                    manifest = verified.manifest
                    self.assertEqual(
                        runtime_job["result"]["publication"]["runtime_bundle"]["content_hash"],
                        manifest["lineage"]["runtime_bundle_hash"],
                    )
                    self.assertEqual("materialization_ready", manifest["state"])
                    self.assertTrue(manifest["materialization_ready"])
                    self.assertEqual("blocked", verified.evidence["release"])
                candidate = service.creation_artifacts.get_document(
                    workspace["workspace_id"],
                    completed["result"]["output_artifact_ids"][0],
                )
                self.assertEqual(manifest, candidate)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
