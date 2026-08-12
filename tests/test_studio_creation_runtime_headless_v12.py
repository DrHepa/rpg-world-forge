from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gamepack_runtime.headless import (
    build_game_execution_script,
    serialize_game_execution_script,
)
from tests.test_multigenre_game_runtime_bundle import _build_bundle
from tests.test_multigenre_generic_headless import _bundle_document, _scenario_inputs


class StudioRuntimeHeadlessPrivateTreeTests(unittest.TestCase):
    def test_private_tree_build_and_exclusive_publish_reverify_exact_bytes(self) -> None:
        from worldforge.generic_headless import (
            build_headless_evidence_tree,
            publish_headless_evidence_tree,
            verify_headless_evidence_set,
        )

        with tempfile.TemporaryDirectory(prefix="wf-studio-headless-") as temporary:
            root = Path(temporary)
            bundle = _build_bundle("abstract-puzzle", root)
            try:
                script = build_game_execution_script(
                    bundle.manifest,
                    gamepack=_bundle_document(bundle, "contracts/gamepack.json"),
                    composition=_bundle_document(
                        bundle,
                        "contracts/runtime-composition.json",
                    ),
                    adapter=_bundle_document(
                        bundle,
                        bundle.manifest["contracts"]["runtime_adapter"]["path"],
                    ),
                    runtime_snapshot=_bundle_document(
                        bundle,
                        "contracts/runtime-snapshot.json",
                    ),
                    scenarios=_scenario_inputs("abstract-puzzle"),
                )
                script_bytes = serialize_game_execution_script(script)
                bundle_root = bundle.root
                bundle_hash = bundle.manifest["content_hash"]
            finally:
                bundle.close()

            private_tree = root / "worker-stage" / "headless-evidence"
            private_tree.parent.mkdir()
            destination = root / "published-headless-evidence"
            with mock.patch(
                "gamepack_runtime.headless._native_machine",
                return_value="x86_64",
            ):
                staged = build_headless_evidence_tree(
                    private_tree,
                    bundle_root=bundle_root,
                    script_bytes=script_bytes,
                    expected_bundle_hash=bundle_hash,
                )
            try:
                staged_manifest = staged.manifest
                staged_files = dict(staged.files)
                staged_identity = staged.root_identity
                self.assertEqual("headless_verified", staged.evidence["execution_status"])
                self.assertEqual("blocked", staged.evidence["release"])
                self.assertFalse(staged.evidence["supported"])
            finally:
                staged.close()

            with mock.patch(
                "gamepack_runtime.headless._native_machine",
                return_value="x86_64",
            ):
                published = publish_headless_evidence_tree(
                    private_tree,
                    destination,
                    bundle_root=bundle_root,
                    expected_content_hash=staged_manifest["content_hash"],
                    expected_tree_hash=staged_manifest["tree_hash"],
                    expected_source_identity=staged_identity,
                )
            try:
                self.assertNotEqual(staged_identity, published.root_identity)
                self.assertEqual(staged_manifest, published.manifest)
                self.assertEqual(staged_files, dict(published.files))
                self.assertTrue(private_tree.is_dir())
            finally:
                published.close()

            with mock.patch(
                "gamepack_runtime.headless._native_machine",
                return_value="x86_64",
            ):
                checked = verify_headless_evidence_set(
                    destination,
                    bundle_root=bundle_root,
                    expected_content_hash=staged_manifest["content_hash"],
                )
            checked.close()

            from worldforge.generic_headless import GenericHeadlessError

            unexpected = destination / "caller-status.json"
            unexpected.write_bytes(b"{}\n")
            with (
                mock.patch(
                    "gamepack_runtime.headless._native_machine",
                    return_value="x86_64",
                ),
                self.assertRaisesRegex(GenericHeadlessError, "exact file closure"),
            ):
                verify_headless_evidence_set(destination, bundle_root=bundle_root)
            unexpected.unlink()

            missing = destination / "runtime" / "evidence.json"
            missing_bytes = missing.read_bytes()
            missing.unlink()
            with (
                mock.patch(
                    "gamepack_runtime.headless._native_machine",
                    return_value="x86_64",
                ),
                self.assertRaises(GenericHeadlessError),
            ):
                verify_headless_evidence_set(destination, bundle_root=bundle_root)
            missing.write_bytes(missing_bytes)

            replacement = root / "replacement"
            with mock.patch(
                "gamepack_runtime.headless._native_machine",
                return_value="x86_64",
            ):
                replacement_verified = build_headless_evidence_tree(
                    replacement,
                    bundle_root=bundle_root,
                    script_bytes=script_bytes,
                    expected_bundle_hash=bundle_hash,
                )
            replacement_identity = replacement_verified.root_identity
            replacement_verified.close()
            with mock.patch(
                "gamepack_runtime.headless._native_machine",
                return_value="x86_64",
            ):
                replayed = publish_headless_evidence_tree(
                    replacement,
                    destination,
                    bundle_root=bundle_root,
                    expected_content_hash=staged_manifest["content_hash"],
                    expected_tree_hash=staged_manifest["tree_hash"],
                    expected_source_identity=replacement_identity,
                )
            try:
                self.assertEqual(published.root_identity, replayed.root_identity)
                self.assertNotEqual(replacement_identity, replayed.root_identity)
                self.assertEqual(staged_manifest, replayed.manifest)
            finally:
                replayed.close()


class StudioRuntimeHeadlessGrantV6Tests(unittest.TestCase):
    def test_v6_grant_is_pathless_publicly_and_keeps_v1_v5_unchanged(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.contracts import (
            validate_studio_creation_output_grant,
            validate_studio_creation_output_grant_v6,
        )

        with tempfile.TemporaryDirectory(prefix="wf-studio-headless-") as temporary:
            root = Path(temporary)
            service, workspace = _prepared_creation_service(root)
            try:
                output_parent = root / "outputs"
                output_parent.mkdir()
                grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_headless_evidence",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "headless_evidence_directory",
                        "display_name": "headless-evidence",
                        "path": str(output_parent / "headless-evidence"),
                    }
                )
                self.assertEqual(6, grant["format_version"])
                self.assertEqual("ready", grant["state"])
                self.assertNotIn("path", grant)
                self.assertEqual(grant, validate_studio_creation_output_grant_v6(grant))
                with self.assertRaisesRegex(ValueError, "format_version"):
                    validate_studio_creation_output_grant(grant)

                leaked = {**grant, "path": str(output_parent / "headless-evidence")}
                with self.assertRaisesRegex(ValueError, "unknown fields"):
                    validate_studio_creation_output_grant_v6(leaked)

                legacy_kinds = {
                    "generic_assetpack_directory": 1,
                    "game_runtime_bundle_directory": 2,
                    "game_materialization_bundle_directory": 3,
                    "standalone_game_directory": 4,
                    "game_package_file": 5,
                }
                for kind, version in legacy_kinds.items():
                    with self.subTest(kind=kind):
                        sibling = service.creation_output_grants.create(
                            {
                                "grant_id": f"grant_legacy_{version}",
                                "workspace_id": workspace["workspace_id"],
                                "kind": kind,
                                "display_name": f"legacy-{version}",
                                "path": str(output_parent / f"legacy-{version}"),
                            }
                        )
                        self.assertEqual(version, sibling["format_version"])
            finally:
                service.close()
                service.store.close()

    def test_restart_preserves_exact_retained_v12_grant_without_requiring_v11_grant(
        self,
    ) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.contracts import (
            CREATION_JOB_FORMAT,
            creation_job_record_hash,
            validate_studio_creation_job,
        )
        from worldforge.studio.errors import StudioError
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore, encode_json

        def running_job(
            *,
            version: int,
            job_id: str,
            operation: str,
            operation_params: dict[str, object],
            workspace_id: str,
        ) -> dict[str, object]:
            timestamp = "2026-08-01T00:00:00.000000Z"
            record: dict[str, object] = {
                "format": CREATION_JOB_FORMAT,
                "format_version": version,
                "job_id": job_id,
                "workspace_id": workspace_id,
                "operation": operation,
                "operation_params": operation_params,
                "state": "running",
                "generation": 0,
                "authority": {
                    "root_generation": 1,
                    "source_revision": "a" * 64,
                    "workflow_status_hash": "b" * 64,
                    "artifact_snapshot_hash": "c" * 64,
                },
                "inputs": [],
                "progress": "worker_started",
                "result": None,
                "error": None,
                "created_at": timestamp,
                "started_at": timestamp,
                "finished_at": None,
                "updated_at": timestamp,
                "record_hash": "",
            }
            record["record_hash"] = creation_job_record_hash(record)
            return validate_studio_creation_job(record)

        with tempfile.TemporaryDirectory(prefix="wf-studio-headless-restart-") as temporary:
            root = Path(temporary)
            service, workspace = _prepared_creation_service(root)
            reopened = None
            restarted = None
            try:
                output_parent = root / "outputs"
                output_parent.mkdir()
                target = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_headless_restart_v12",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "headless_evidence_directory",
                        "display_name": "headless-restart-v12",
                        "path": str(output_parent / "headless-restart-v12"),
                    }
                )
                v11 = running_job(
                    version=11,
                    job_id="job_blocked_release_without_grant_v11",
                    operation="asset.release.authorize",
                    operation_params={
                        "review_receipt_artifact_ids": ["artifact_review_v11"],
                        "manifest_id": "manifest_v11",
                        "assetpack_id": "assetpack_v11",
                        "release_authority_id": "release_authority_v11",
                        "blockers": ["asset_review_rejected"],
                        "target_grant_id": "grant_not_reserved_v11",
                        "target_grant_generation": 0,
                    },
                    workspace_id=workspace["workspace_id"],
                )
                v12 = running_job(
                    version=12,
                    job_id="job_headless_restart_v12",
                    operation="runtime.headless.verify",
                    operation_params={
                        "gamepack_artifact_id": "artifact_gamepack_v12",
                        "asset_inventory_artifact_id": "artifact_inventory_v12",
                        "assetpack_artifact_id": "artifact_assetpack_v12",
                        "asset_release_authority_artifact_id": "artifact_release_v12",
                        "runtime_snapshot_artifact_id": "artifact_snapshot_v12",
                        "runtime_adapter_registry_artifact_id": "artifact_registry_v12",
                        "runtime_composition_artifact_id": "artifact_composition_v12",
                        "runtime_bundle_artifact_id": "artifact_bundle_v12",
                        "source_grant_id": "grant_runtime_source_v12",
                        "expected_source_grant_generation": 4,
                        "platform_id": "platform:linux_x86_64",
                        "headless_script_artifact_id": "artifact_script_v12",
                        "target_grant_id": target["grant_id"],
                        "expected_target_grant_generation": target["generation"],
                    },
                    workspace_id=workspace["workspace_id"],
                )
                with service.store.connection:
                    for record in (v11, v12):
                        service.store.connection.execute(
                            "INSERT INTO creation_jobs "
                            "(job_id, workspace_id, operation, state, progress, generation, "
                            "record_json) VALUES (?, ?, ?, 'running', 'worker_started', 0, ?)",
                            (
                                record["job_id"],
                                record["workspace_id"],
                                record["operation"],
                                encode_json(record),
                            ),
                        )
                    service.creation_output_grants.reserve_for_job(
                        grant_id=target["grant_id"],
                        job_id=v12["job_id"],
                        workspace_id=workspace["workspace_id"],
                        expected_generation=target["generation"],
                        expected_manifest_hash="d" * 64,
                        expected_tree_hash="e" * 64,
                    )
                    service.creation_output_grants.begin_publication(v12["job_id"])

                data_dir = service.store.data_dir
                service.close()
                service.store.close()
                reopened = StudioStore(data_dir)
                restarted = StudioService(reopened)

                states = {
                    row["job_id"]: row["state"]
                    for row in reopened.connection.execute(
                        "SELECT job_id, state FROM creation_jobs ORDER BY sequence"
                    )
                }
                self.assertEqual("orphaned", states[v11["job_id"]])
                self.assertEqual("orphaned", states[v12["job_id"]])
                recovered = reopened.connection.execute(
                    "SELECT state, generation FROM creation_output_grants WHERE grant_id = ?",
                    (target["grant_id"],),
                ).fetchone()
                self.assertEqual("reserved", recovered["state"])
                self.assertEqual(1, recovered["generation"])

                orphaned = restarted.creation_jobs.get(v12["job_id"])
                binding = restarted.creation_output_grants.binding_for_job(
                    v12["job_id"],
                    allow_visible=None,
                )
                verify_recovery = (
                    restarted.creation_job_coordinator._verify_runtime_headless_recovery_binding
                )
                verify_recovery(
                    orphaned,
                    binding,
                    published_identity=None,
                    require_verified=False,
                )
                wrong_job = copy.deepcopy(orphaned)
                wrong_job["job_id"] = "job_wrong_headless_owner"
                with self.assertRaisesRegex(StudioError, "reservation|job"):
                    verify_recovery(
                        wrong_job,
                        binding,
                        published_identity=None,
                        require_verified=False,
                    )
            finally:
                if restarted is not None:
                    restarted.close()
                if reopened is not None:
                    reopened.close()
                else:
                    service.close()
                    service.store.close()


class StudioRuntimeHeadlessV12ClosedRequestTests(unittest.TestCase):
    def test_public_request_rejects_every_caller_execution_or_result_claim(self) -> None:
        from worldforge.studio.contracts import validate_studio_protocol_envelope

        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 5,
            "kind": "request",
            "request_id": "verify_headless",
            "method": "creation_job.create",
            "params": {
                "workspace_id": "workspace_01",
                "operation": "runtime.headless.verify",
                "expected_root_generation": 3,
                "expected_source_revision": "a" * 64,
                "expected_workflow_status_hash": "b" * 64,
                "expected_artifact_snapshot_hash": "c" * 64,
                "gamepack_artifact_id": "artifact_gamepack",
                "asset_inventory_artifact_id": "artifact_inventory",
                "assetpack_artifact_id": "artifact_assetpack",
                "asset_release_authority_artifact_id": "artifact_release_authority",
                "runtime_snapshot_artifact_id": "artifact_runtime_snapshot",
                "runtime_adapter_registry_artifact_id": "artifact_runtime_registry",
                "runtime_composition_artifact_id": "artifact_runtime_composition",
                "runtime_bundle_artifact_id": "artifact_runtime_bundle",
                "source_grant_id": "grant_runtime_bundle",
                "expected_source_grant_generation": 4,
                "platform_id": "platform:linux_x86_64",
                "headless_script_artifact_id": "artifact_execution_script",
                "target_grant_id": "grant_headless_evidence",
                "expected_target_grant_generation": 0,
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        for forbidden, value in {
            "path": "/caller/path",
            "command": ["python", "script.py"],
            "script": {"format": "world-forge.game_execution_script"},
            "script_bytes": "e30=",
            "status": "passed",
            "content_hash": "d" * 64,
            "headless_id": "caller_headless",
            "provider": "caller",
            "env": {"TOKEN": "secret"},
        }.items():
            with self.subTest(forbidden=forbidden):
                leaked = copy.deepcopy(request)
                leaked["params"][forbidden] = value
                with self.assertRaisesRegex(ValueError, "invalid fields|unknown fields"):
                    validate_studio_protocol_envelope(leaked)

        wrong_platform = copy.deepcopy(request)
        wrong_platform["params"]["platform_id"] = "linux-x86_64"
        with self.assertRaisesRegex(ValueError, "platform"):
            validate_studio_protocol_envelope(wrong_platform)

        same_grant = copy.deepcopy(request)
        same_grant["params"]["target_grant_id"] = same_grant["params"]["source_grant_id"]
        with self.assertRaisesRegex(ValueError, "source|target|distinct"):
            validate_studio_protocol_envelope(same_grant)

        self.assertNotIn("/caller", json.dumps(request))


class StudioRuntimeHeadlessV12CoordinatorTests(unittest.TestCase):
    def test_exact_retained_chain_publishes_and_reconstructs_after_restart(self) -> None:
        from tests.test_studio_creation_asset_release_v11 import _snapshot
        from tests.test_studio_creation_runtime_compose_v4 import (
            _prepare_published_runtime_inputs,
        )
        from worldforge.game_runtime_bundle import verify_game_runtime_bundle
        from worldforge.generic_headless import verify_headless_evidence_set
        from worldforge.phase_report_v3 import (
            artifact_dependency_identities,
            document_identity,
        )
        from worldforge.studio.creation_executor import (
            CreationWorkerExecution,
            _verified_outputs,
        )
        from worldforge.studio.creation_runtime_authority import (
            StudioRuntimeAuthorityResolver,
        )
        from worldforge.studio.creation_worker import _execute
        from worldforge.studio.errors import StudioError
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        def inline_worker(
            stage: Path,
            _stage_identity: tuple[int, int],
            envelope: object,
            **_kwargs: object,
        ) -> CreationWorkerExecution:
            import os

            from worldforge.studio.creation_process import creation_process_identity

            proof = creation_process_identity(os.getpid())
            started = _kwargs.get("process_started")
            stopped = _kwargs.get("process_stopped")
            if started is not None:
                started(os.getpid(), proof)
            try:
                response = _execute(envelope, stage)
                return CreationWorkerExecution(response, _verified_outputs(stage, response))
            finally:
                if stopped is not None:
                    stopped(os.getpid(), proof)

        with tempfile.TemporaryDirectory(prefix="wf-studio-headless-chain-") as temporary:
            base = Path(temporary)
            service, workspace, before, artifact_ids, asset_grant, _assetpack_root = (
                _prepare_published_runtime_inputs(base)
            )
            reopened = None
            try:
                published_asset = service.creation_output_grants.get(asset_grant["grant_id"])
                compose = service.creation_jobs.create_runtime_compose(
                    {
                        "job_id": "job_compose_for_headless_v12",
                        "workspace_id": workspace["workspace_id"],
                        "operation": "runtime.compose",
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": before["artifact_snapshot_hash"],
                        "gamepack_artifact_id": artifact_ids["world-forge.gamepack"],
                        "asset_inventory_artifact_id": artifact_ids["world-forge.asset_inventory"],
                        "assetpack_artifact_id": artifact_ids["world-forge.assetpack"],
                        "target_grant_id": asset_grant["grant_id"],
                        "expected_target_grant_generation": published_asset["generation"],
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

                output_parent = base / "outputs"
                runtime_root = output_parent / "puzzle-runtime-bundle-v12"
                runtime_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_runtime_bundle_v12",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "game_runtime_bundle_directory",
                        "display_name": "runtime-bundle-v12",
                        "path": str(runtime_root),
                    }
                )
                current = _snapshot(service, workspace)
                runtime_job = service.creation_jobs.create_runtime_bundle(
                    {
                        "job_id": "job_runtime_bundle_for_headless_v12",
                        "workspace_id": workspace["workspace_id"],
                        "operation": "runtime.bundle.build",
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                        "gamepack_artifact_id": artifact_ids["world-forge.gamepack"],
                        "asset_inventory_artifact_id": artifact_ids["world-forge.asset_inventory"],
                        "assetpack_artifact_id": artifact_ids["world-forge.assetpack"],
                        **runtime_ids,
                        "source_grant_id": asset_grant["grant_id"],
                        "expected_source_grant_generation": published_asset["generation"],
                        "target_grant_id": runtime_grant["grant_id"],
                        "expected_target_grant_generation": runtime_grant["generation"],
                    }
                )
                self.assertEqual(runtime_job["job_id"], service.creation_job_coordinator.run_once())
                completed_runtime = service.creation_jobs.get(runtime_job["job_id"])
                self.assertEqual("succeeded", completed_runtime["state"], completed_runtime)
                runtime_artifact_id = completed_runtime["result"]["output_artifact_ids"][0]
                published_runtime = service.creation_output_grants.get(runtime_grant["grant_id"])
                with verify_game_runtime_bundle(runtime_root) as verified_runtime:
                    script = build_game_execution_script(
                        verified_runtime.manifest,
                        gamepack=_bundle_document(
                            verified_runtime,
                            "contracts/gamepack.json",
                        ),
                        composition=_bundle_document(
                            verified_runtime,
                            "contracts/runtime-composition.json",
                        ),
                        adapter=_bundle_document(
                            verified_runtime,
                            verified_runtime.manifest["contracts"]["runtime_adapter"]["path"],
                        ),
                        runtime_snapshot=_bundle_document(
                            verified_runtime,
                            "contracts/runtime-snapshot.json",
                        ),
                        scenarios=_scenario_inputs("abstract-puzzle"),
                    )

                current = _snapshot(service, workspace)
                project = service.creation_workspaces._refresh_snapshot(  # noqa: SLF001
                    workspace["workspace_id"]
                )[1]
                source_keys = {
                    tuple(document_identity(document).values())
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
                artifact_by_key = {
                    tuple(record["subject"].values()): record["artifact_id"]
                    for record in current["artifacts"]
                    if record["lifecycle"] == "candidate"
                }
                document_by_key = {
                    tuple(record["subject"].values()): service.creation_artifacts.get_document(
                        workspace["workspace_id"],
                        record["artifact_id"],
                    )
                    for record in current["artifacts"]
                    if record["lifecycle"] == "candidate"
                }
                required_keys: set[tuple[object, ...]] = set()
                pending = list(artifact_dependency_identities(script))
                while pending:
                    identity = pending.pop()
                    key = tuple(identity.values())
                    if key in source_keys or key in required_keys:
                        continue
                    dependency = document_by_key[key]
                    required_keys.add(key)
                    pending.extend(artifact_dependency_identities(dependency))
                dependency_ids = sorted(
                    (artifact_by_key[key] for key in required_keys),
                    key=lambda item: item.encode("utf-8"),
                )
                admission = service.creation_jobs.create_admission(
                    {
                        "job_id": "job_admit_headless_script_v12",
                        "workspace_id": workspace["workspace_id"],
                        "operation": "artifact.admit",
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                        "document": script,
                        "dependency_artifact_ids": dependency_ids,
                    }
                )
                with mock.patch(
                    "worldforge.studio.creation_jobs.run_isolated_creation_worker",
                    side_effect=inline_worker,
                ):
                    self.assertEqual(
                        admission["job_id"], service.creation_job_coordinator.run_once()
                    )
                completed_admission = service.creation_jobs.get(admission["job_id"])
                self.assertEqual("succeeded", completed_admission["state"])
                script_artifact_id = completed_admission["result"]["output_artifact_ids"][0]

                release = service.creation_jobs.get("job_authorize_runtime_assetpack")
                release_authority_artifact_id = release["result"]["output_artifact_ids"][2]
                target_root = output_parent / "puzzle-headless-v12"
                target_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_headless_target_v12",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "headless_evidence_directory",
                        "display_name": "headless-target-v12",
                        "path": str(target_root),
                    }
                )
                current = _snapshot(service, workspace)
                headless_params = {
                    "job_id": "job_verify_headless_v12",
                    "workspace_id": workspace["workspace_id"],
                    "operation": "runtime.headless.verify",
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                    "gamepack_artifact_id": artifact_ids["world-forge.gamepack"],
                    "asset_inventory_artifact_id": artifact_ids["world-forge.asset_inventory"],
                    "assetpack_artifact_id": artifact_ids["world-forge.assetpack"],
                    "asset_release_authority_artifact_id": release_authority_artifact_id,
                    **{
                        key: value
                        for key, value in runtime_ids.items()
                        if key != "runtime_support_report_artifact_id"
                    },
                    "runtime_bundle_artifact_id": runtime_artifact_id,
                    "source_grant_id": runtime_grant["grant_id"],
                    "expected_source_grant_generation": published_runtime["generation"],
                    "platform_id": "platform:linux_x86_64",
                    "headless_script_artifact_id": script_artifact_id,
                    "target_grant_id": target_grant["grant_id"],
                    "expected_target_grant_generation": target_grant["generation"],
                }
                with (
                    mock.patch(
                        "gamepack_runtime.headless._native_machine",
                        return_value="x86_64",
                    ),
                    mock.patch(
                        "worldforge.studio.creation_jobs.run_isolated_creation_worker",
                        side_effect=inline_worker,
                    ),
                ):
                    queued = service.creation_jobs.create_runtime_headless(headless_params)
                    with (
                        mock.patch.object(
                            service.creation_job_coordinator,
                            "_commit_registry",
                            side_effect=SystemExit("simulated v12 post-publication registry crash"),
                        ),
                        self.assertRaisesRegex(SystemExit, "post-publication"),
                    ):
                        service.creation_job_coordinator.run_once()
                    self.assertTrue(target_root.is_dir())
                    pre_restart_binding = service.creation_output_grants.binding_for_job(
                        queued["job_id"],
                        allow_visible=True,
                    )
                    self.assertEqual(
                        "publication_verified",
                        pre_restart_binding["recovery"]["phase"],
                    )
                    published_identity = pre_restart_binding["published_identity"]
                    self.assertIsNotNone(published_identity)
                    with service.store.connection:
                        first_note = service.creation_output_grants.note_publication_verified(
                            queued["job_id"],
                            published_identity=published_identity,
                        )
                        second_note = service.creation_output_grants.note_publication_verified(
                            queued["job_id"],
                            published_identity=published_identity,
                        )
                    self.assertEqual(first_note, second_note)
                    with self.assertRaisesRegex(StudioError, "reservation|unavailable"):
                        service.creation_output_grants.note_publication_verified(
                            "job_not_headless_owner",
                            published_identity=published_identity,
                        )

                    data_dir = service.store.data_dir
                    service.close()
                    service.store.close()
                    service = StudioService(StudioStore(data_dir))
                    orphaned = service.creation_jobs.get(queued["job_id"])
                    self.assertEqual("orphaned", orphaned["state"], orphaned)
                    attempt = service.store.connection.execute(
                        "SELECT phase FROM creation_job_attempts WHERE job_id = ?",
                        (orphaned["job_id"],),
                    ).fetchone()
                    self.assertEqual("registry_committing", attempt["phase"])
                    recovery_binding = service.creation_output_grants.binding_for_job(
                        orphaned["job_id"],
                        allow_visible=True,
                    )
                    verify_recovery = (
                        service.creation_job_coordinator._verify_runtime_headless_recovery_binding
                    )
                    verify_recovery(
                        orphaned,
                        recovery_binding,
                        published_identity=published_identity,
                        require_verified=True,
                    )
                    wrong_generation_binding = copy.deepcopy(recovery_binding)
                    wrong_generation_binding["generation"] += 1
                    with self.assertRaisesRegex(StudioError, "generation"):
                        verify_recovery(
                            orphaned,
                            wrong_generation_binding,
                            published_identity=published_identity,
                            require_verified=True,
                        )
                    wrong_recovery_binding = copy.deepcopy(recovery_binding)
                    wrong_recovery_binding["recovery"]["expected_tree_hash"] = "0" * 64
                    with self.assertRaisesRegex(StudioError, "recovery metadata"):
                        verify_recovery(
                            orphaned,
                            wrong_recovery_binding,
                            published_identity=published_identity,
                            require_verified=True,
                        )
                    wrong_identity_binding = copy.deepcopy(recovery_binding)
                    wrong_identity_binding["recovery"]["published_identity"] = [
                        published_identity[0],
                        published_identity[1] + 1,
                    ]
                    with self.assertRaisesRegex(StudioError, "identity"):
                        verify_recovery(
                            orphaned,
                            wrong_identity_binding,
                            published_identity=published_identity,
                            require_verified=True,
                        )
                    before_wrong_generation = service.creation_output_grants.get(
                        target_grant["grant_id"]
                    )
                    self.assertEqual("reserved", before_wrong_generation["state"])
                    with self.assertRaisesRegex(StudioError, "changed"):
                        service.creation_jobs.recover(
                            orphaned["job_id"],
                            mode="resume",
                            expected_generation=orphaned["generation"] + 1,
                            expected_record_hash=orphaned["record_hash"],
                        )
                    self.assertEqual(
                        before_wrong_generation,
                        service.creation_output_grants.get(target_grant["grant_id"]),
                    )
                    service.creation_job_coordinator._recover_cleanup_with_evidence(
                        orphaned,
                        allow_requeue_retirement=True,
                    )
                    service.creation_job_coordinator._recover_cleanup_with_evidence(
                        orphaned,
                        allow_requeue_retirement=True,
                    )
                    resumed = service.creation_jobs.recover(
                        orphaned["job_id"],
                        mode="resume",
                        expected_generation=orphaned["generation"],
                        expected_record_hash=orphaned["record_hash"],
                    )
                    self.assertEqual("queued", resumed["state"], resumed)
                    resumed_grant = service.creation_output_grants.get(target_grant["grant_id"])
                    self.assertEqual("reserved", resumed_grant["state"])
                    self.assertEqual(
                        resumed_grant["generation"],
                        resumed["operation_params"]["expected_target_grant_generation"] + 1,
                    )
                    with self.assertRaisesRegex(StudioError, "changed|recoverable"):
                        service.creation_jobs.recover(
                            orphaned["job_id"],
                            mode="resume",
                            expected_generation=orphaned["generation"],
                            expected_record_hash=orphaned["record_hash"],
                        )
                    self.assertEqual(
                        resumed_grant,
                        service.creation_output_grants.get(target_grant["grant_id"]),
                    )
                    self.assertEqual(
                        resumed["job_id"],
                        service.creation_job_coordinator.run_once(),
                    )
                    completed = service.creation_jobs.get(queued["job_id"])
                    self.assertEqual("succeeded", completed["state"], completed)
                    self.assertEqual("committed", completed["progress"])
                    self.assertEqual("blocked", completed["result"]["release_status"])
                    self.assertEqual("unavailable", completed["result"]["native_status"])
                    self.assertFalse(completed["result"]["supported"])
                    published_target_grant = service.creation_output_grants.get(
                        target_grant["grant_id"]
                    )
                    self.assertEqual("published", published_target_grant["state"])
                    self.assertEqual(
                        resumed_grant["generation"] + 1,
                        published_target_grant["generation"],
                    )
                    self.assertEqual(
                        published_target_grant["generation"],
                        completed["result"]["publication"]["grant_generation"],
                    )
                    self.assertNotIn(str(target_root), json.dumps(completed))
                    self.assertNotIn(str(runtime_root), json.dumps(completed))
                    self.assertEqual(3, len(completed["result"]["output_artifact_ids"]))
                    retained = tuple(
                        service.creation_artifacts.get_document(
                            workspace["workspace_id"],
                            artifact_id,
                        )
                        for artifact_id in completed["result"]["output_artifact_ids"]
                    )
                    rebuilt = StudioRuntimeAuthorityResolver(
                        service.store,
                        artifacts=service.creation_artifacts,
                    ).reconstruct(job=completed, retained_documents=retained)
                    self.assertEqual(retained, rebuilt.documents)
                    with verify_headless_evidence_set(
                        target_root,
                        bundle_root=runtime_root,
                    ) as verified_evidence:
                        self.assertEqual(
                            rebuilt.evidence_manifest,
                            verified_evidence.manifest,
                        )

                    reopened = StudioService(StudioStore(data_dir, mode="secondary"))
                    original_reconstruct = StudioRuntimeAuthorityResolver.reconstruct
                    reconstruct_calls: list[str] = []

                    def tracked_reconstruct(
                        resolver: StudioRuntimeAuthorityResolver,
                        *,
                        job: object,
                        retained_documents: object,
                    ) -> object:
                        reconstruct_calls.append(str(job["job_id"]))
                        return original_reconstruct(
                            resolver,
                            job=job,
                            retained_documents=retained_documents,
                        )

                    with mock.patch.object(
                        StudioRuntimeAuthorityResolver,
                        "reconstruct",
                        new=tracked_reconstruct,
                    ):
                        restarted = reopened.creation_jobs.get(completed["job_id"])
                    self.assertEqual(completed, restarted)
                    self.assertIn(completed["job_id"], reconstruct_calls)

                    retained_evidence_path = target_root / "runtime" / "evidence.json"
                    retained_evidence_path.write_bytes(retained_evidence_path.read_bytes() + b" ")
                    with self.assertRaisesRegex(
                        StudioError,
                        "authority|projection|diverged",
                    ):
                        reopened.creation_jobs.get(completed["job_id"])
            finally:
                if reopened is not None:
                    reopened.close()
                    reopened.store.close()
                service.close()
                service.store.close()


if __name__ == "__main__":
    unittest.main()
