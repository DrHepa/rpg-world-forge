from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests.test_multigenre_game_package import _standalone

ROOT = Path(__file__).resolve().parents[1]


def _prepare_published_package(base: Path):
    from tests.test_studio_creation_game_package_v4 import (
        _game_package_params,
        _prepare_published_standalone,
    )

    service, workspace, _standalone_root, standalone_grant, standalone_job = (
        _prepare_published_standalone(base)
    )
    package_path = base / "outputs" / "source-package.wfgame"
    package_grant = service.creation_output_grants.create(
        {
            "grant_id": "grant_package_for_extraction",
            "workspace_id": workspace["workspace_id"],
            "kind": "game_package_file",
            "display_name": "source-package.wfgame",
            "path": str(package_path),
        }
    )
    package_job = service.creation_jobs.create_game_package(
        _game_package_params(
            service,
            workspace,
            standalone_grant,
            standalone_job,
            package_grant,
            job_id="job_package_for_extraction",
        )
    )
    service.creation_job_coordinator.run_once()
    completed = service.creation_jobs.get(package_job["job_id"])
    assert completed["state"] == "succeeded", completed
    return service, workspace, package_path, package_grant, completed


def _game_package_extract_params(
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
        "operation": "game.package.extract",
        "expected_root_generation": workspace["root_generation"],
        "expected_source_revision": workspace["source_revision"],
        "expected_workflow_status_hash": workspace["workflow_status_hash"],
        "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
        "game_package_artifact_id": source_job["result"]["output_artifact_ids"][0],
        "source_grant_id": source_grant["grant_id"],
        "expected_source_grant_generation": published_source["generation"],
        "target_grant_id": target_grant["grant_id"],
        "expected_target_grant_generation": target_grant["generation"],
    }


class StudioGamePackageExtractionContractTests(unittest.TestCase):
    def test_extraction_evidence_is_deterministic_closed_and_package_bound(self) -> None:
        from gamepack_runtime.game_package import build_game_package_from_files
        from worldforge.game_package_extraction import (
            build_game_package_extraction_evidence,
            validate_game_package_extraction_evidence,
        )
        from worldforge.integrity import canonical_payload_hash

        with tempfile.TemporaryDirectory(prefix="wf-studio-extraction-contract-") as temporary:
            root = Path(temporary)
            standalone_root = root / "standalone"
            standalone_root.mkdir()
            with _standalone("abstract-puzzle", standalone_root) as (_path, source):
                package = build_game_package_from_files(source.files)
                first = build_game_package_extraction_evidence(
                    package.manifest,
                    archive_sha256=package.archive_sha256,
                    archive_size_bytes=len(package.archive_bytes),
                )
                second = build_game_package_extraction_evidence(
                    package.manifest,
                    archive_sha256=package.archive_sha256,
                    archive_size_bytes=len(package.archive_bytes),
                )

        self.assertEqual(first, second)
        self.assertEqual("world-forge.game_package_extraction", first["format"])
        self.assertEqual(package.manifest["content_hash"], first["package"]["content_hash"])
        self.assertEqual(package.manifest["standalone_game"], first["standalone_game"])
        self.assertEqual(
            package.manifest["payload_lock"]["tree_hash"],
            first["extracted_tree_hash"],
        )
        self.assertEqual(first, validate_game_package_extraction_evidence(copy.deepcopy(first)))
        with self.assertRaises(ValueError):
            validate_game_package_extraction_evidence({**first, "extracted_tree_hash": "0" * 64})
        with self.assertRaises(ValueError):
            validate_game_package_extraction_evidence({**first, "native_path": "/private"})
        for section, field, value in (
            ("standalone_game", "native_path", "/private"),
            ("payload_lock", "provider", "forbidden"),
            ("lineage", "prompt_hash", "0" * 64),
        ):
            with self.subTest(section=section, field=field):
                mutated = copy.deepcopy(first)
                mutated[section][field] = value
                mutated["content_hash"] = canonical_payload_hash(mutated)
                with self.assertRaises(ValueError):
                    validate_game_package_extraction_evidence(mutated)

        mutated = copy.deepcopy(first)
        mutated["package"]["id"] = "bogus_package"
        mutated["extraction_id"] = (
            "game_package_extraction_"
            + canonical_payload_hash({"package": mutated["package"]})[:40]
        )
        mutated["content_hash"] = canonical_payload_hash(mutated)
        with self.assertRaises(ValueError):
            validate_game_package_extraction_evidence(mutated)

    def test_v9_job_worker_and_protocol_are_additive_and_pathless(self) -> None:
        from worldforge.studio.contracts import validate_studio_protocol_envelope

        job_schema = json.loads(
            (ROOT / "schemas/studio-creation-job.schema.json").read_text(encoding="utf-8")
        )
        worker_schema = json.loads(
            (ROOT / "schemas/studio-creation-worker.schema.json").read_text(encoding="utf-8")
        )
        protocol_schema = json.loads(
            (ROOT / "schemas/studio-protocol-v4.schema.json").read_text(encoding="utf-8")
        )
        evidence_schema = json.loads(
            (ROOT / "schemas/game-package-extraction.schema.json").read_text(encoding="utf-8")
        )
        catalog = json.loads((ROOT / "contracts/catalog.json").read_text(encoding="utf-8"))

        self.assertEqual("World Forge Studio creation job v9", job_schema["title"])
        self.assertEqual(9, len(job_schema["oneOf"]))
        self.assertEqual(
            "game.package.extract",
            job_schema["oneOf"][8]["properties"]["operation"]["const"],
        )
        self.assertEqual(
            "World Forge Studio isolated creation worker envelope v11",
            worker_schema["title"],
        )
        self.assertEqual(33, len(worker_schema["oneOf"]))
        self.assertEqual(10, len(protocol_schema["$defs"]["jobCreateParams"]["oneOf"]))
        self.assertEqual(
            "https://world-forge.local/schemas/game-package-extraction.schema.json",
            evidence_schema["$id"],
        )
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        self.assertEqual(9, entries["studio-creation-job"]["version"])
        self.assertEqual(11, entries["studio-creation-worker"]["version"])
        self.assertEqual(1, entries["game-package-extraction"]["version"])

        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "request",
            "request_id": "game-package-extract-request-01",
            "method": "creation_job.create",
            "params": {
                "job_id": "job_game_package_extract",
                "workspace_id": "workspace_puzzle",
                "operation": "game.package.extract",
                "expected_root_generation": 1,
                "expected_source_revision": "a" * 64,
                "expected_workflow_status_hash": None,
                "expected_artifact_snapshot_hash": "b" * 64,
                "game_package_artifact_id": "artifact_game_package",
                "source_grant_id": "grant_package_source",
                "expected_source_grant_generation": 2,
                "target_grant_id": "grant_extracted_target",
                "expected_target_grant_generation": 0,
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(copy.deepcopy(request)))
        for leaked in (
            {**request["params"], "path": "/renderer/private"},
            {**request["params"], "archive_path": "/renderer/private.wfgame"},
            {**request["params"], "kind": "standalone_game_directory"},
            {**request["params"], "command": ["unzip"]},
        ):
            with self.assertRaises(ValueError):
                validate_studio_protocol_envelope({**request, "params": leaked})

    def test_v9_worker_verifies_exact_archive_and_emits_only_extraction_evidence(self) -> None:
        import hashlib

        from gamepack_runtime.game_package import build_game_package_from_files
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio.creation_job_protocol import (
            build_private_game_package_extract_request,
            execute_private_creation_request,
            validate_private_creation_request,
        )

        with tempfile.TemporaryDirectory(prefix="wf-studio-extraction-worker-") as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_root.mkdir()
            with _standalone("abstract-puzzle", source_root) as (_path, source):
                package = build_game_package_from_files(source.files)
            artifact_root = root / "artifact-root"
            artifact_root.mkdir()
            archive_path = artifact_root / "game_package_archive.wfgame"
            archive_path.write_bytes(package.archive_bytes)
            staged_inputs = [
                {
                    "source_locator": "game_package_archive.wfgame",
                    "sha256": package.archive_sha256,
                    "size_bytes": len(package.archive_bytes),
                }
            ]
            request = build_private_game_package_extract_request(
                job_id="job_game_package_extract_worker",
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
                game_package_manifest=package.manifest,
                archive_sha256=package.archive_sha256,
                archive_size_bytes=len(package.archive_bytes),
                source_grant_id="grant_package_source",
                source_grant_generation=2,
                target_grant_id="grant_extracted_target",
                target_grant_generation=0,
                staged_inputs=staged_inputs,
            )
            self.assertEqual(9, request["format_version"])
            self.assertEqual(request, validate_private_creation_request(copy.deepcopy(request)))
            self.assertNotIn(str(archive_path), json.dumps(request))
            result = execute_private_creation_request(request, artifact_root=artifact_root)
            self.assertEqual(1, len(result.outputs))
            self.assertEqual((), result.binary_outputs)
            evidence = json.loads(result.outputs[0].payload)
            self.assertEqual(
                "world-forge.game_package_extraction",
                evidence["format"],
            )
            self.assertEqual(package.archive_sha256, evidence["package"]["archive_sha256"])

            archive_path.write_bytes(package.archive_bytes + b"tamper")
            self.assertNotEqual(
                package.archive_sha256,
                hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            )
            with self.assertRaises(ValueError):
                execute_private_creation_request(request, artifact_root=artifact_root)


class StudioGamePackageExtractionCoordinatorTests(unittest.TestCase):
    def test_v9_job_extracts_exact_package_into_published_standalone_grant(self) -> None:
        from worldforge.standalone_game import verify_standalone_game
        from worldforge.studio.creation_jobs import CreationJobManager
        from worldforge.studio.errors import StudioError

        self.assertTrue(hasattr(CreationJobManager, "create_game_package_extract"))
        with tempfile.TemporaryDirectory(prefix="wf-studio-package-extract-job-") as temporary:
            base = Path(temporary)
            service, workspace, package_path, source_grant, source_job = _prepare_published_package(
                base
            )
            try:
                target_path = base / "outputs" / "extracted-standalone"
                target_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_extracted_standalone",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "standalone_game_directory",
                        "display_name": "extracted-standalone",
                        "path": str(target_path),
                    }
                )
                params = _game_package_extract_params(
                    service,
                    workspace,
                    source_grant,
                    source_job,
                    target_grant,
                    job_id="job_game_package_extract",
                )
                with self.assertRaises(StudioError):
                    service.creation_jobs.create_game_package_extract(
                        {
                            **params,
                            "job_id": "job_game_package_extract_stale",
                            "expected_source_grant_generation": params[
                                "expected_source_grant_generation"
                            ]
                            + 1,
                        }
                    )
                queued = service.creation_jobs.create_game_package_extract(params)
                self.assertEqual(9, queued["format_version"])
                self.assertNotIn(str(package_path), json.dumps(queued))
                self.assertNotIn(str(target_path), json.dumps(queued))
                self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                completed = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("succeeded", completed["state"], completed)
                self.assertEqual("committed", completed["progress"])
                publication = completed["result"]["publication"]
                self.assertEqual("standalone_game_directory", publication["kind"])
                self.assertNotIn("path", json.dumps(publication))
                evidence = service.creation_artifacts.get_document(
                    workspace["workspace_id"],
                    completed["result"]["output_artifact_ids"][0],
                )
                self.assertEqual("world-forge.game_package_extraction", evidence["format"])
                with verify_standalone_game(
                    target_path,
                    expected_content_hash=evidence["standalone_game"]["content_hash"],
                ) as extracted:
                    self.assertEqual(evidence["extracted_tree_hash"], extracted.lock["tree_hash"])
                    self.assertEqual(evidence["lineage"], extracted.manifest["lineage"])
            finally:
                service.close()


class StudioGamePackageExtractionRecoveryTests(unittest.TestCase):
    def test_bound_extraction_recovery_rejects_same_inode_journal_rewrite(self) -> None:
        from gamepack_runtime.distribution import canonical_contract_bytes
        from gamepack_runtime.game_package import build_game_package_from_files
        from worldforge.game_package import (
            WorldForgeGamePackageError,
            extract_game_package,
            recover_game_package_extraction,
            rollback_game_package_extraction,
        )

        with tempfile.TemporaryDirectory(prefix="wf-studio-extraction-journal-") as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_root.mkdir()
            with _standalone("abstract-puzzle", source_root) as (_path, source):
                package = build_game_package_from_files(source.files)
            package_path = root / "source.wfgame"
            package_path.write_bytes(package.archive_bytes)
            destination = root / "extracted"
            started: dict[str, object] = {}

            def remember_authority(phase: str, evidence: dict[str, object]) -> None:
                if phase == "publication_started":
                    started.update(evidence)

            def stop_after_intent(phase: str, _path: Path | None) -> None:
                if phase == "after_intent_journal_written":
                    raise SystemExit("simulated stop after extraction intent")

            with self.assertRaisesRegex(SystemExit, "extraction intent"):
                extract_game_package(
                    package_path,
                    destination,
                    _publication_hook=stop_after_intent,
                    _authority_hook=remember_authority,
                )
            journal = root / ".extracted.game-package-extraction.journal.json"
            original = journal.read_bytes()
            document = json.loads(original)
            before = journal.stat()
            replacement = "f" * 64 if document["archive_sha256"] != "f" * 64 else "e" * 64
            journal.write_bytes(
                canonical_contract_bytes({**document, "archive_sha256": replacement})
            )
            after = journal.stat()
            self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
            authority = {
                "expected_journal_identity": tuple(started["journal_identity"]),
                "expected_operation_id": str(started["operation_id"]),
                "expected_content_hash": document["standalone_game_hash"],
                "expected_tree_hash": document["payload_tree_hash"],
                "expected_journal_payload_sha256": str(started["journal_payload_sha256"]),
                "expected_journal_payload_state": str(started["journal_payload_state"]),
            }
            with self.assertRaises(WorldForgeGamePackageError) as recovery_rejected:
                recover_game_package_extraction(destination, **authority)
            self.assertEqual(
                "game_package_recovery_ambiguous",
                recovery_rejected.exception.reason_code,
            )
            with self.assertRaises(WorldForgeGamePackageError) as rollback_rejected:
                rollback_game_package_extraction(destination, **authority)
            self.assertEqual(
                "game_package_rollback_ambiguous",
                rollback_rejected.exception.reason_code,
            )
            journal.write_bytes(original)
            self.assertEqual(
                "rolled_back",
                rollback_game_package_extraction(destination, **authority)["status"],
            )

    def test_coordinator_adopts_a_bound_ready_extraction_without_republishing(self) -> None:
        from gamepack_runtime.game_package import build_game_package_from_files
        from worldforge.file_stat import path_file_stat
        from worldforge.game_package import extract_game_package
        from worldforge.standalone_game import verify_standalone_game
        from worldforge.studio.creation_jobs import CreationJobCoordinator

        with tempfile.TemporaryDirectory(prefix="wf-studio-extraction-adopt-") as temporary:
            root = Path(temporary)
            source_root = root / "source"
            source_root.mkdir()
            with _standalone("abstract-puzzle", source_root) as (_path, source):
                package = build_game_package_from_files(source.files)
            package_path = root / "source.wfgame"
            package_path.write_bytes(package.archive_bytes)
            destination = root / "extracted"
            retained: dict[str, dict[str, object]] = {}

            def remember_authority(phase: str, evidence: dict[str, object]) -> None:
                retained[phase] = dict(evidence)

            def stop_at_ready(phase: str, _path: Path | None) -> None:
                if phase == "after_ready_journal_written":
                    raise SystemExit("simulated ready extraction stop")

            with self.assertRaisesRegex(SystemExit, "ready extraction stop"):
                extract_game_package(
                    package_path,
                    destination,
                    _publication_hook=stop_at_ready,
                    _authority_hook=remember_authority,
                )
            staged = retained["publication_staged"]
            parent_info = path_file_stat(root)
            binding = {
                "path": str(destination),
                "parent_identity": (int(parent_info.st_dev), int(parent_info.st_ino)),
                "expected_manifest_hash": package.manifest["standalone_game"]["content_hash"],
                "expected_tree_hash": package.manifest["payload_lock"]["tree_hash"],
                "published_identity": None,
                "recovery": {
                    "phase": "publication_staged",
                    "expected_manifest_hash": package.manifest["standalone_game"]["content_hash"],
                    "expected_tree_hash": package.manifest["payload_lock"]["tree_hash"],
                    **staged,
                },
            }
            recovered_authority: dict[str, object] = {}

            def remember_recovery(phase: str, evidence: dict[str, object]) -> None:
                if phase == "publication_verified":
                    recovered_authority.update(evidence)

            identity = CreationJobCoordinator._game_package_extraction_publication_identity(
                binding,
                authority_hook=remember_recovery,
            )
            self.assertEqual(tuple(recovered_authority["published_identity"]), identity)
            assert identity is not None
            with verify_standalone_game(
                destination,
                expected_content_hash=package.manifest["standalone_game"]["content_hash"],
                expected_root_identity=identity,
            ) as verified:
                self.assertEqual(
                    package.manifest["payload_lock"]["tree_hash"],
                    verified.lock["tree_hash"],
                )
            verified_binding = {
                **binding,
                "published_identity": identity,
                "recovery": {
                    "phase": "publication_verified",
                    "expected_manifest_hash": binding["expected_manifest_hash"],
                    "expected_tree_hash": binding["expected_tree_hash"],
                    **recovered_authority,
                },
            }
            self.assertEqual(
                identity,
                CreationJobCoordinator._game_package_extraction_publication_identity(
                    verified_binding
                ),
            )

    def test_output_grant_reset_uses_the_extraction_stage_family(self) -> None:
        import sqlite3

        from worldforge.studio.creation_output_grants import (
            _require_reset_stage_absent,
        )
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory(prefix="wf-studio-extraction-stage-") as temporary:
            root = Path(temporary)
            destination = root / "extracted"
            operation_id = "a" * 32
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            connection.execute(
                "CREATE TABLE creation_jobs (job_id TEXT PRIMARY KEY, operation TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO creation_jobs (job_id, operation) VALUES (?, ?)",
                ("job_extract", "game.package.extract"),
            )
            row = {
                "absolute_path": str(destination),
                "reserved_job_id": "job_extract",
            }
            _require_reset_stage_absent(connection, row, operation_id)
            stage = root / f".extracted.game-package-stage-{operation_id}"
            stage.mkdir()
            with self.assertRaises(StudioError):
                _require_reset_stage_absent(connection, row, operation_id)
            connection.close()

    def test_restart_marks_v9_extraction_and_grant_recovery_required(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.integrity import canonical_payload_hash
        from worldforge.studio.creation_output_grants import CreationOutputGrantManager
        from worldforge.studio.storage import StudioStore, decode_object, encode_json, utc_now

        with tempfile.TemporaryDirectory(prefix="wf-studio-extraction-restart-") as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            data_dir = service.store.data_dir
            output_parent = base / "outputs"
            output_parent.mkdir()
            grant = service.creation_output_grants.create(
                {
                    "grant_id": "grant_extraction_restart",
                    "workspace_id": workspace["workspace_id"],
                    "kind": "standalone_game_directory",
                    "display_name": "extraction-restart",
                    "path": str(output_parent / "extraction-restart"),
                }
            )
            job_id = "job_extraction_restart"
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
                    "format_version": 9,
                    "job_id": job_id,
                    "workspace_id": workspace["workspace_id"],
                    "operation": "game.package.extract",
                    "operation_params": {
                        "game_package_artifact_id": "artifact_package_restart",
                        "source_grant_id": "grant_package_source_restart",
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
                    "record_json) VALUES (?, ?, 'game.package.extract', 'running', "
                    "'reserved', 1, ?)",
                    (job_id, workspace["workspace_id"], encode_json(record)),
                )
            service.close()
            service.store.close()
            with StudioStore(data_dir) as reopened:
                row = reopened.connection.execute(
                    "SELECT record_json FROM creation_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                assert row is not None
                orphaned = decode_object(
                    row["record_json"],
                    context="restarted package extraction",
                )
                self.assertEqual("orphaned", orphaned["state"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                recovered_grant = CreationOutputGrantManager(reopened).get(grant["grant_id"])
                self.assertEqual("recovery_required", recovered_grant["state"])
                self.assertEqual(reserved["generation"] + 1, recovered_grant["generation"])
            self.assertFalse((output_parent / "extraction-restart").exists())

    def test_explicit_v9_rollback_releases_an_absent_extraction_target(self) -> None:
        from gamepack_runtime.game_package import build_game_package_from_files
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.game_package import extract_game_package
        from worldforge.integrity import canonical_payload_hash
        from worldforge.studio.storage import encode_json, utc_now

        with tempfile.TemporaryDirectory(prefix="wf-studio-extraction-rollback-") as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                source_root = base / "source"
                source_root.mkdir()
                with _standalone("abstract-puzzle", source_root) as (_path, source):
                    package = build_game_package_from_files(source.files)
                package_path = base / "source.wfgame"
                package_path.write_bytes(package.archive_bytes)
                output_parent = base / "outputs"
                output_parent.mkdir()
                destination = output_parent / "extraction-rollback"
                grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_extraction_rollback",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "standalone_game_directory",
                        "display_name": "extraction-rollback",
                        "path": str(destination),
                    }
                )
                job_id = "job_extraction_rollback"
                with service.store.connection:
                    reserved, _binding = service.creation_output_grants.reserve_for_job(
                        grant_id=grant["grant_id"],
                        job_id=job_id,
                        workspace_id=workspace["workspace_id"],
                        expected_generation=grant["generation"],
                        expected_manifest_hash=package.manifest["standalone_game"]["content_hash"],
                        expected_tree_hash=package.manifest["payload_lock"]["tree_hash"],
                    )
                    service.creation_output_grants.begin_publication(job_id)
                    timestamp = utc_now()
                    record = {
                        "format": "world-forge.studio_creation_job",
                        "format_version": 9,
                        "job_id": job_id,
                        "workspace_id": workspace["workspace_id"],
                        "operation": "game.package.extract",
                        "operation_params": {
                            "game_package_artifact_id": "artifact_package_rollback",
                            "source_grant_id": "grant_package_source_rollback",
                            "source_grant_generation": 2,
                            "target_grant_id": grant["grant_id"],
                            "target_grant_generation": reserved["generation"],
                        },
                        "state": "orphaned",
                        "generation": 1,
                        "authority": {
                            "root_generation": workspace["root_generation"],
                            "source_revision": workspace["source_revision"],
                            "workflow_status_hash": workspace["workflow_status_hash"],
                            "artifact_snapshot_hash": "c" * 64,
                        },
                        "inputs": [],
                        "progress": "orphaned",
                        "result": None,
                        "error": {
                            "code": "recovery_required",
                            "message": "Extraction publication requires recovery",
                            "retryable": True,
                        },
                        "created_at": timestamp,
                        "started_at": timestamp,
                        "finished_at": timestamp,
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
                        "record_json) VALUES (?, ?, 'game.package.extract', 'orphaned', "
                        "'orphaned', 1, ?)",
                        (job_id, workspace["workspace_id"], encode_json(record)),
                    )

                def stop_after_intent(phase: str, _path: Path | None) -> None:
                    if phase == "after_intent_journal_written":
                        raise SystemExit("simulated extraction rollback stop")

                with self.assertRaisesRegex(SystemExit, "extraction rollback stop"):
                    extract_game_package(
                        package_path,
                        destination,
                        _publication_hook=stop_after_intent,
                        _authority_hook=lambda phase, evidence: (
                            service.creation_job_coordinator._persist_standalone_authority(  # noqa: SLF001
                                job_id,
                                phase,
                                evidence,
                            )
                        ),
                    )
                rolled_back = service.creation_job_coordinator.recover(
                    job_id,
                    mode="rollback",
                    expected_generation=record["generation"],
                    expected_record_hash=record["record_hash"],
                )
                self.assertEqual("failed", rolled_back["state"])
                self.assertFalse(destination.exists())
                released = service.creation_output_grants.get(grant["grant_id"])
                self.assertEqual("ready", released["state"])
                self.assertIsNone(released["publication"])
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
