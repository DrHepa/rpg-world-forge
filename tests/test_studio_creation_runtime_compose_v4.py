from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _fixture(name: str, relative: str) -> dict[str, object]:
    return json.loads(
        (_ROOT / "examples" / "multigenre-contracts" / name / relative).read_text(encoding="utf-8")
    )


def _runtime_inputs() -> tuple[dict[str, object], dict[str, object]]:
    from worldforge.generic_runtime import (
        build_builtin_runtime_adapters,
        build_game_runtime_snapshot,
        build_runtime_adapter_registry,
    )

    adapters = build_builtin_runtime_adapters()
    snapshot = build_game_runtime_snapshot(
        _ROOT / "src" / "gamepack_runtime",
        adapter_runtime_root=_ROOT / "src" / "gamepack_raylib_2d",
        adapters=adapters,
    )
    registry = build_runtime_adapter_registry(snapshot=snapshot, adapters=adapters)
    return snapshot, registry


class RuntimeCompositionDependencyBoundaryTests(unittest.TestCase):
    def test_composition_dependency_closure_includes_every_direct_subject(self) -> None:
        from worldforge.phase_report_v3 import artifact_dependency_identities

        composition = _fixture(
            "abstract-puzzle",
            "runtime/composition.json",
        )
        dependencies = artifact_dependency_identities(composition)

        self.assertEqual(
            {
                "world-forge.gamepack",
                "world-forge.asset_inventory",
                "world-forge.assetpack",
                "world-forge.runtime_adapter_registry",
                "world-forge.game_runtime_snapshot",
            },
            {identity["format"] for identity in dependencies},
        )
        self.assertEqual(5, len(dependencies))


class RuntimeBuildReadinessBoundaryTests(unittest.TestCase):
    def test_optional_gap_allows_materialization_but_release_stays_blocked(self) -> None:
        from worldforge.__main__ import _resolve_generic_assetpack_cli_source
        from worldforge.generic_assetpack import seal_generic_assetpack
        from worldforge.generic_runtime import (
            build_game_runtime_composition,
            build_runtime_support_report,
            resolve_runtime_build_readiness,
        )

        gamepack = _fixture(
            "abstract-puzzle",
            "artifacts/abstract-puzzle.gamepack.json",
        )
        inventory = _fixture("abstract-puzzle", "assets/inventory.json")
        snapshot, registry = _runtime_inputs()
        readiness = resolve_runtime_build_readiness(
            gamepack,
            registry=registry,
            snapshot=snapshot,
        )

        self.assertEqual("materialization_ready", readiness["status"])
        self.assertEqual([], readiness["missing_required_feature_ids"])
        self.assertEqual(["audio:sfx"], readiness["missing_optional_feature_ids"])
        self.assertEqual(["optional_feature_unsupported"], readiness["reason_codes"])

        source = _resolve_generic_assetpack_cli_source(
            _ROOT / "examples/multigenre-contracts/abstract-puzzle/assets/manifest.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            verified = seal_generic_assetpack(Path(temporary) / "assetpack", **source)
            try:
                composition = build_game_runtime_composition(
                    gamepack,
                    inventory,
                    verified.root,
                    registry=registry,
                    snapshot=snapshot,
                )
            finally:
                verified.close()
            report = build_runtime_support_report(
                composition,
                gamepack=gamepack,
                registry=registry,
                snapshot=snapshot,
                evidence=[],
            )
        self.assertFalse(report["supported"])
        self.assertEqual("blocked", report["dimensions"]["release"])
        self.assertIn("native_evidence_missing", report["reason_codes"])
        self.assertIn("packaging_evidence_missing", report["reason_codes"])

    def test_required_gap_blocks_materialization_with_precise_reasons(self) -> None:
        from worldforge.creation_contracts import canonical_creation_hash
        from worldforge.generic_runtime import resolve_runtime_build_readiness

        gamepack = _fixture(
            "abstract-puzzle",
            "artifacts/abstract-puzzle.gamepack.json",
        )
        gamepack = copy.deepcopy(gamepack)
        gamepack["runtime_requirements"]["required_features"].append("audio:sfx")  # type: ignore[index]
        gamepack["runtime_requirements"]["required_features"].sort()  # type: ignore[index]
        gamepack["runtime_requirements"]["optional_features"] = []  # type: ignore[index]
        gamepack["mechanic_requirements"][0]["required_feature_ids"].append(  # type: ignore[index]
            "audio:sfx"
        )
        gamepack["mechanic_requirements"][0]["required_feature_ids"].sort()  # type: ignore[index]
        gamepack["logic"]["mechanics"][0]["required_feature_ids"].append(  # type: ignore[index]
            "audio:sfx"
        )
        gamepack["logic"]["mechanics"][0]["required_feature_ids"].sort()  # type: ignore[index]
        gamepack["logic"]["actions"][0]["required_feature_ids"].append(  # type: ignore[index]
            "audio:sfx"
        )
        gamepack["logic"]["actions"][0]["required_feature_ids"].sort()  # type: ignore[index]
        gamepack["content_hash"] = canonical_creation_hash(gamepack)
        snapshot, registry = _runtime_inputs()

        readiness = resolve_runtime_build_readiness(
            gamepack,
            registry=registry,
            snapshot=snapshot,
        )

        self.assertEqual("unsupported", readiness["status"])
        self.assertEqual(["audio:sfx"], readiness["missing_required_feature_ids"])
        self.assertIn("required_feature_unsupported", readiness["reason_codes"])


class StudioRuntimeComposeWorkerContractTests(unittest.TestCase):
    @staticmethod
    def _request(assetpack_root: Path) -> dict[str, object]:
        import hashlib

        from worldforge.creation_contracts import load_creation_project
        from worldforge.generic_assetpack import verify_generic_assetpack
        from worldforge.studio.creation_job_protocol import (
            build_private_runtime_compose_request,
        )

        with verify_generic_assetpack(assetpack_root) as verified:
            files = dict(verified.files)
            assetpack = verified.manifest
        staged_inputs = [
            {
                "source_locator": path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
            for path, payload in sorted(files.items())
        ]
        return build_private_runtime_compose_request(
            job_id="job_compose_runtime",
            workspace_id="workspace_puzzle",
            authority={
                "root_generation": 0,
                "source_revision": "a" * 64,
                "workflow_status_hash": None,
                "artifact_snapshot_hash": "b" * 64,
            },
            project=load_creation_project(
                _ROOT / "examples/multigenre-contracts/abstract-puzzle/project.json"
            ),
            lineage_documents=(
                _fixture(
                    "abstract-puzzle",
                    "artifacts/abstract-puzzle.gamepack.json",
                ),
                _fixture("abstract-puzzle", "assets/inventory.json"),
                assetpack,
            ),
            target_grant_id="grant_assetpack_output",
            target_grant_generation=2,
            staged_inputs=staged_inputs,
        )

    def test_v4_request_is_closed_pathless_and_preserves_v1_v3(self) -> None:
        from worldforge.__main__ import _resolve_generic_assetpack_cli_source
        from worldforge.generic_assetpack import seal_generic_assetpack
        from worldforge.studio.creation_job_protocol import (
            execute_private_creation_request,
            validate_private_creation_request,
        )

        source = _resolve_generic_assetpack_cli_source(
            _ROOT / "examples/multigenre-contracts/abstract-puzzle/assets/manifest.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "assetpack"
            sealed = seal_generic_assetpack(root, **source)
            sealed.close()
            request = self._request(root)

            self.assertEqual(4, request["format_version"])
            self.assertEqual("runtime.compose", request["operation"])
            self.assertNotIn(str(root), json.dumps(request))
            self.assertEqual(request, validate_private_creation_request(request))
            leaked = copy.deepcopy(request)
            leaked["adapter_id"] = "renderer_supplied_adapter"
            with self.assertRaisesRegex(ValueError, "fields"):
                validate_private_creation_request(leaked)

            result = execute_private_creation_request(
                request,
                artifact_root=root,
            )
            self.assertEqual(
                [
                    "world-forge.game_runtime_snapshot",
                    "world-forge.runtime_adapter_registry",
                    "world-forge.game_runtime_composition",
                    "world-forge.runtime_support_report",
                ],
                [output.subject["format"] for output in result.outputs],
            )
            self.assertEqual("passed", result.analysis_status)
            self.assertEqual(("optional_feature_unsupported",), result.reason_codes)

    def test_v4_worker_outputs_are_byte_deterministic(self) -> None:
        from worldforge.__main__ import _resolve_generic_assetpack_cli_source
        from worldforge.generic_assetpack import seal_generic_assetpack
        from worldforge.studio.creation_job_protocol import execute_private_creation_request

        source = _resolve_generic_assetpack_cli_source(
            _ROOT / "examples/multigenre-contracts/abstract-puzzle/assets/manifest.json"
        )
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_root = Path(first) / "assetpack"
            second_root = Path(second) / "assetpack"
            seal_generic_assetpack(first_root, **source).close()
            seal_generic_assetpack(second_root, **source).close()
            first_result = execute_private_creation_request(
                self._request(first_root),
                artifact_root=first_root,
            )
            second_result = execute_private_creation_request(
                self._request(second_root),
                artifact_root=second_root,
            )

        self.assertEqual(
            [output.payload for output in first_result.outputs],
            [output.payload for output in second_result.outputs],
        )

    def test_v4_request_runs_in_the_isolated_pathless_worker(self) -> None:
        from worldforge.__main__ import _resolve_generic_assetpack_cli_source
        from worldforge.generic_assetpack import seal_generic_assetpack, verify_generic_assetpack
        from worldforge.studio.creation_executor import (
            create_creation_stage,
            run_isolated_creation_worker,
            stage_private_asset_inputs,
            write_private_request,
        )

        source = _resolve_generic_assetpack_cli_source(
            _ROOT / "examples/multigenre-contracts/abstract-puzzle/assets/manifest.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assetpack_root = root / "published-assetpack"
            seal_generic_assetpack(assetpack_root, **source).close()
            request = self._request(assetpack_root)
            with verify_generic_assetpack(assetpack_root) as verified:
                payloads = tuple(
                    sorted(verified.files.items(), key=lambda item: item[0].encode("utf-8"))
                )
            stage_parent = root / "jobs"
            stage_parent.mkdir()
            stage, stage_identity = create_creation_stage(stage_parent, "job_compose_runtime")
            locator, digest = write_private_request(stage, request)
            stage_private_asset_inputs(stage, request, payloads)
            execution = run_isolated_creation_worker(
                stage,
                stage_identity,
                {
                    "format": "world-forge.studio_creation_worker",
                    "format_version": 4,
                    "kind": "request",
                    "job_id": "job_compose_runtime",
                    "operation": "runtime.compose",
                    "request_locator": locator,
                    "request_sha256": digest,
                },
                timeout_seconds=30,
                cancel_requested=lambda: False,
            )

        self.assertTrue(execution.response["ok"])
        self.assertEqual(4, len(execution.outputs))
        self.assertNotIn(str(assetpack_root), json.dumps(execution.response))


class StudioRuntimeComposePublicContractTests(unittest.TestCase):
    def test_job_and_worker_v4_are_closed_while_earlier_versions_stay_exact(self) -> None:
        from worldforge.integrity import canonical_payload_hash
        from worldforge.studio.contracts import (
            validate_studio_creation_job,
            validate_studio_creation_worker_envelope,
        )

        record: dict[str, object] = {
            "format": "world-forge.studio_creation_job",
            "format_version": 4,
            "job_id": "job_compose_runtime",
            "workspace_id": "workspace_puzzle",
            "operation": "runtime.compose",
            "operation_params": {
                "gamepack_artifact_id": "artifact_gamepack",
                "asset_inventory_artifact_id": "artifact_inventory",
                "assetpack_artifact_id": "artifact_assetpack",
                "target_grant_id": "grant_assetpack_output",
                "target_grant_generation": 2,
            },
            "state": "queued",
            "generation": 0,
            "authority": {
                "root_generation": 0,
                "source_revision": "a" * 64,
                "workflow_status_hash": None,
                "artifact_snapshot_hash": "b" * 64,
            },
            "inputs": [],
            "progress": "queued",
            "result": None,
            "error": None,
            "created_at": "2026-08-03T00:00:00.000000Z",
            "started_at": None,
            "finished_at": None,
            "updated_at": "2026-08-03T00:00:00.000000Z",
            "record_hash": "",
        }
        record["record_hash"] = canonical_payload_hash(record, hash_field="record_hash")
        self.assertEqual(record, validate_studio_creation_job(record))

        request = {
            "format": "world-forge.studio_creation_worker",
            "format_version": 4,
            "kind": "request",
            "job_id": "job_compose_runtime",
            "operation": "runtime.compose",
            "request_locator": "request_" + "a" * 32,
            "request_sha256": "c" * 64,
        }
        self.assertEqual(request, validate_studio_creation_worker_envelope(request))
        leaked = {**request, "adapter_id": "renderer_selected"}
        with self.assertRaisesRegex(ValueError, "fields"):
            validate_studio_creation_worker_envelope(leaked)

    def test_restart_census_recovers_all_four_v4_outputs(self) -> None:
        from worldforge.integrity import canonical_json_bytes
        from worldforge.studio.creation_jobs import CreationJobCoordinator

        documents = (
            json.loads(
                (_ROOT / "examples/multigenre-contracts/runtime/snapshot.json").read_text(
                    encoding="utf-8"
                )
            ),
            json.loads(
                (_ROOT / "examples/multigenre-contracts/runtime/registry.json").read_text(
                    encoding="utf-8"
                )
            ),
            _fixture("abstract-puzzle", "runtime/composition.json"),
            _fixture("abstract-puzzle", "runtime/support-report.json"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            for index, document in enumerate(documents, 1):
                (stage / f"output_{index:04d}.json").write_bytes(canonical_json_bytes(document))
            recovered = CreationJobCoordinator._unjournaled_outputs_for_cleanup(  # noqa: SLF001
                stage,
                "runtime.compose",
            )
        self.assertEqual(4, len(recovered))
        self.assertEqual(
            [document["format"] for document in documents],
            [json.loads(output.payload)["format"] for output in recovered],
        )


def _prepare_published_runtime_inputs(base: Path):
    from tests.test_studio_creation_asset_release_v11 import (
        _release_candidates,
        _review_processed_outputs,
        _snapshot,
    )
    from tests.test_studio_creation_asset_seal_v4 import (
        _prepare_processed_creation_service,
    )

    service, workspace, _before, qa_ids = _prepare_processed_creation_service(base)
    review_ids = _review_processed_outputs(service, workspace, list(qa_ids))
    manifest, assetpack = _release_candidates(
        service,
        workspace,
        review_ids,
        manifest_id="puzzle_runtime_release_manifest",
    )
    output_parent = base / "outputs"
    output_parent.mkdir()
    target = output_parent / "puzzle-assets"
    grant = service.creation_output_grants.create(
        {
            "grant_id": "grant_runtime_assetpack",
            "workspace_id": workspace["workspace_id"],
            "kind": "generic_assetpack_directory",
            "display_name": "puzzle-assets",
            "path": str(target),
        }
    )
    current = _snapshot(service, workspace)
    queued = service.creation_jobs.create_asset_release_authorize(
        {
            "job_id": "job_authorize_runtime_assetpack",
            "workspace_id": workspace["workspace_id"],
            "operation": "asset.release.authorize",
            "expected_root_generation": workspace["root_generation"],
            "expected_source_revision": workspace["source_revision"],
            "expected_workflow_status_hash": workspace["workflow_status_hash"],
            "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
            "review_receipt_artifact_ids": review_ids,
            "manifest_id": manifest["manifest_id"],
            "assetpack_id": assetpack["assetpack_id"],
            "release_authority_id": "runtime_release_authority",
            "blockers": [],
            "target_grant_id": grant["grant_id"],
            "expected_target_grant_generation": grant["generation"],
        }
    )
    if service.creation_job_coordinator.run_once() != queued["job_id"]:
        raise AssertionError("asset release authority job was not claimed")
    completed = service.creation_jobs.get(queued["job_id"])
    if completed["state"] != "succeeded":
        raise AssertionError(completed)
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
    artifact_ids = {
        record["subject"]["format"]: record["artifact_id"]
        for record in evidence["artifacts"]
        if record["subject"]["format"]
        in {
            "world-forge.gamepack",
            "world-forge.asset_inventory",
            "world-forge.assetpack",
        }
    }
    if set(artifact_ids) != {
        "world-forge.gamepack",
        "world-forge.asset_inventory",
        "world-forge.assetpack",
    }:
        raise AssertionError(artifact_ids)
    return service, workspace, evidence, artifact_ids, grant, target


class StudioRuntimeComposeCoordinatorTests(unittest.TestCase):
    def test_compose_commits_only_four_candidate_artifacts_with_exact_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace, before, artifact_ids, grant, target = (
                _prepare_published_runtime_inputs(base)
            )
            try:
                from worldforge.studio.errors import StudioError

                published = service.creation_output_grants.get(grant["grant_id"])
                compose_params = {
                    "workspace_id": workspace["workspace_id"],
                    "operation": "runtime.compose",
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": before["artifact_snapshot_hash"],
                    "gamepack_artifact_id": artifact_ids["world-forge.gamepack"],
                    "asset_inventory_artifact_id": artifact_ids["world-forge.asset_inventory"],
                    "assetpack_artifact_id": artifact_ids["world-forge.assetpack"],
                    "target_grant_id": grant["grant_id"],
                    "expected_target_grant_generation": published["generation"],
                }
                with self.assertRaisesRegex(StudioError, "generation changed"):
                    service.creation_jobs.create_runtime_compose(
                        {
                            **compose_params,
                            "job_id": "job_compose_stale_grant",
                            "expected_target_grant_generation": published["generation"] + 1,
                        }
                    )
                with self.assertRaisesRegex(StudioError, "input artifact"):
                    service.creation_jobs.create_runtime_compose(
                        {
                            **compose_params,
                            "job_id": "job_compose_crossed_inputs",
                            "gamepack_artifact_id": artifact_ids["world-forge.asset_inventory"],
                            "asset_inventory_artifact_id": artifact_ids["world-forge.gamepack"],
                        }
                    )
                queued = service.creation_jobs.create_runtime_compose(
                    {**compose_params, "job_id": "job_compose_runtime"}
                )
                self.assertEqual(4, queued["format_version"])
                self.assertNotIn(str(target), json.dumps(queued))
                self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                completed = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("succeeded", completed["state"], completed)
                self.assertEqual("committed", completed["progress"])
                self.assertEqual(4, len(completed["result"]["output_artifact_ids"]))
                self.assertEqual(
                    ["optional_feature_unsupported"],
                    completed["result"]["reason_codes"],
                )
                documents = [
                    service.creation_artifacts.get_document(workspace["workspace_id"], artifact_id)
                    for artifact_id in completed["result"]["output_artifact_ids"]
                ]
                self.assertEqual(
                    [
                        "world-forge.game_runtime_snapshot",
                        "world-forge.runtime_adapter_registry",
                        "world-forge.game_runtime_composition",
                        "world-forge.runtime_support_report",
                    ],
                    [document["format"] for document in documents],
                )
                gamepack = service.creation_artifacts.get_document(
                    workspace["workspace_id"], artifact_ids["world-forge.gamepack"]
                )
                composition = documents[2]
                support = documents[3]
                self.assertEqual(gamepack["content_hash"], composition["gamepack"]["content_hash"])
                self.assertEqual(gamepack["content_hash"], support["gamepack"]["content_hash"])
                self.assertFalse(support["supported"])
                self.assertEqual("blocked", support["dimensions"]["release"])
                after = service.creation_evidence.list(
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
                self.assertEqual(before["counts"]["active"], after["counts"]["active"])
                self.assertEqual(
                    before["counts"]["candidate"] + 4,
                    after["counts"]["candidate"],
                )
                cancelable = service.creation_jobs.create_runtime_compose(
                    {
                        **compose_params,
                        "job_id": "job_compose_cancelable",
                        "expected_artifact_snapshot_hash": after["artifact_snapshot_hash"],
                    }
                )
                canceled = service.creation_jobs.cancel(
                    cancelable["job_id"],
                    expected_generation=cancelable["generation"],
                    expected_record_hash=cancelable["record_hash"],
                )
                self.assertEqual("canceled", canceled["state"])
                self.assertEqual(
                    before["counts"]["candidate"] + 4,
                    service.creation_evidence.list(
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
                    )["counts"]["candidate"],
                )
            finally:
                service.close()
                service.store.close()


if __name__ == "__main__":
    unittest.main()
