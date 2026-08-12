from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_multigenre_standalone_materialization import _ready_materialization

ROOT = Path(__file__).resolve().parents[1]


def _prepare_published_materialization_bundle(base: Path):
    from tests.test_studio_creation_materialization_bundle_v4 import (
        _prepare_published_runtime_bundle,
    )

    service, workspace, _runtime_root, runtime_grant, runtime_job = (
        _prepare_published_runtime_bundle(base)
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
    published_runtime = service.creation_output_grants.get(runtime_grant["grant_id"])
    materialization_root = base / "outputs" / "materialization-for-standalone"
    materialization_grant = service.creation_output_grants.create(
        {
            "grant_id": "grant_materialization_for_standalone",
            "workspace_id": workspace["workspace_id"],
            "kind": "game_materialization_bundle_directory",
            "display_name": "materialization-for-standalone",
            "path": str(materialization_root),
        }
    )
    materialization_job = service.creation_jobs.create_materialization_bundle(
        {
            "job_id": "job_materialization_for_standalone",
            "workspace_id": workspace["workspace_id"],
            "operation": "game.materialization.bundle.build",
            "expected_root_generation": workspace["root_generation"],
            "expected_source_revision": workspace["source_revision"],
            "expected_workflow_status_hash": workspace["workflow_status_hash"],
            "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
            "runtime_bundle_artifact_id": runtime_job["result"]["output_artifact_ids"][0],
            "source_grant_id": runtime_grant["grant_id"],
            "expected_source_grant_generation": published_runtime["generation"],
            "target_grant_id": materialization_grant["grant_id"],
            "expected_target_grant_generation": materialization_grant["generation"],
        }
    )
    service.creation_job_coordinator.run_once()
    completed = service.creation_jobs.get(materialization_job["job_id"])
    assert completed["state"] == "succeeded", completed
    return service, workspace, materialization_root, materialization_grant, completed


def _game_materialize_params(
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
        "operation": "game.materialize",
        "expected_root_generation": workspace["root_generation"],
        "expected_source_revision": workspace["source_revision"],
        "expected_workflow_status_hash": workspace["workflow_status_hash"],
        "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
        "materialization_bundle_artifact_id": source_job["result"]["output_artifact_ids"][0],
        "source_grant_id": source_grant["grant_id"],
        "expected_source_grant_generation": published_source["generation"],
        "target_grant_id": target_grant["grant_id"],
        "expected_target_grant_generation": target_grant["generation"],
    }


class StudioGameMaterializeContractTests(unittest.TestCase):
    def test_v7_worker_request_is_closed_pathless_and_deterministic(self) -> None:
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio.creation_job_protocol import (
            build_private_game_materialize_request,
            execute_private_creation_request,
            validate_private_creation_request,
        )

        with tempfile.TemporaryDirectory(prefix="wf-studio-game-materialize-worker-") as temporary:
            root = Path(temporary)
            with _ready_materialization("abstract-puzzle", root) as source:
                staged_inputs = [
                    {
                        "source_locator": locator,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "size_bytes": len(payload),
                    }
                    for locator, payload in sorted(
                        source.files.items(),
                        key=lambda item: item[0].encode("utf-8"),
                    )
                ]
                request = build_private_game_materialize_request(
                    job_id="job_game_materialize_worker",
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
                    materialization_bundle_manifest=source.manifest,
                    source_grant_id="grant_materialization_source",
                    source_grant_generation=2,
                    target_grant_id="grant_standalone_target",
                    target_grant_generation=1,
                    staged_inputs=staged_inputs,
                )
                self.assertEqual(7, request["format_version"])
                self.assertEqual("game.materialize", request["operation"])
                self.assertNotIn(str(source.root), json.dumps(request))
                self.assertEqual(request, validate_private_creation_request(request))
                with self.assertRaisesRegex(ValueError, "fields"):
                    validate_private_creation_request({**request, "native_path": str(source.root)})
                first = execute_private_creation_request(
                    request,
                    artifact_root=source.root,
                )
                second = execute_private_creation_request(
                    request,
                    artifact_root=source.root,
                )
                self.assertEqual(1, len(first.outputs))
                self.assertEqual(
                    "world-forge.standalone_game",
                    first.outputs[0].subject["format"],
                )
                self.assertEqual(first.outputs[0].payload, second.outputs[0].payload)

    def test_output_grant_v4_and_job_v7_are_closed_without_changing_v1_v6(self) -> None:
        from tests.test_studio_creation_asset_seal_v4 import _grant_record
        from worldforge.studio.contracts import (
            StudioContractError,
            validate_studio_creation_output_grant,
        )
        from worldforge.studio.service import StudioService

        initialized = StudioService._initialize({}, protocol_version=4)  # noqa: SLF001
        self.assertTrue(initialized["capabilities"]["materialization_execution"])
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
            "game.materialize",
            job_schema["oneOf"][6]["properties"]["operation"]["const"],
        )
        self.assertEqual(
            "World Forge Studio isolated creation worker envelope v11",
            worker_schema["title"],
        )
        self.assertEqual(33, len(worker_schema["oneOf"]))
        self.assertEqual("World Forge Studio creation output grant v5", output_schema["title"])
        self.assertEqual(10, len(protocol_schema["$defs"]["jobCreateParams"]["oneOf"]))
        self.assertIn(
            "standalone_game_directory",
            protocol_schema["$defs"]["outputGrantCreateParams"]["properties"]["kind"]["enum"],
        )
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        self.assertEqual(9, entries["studio-creation-job"]["version"])
        self.assertEqual(11, entries["studio-creation-worker"]["version"])
        self.assertEqual(5, entries["studio-creation-output-grant"]["version"])

        legacy = _grant_record()
        documents = (
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
        for document in documents:
            self.assertEqual(
                document,
                validate_studio_creation_output_grant(copy.deepcopy(document)),
            )
        with self.assertRaisesRegex(StudioContractError, "unknown fields"):
            validate_studio_creation_output_grant(
                {**documents[-1], "native_path": "/private/native/path"}
            )

    def test_public_v4_game_materialize_request_is_fixed_pathless_and_cas_bound(self) -> None:
        from worldforge.studio.contracts import (
            StudioContractError,
            validate_studio_protocol_envelope,
        )

        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "request",
            "request_id": "game-materialize-request-01",
            "method": "creation_job.create",
            "params": {
                "job_id": "job_game_materialize_request",
                "workspace_id": "workspace_puzzle",
                "operation": "game.materialize",
                "expected_root_generation": 1,
                "expected_source_revision": "a" * 64,
                "expected_workflow_status_hash": None,
                "expected_artifact_snapshot_hash": "b" * 64,
                "materialization_bundle_artifact_id": "artifact_materialization_bundle",
                "source_grant_id": "grant_materialization_source",
                "expected_source_grant_generation": 2,
                "target_grant_id": "grant_standalone_target",
                "expected_target_grant_generation": 0,
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(copy.deepcopy(request)))
        for leaked in (
            {**request["params"], "path": "/renderer/private"},
            {**request["params"], "kind": "standalone_game_directory"},
            {**request["params"], "adapter_id": "renderer_selected"},
        ):
            with self.assertRaises(StudioContractError):
                validate_studio_protocol_envelope({**request, "params": leaked})


class StudioGameMaterializeCoordinatorTests(unittest.TestCase):
    def test_recovery_rejects_unjournaled_foreign_and_replaced_visible_roots(self) -> None:
        from worldforge.directory_publish import directory_identity
        from worldforge.standalone_game import StandaloneGameError, materialize_game
        from worldforge.studio.creation_jobs import CreationJobCoordinator

        with tempfile.TemporaryDirectory(prefix="wf-studio-game-materialize-authority-") as temp:
            root = Path(temp)
            output_parent = root / "outputs"
            output_parent.mkdir()
            parent_identity = directory_identity(
                output_parent,
                context="standalone recovery test parent",
            )
            source_parent = root / "source"
            source_parent.mkdir()
            with _ready_materialization("abstract-puzzle", source_parent) as source:
                foreign = output_parent / "foreign-visible"
                with materialize_game(source.root, foreign) as visible:
                    manifest_hash = visible.manifest["content_hash"]
                    tree_hash = visible.lock["tree_hash"]
                started_binding = {
                    "path": foreign,
                    "parent_identity": parent_identity,
                    "expected_manifest_hash": manifest_hash,
                    "expected_tree_hash": tree_hash,
                    "published_identity": None,
                    "recovery": {
                        "phase": "publication_started",
                        "expected_manifest_hash": manifest_hash,
                        "expected_tree_hash": tree_hash,
                        "journal_identity": [7, 11],
                        "operation_id": "0" * 32,
                        "journal_payload_sha256": "1" * 64,
                        "journal_payload_state": "intent",
                    },
                }
                with self.assertRaises(StandaloneGameError):
                    CreationJobCoordinator._standalone_publication_identity(  # noqa: SLF001
                        started_binding
                    )

                retained = output_parent / "retained-visible"
                with materialize_game(source.root, retained) as visible:
                    retained_identity = tuple(visible.root_identity)
                    manifest_hash = visible.manifest["content_hash"]
                    tree_hash = visible.lock["tree_hash"]
                original = output_parent / "retained-original"
                retained.rename(original)
                shutil.copytree(original, retained)
                replacement_identity = directory_identity(
                    retained,
                    context="replacement standalone root",
                )
                self.assertNotEqual(retained_identity, replacement_identity)
                verified_binding = {
                    "path": retained,
                    "parent_identity": parent_identity,
                    "expected_manifest_hash": manifest_hash,
                    "expected_tree_hash": tree_hash,
                    "published_identity": retained_identity,
                    "recovery": {
                        "phase": "publication_verified",
                        "expected_manifest_hash": manifest_hash,
                        "expected_tree_hash": tree_hash,
                        "published_identity": list(retained_identity),
                        "journal_identity": [7, 11],
                        "operation_id": "0" * 32,
                        "stage_identity": list(retained_identity),
                        "journal_payload_sha256": "3" * 64,
                        "journal_payload_state": "ready",
                    },
                }
                with self.assertRaises(StandaloneGameError):
                    CreationJobCoordinator._standalone_publication_identity(  # noqa: SLF001
                        verified_binding
                    )

    def test_verified_publication_identity_is_write_once(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.directory_publish import directory_identity
        from worldforge.integrity import canonical_payload_hash
        from worldforge.studio.errors import StudioError
        from worldforge.studio.storage import encode_json, utc_now

        with tempfile.TemporaryDirectory(prefix="wf-studio-game-materialize-identity-") as temp:
            base = Path(temp)
            service, workspace = _prepared_creation_service(base)
            try:
                output_parent = base / "outputs"
                output_parent.mkdir()
                target = output_parent / "standalone-identity"
                grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_standalone_identity",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "standalone_game_directory",
                        "display_name": "standalone-identity",
                        "path": str(target),
                    }
                )
                with service.store.connection:
                    reserved, _binding = service.creation_output_grants.reserve_for_job(
                        grant_id=grant["grant_id"],
                        job_id="job_standalone_identity",
                        workspace_id=workspace["workspace_id"],
                        expected_generation=grant["generation"],
                        expected_manifest_hash="a" * 64,
                        expected_tree_hash="b" * 64,
                    )
                    timestamp = utc_now()
                    record = {
                        "format": "world-forge.studio_creation_job",
                        "format_version": 7,
                        "job_id": "job_standalone_identity",
                        "workspace_id": workspace["workspace_id"],
                        "operation": "game.materialize",
                        "operation_params": {
                            "materialization_bundle_artifact_id": "artifact_identity_source",
                            "source_grant_id": "grant_identity_source",
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
                        "record_json) VALUES (?, ?, 'game.materialize', 'queued', "
                        "'queued', 0, ?)",
                        (
                            record["job_id"],
                            workspace["workspace_id"],
                            encode_json(record),
                        ),
                    )
                    service.creation_output_grants.begin_publication("job_standalone_identity")
                    service.creation_output_grants.note_publication_started(
                        "job_standalone_identity",
                        journal_identity=(7, 11),
                        operation_id="0" * 32,
                        journal_payload_sha256="1" * 64,
                        journal_payload_state="intent",
                    )
                    service.creation_output_grants.note_publication_resetting(
                        "job_standalone_identity",
                        journal_identity=(7, 11),
                        operation_id="0" * 32,
                    )
                reappeared_stage = output_parent / (f".{target.name}.standalone-stage-{'0' * 32}")
                reappeared_stage.mkdir()
                with self.assertRaises(StudioError):
                    with service.store.connection:
                        service.creation_output_grants.reset_publication_started(
                            "job_standalone_identity",
                            journal_identity=(7, 11),
                            operation_id="0" * 32,
                        )
                retained_reset = service.creation_output_grants.binding_for_job(
                    "job_standalone_identity",
                    allow_visible=False,
                )
                self.assertEqual(
                    "publication_resetting",
                    retained_reset["recovery"]["phase"],
                )
                reappeared_stage.rmdir()
                with service.store.connection:
                    service.creation_output_grants.reset_publication_started(
                        "job_standalone_identity",
                        journal_identity=(7, 11),
                        operation_id="0" * 32,
                    )
                    service.creation_output_grants.note_publication_started(
                        "job_standalone_identity",
                        journal_identity=(7, 11),
                        operation_id="0" * 32,
                        journal_payload_sha256="1" * 64,
                        journal_payload_state="intent",
                    )
                stage = output_parent / ".standalone-identity-stage"
                stage.mkdir()
                retained_identity = directory_identity(
                    stage,
                    context="retained standalone identity",
                )
                with service.store.connection:
                    service.creation_output_grants.note_publication_stage_allocated(
                        "job_standalone_identity",
                        journal_identity=(7, 11),
                        operation_id="0" * 32,
                        stage_identity=retained_identity,
                        journal_payload_sha256="1" * 64,
                        journal_payload_state="intent",
                    )
                    service.creation_output_grants.note_publication_staged(
                        "job_standalone_identity",
                        journal_identity=(7, 11),
                        operation_id="0" * 32,
                        stage_identity=retained_identity,
                        journal_payload_sha256="2" * 64,
                        journal_payload_state="copying",
                    )
                stage.rename(target)
                with service.store.connection:
                    service.creation_output_grants.note_publication_verified(
                        "job_standalone_identity",
                        published_identity=retained_identity,
                        journal_identity=(7, 11),
                        operation_id="0" * 32,
                        stage_identity=retained_identity,
                        journal_payload_sha256="3" * 64,
                        journal_payload_state="ready",
                    )
                target.rename(output_parent / "standalone-identity-original")
                target.mkdir()
                replacement_identity = directory_identity(
                    target,
                    context="replacement standalone identity",
                )
                self.assertNotEqual(retained_identity, replacement_identity)
                with self.assertRaises(StudioError):
                    with service.store.connection:
                        service.creation_output_grants.note_publication_verified(
                            "job_standalone_identity",
                            published_identity=replacement_identity,
                        )
            finally:
                service.close()

    def test_standalone_journal_authority_handoff_precedes_retirement(self) -> None:
        from worldforge.directory_publish import directory_identity
        from worldforge.standalone_game import (
            StandaloneGameError,
            materialize_game,
            recover_standalone_game,
        )

        with tempfile.TemporaryDirectory(prefix="wf-standalone-authority-handoff-") as temp:
            root = Path(temp)
            source_parent = root / "source"
            source_parent.mkdir()
            output_parent = root / "outputs"
            output_parent.mkdir()
            destination = output_parent / "standalone"
            parent_identity = directory_identity(
                output_parent,
                context="standalone authority handoff parent",
            )
            with _ready_materialization("abstract-puzzle", source_parent) as source:
                retained_authority: dict[str, object] = {}

                def crash_before_retirement(phase: str, evidence: dict[str, object]) -> None:
                    if phase in {
                        "publication_started",
                        "publication_stage_allocated",
                        "publication_staged",
                        "publication_verified",
                    }:
                        retained_authority.clear()
                        retained_authority.update(evidence)
                    if phase == "publication_verified":
                        raise SystemExit("simulated crash before journal retirement")

                with self.assertRaises(StandaloneGameError) as interrupted:
                    materialize_game(
                        source.root,
                        destination,
                        _authority_hook=crash_before_retirement,
                    )
                self.assertIn(
                    interrupted.exception.reason_code,
                    {
                        "standalone_game_publication_failed",
                        "standalone_game_publication_indeterminate",
                    },
                )
                self.assertTrue(destination.is_dir())
                self.assertEqual(
                    {
                        "journal_identity",
                        "journal_payload_sha256",
                        "journal_payload_state",
                        "operation_id",
                        "published_identity",
                        "stage_identity",
                    },
                    set(retained_authority),
                )
                verified_events: list[dict[str, object]] = []

                def persist_recovered_identity(
                    phase: str,
                    evidence: dict[str, object],
                ) -> None:
                    if phase == "publication_verified":
                        verified_events.append(dict(evidence))

                with recover_standalone_game(
                    destination,
                    expected_parent_identity=parent_identity,
                    expected_journal_identity=tuple(retained_authority["journal_identity"]),
                    expected_operation_id=str(retained_authority["operation_id"]),
                    expected_stage_identity=tuple(retained_authority["stage_identity"]),
                    expected_journal_payload_sha256=str(
                        retained_authority["journal_payload_sha256"]
                    ),
                    expected_journal_payload_state=str(retained_authority["journal_payload_state"]),
                    require_journal_for_visible=True,
                    _authority_hook=persist_recovered_identity,
                ) as recovered:
                    assert recovered is not None
                    self.assertEqual(
                        tuple(verified_events[0]["published_identity"]),
                        tuple(recovered.root_identity),
                    )

    def test_standalone_recovery_rejects_a_replaced_bound_journal(self) -> None:
        from worldforge.standalone_game import (
            StandaloneGameError,
            materialize_game,
            recover_standalone_game,
            rollback_standalone_game,
        )

        with tempfile.TemporaryDirectory(prefix="wf-standalone-journal-binding-") as temp:
            root = Path(temp)
            source_parent = root / "source"
            source_parent.mkdir()
            output_parent = root / "outputs"
            output_parent.mkdir()
            destination = output_parent / "standalone"
            with _ready_materialization("abstract-puzzle", source_parent) as source:
                started: dict[str, object] = {}

                def remember_authority(phase: str, evidence: dict[str, object]) -> None:
                    if phase == "publication_started":
                        started.update(evidence)

                def stop_at_ready(phase: str, _path: Path | None) -> None:
                    if phase == "after_ready_journal_written":
                        raise SystemExit("simulated stop with active ready journal")

                with self.assertRaisesRegex(SystemExit, "active ready journal"):
                    materialize_game(
                        source.root,
                        destination,
                        _publication_hook=stop_at_ready,
                        _authority_hook=remember_authority,
                    )
                journal_identity = tuple(started["journal_identity"])
                replaced_identity = (journal_identity[0], journal_identity[1] + 1)
                with self.assertRaises(StandaloneGameError):
                    recover_standalone_game(
                        destination,
                        expected_journal_identity=replaced_identity,
                        expected_operation_id=str(started["operation_id"]),
                        require_journal_for_visible=True,
                    )
                with self.assertRaises(StandaloneGameError):
                    rollback_standalone_game(
                        destination,
                        expected_journal_identity=replaced_identity,
                        expected_operation_id=str(started["operation_id"]),
                    )

    def test_standalone_rollback_rejects_same_inode_journal_content_rewrite(self) -> None:
        from gamepack_runtime.distribution import canonical_contract_bytes
        from worldforge.standalone_game import (
            StandaloneGameError,
            materialize_game,
            recover_standalone_game,
            rollback_standalone_game,
        )

        with tempfile.TemporaryDirectory(prefix="wf-standalone-journal-content-") as temp:
            root = Path(temp)
            source_parent = root / "source"
            source_parent.mkdir()
            output_parent = root / "outputs"
            output_parent.mkdir()
            destination = output_parent / "standalone"
            started: dict[str, object] = {}

            def remember_authority(phase: str, evidence: dict[str, object]) -> None:
                if phase == "publication_started":
                    started.update(evidence)

            def stop_after_intent(phase: str, _path: Path | None) -> None:
                if phase == "after_intent_journal_written":
                    raise SystemExit("simulated stop after bound intent")

            with _ready_materialization("abstract-puzzle", source_parent) as source:
                with self.assertRaisesRegex(SystemExit, "bound intent"):
                    materialize_game(
                        source.root,
                        destination,
                        _publication_hook=stop_after_intent,
                        _authority_hook=remember_authority,
                    )

            journal = output_parent / ".standalone.standalone-game.journal.json"
            before = journal.stat()
            document = json.loads(journal.read_bytes())
            expected_content_hash = document["standalone_game_hash"]
            expected_tree_hash = document["payload_tree_hash"]
            original_payload = canonical_contract_bytes(document)
            mutations: dict[str, object] = {
                field: ("f" * 64 if document[field] != "f" * 64 else "e" * 64)
                for field in (
                    "standalone_game_hash",
                    "payload_lock_hash",
                    "payload_tree_hash",
                    "materialization_bundle_hash",
                    "manifest_sha256",
                    "lock_sha256",
                )
            }
            mutations.update(
                {
                    "manifest_size_bytes": document["manifest_size_bytes"] + 1,
                    "lock_size_bytes": document["lock_size_bytes"] + 1,
                }
            )
            for field, replacement in mutations.items():
                with self.subTest(field=field):
                    journal.write_bytes(canonical_contract_bytes({**document, field: replacement}))
                    after = journal.stat()
                    self.assertEqual(
                        (before.st_dev, before.st_ino),
                        (after.st_dev, after.st_ino),
                    )

                    with self.assertRaises(StandaloneGameError) as recovery_rejected:
                        recover_standalone_game(
                            destination,
                            expected_journal_identity=tuple(started["journal_identity"]),
                            expected_operation_id=str(started["operation_id"]),
                            expected_content_hash=expected_content_hash,
                            expected_tree_hash=expected_tree_hash,
                            expected_journal_payload_sha256=str(started["journal_payload_sha256"]),
                            expected_journal_payload_state=str(started["journal_payload_state"]),
                        )
                    self.assertEqual(
                        "standalone_game_recovery_ambiguous",
                        recovery_rejected.exception.reason_code,
                    )
                    with self.assertRaises(StandaloneGameError) as rejected:
                        rollback_standalone_game(
                            destination,
                            expected_journal_identity=tuple(started["journal_identity"]),
                            expected_operation_id=str(started["operation_id"]),
                            expected_content_hash=expected_content_hash,
                            expected_tree_hash=expected_tree_hash,
                            expected_journal_payload_sha256=str(started["journal_payload_sha256"]),
                            expected_journal_payload_state=str(started["journal_payload_state"]),
                        )
                    self.assertEqual(
                        "standalone_game_rollback_ambiguous",
                        rejected.exception.reason_code,
                    )
                    journal.write_bytes(original_payload)
            self.assertTrue(journal.is_file())

    def test_copying_and_ready_history_rewrites_fail_exact_payload_authority(self) -> None:
        from worldforge.directory_publish import _journal_frame
        from worldforge.standalone_game import (
            StandaloneGameError,
            _history_payloads,
            _read_journal,
            materialize_game,
            recover_standalone_game,
            rollback_standalone_game,
        )

        for terminal_hook in (
            "after_copying_journal_written",
            "after_ready_journal_written",
        ):
            with self.subTest(terminal_hook=terminal_hook):
                with tempfile.TemporaryDirectory(
                    prefix="wf-standalone-journal-history-content-"
                ) as temp:
                    root = Path(temp)
                    source_parent = root / "source"
                    source_parent.mkdir()
                    output_parent = root / "outputs"
                    output_parent.mkdir()
                    destination = output_parent / "standalone"
                    staged: dict[str, object] = {}

                    def remember_staged(
                        phase: str,
                        evidence: dict[str, object],
                        *,
                        retained: dict[str, object] = staged,
                    ) -> None:
                        if phase == "publication_staged":
                            retained.update(evidence)

                    def stop_at_terminal(
                        phase: str,
                        _path: Path | None,
                        *,
                        expected: str = terminal_hook,
                    ) -> None:
                        if phase == expected:
                            raise SystemExit(f"simulated {expected}")

                    with _ready_materialization("abstract-puzzle", source_parent) as source:
                        with self.assertRaisesRegex(SystemExit, terminal_hook):
                            materialize_game(
                                source.root,
                                destination,
                                _publication_hook=stop_at_terminal,
                                _authority_hook=remember_staged,
                            )

                    loaded = _read_journal(destination)
                    assert loaded is not None
                    document, _identity, _payload, _partial = loaded
                    journal = output_parent / ".standalone.standalone-game.journal.json"
                    before = journal.stat()
                    replacement = (
                        "f" * 64
                        if document["materialization_bundle_hash"] != "f" * 64
                        else "e" * 64
                    )
                    payloads = _history_payloads(
                        {**document, "materialization_bundle_hash": replacement}
                    )
                    journal.write_bytes(
                        payloads[0] + b"".join(_journal_frame(item) for item in payloads[1:])
                    )
                    after = journal.stat()
                    self.assertEqual(
                        (before.st_dev, before.st_ino),
                        (after.st_dev, after.st_ino),
                    )
                    authority = {
                        "expected_journal_identity": tuple(staged["journal_identity"]),
                        "expected_operation_id": str(staged["operation_id"]),
                        "expected_content_hash": document["standalone_game_hash"],
                        "expected_tree_hash": document["payload_tree_hash"],
                        "expected_stage_identity": tuple(staged["stage_identity"]),
                        "expected_journal_payload_sha256": str(staged["journal_payload_sha256"]),
                        "expected_journal_payload_state": str(staged["journal_payload_state"]),
                    }
                    with self.assertRaises(StandaloneGameError) as recovery_rejected:
                        recover_standalone_game(destination, **authority)
                    self.assertEqual(
                        "standalone_game_recovery_ambiguous",
                        recovery_rejected.exception.reason_code,
                    )
                    with self.assertRaises(StandaloneGameError) as rollback_rejected:
                        rollback_standalone_game(destination, **authority)
                    self.assertEqual(
                        "standalone_game_rollback_ambiguous",
                        rollback_rejected.exception.reason_code,
                    )

    def test_stage_authority_is_persisted_before_stage_writes(self) -> None:
        from worldforge.standalone_game import materialize_game

        with tempfile.TemporaryDirectory(prefix="wf-standalone-stage-authority-") as temp:
            root = Path(temp)
            source_parent = root / "source"
            source_parent.mkdir()
            output_parent = root / "outputs"
            output_parent.mkdir()
            destination = output_parent / "standalone"
            authority: list[tuple[str, dict[str, object]]] = []

            def remember_authority(phase: str, evidence: dict[str, object]) -> None:
                authority.append((phase, dict(evidence)))

            def stop_before_writes(phase: str, _path: Path | None) -> None:
                if phase == "after_copying_journal_written":
                    raise SystemExit("simulated stop before stage writes")

            with _ready_materialization("abstract-puzzle", source_parent) as source:
                with self.assertRaisesRegex(SystemExit, "before stage writes"):
                    materialize_game(
                        source.root,
                        destination,
                        _publication_hook=stop_before_writes,
                        _authority_hook=remember_authority,
                    )
            self.assertEqual(
                [
                    "publication_started",
                    "publication_stage_allocated",
                    "publication_staged",
                ],
                [phase for phase, _evidence in authority],
            )
            staged = authority[-1][1]
            self.assertEqual(
                {
                    "journal_identity",
                    "journal_payload_sha256",
                    "journal_payload_state",
                    "operation_id",
                    "stage_identity",
                },
                set(staged),
            )

    def test_allocated_stage_reconciles_copying_journal_before_recovery(self) -> None:
        from worldforge.standalone_game import (
            StandaloneGameError,
            _read_journal,
            materialize_game,
            recover_standalone_game,
        )

        with tempfile.TemporaryDirectory(prefix="wf-standalone-stage-split-") as temp:
            root = Path(temp)
            source_parent = root / "source"
            source_parent.mkdir()
            output_parent = root / "outputs"
            output_parent.mkdir()
            destination = output_parent / "standalone"
            allocated: dict[str, object] = {}

            def crash_before_staged_commit(
                phase: str,
                evidence: dict[str, object],
            ) -> None:
                if phase == "publication_stage_allocated":
                    allocated.update(evidence)
                elif phase == "publication_staged":
                    raise SystemExit("simulated crash before staged SQLite commit")

            with _ready_materialization("abstract-puzzle", source_parent) as source:
                with self.assertRaisesRegex(SystemExit, "before staged SQLite commit"):
                    materialize_game(
                        source.root,
                        destination,
                        _authority_hook=crash_before_staged_commit,
                    )

            loaded = _read_journal(destination)
            assert loaded is not None
            journal = loaded[0]
            reconciled: list[str] = []
            with self.assertRaises(StandaloneGameError) as incomplete:
                recover_standalone_game(
                    destination,
                    expected_journal_identity=tuple(allocated["journal_identity"]),
                    expected_operation_id=str(allocated["operation_id"]),
                    expected_content_hash=journal["standalone_game_hash"],
                    expected_tree_hash=journal["payload_tree_hash"],
                    expected_stage_identity=tuple(allocated["stage_identity"]),
                    expected_journal_payload_sha256=str(allocated["journal_payload_sha256"]),
                    expected_journal_payload_state=str(allocated["journal_payload_state"]),
                    stage_allocated=True,
                    _authority_hook=lambda phase, _evidence: reconciled.append(phase),
                )
            self.assertEqual("standalone_game_recovery_required", incomplete.exception.reason_code)
            self.assertEqual(["publication_staged"], reconciled)

    def test_rollback_validates_stage_before_resetting_authority(self) -> None:
        from gamepack_runtime.distribution import canonical_contract_bytes
        from worldforge.standalone_game import (
            StandaloneGameError,
            materialize_game,
            rollback_standalone_game,
        )

        with tempfile.TemporaryDirectory(prefix="wf-standalone-rollback-order-") as temp:
            root = Path(temp)
            source_parent = root / "source"
            source_parent.mkdir()
            output_parent = root / "outputs"
            output_parent.mkdir()
            destination = output_parent / "standalone"
            started: dict[str, object] = {}

            def remember_started(phase: str, evidence: dict[str, object]) -> None:
                if phase == "publication_started":
                    started.update(evidence)

            def stop_after_intent(phase: str, _path: Path | None) -> None:
                if phase == "after_intent_journal_written":
                    raise SystemExit("simulated stop before rollback ordering")

            with _ready_materialization("abstract-puzzle", source_parent) as source:
                with self.assertRaisesRegex(SystemExit, "rollback ordering"):
                    materialize_game(
                        source.root,
                        destination,
                        _publication_hook=stop_after_intent,
                        _authority_hook=remember_started,
                    )

            journal = output_parent / ".standalone.standalone-game.journal.json"
            document = json.loads(journal.read_bytes())
            foreign_stage = output_parent / document["stage_name"]
            foreign_stage.mkdir()
            events: list[str] = []
            with self.assertRaises(StandaloneGameError) as rejected:
                rollback_standalone_game(
                    destination,
                    expected_journal_identity=tuple(started["journal_identity"]),
                    expected_operation_id=str(started["operation_id"]),
                    expected_content_hash=document["standalone_game_hash"],
                    expected_tree_hash=document["payload_tree_hash"],
                    expected_journal_payload_sha256=hashlib.sha256(
                        canonical_contract_bytes(document)
                    ).hexdigest(),
                    expected_journal_payload_state="intent",
                    _authority_hook=lambda phase, _evidence: events.append(phase),
                )
            self.assertEqual("standalone_game_rollback_ambiguous", rejected.exception.reason_code)
            self.assertEqual([], events)

    def test_stage_bound_reset_retries_after_exact_stage_deletion(self) -> None:
        from worldforge import standalone_game as standalone_module
        from worldforge.directory_publish import directory_identity
        from worldforge.standalone_game import (
            _read_journal,
            materialize_game,
            rollback_standalone_game,
        )

        with tempfile.TemporaryDirectory(prefix="wf-standalone-reset-stage-deleted-") as temp:
            root = Path(temp)
            source_parent = root / "source"
            source_parent.mkdir()
            output_parent = root / "outputs"
            output_parent.mkdir()
            destination = output_parent / "standalone"
            staged: dict[str, object] = {}

            def remember_staged(phase: str, evidence: dict[str, object]) -> None:
                if phase == "publication_staged":
                    staged.update(evidence)

            def stop_before_writes(phase: str, _path: Path | None) -> None:
                if phase == "after_copying_journal_written":
                    raise SystemExit("simulated stop with an exact empty stage")

            with _ready_materialization("abstract-puzzle", source_parent) as source:
                with self.assertRaisesRegex(SystemExit, "exact empty stage"):
                    materialize_game(
                        source.root,
                        destination,
                        _publication_hook=stop_before_writes,
                        _authority_hook=remember_staged,
                    )

            journal = output_parent / ".standalone.standalone-game.journal.json"
            loaded = _read_journal(destination)
            assert loaded is not None
            document = loaded[0]
            stage = output_parent / document["stage_name"]

            def remove_then_crash(path: Path, expected_identity, **_kwargs) -> None:
                self.assertEqual(
                    tuple(expected_identity),
                    directory_identity(path, context="simulated completed stage cleanup"),
                )
                path.rmdir()
                raise SystemExit("simulated crash after exact stage deletion")

            first_events: list[str] = []
            with patch.object(
                standalone_module,
                "quarantine_and_remove_verified_directory",
                side_effect=remove_then_crash,
            ):
                with self.assertRaisesRegex(SystemExit, "after exact stage deletion"):
                    rollback_standalone_game(
                        destination,
                        expected_journal_identity=tuple(staged["journal_identity"]),
                        expected_operation_id=str(staged["operation_id"]),
                        expected_content_hash=document["standalone_game_hash"],
                        expected_tree_hash=document["payload_tree_hash"],
                        expected_stage_identity=tuple(staged["stage_identity"]),
                        expected_journal_payload_sha256=str(staged["journal_payload_sha256"]),
                        expected_journal_payload_state=str(staged["journal_payload_state"]),
                        _authority_hook=lambda phase, _evidence: first_events.append(phase),
                    )
            self.assertEqual(["publication_resetting"], first_events)
            self.assertFalse(stage.exists())
            self.assertTrue(journal.is_file())

            retry_events: list[str] = []
            result = rollback_standalone_game(
                destination,
                expected_journal_identity=tuple(staged["journal_identity"]),
                expected_operation_id=str(staged["operation_id"]),
                expected_content_hash=document["standalone_game_hash"],
                expected_tree_hash=document["payload_tree_hash"],
                expected_stage_identity=tuple(staged["stage_identity"]),
                expected_journal_payload_sha256=str(staged["journal_payload_sha256"]),
                expected_journal_payload_state=str(staged["journal_payload_state"]),
                allow_missing_expected_journal=True,
                reset_pending=True,
                _authority_hook=lambda phase, _evidence: retry_events.append(phase),
            )
            self.assertEqual("rolled_back", result["status"])
            self.assertEqual(["publication_resetting", "publication_reset"], retry_events)
            self.assertFalse(stage.exists())
            self.assertFalse(journal.exists())

    def test_missing_journal_reset_rejects_a_reappeared_stage(self) -> None:
        from worldforge.standalone_game import (
            StandaloneGameError,
            materialize_game,
            recover_standalone_game,
            rollback_standalone_game,
        )

        with tempfile.TemporaryDirectory(prefix="wf-standalone-reset-stage-reappeared-") as temp:
            root = Path(temp)
            source_parent = root / "source"
            source_parent.mkdir()
            output_parent = root / "outputs"
            output_parent.mkdir()
            destination = output_parent / "standalone"
            started: dict[str, object] = {}

            def remember_started(phase: str, evidence: dict[str, object]) -> None:
                if phase == "publication_started":
                    started.update(evidence)

            def stop_after_intent(phase: str, _path: Path | None) -> None:
                if phase == "after_intent_journal_written":
                    raise SystemExit("simulated stop before reset")

            with _ready_materialization("abstract-puzzle", source_parent) as source:
                with self.assertRaisesRegex(SystemExit, "before reset"):
                    materialize_game(
                        source.root,
                        destination,
                        _publication_hook=stop_after_intent,
                        _authority_hook=remember_started,
                    )

            journal = output_parent / ".standalone.standalone-game.journal.json"
            document = json.loads(journal.read_bytes())
            stage = output_parent / document["stage_name"]

            def reappear_before_reset_commit(
                phase: str,
                _evidence: dict[str, object],
            ) -> None:
                if phase == "publication_reset":
                    stage.mkdir()
                    raise SystemExit("simulated foreign stage reappearance")

            with self.assertRaisesRegex(SystemExit, "foreign stage reappearance"):
                recover_standalone_game(
                    destination,
                    expected_journal_identity=tuple(started["journal_identity"]),
                    expected_operation_id=str(started["operation_id"]),
                    expected_content_hash=document["standalone_game_hash"],
                    expected_tree_hash=document["payload_tree_hash"],
                    expected_journal_payload_sha256=str(started["journal_payload_sha256"]),
                    expected_journal_payload_state=str(started["journal_payload_state"]),
                    require_intent_journal=True,
                    _authority_hook=reappear_before_reset_commit,
                )
            self.assertFalse(journal.exists())
            self.assertTrue(stage.is_dir())

            reset_events: list[str] = []
            with self.assertRaises(StandaloneGameError) as recovery_rejected:
                recover_standalone_game(
                    destination,
                    expected_journal_identity=tuple(started["journal_identity"]),
                    expected_operation_id=str(started["operation_id"]),
                    expected_content_hash=document["standalone_game_hash"],
                    expected_tree_hash=document["payload_tree_hash"],
                    expected_journal_payload_sha256=str(started["journal_payload_sha256"]),
                    expected_journal_payload_state=str(started["journal_payload_state"]),
                    allow_missing_expected_journal=True,
                    reset_pending=True,
                    _authority_hook=lambda phase, _evidence: reset_events.append(phase),
                )
            self.assertEqual(
                "standalone_game_recovery_ambiguous",
                recovery_rejected.exception.reason_code,
            )
            with self.assertRaises(StandaloneGameError) as rollback_rejected:
                rollback_standalone_game(
                    destination,
                    expected_journal_identity=tuple(started["journal_identity"]),
                    expected_operation_id=str(started["operation_id"]),
                    expected_content_hash=document["standalone_game_hash"],
                    expected_tree_hash=document["payload_tree_hash"],
                    expected_journal_payload_sha256=str(started["journal_payload_sha256"]),
                    expected_journal_payload_state=str(started["journal_payload_state"]),
                    allow_missing_expected_journal=True,
                    reset_pending=True,
                    _authority_hook=lambda phase, _evidence: reset_events.append(phase),
                )
            self.assertEqual(
                "standalone_game_rollback_ambiguous",
                rollback_rejected.exception.reason_code,
            )
            self.assertEqual([], reset_events)

    def test_intent_reset_is_retryable_across_the_authority_crash_window(self) -> None:
        from worldforge.standalone_game import materialize_game, recover_standalone_game

        with tempfile.TemporaryDirectory(prefix="wf-standalone-reset-window-") as temp:
            root = Path(temp)
            source_parent = root / "source"
            source_parent.mkdir()
            output_parent = root / "outputs"
            output_parent.mkdir()
            destination = output_parent / "standalone"
            started: dict[str, object] = {}

            def remember_authority(phase: str, evidence: dict[str, object]) -> None:
                if phase == "publication_started":
                    started.update(evidence)

            def stop_after_intent(phase: str, _path: Path | None) -> None:
                if phase == "after_intent_journal_written":
                    raise SystemExit("simulated stop after intent")

            with _ready_materialization("abstract-puzzle", source_parent) as source:
                with self.assertRaisesRegex(SystemExit, "after intent"):
                    materialize_game(
                        source.root,
                        destination,
                        _publication_hook=stop_after_intent,
                        _authority_hook=remember_authority,
                    )

            def crash_after_resetting(
                phase: str,
                _evidence: dict[str, object],
            ) -> None:
                if phase == "publication_resetting":
                    raise SystemExit("simulated crash after reset authority")

            with self.assertRaisesRegex(SystemExit, "after reset authority"):
                recover_standalone_game(
                    destination,
                    expected_journal_identity=tuple(started["journal_identity"]),
                    expected_operation_id=str(started["operation_id"]),
                    _authority_hook=crash_after_resetting,
                )
            journal = output_parent / ".standalone.standalone-game.journal.json"
            self.assertTrue(journal.is_file())
            completed: list[str] = []
            self.assertIsNone(
                recover_standalone_game(
                    destination,
                    expected_journal_identity=tuple(started["journal_identity"]),
                    expected_operation_id=str(started["operation_id"]),
                    allow_missing_expected_journal=True,
                    reset_pending=True,
                    _authority_hook=lambda phase, _evidence: completed.append(phase),
                )
            )
            self.assertEqual(
                ["publication_resetting", "publication_reset"],
                completed,
            )
            self.assertFalse(journal.exists())

    def test_coordinator_rollback_accepts_its_exact_bound_intent_cleanup(self) -> None:
        from worldforge.directory_publish import directory_identity
        from worldforge.standalone_game import materialize_game
        from worldforge.studio.creation_jobs import CreationJobCoordinator

        with tempfile.TemporaryDirectory(prefix="wf-standalone-bound-rollback-") as temp:
            root = Path(temp)
            source_parent = root / "source"
            source_parent.mkdir()
            output_parent = root / "outputs"
            output_parent.mkdir()
            destination = output_parent / "standalone"
            started: dict[str, object] = {}

            def remember_authority(phase: str, evidence: dict[str, object]) -> None:
                if phase == "publication_started":
                    started.update(evidence)

            def stop_after_intent(phase: str, _path: Path | None) -> None:
                if phase == "after_intent_journal_written":
                    raise SystemExit("simulated stop with an exact owned intent")

            with _ready_materialization("abstract-puzzle", source_parent) as source:
                with self.assertRaisesRegex(SystemExit, "exact owned intent"):
                    materialize_game(
                        source.root,
                        destination,
                        _publication_hook=stop_after_intent,
                        _authority_hook=remember_authority,
                    )

            binding = {
                "path": destination,
                "parent_identity": directory_identity(
                    output_parent,
                    context="bound rollback parent",
                ),
                "expected_manifest_hash": "",
                "expected_tree_hash": "",
                "published_identity": None,
                "recovery": {
                    "phase": "publication_started",
                    "expected_manifest_hash": "",
                    "expected_tree_hash": "",
                    "journal_identity": list(started["journal_identity"]),
                    "operation_id": started["operation_id"],
                    "journal_payload_sha256": started["journal_payload_sha256"],
                    "journal_payload_state": started["journal_payload_state"],
                },
            }
            journal = json.loads(
                (output_parent / ".standalone.standalone-game.journal.json").read_bytes()
            )
            binding["expected_manifest_hash"] = journal["standalone_game_hash"]
            binding["expected_tree_hash"] = journal["payload_tree_hash"]
            binding["recovery"]["expected_manifest_hash"] = journal["standalone_game_hash"]
            binding["recovery"]["expected_tree_hash"] = journal["payload_tree_hash"]
            coordinator = object.__new__(CreationJobCoordinator)
            coordinator._persist_standalone_authority = (  # type: ignore[method-assign]
                lambda _job_id, _phase, _evidence: None
            )
            coordinator._rollback_standalone_publication(  # noqa: SLF001
                "job_bound_rollback",
                binding,
            )
            self.assertFalse(destination.exists())

    def test_queued_v7_cancellation_releases_only_the_reserved_target(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.integrity import canonical_payload_hash
        from worldforge.studio.storage import encode_json, utc_now

        with tempfile.TemporaryDirectory(prefix="wf-studio-game-materialize-cancel-") as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                output_parent = base / "outputs"
                output_parent.mkdir()
                grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_standalone_cancel",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "standalone_game_directory",
                        "display_name": "standalone-cancel",
                        "path": str(output_parent / "standalone-cancel"),
                    }
                )
                job_id = "job_standalone_cancel"
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
                        "format_version": 7,
                        "job_id": job_id,
                        "workspace_id": workspace["workspace_id"],
                        "operation": "game.materialize",
                        "operation_params": {
                            "materialization_bundle_artifact_id": "artifact_materialization_cancel",
                            "source_grant_id": "grant_materialization_source_cancel",
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
                        "record_json) VALUES (?, ?, 'game.materialize', 'queued', "
                        "'queued', 0, ?)",
                        (job_id, workspace["workspace_id"], encode_json(record)),
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

    def test_restart_marks_v7_publication_and_grant_recovery_required(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.integrity import canonical_payload_hash
        from worldforge.studio.creation_output_grants import CreationOutputGrantManager
        from worldforge.studio.storage import StudioStore, decode_object, encode_json, utc_now

        with tempfile.TemporaryDirectory(prefix="wf-studio-game-materialize-restart-") as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            data_dir = service.store.data_dir
            output_parent = base / "outputs"
            output_parent.mkdir()
            grant = service.creation_output_grants.create(
                {
                    "grant_id": "grant_standalone_restart",
                    "workspace_id": workspace["workspace_id"],
                    "kind": "standalone_game_directory",
                    "display_name": "standalone-restart",
                    "path": str(output_parent / "standalone-restart"),
                }
            )
            job_id = "job_standalone_restart"
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
                    "format_version": 7,
                    "job_id": job_id,
                    "workspace_id": workspace["workspace_id"],
                    "operation": "game.materialize",
                    "operation_params": {
                        "materialization_bundle_artifact_id": "artifact_materialization_restart",
                        "source_grant_id": "grant_materialization_source_restart",
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
                    "record_json) VALUES (?, ?, 'game.materialize', 'running', "
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
                orphaned = decode_object(row["record_json"], context="restarted standalone")
                self.assertEqual("orphaned", orphaned["state"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                recovered_grant = CreationOutputGrantManager(reopened).get(grant["grant_id"])
                self.assertEqual("recovery_required", recovered_grant["state"])
                self.assertEqual(reserved["generation"] + 1, recovered_grant["generation"])
            self.assertFalse((output_parent / "standalone-restart").exists())

    def test_resume_adopts_verified_visible_publication_without_republishing(self) -> None:
        from worldforge.standalone_game import materialize_game, verify_standalone_game
        from worldforge.studio import creation_jobs as creation_jobs_module
        from worldforge.studio.storage import encode_json, utc_now

        with tempfile.TemporaryDirectory(prefix="wf-studio-game-materialize-resume-") as temporary:
            base = Path(temporary)
            service, workspace, source_root, source_grant, source_job = (
                _prepare_published_materialization_bundle(base)
            )
            target_root = base / "outputs" / "standalone-resume"
            target_grant = service.creation_output_grants.create(
                {
                    "grant_id": "grant_standalone_resume",
                    "workspace_id": workspace["workspace_id"],
                    "kind": "standalone_game_directory",
                    "display_name": "standalone-resume",
                    "path": str(target_root),
                }
            )
            queued = service.creation_jobs.create_game_materialize(
                _game_materialize_params(
                    service,
                    workspace,
                    source_grant,
                    source_job,
                    target_grant,
                    job_id="job_game_materialize_resume",
                )
            )
            try:
                with service.store.connection:
                    service.creation_output_grants.begin_publication(queued["job_id"])
                source_publication = source_job["result"]["publication"]["materialization_bundle"]

                def persist_authority(phase: str, evidence: dict[str, object]) -> None:
                    with service.store.connection:
                        if phase == "publication_started":
                            service.creation_output_grants.note_publication_started(
                                queued["job_id"],
                                journal_identity=tuple(evidence["journal_identity"]),
                                operation_id=str(evidence["operation_id"]),
                                journal_payload_sha256=str(evidence["journal_payload_sha256"]),
                                journal_payload_state=str(evidence["journal_payload_state"]),
                            )
                        elif phase == "publication_stage_allocated":
                            service.creation_output_grants.note_publication_stage_allocated(
                                queued["job_id"],
                                journal_identity=tuple(evidence["journal_identity"]),
                                operation_id=str(evidence["operation_id"]),
                                stage_identity=tuple(evidence["stage_identity"]),
                                journal_payload_sha256=str(evidence["journal_payload_sha256"]),
                                journal_payload_state=str(evidence["journal_payload_state"]),
                            )
                        elif phase == "publication_staged":
                            service.creation_output_grants.note_publication_staged(
                                queued["job_id"],
                                journal_identity=tuple(evidence["journal_identity"]),
                                operation_id=str(evidence["operation_id"]),
                                stage_identity=tuple(evidence["stage_identity"]),
                                journal_payload_sha256=str(evidence["journal_payload_sha256"]),
                                journal_payload_state=str(evidence["journal_payload_state"]),
                            )
                        elif phase == "publication_verified":
                            service.creation_output_grants.note_publication_verified(
                                queued["job_id"],
                                published_identity=tuple(evidence["published_identity"]),
                                journal_identity=tuple(evidence["journal_identity"]),
                                operation_id=str(evidence["operation_id"]),
                                stage_identity=tuple(evidence["stage_identity"]),
                                journal_payload_sha256=str(evidence["journal_payload_sha256"]),
                                journal_payload_state=str(evidence["journal_payload_state"]),
                            )

                with materialize_game(
                    source_root,
                    target_root,
                    expected_content_hash=source_publication["content_hash"],
                    _authority_hook=persist_authority,
                ) as verified:
                    published_identity = verified.root_identity
                retained = service.creation_output_grants.binding_for_job(
                    queued["job_id"],
                    allow_visible=True,
                )["recovery"]
                assert retained is not None
                with service.store.connection:
                    service.creation_output_grants.note_publication_verified(
                        queued["job_id"],
                        published_identity=published_identity,
                        journal_identity=tuple(retained["journal_identity"]),
                        operation_id=str(retained["operation_id"]),
                        stage_identity=tuple(retained["stage_identity"]),
                        journal_payload_sha256=str(retained["journal_payload_sha256"]),
                        journal_payload_state=str(retained["journal_payload_state"]),
                    )
                    timestamp = utc_now()
                    orphaned = service.creation_jobs._updated_record(  # noqa: SLF001
                        queued,
                        state="orphaned",
                        progress="orphaned",
                        error={
                            "code": "recovery_required",
                            "message": "Directory publication requires explicit recovery",
                            "retryable": True,
                        },
                        started_at=timestamp,
                        finished_at=timestamp,
                        updated_at=timestamp,
                    )
                    cursor = service.store.connection.execute(
                        "UPDATE creation_jobs SET state = 'orphaned', progress = ?, "
                        "generation = ?, record_json = ? WHERE job_id = ? "
                        "AND state = 'queued' AND generation = ?",
                        (
                            orphaned["progress"],
                            orphaned["generation"],
                            encode_json(orphaned),
                            queued["job_id"],
                            queued["generation"],
                        ),
                    )
                    self.assertEqual(1, cursor.rowcount)
                self.assertTrue(target_root.is_dir())
                self.assertEqual("orphaned", orphaned["state"])
                resumed = service.creation_job_coordinator.recover(
                    orphaned["job_id"],
                    mode="resume",
                    expected_generation=orphaned["generation"],
                    expected_record_hash=orphaned["record_hash"],
                )
                self.assertEqual("queued", resumed["state"])
                with patch.object(
                    creation_jobs_module,
                    "materialize_game",
                    side_effect=AssertionError("verified visible output must be adopted"),
                ):
                    self.assertEqual(
                        resumed["job_id"],
                        service.creation_job_coordinator.run_once(),
                    )
                completed = service.creation_jobs.get(resumed["job_id"])
                self.assertEqual("succeeded", completed["state"], completed)
                self.assertEqual("committed", completed["progress"])
                with verify_standalone_game(target_root) as verified:
                    self.assertEqual(
                        source_job["result"]["publication"]["materialization_bundle"][
                            "content_hash"
                        ],
                        verified.manifest["materialization_bundle"]["content_hash"],
                    )
                    self.assertEqual(
                        completed["result"]["publication"]["standalone_game"]["tree_hash"],
                        verified.lock["tree_hash"],
                    )
                self.assertTrue(source_root.is_dir())
            finally:
                service.close()

    def test_commit_rechecks_source_and_target_bytes_after_publication(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="wf-studio-game-materialize-final-cas-"
        ) as temporary:
            base = Path(temporary)
            service, workspace, source_root, source_grant, source_job = (
                _prepare_published_materialization_bundle(base)
            )
            try:
                for mutation in ("source", "target"):
                    with self.subTest(mutation=mutation):
                        target_root = base / "outputs" / f"standalone-final-cas-{mutation}"
                        target_grant = service.creation_output_grants.create(
                            {
                                "grant_id": f"grant_standalone_final_cas_{mutation}",
                                "workspace_id": workspace["workspace_id"],
                                "kind": "standalone_game_directory",
                                "display_name": f"standalone-final-cas-{mutation}",
                                "path": str(target_root),
                            }
                        )
                        queued = service.creation_jobs.create_game_materialize(
                            _game_materialize_params(
                                service,
                                workspace,
                                source_grant,
                                source_job,
                                target_grant,
                                job_id=f"job_game_materialize_final_cas_{mutation}",
                            )
                        )
                        original_commit = service.creation_job_coordinator._commit_registry  # noqa: SLF001
                        foreign = (
                            source_root / "foreign-after-publication.txt"
                            if mutation == "source"
                            else target_root / "foreign-after-publication.txt"
                        )

                        def mutate_then_commit(
                            *args,
                            _foreign=foreign,
                            _commit=original_commit,
                            **kwargs,
                        ):
                            _foreign.write_bytes(b"unbound bytes after publication")
                            return _commit(*args, **kwargs)

                        try:
                            with patch.object(
                                service.creation_job_coordinator,
                                "_commit_registry",
                                side_effect=mutate_then_commit,
                            ):
                                self.assertEqual(
                                    queued["job_id"],
                                    service.creation_job_coordinator.run_once(),
                                )
                            failed = service.creation_jobs.get(queued["job_id"])
                            self.assertEqual("orphaned", failed["state"], failed)
                            self.assertEqual("recovery_required", failed["error"]["code"])
                            retained = service.creation_output_grants.get(target_grant["grant_id"])
                            self.assertEqual("recovery_required", retained["state"])
                            self.assertIsNone(retained["publication"])
                        finally:
                            foreign.unlink(missing_ok=True)
            finally:
                service.close()

    def test_publication_transition_rechecks_bytes_after_registry_recensus(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="wf-studio-game-materialize-transition-cas-"
        ) as temporary:
            base = Path(temporary)
            service, workspace, _source_root, source_grant, source_job = (
                _prepare_published_materialization_bundle(base)
            )
            try:
                target_root = base / "outputs" / "standalone-transition-cas"
                target_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_standalone_transition_cas",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "standalone_game_directory",
                        "display_name": "standalone-transition-cas",
                        "path": str(target_root),
                    }
                )
                queued = service.creation_jobs.create_game_materialize(
                    _game_materialize_params(
                        service,
                        workspace,
                        source_grant,
                        source_job,
                        target_grant,
                        job_id="job_game_materialize_transition_cas",
                    )
                )
                original_verify = (
                    service.creation_job_coordinator._verify_standalone_registry_commit  # noqa: SLF001
                )
                foreign = target_root / "foreign-after-registry-recensus.txt"
                calls = 0

                def verify_then_mutate(*args, **kwargs):
                    nonlocal calls
                    calls += 1
                    result = original_verify(*args, **kwargs)
                    if calls == 1:
                        foreign.write_bytes(b"in-place mutation after registry recensus")
                    return result

                with patch.object(
                    service.creation_job_coordinator,
                    "_verify_standalone_registry_commit",
                    side_effect=verify_then_mutate,
                ):
                    self.assertEqual(
                        queued["job_id"],
                        service.creation_job_coordinator.run_once(),
                    )
                failed = service.creation_jobs.get(queued["job_id"])
                self.assertGreaterEqual(calls, 2)
                self.assertEqual("orphaned", failed["state"], failed)
                self.assertEqual("recovery_required", failed["error"]["code"])
                retained = service.creation_output_grants.get(target_grant["grant_id"])
                self.assertEqual("recovery_required", retained["state"])
                self.assertIsNone(retained["publication"])
            finally:
                service.close()

    def test_v7_job_publishes_one_pathless_standalone_with_exact_lineage(self) -> None:
        from worldforge.standalone_game import verify_standalone_game
        from worldforge.studio.creation_jobs import CreationJobManager
        from worldforge.studio.errors import StudioError

        self.assertTrue(hasattr(CreationJobManager, "create_game_materialize"))
        with tempfile.TemporaryDirectory(prefix="wf-studio-game-materialize-job-") as temporary:
            base = Path(temporary)
            service, workspace, source_root, source_grant, source_job = (
                _prepare_published_materialization_bundle(base)
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
                published_source = service.creation_output_grants.get(source_grant["grant_id"])
                target_root = base / "outputs" / "standalone-game"
                target_grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_standalone_target",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "standalone_game_directory",
                        "display_name": "standalone-game",
                        "path": str(target_root),
                    }
                )
                params = {
                    "job_id": "job_game_materialize",
                    "workspace_id": workspace["workspace_id"],
                    "operation": "game.materialize",
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
                    "materialization_bundle_artifact_id": source_job["result"][
                        "output_artifact_ids"
                    ][0],
                    "source_grant_id": source_grant["grant_id"],
                    "expected_source_grant_generation": published_source["generation"],
                    "target_grant_id": target_grant["grant_id"],
                    "expected_target_grant_generation": target_grant["generation"],
                }
                with self.assertRaises(StudioError):
                    service.creation_jobs.create_game_materialize(
                        {
                            **params,
                            "job_id": "job_game_materialize_stale_source",
                            "expected_source_grant_generation": published_source["generation"] + 1,
                        }
                    )
                foreign = source_root / "foreign.txt"
                foreign.write_bytes(b"unbound source bytes")
                try:
                    with self.assertRaises(StudioError):
                        service.creation_jobs.create_game_materialize(
                            {**params, "job_id": "job_game_materialize_tampered"}
                        )
                finally:
                    foreign.unlink()
                queued = service.creation_jobs.create_game_materialize(params)
                self.assertEqual(7, queued["format_version"])
                self.assertNotIn(str(source_root), json.dumps(queued))
                self.assertNotIn(str(target_root), json.dumps(queued))
                self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                completed = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("succeeded", completed["state"], completed)
                self.assertEqual("committed", completed["progress"])
                self.assertEqual(
                    ["native_execution_unverified", "release_blocked"],
                    completed["result"]["reason_codes"],
                )
                publication = completed["result"]["publication"]
                self.assertEqual("standalone_game_directory", publication["kind"])
                self.assertNotIn("path", publication)
                with verify_standalone_game(target_root) as verified:
                    self.assertEqual(
                        source_job["result"]["publication"]["materialization_bundle"][
                            "content_hash"
                        ],
                        verified.manifest["materialization_bundle"]["content_hash"],
                    )
                    self.assertEqual(
                        verified.lock["tree_hash"],
                        publication["standalone_game"]["tree_hash"],
                    )
                    candidate = service.creation_artifacts.get_document(
                        workspace["workspace_id"],
                        completed["result"]["output_artifact_ids"][0],
                    )
                    self.assertEqual(verified.manifest, candidate)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
