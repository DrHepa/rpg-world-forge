from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_multigenre_game_package import _standalone

ROOT = Path(__file__).resolve().parents[1]


def _prepare_published_standalone(base: Path):
    from tests.test_studio_creation_game_materialize_v4 import (
        _game_materialize_params,
        _prepare_published_materialization_bundle,
    )

    service, workspace, _materialization_root, source_grant, source_job = (
        _prepare_published_materialization_bundle(base)
    )
    standalone_root = base / "outputs" / "standalone-for-package"
    standalone_grant = service.creation_output_grants.create(
        {
            "grant_id": "grant_standalone_for_package",
            "workspace_id": workspace["workspace_id"],
            "kind": "standalone_game_directory",
            "display_name": "standalone-for-package",
            "path": str(standalone_root),
        }
    )
    standalone_job = service.creation_jobs.create_game_materialize(
        _game_materialize_params(
            service,
            workspace,
            source_grant,
            source_job,
            standalone_grant,
            job_id="job_standalone_for_package",
        )
    )
    service.creation_job_coordinator.run_once()
    completed = service.creation_jobs.get(standalone_job["job_id"])
    assert completed["state"] == "succeeded", completed
    return service, workspace, standalone_root, standalone_grant, completed


def _game_package_params(
    service,
    workspace,
    source_grant,
    source_job,
    target_grant,
    *,
    job_id: str,
) -> dict[str, object]:
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
    published_source = service.creation_output_grants.get(source_grant["grant_id"])
    return {
        "job_id": job_id,
        "workspace_id": workspace["workspace_id"],
        "operation": "game.package",
        "expected_root_generation": workspace["root_generation"],
        "expected_source_revision": workspace["source_revision"],
        "expected_workflow_status_hash": workspace["workflow_status_hash"],
        "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
        "standalone_game_artifact_id": source_job["result"]["output_artifact_ids"][0],
        "source_grant_id": source_grant["grant_id"],
        "expected_source_grant_generation": published_source["generation"],
        "target_grant_id": target_grant["grant_id"],
        "expected_target_grant_generation": target_grant["generation"],
    }


class StudioGamePackageContractTests(unittest.TestCase):
    def test_v8_worker_builds_one_closed_manifest_and_exact_private_archive(self) -> None:
        from gamepack_runtime.game_package import (
            build_game_package_from_files,
            verify_game_package_bytes,
        )
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio.creation_job_protocol import (
            build_private_game_package_request,
            execute_private_creation_request,
            validate_private_creation_request,
        )

        archives: list[bytes] = []
        with tempfile.TemporaryDirectory(prefix="wf-studio-game-package-worker-") as temporary:
            root = Path(temporary)
            for index in range(2):
                source_root = root / f"source-{index}"
                source_root.mkdir()
                with _standalone("abstract-puzzle", source_root) as (_path, source):
                    expected_package = build_game_package_from_files(source.files)
                    staged_inputs = [
                        {
                            "source_locator": locator,
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "size_bytes": len(payload),
                        }
                        for locator, payload in sorted(
                            source.files.items(), key=lambda item: item[0].encode("utf-8")
                        )
                    ]
                    request = build_private_game_package_request(
                        job_id=f"job_game_package_worker_{index}",
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
                        standalone_game_manifest=source.manifest,
                        standalone_game_lock=source.lock,
                        game_package_manifest=expected_package.manifest,
                        archive_sha256=expected_package.archive_sha256,
                        archive_size_bytes=len(expected_package.archive_bytes),
                        source_grant_id="grant_standalone_source",
                        source_grant_generation=2,
                        target_grant_id="grant_package_target",
                        target_grant_generation=1,
                        staged_inputs=staged_inputs,
                    )
                    self.assertEqual(8, request["format_version"])
                    self.assertEqual("game.package", request["operation"])
                    self.assertNotIn(str(source.root), json.dumps(request))
                    self.assertEqual(request, validate_private_creation_request(request))
                    with self.assertRaisesRegex(ValueError, "fields"):
                        validate_private_creation_request(
                            {**request, "native_path": str(source.root)}
                        )
                    with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": str(1 + index)}):
                        result = execute_private_creation_request(
                            request,
                            artifact_root=source.root,
                        )
                    self.assertEqual(1, len(result.outputs))
                    self.assertEqual(1, len(result.binary_outputs))
                    self.assertEqual(
                        "world-forge.game_package",
                        result.outputs[0].subject["format"],
                    )
                    archive = result.binary_outputs[0]
                    self.assertEqual("game_package_archive", archive.locator)
                    verified = verify_game_package_bytes(archive.payload)
                    self.assertEqual(
                        json.loads(result.outputs[0].payload),
                        verified.manifest,
                    )
                    self.assertEqual(source.manifest["lineage"], verified.manifest["lineage"])
                    archives.append(archive.payload)
        self.assertEqual(archives[0], archives[1])

    def test_v8_job_and_v5_file_grant_are_closed_without_reinterpreting_old_versions(
        self,
    ) -> None:
        from tests.test_studio_creation_asset_seal_v4 import _grant_record
        from worldforge.studio.contracts import (
            StudioContractError,
            validate_studio_creation_output_grant,
            validate_studio_protocol_envelope,
        )
        from worldforge.studio.service import StudioService

        initialized = StudioService._initialize({}, protocol_version=4)  # noqa: SLF001
        self.assertTrue(initialized["capabilities"]["game_packaging"])
        validate_studio_protocol_envelope(
            {
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": 4,
                "kind": "response",
                "request_id": "game-package-initialize",
                "method": "service.initialize",
                "result": initialized,
            }
        )
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
            "game.package",
            job_schema["oneOf"][7]["properties"]["operation"]["const"],
        )
        self.assertEqual(
            "World Forge Studio isolated creation worker envelope v11",
            worker_schema["title"],
        )
        self.assertEqual(33, len(worker_schema["oneOf"]))
        self.assertEqual("World Forge Studio creation output grant v5", output_schema["title"])
        self.assertEqual(10, len(protocol_schema["$defs"]["jobCreateParams"]["oneOf"]))
        self.assertIn(
            "game_package_file",
            protocol_schema["$defs"]["outputGrantCreateParams"]["properties"]["kind"]["enum"],
        )
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        self.assertEqual(9, entries["studio-creation-job"]["version"])
        self.assertEqual(11, entries["studio-creation-worker"]["version"])
        self.assertEqual(5, entries["studio-creation-output-grant"]["version"])

        legacy = _grant_record()
        old = (
            legacy,
            {
                **legacy,
                "format_version": 2,
                "grant_id": "grant_runtime",
                "kind": "game_runtime_bundle_directory",
            },
            {
                **legacy,
                "format_version": 3,
                "grant_id": "grant_materialization",
                "kind": "game_materialization_bundle_directory",
            },
            {
                **legacy,
                "format_version": 4,
                "grant_id": "grant_standalone",
                "kind": "standalone_game_directory",
            },
        )
        for document in old:
            self.assertEqual(
                document,
                validate_studio_creation_output_grant(copy.deepcopy(document)),
            )
        package = {
            **legacy,
            "format_version": 5,
            "grant_id": "grant_package",
            "kind": "game_package_file",
        }
        self.assertEqual(package, validate_studio_creation_output_grant(copy.deepcopy(package)))
        with self.assertRaisesRegex(StudioContractError, "unknown fields"):
            validate_studio_creation_output_grant({**package, "native_path": "/private"})

    def test_public_v4_game_package_request_is_fixed_pathless_and_cas_bound(self) -> None:
        from worldforge.studio.contracts import (
            StudioContractError,
            validate_studio_protocol_envelope,
        )

        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "request",
            "request_id": "game-package-request-01",
            "method": "creation_job.create",
            "params": {
                "job_id": "job_game_package_request",
                "workspace_id": "workspace_puzzle",
                "operation": "game.package",
                "expected_root_generation": 1,
                "expected_source_revision": "a" * 64,
                "expected_workflow_status_hash": None,
                "expected_artifact_snapshot_hash": "b" * 64,
                "standalone_game_artifact_id": "artifact_standalone_game",
                "source_grant_id": "grant_standalone_source",
                "expected_source_grant_generation": 2,
                "target_grant_id": "grant_package_target",
                "expected_target_grant_generation": 0,
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(copy.deepcopy(request)))
        for leaked in (
            {**request["params"], "path": "/renderer/private"},
            {**request["params"], "kind": "game_package_file"},
            {**request["params"], "archive_name": "renderer-selected.wfgame"},
            {**request["params"], "adapter_id": "renderer-selected"},
        ):
            with self.assertRaises(StudioContractError):
                validate_studio_protocol_envelope({**request, "params": leaked})

    def test_v4_storage_migrates_file_authority_columns_idempotently(self) -> None:
        from worldforge.studio.storage import StudioStore

        with tempfile.TemporaryDirectory(prefix="wf-studio-package-migration-") as temporary:
            data = Path(temporary) / "studio"
            with StudioStore(data) as store:
                with store.connection:
                    store.connection.execute(
                        "INSERT INTO events "
                        "(workspace_id, topic, entity_type, entity_id, payload_json, created_at) "
                        "VALUES (NULL, 'legacy', 'legacy', 'legacy_package', ?, ?)",
                        ('{"legacy":"unchanged"}', "2026-08-04T00:00:00.000000Z"),
                    )
            connection = sqlite3.connect(data / "studio.sqlite3")
            try:
                connection.execute(
                    "ALTER TABLE creation_output_grants DROP COLUMN expected_archive_sha256"
                )
                connection.execute(
                    "ALTER TABLE creation_output_grants DROP COLUMN expected_size_bytes"
                )
                connection.execute(
                    "ALTER TABLE creation_job_attempts DROP COLUMN binary_output_dev"
                )
                connection.execute(
                    "ALTER TABLE creation_job_attempts DROP COLUMN binary_output_ino"
                )
                connection.execute(
                    "UPDATE schema_meta SET value = '4' WHERE key = 'schema_version'"
                )
                connection.commit()
            finally:
                connection.close()
            for _ in range(2):
                with StudioStore(data) as migrated:
                    self.assertEqual(
                        "5",
                        migrated.connection.execute(
                            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                        ).fetchone()["value"],
                    )
                    output_columns = {
                        row["name"]
                        for row in migrated.connection.execute(
                            "PRAGMA table_info(creation_output_grants)"
                        )
                    }
                    attempt_columns = {
                        row["name"]
                        for row in migrated.connection.execute(
                            "PRAGMA table_info(creation_job_attempts)"
                        )
                    }
                    self.assertLessEqual(
                        {"expected_archive_sha256", "expected_size_bytes"},
                        output_columns,
                    )
                    self.assertLessEqual(
                        {"binary_output_dev", "binary_output_ino"},
                        attempt_columns,
                    )
                    self.assertEqual(
                        '{"legacy":"unchanged"}',
                        migrated.connection.execute(
                            "SELECT payload_json FROM events WHERE entity_id = 'legacy_package'"
                        ).fetchone()["payload_json"],
                    )


class StudioGamePackageCoordinatorTests(unittest.TestCase):
    def test_restart_marks_v8_file_publication_recovery_required(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.contracts import creation_job_record_hash
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        with tempfile.TemporaryDirectory(prefix="wf-studio-game-package-restart-") as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            store = service.store
            data_dir = store.data_dir
            output_parent = base / "outputs"
            output_parent.mkdir()
            target = service.creation_output_grants.create(
                {
                    "grant_id": "grant_game_package_restart",
                    "workspace_id": workspace["workspace_id"],
                    "kind": "game_package_file",
                    "display_name": "restart.wfgame",
                    "path": str(output_parent / "restart.wfgame"),
                }
            )
            timestamp = "2026-08-04T00:00:00.000000Z"
            job_id = "job_game_package_restart"
            running: dict[str, object] = {
                "format": "world-forge.studio_creation_job",
                "format_version": 8,
                "job_id": job_id,
                "workspace_id": workspace["workspace_id"],
                "operation": "game.package",
                "operation_params": {
                    "standalone_game_artifact_id": "artifact_standalone_restart",
                    "source_grant_id": "grant_standalone_restart",
                    "source_grant_generation": 1,
                    "target_grant_id": target["grant_id"],
                    "target_grant_generation": 1,
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
                "progress": "output_published",
                "result": None,
                "error": None,
                "created_at": timestamp,
                "started_at": timestamp,
                "finished_at": None,
                "updated_at": timestamp,
                "record_hash": "",
            }
            running["record_hash"] = creation_job_record_hash(running)
            try:
                with store.connection:
                    reserved, _binding = service.creation_output_grants.reserve_for_job(
                        grant_id=target["grant_id"],
                        job_id=job_id,
                        workspace_id=workspace["workspace_id"],
                        expected_generation=target["generation"],
                        expected_manifest_hash="a" * 64,
                        expected_archive_sha256="b" * 64,
                        expected_size_bytes=1,
                    )
                    self.assertEqual(1, reserved["generation"])
                    binding = service.creation_output_grants.begin_publication(job_id)
                    self.assertEqual("file_publication_reserved", binding["recovery"]["phase"])
                    store.connection.execute(
                        "INSERT INTO creation_jobs "
                        "(job_id, workspace_id, operation, state, progress, generation, "
                        "record_json) VALUES (?, ?, 'game.package', 'running', "
                        "'output_published', 1, ?)",
                        (
                            job_id,
                            workspace["workspace_id"],
                            json.dumps(running, sort_keys=True, separators=(",", ":")),
                        ),
                    )
            finally:
                service.close()
                store.close()

            reopened_store = StudioStore(data_dir)
            reopened = StudioService(reopened_store)
            try:
                self.assertEqual("orphaned", reopened.creation_jobs.get(job_id)["state"])
                recovered = reopened.creation_output_grants.get(target["grant_id"])
                self.assertEqual("recovery_required", recovered["state"])
            finally:
                reopened.close()
                reopened_store.close()

    def test_v8_job_publishes_one_pathless_reproducible_package_with_exact_lineage(self) -> None:
        from gamepack_runtime.game_package import verify_game_package_file
        from worldforge.studio.creation_jobs import CreationJobManager
        from worldforge.studio.errors import StudioError

        self.assertTrue(hasattr(CreationJobManager, "create_game_package"))
        with tempfile.TemporaryDirectory(prefix="wf-studio-game-package-job-") as temporary:
            base = Path(temporary)
            service, workspace, source_root, source_grant, source_job = (
                _prepare_published_standalone(base)
            )
            try:
                output_parent = base / "outputs"
                target_path = output_parent / "abstract-puzzle.wfgame"
                target_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_game_package_target",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "game_package_file",
                        "display_name": "abstract-puzzle.wfgame",
                        "path": str(target_path),
                    }
                )
                params = _game_package_params(
                    service,
                    workspace,
                    source_grant,
                    source_job,
                    target_grant,
                    job_id="job_game_package",
                )
                with self.assertRaises(StudioError):
                    service.creation_jobs.create_game_package(
                        {
                            **params,
                            "job_id": "job_game_package_stale_source",
                            "expected_source_grant_generation": params[
                                "expected_source_grant_generation"
                            ]
                            + 1,
                        }
                    )
                foreign = source_root / "foreign-authoring-data.json"
                foreign.write_bytes(b"{}")
                try:
                    with self.assertRaises(StudioError):
                        service.creation_jobs.create_game_package(
                            {**params, "job_id": "job_game_package_tampered"}
                        )
                finally:
                    foreign.unlink()

                queued = service.creation_jobs.create_game_package(params)
                self.assertEqual(8, queued["format_version"])
                self.assertNotIn(str(source_root), json.dumps(queued))
                self.assertNotIn(str(target_path), json.dumps(queued))
                self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                completed = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("succeeded", completed["state"], completed)
                self.assertEqual("committed", completed["progress"])
                self.assertEqual(
                    ["extraction_unverified", "native_execution_unverified", "release_blocked"],
                    completed["result"]["reason_codes"],
                )
                publication = completed["result"]["publication"]
                self.assertEqual("game_package_file", publication["kind"])
                self.assertNotIn("path", json.dumps(publication))
                verified = verify_game_package_file(target_path)
                try:
                    package = publication["game_package"]
                    self.assertEqual(verified.archive_sha256, package["archive_sha256"])
                    self.assertEqual(len(verified.archive_bytes), package["size_bytes"])
                    self.assertEqual(verified.manifest["content_hash"], package["content_hash"])
                    self.assertEqual(
                        source_job["result"]["publication"]["standalone_game"]["content_hash"],
                        verified.manifest["standalone_game"]["content_hash"],
                    )
                    candidate = service.creation_artifacts.get_document(
                        workspace["workspace_id"],
                        completed["result"]["output_artifact_ids"][0],
                    )
                    self.assertEqual(verified.manifest, candidate)
                finally:
                    verified.close()

                existing = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_game_package_existing",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "game_package_file",
                        "display_name": "existing.wfgame",
                        "path": str(output_parent / "existing.wfgame"),
                    }
                )
                (output_parent / "existing.wfgame").write_bytes(b"foreign")
                with self.assertRaises(StudioError):
                    service.creation_jobs.create_game_package(
                        _game_package_params(
                            service,
                            workspace,
                            source_grant,
                            source_job,
                            existing,
                            job_id="job_game_package_existing",
                        )
                    )
            finally:
                service.close()

    def test_file_grant_rejects_nonportable_colliding_link_and_hardlink_targets(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory(prefix="wf-studio-package-grant-negative-") as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                output = base / "outputs"
                output.mkdir()
                (output / "Puzzle.wfgame").write_bytes(b"foreign")
                for index, target in enumerate(
                    (
                        output / "puzzle.wfgame",
                        output / "CON.wfgame",
                    )
                ):
                    with self.subTest(target=target.name), self.assertRaises(StudioError):
                        service.creation_output_grants.create(
                            {
                                "grant_id": f"grant_bad_package_{index}",
                                "workspace_id": workspace["workspace_id"],
                                "kind": "game_package_file",
                                "display_name": target.name,
                                "path": str(target),
                            }
                        )
                original = output / "original.wfgame"
                original.write_bytes(b"foreign")
                hardlink = output / "hardlink.wfgame"
                try:
                    os.link(original, hardlink)
                except OSError:
                    pass
                else:
                    with self.assertRaises(StudioError):
                        service.creation_output_grants.create(
                            {
                                "grant_id": "grant_hardlink_package",
                                "workspace_id": workspace["workspace_id"],
                                "kind": "game_package_file",
                                "display_name": hardlink.name,
                                "path": str(hardlink),
                            }
                        )
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
