from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from worldforge.integrity import canonical_json_bytes, canonical_payload_hash

_ROOT = Path(__file__).resolve().parents[1]
_PUZZLE_ROOT = _ROOT / "examples/multigenre-contracts/abstract-puzzle"
_SIMULATION_ROOT = _ROOT / "examples/multigenre-contracts/systemic-simulation"

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64


def _prepared_creation_service(base: Path):
    from tests.test_studio_creation_evidence_v4 import _register_root
    from worldforge.creation_contracts import load_creation_project
    from worldforge.creation_workflow import initial_creation_workflow_status

    project_root = base / "project"
    shutil.copytree(_PUZZLE_ROOT, project_root)
    project = load_creation_project(project_root / "project.json")
    internal = project_root / ".worldforge"
    history = internal / "artifact_history"
    (internal / "phase_reports").mkdir(parents=True)
    history.mkdir()
    (internal / "status.json").write_bytes(
        canonical_json_bytes(initial_creation_workflow_status(project))
    )
    for document in (
        project.project,
        project.profile,
        project.manifest,
        *project.world_modules,
        *project.activity_modules,
        *project.narrative_modules,
        *project.system_modules,
        *project.logic_modules,
    ):
        (history / f"{document['content_hash']}.json").write_bytes(canonical_json_bytes(document))
    return _register_root(base, project_root)


def _queue_compile(service: object, workspace: dict[str, object], job_id: str):
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
    return service.creation_jobs.create_compile(
        {
            "job_id": job_id,
            "workspace_id": workspace["workspace_id"],
            "expected_root_generation": workspace["root_generation"],
            "expected_source_revision": workspace["source_revision"],
            "expected_workflow_status_hash": workspace["workflow_status_hash"],
            "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
        }
    )


def _assert_linux_recovery_required(
    test: unittest.TestCase,
    service: object,
    job_id: str,
) -> dict[str, object]:
    record = service.creation_jobs.get(job_id)
    test.assertEqual("orphaned", record["state"])
    test.assertEqual("recovery_required", record["error"]["code"])
    evidence = record["error"]["recovery_evidence"]
    test.assertEqual({"journal", "stage"}, set(evidence))
    test.assertNotIn("/", evidence["stage"]["locator"])
    test.assertNotIn("/", evidence["journal"]["locator"])
    attempt = service.store.connection.execute(
        "SELECT stage_locator, journal_name, journal_dev, journal_ino "
        "FROM creation_job_attempts WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    test.assertIsNotNone(attempt)
    test.assertTrue((service.store.creation_jobs_dir / attempt["stage_locator"]).is_dir())
    journal = service.store.creation_job_journals_dir / attempt["journal_name"]
    if attempt["journal_dev"] is None or attempt["journal_ino"] is None:
        if journal.exists():
            test.assertTrue(journal.is_file())
    else:
        test.assertTrue(journal.is_file())
    return record


def _seed_global_creation_artifact_registry(data_dir: Path) -> None:
    from worldforge.studio.storage import StudioStore

    store = StudioStore(data_dir)
    timestamp = "2026-08-02T00:00:00.000000Z"
    with store.connection:
        store.connection.execute(
            "INSERT INTO creation_workspaces "
            "(workspace_id, record_json, absolute_root, root_dev, root_ino, generation) "
            "VALUES ('workspace_legacy', '{}', ?, '1', '2', 0)",
            (str(data_dir),),
        )
        store.connection.execute(
            "INSERT INTO creation_jobs "
            "(job_id, workspace_id, operation, state, progress, generation, record_json) "
            "VALUES ('job_legacy', 'workspace_legacy', 'creation.compile', "
            "'succeeded', 'committed', 1, '{}')"
        )
    store.connection.execute("PRAGMA foreign_keys = OFF")
    store.connection.executescript(
        """
        DROP TABLE creation_artifact_dependencies;
        DROP TABLE creation_artifacts;
        CREATE TABLE creation_artifacts (
            artifact_id TEXT PRIMARY KEY NOT NULL,
            workspace_id TEXT NOT NULL REFERENCES creation_workspaces(workspace_id),
            lifecycle TEXT NOT NULL,
            subject_format TEXT NOT NULL,
            subject_version INTEGER NOT NULL,
            subject_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            roles_json TEXT NOT NULL,
            record_json TEXT NOT NULL,
            document_blob_sha256 TEXT NOT NULL,
            document_size INTEGER NOT NULL,
            blob_dev TEXT NOT NULL,
            blob_ino TEXT NOT NULL,
            producer_job_id TEXT NOT NULL REFERENCES creation_jobs(job_id),
            producer_operation TEXT NOT NULL,
            producer_output_position INTEGER NOT NULL,
            root_generation INTEGER NOT NULL,
            source_revision TEXT NOT NULL,
            workflow_status_hash TEXT,
            input_artifact_snapshot_hash TEXT NOT NULL,
            generation INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE (subject_format, subject_version, subject_id, content_hash),
            UNIQUE (producer_job_id, producer_output_position)
        );
        CREATE INDEX creation_artifacts_workspace_idx
            ON creation_artifacts(workspace_id, lifecycle, artifact_id);
        CREATE INDEX creation_artifacts_subject_idx
            ON creation_artifacts(subject_format, subject_version, subject_id, content_hash);
        CREATE TABLE creation_artifact_dependencies (
            artifact_id TEXT NOT NULL REFERENCES creation_artifacts(artifact_id)
                ON DELETE CASCADE,
            position INTEGER NOT NULL,
            dependency_artifact_id TEXT NOT NULL,
            subject_format TEXT NOT NULL,
            subject_version INTEGER NOT NULL,
            subject_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            PRIMARY KEY (artifact_id, position),
            UNIQUE (artifact_id, dependency_artifact_id)
        );
        CREATE INDEX creation_artifact_dependencies_input_idx
            ON creation_artifact_dependencies(dependency_artifact_id, artifact_id);
        """
    )
    with store.connection:
        store.connection.execute(
            "INSERT INTO creation_artifacts VALUES "
            "('artifact_legacy', 'workspace_legacy', 'candidate', "
            "'world-forge.gamepack', 1, 'legacy_game', ?, '[]', '{}', ?, 2, "
            "'3', '4', 'job_legacy', 'creation.compile', 0, 0, ?, NULL, ?, 0, ?)",
            (_HASH_A, _HASH_B, _HASH_C, _HASH_A, timestamp),
        )
        store.connection.execute(
            "INSERT INTO creation_artifact_dependencies VALUES "
            "('artifact_legacy', 0, 'artifact_source', "
            "'world-forge.project', 1, 'legacy_game', ?)",
            (_HASH_B,),
        )
        store.connection.execute("UPDATE schema_meta SET value = '3' WHERE key = 'schema_version'")
    store.connection.execute("PRAGMA foreign_keys = ON")
    store.close()


def _authority() -> dict[str, object]:
    return {
        "root_generation": 4,
        "source_revision": _HASH_A,
        "workflow_status_hash": _HASH_B,
        "artifact_snapshot_hash": _HASH_C,
    }


def _subject(identifier: str = "neutral_game", content_hash: str = _HASH_A) -> dict[str, object]:
    return {
        "format": "world-forge.gamepack",
        "format_version": 1,
        "id": identifier,
        "content_hash": content_hash,
    }


def _job() -> dict[str, object]:
    record: dict[str, object] = {
        "format": "world-forge.studio_creation_job",
        "format_version": 1,
        "job_id": "job_compile_01",
        "workspace_id": "workspace_01",
        "operation": "creation.compile",
        "state": "queued",
        "generation": 0,
        "authority": _authority(),
        "inputs": [{"artifact_id": "artifact_source_01", "subject": _subject()}],
        "progress": "queued",
        "result": None,
        "error": None,
        "created_at": "2026-08-02T00:00:00.000000Z",
        "started_at": None,
        "finished_at": None,
        "updated_at": "2026-08-02T00:00:00.000000Z",
        "record_hash": "",
    }
    record["record_hash"] = canonical_payload_hash(record, hash_field="record_hash")
    return record


class StudioCreationJobV4ContractTests(unittest.TestCase):
    def test_creation_recovery_error_carries_closed_pathless_evidence(self) -> None:
        from worldforge.studio.contracts import validate_studio_creation_job

        job = _job()
        job.update(
            {
                "state": "orphaned",
                "progress": "orphaned",
                "generation": 1,
                "error": {
                    "code": "recovery_required",
                    "message": "Exact retained cleanup evidence requires recovery",
                    "retryable": True,
                    "recovery_evidence": {
                        "stage": {
                            "locator": ".worldforge-retained-creation-stage-abc",
                            "identity": [7, 11],
                            "retention": "active",
                        }
                    },
                },
                "finished_at": "2026-08-02T00:00:01.000000Z",
                "updated_at": "2026-08-02T00:00:01.000000Z",
            }
        )
        job["record_hash"] = canonical_payload_hash(job, hash_field="record_hash")
        self.assertEqual(job, validate_studio_creation_job(job))
        leaked = copy.deepcopy(job)
        leaked["error"]["recovery_evidence"]["stage"]["locator"] = "../stage"
        leaked["record_hash"] = canonical_payload_hash(leaked, hash_field="record_hash")
        with self.assertRaisesRegex(ValueError, "locator"):
            validate_studio_creation_job(leaked)

    def test_public_job_page_and_admission_bounds_fit_the_ndjson_transport(self) -> None:
        from worldforge.studio.contracts import (
            MAX_CREATION_ADMISSION_DOCUMENT_BYTES,
            MAX_CREATION_JOB_PAGE,
            validate_studio_protocol_envelope,
        )
        from worldforge.studio.jsonio import MAX_NDJSON_LINE_BYTES, encode_ndjson_object

        self.assertEqual(768 * 1024, MAX_CREATION_ADMISSION_DOCUMENT_BYTES)
        self.assertEqual(8, MAX_CREATION_JOB_PAGE)
        schema = json.loads((_ROOT / "schemas/studio-protocol-v4.schema.json").read_text())
        self.assertEqual(
            MAX_CREATION_JOB_PAGE,
            schema["$defs"]["jobListResult"]["properties"]["jobs"]["maxItems"],
        )
        document = {"padding": "x" * (MAX_CREATION_ADMISSION_DOCUMENT_BYTES - 32)}
        while len(canonical_json_bytes(document)) < MAX_CREATION_ADMISSION_DOCUMENT_BYTES:
            document["padding"] += "x"
        while len(canonical_json_bytes(document)) > MAX_CREATION_ADMISSION_DOCUMENT_BYTES:
            document["padding"] = document["padding"][:-1]
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "request",
            "request_id": "request_admission_bound",
            "method": "creation_job.create",
            "params": {
                "workspace_id": "workspace_01",
                "operation": "artifact.admit",
                "expected_root_generation": 0,
                "expected_source_revision": _HASH_A,
                "expected_workflow_status_hash": None,
                "expected_artifact_snapshot_hash": _HASH_B,
                "document": document,
                "dependency_artifact_ids": [f"artifact_{index:03d}" for index in range(128)],
            },
        }
        checked = validate_studio_protocol_envelope(request)
        self.assertLessEqual(len(encode_ndjson_object(checked)) - 1, MAX_NDJSON_LINE_BYTES)
        oversized = copy.deepcopy(request)
        oversized["params"]["document"]["padding"] += "x"
        with self.assertRaisesRegex(ValueError, "document exceeds"):
            validate_studio_protocol_envelope(oversized)

        list_request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "request",
            "request_id": "request_list_bound",
            "method": "creation_job.list",
            "params": {
                "workspace_id": "workspace_01",
                "state": None,
                "after_sequence": 0,
                "limit": MAX_CREATION_JOB_PAGE + 1,
            },
        }
        with self.assertRaisesRegex(ValueError, "limit"):
            validate_studio_protocol_envelope(list_request)

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                with self.assertRaisesRegex(Exception, "list limit"):
                    service.creation_jobs.list(
                        workspace_id=workspace["workspace_id"],
                        limit=MAX_CREATION_JOB_PAGE + 1,
                    )
            finally:
                service.close()
                service.store.close()

    def test_creation_job_and_worker_records_are_closed_pathless_and_provider_free(self) -> None:
        from worldforge.studio.contracts import (
            validate_studio_creation_job,
            validate_studio_creation_worker_envelope,
        )

        self.assertEqual("creation.compile", validate_studio_creation_job(_job())["operation"])
        for forbidden in (
            "path",
            "command",
            "env",
            "document",
            "prompt",
            "provider",
            "model_id",
            "credentials",
        ):
            candidate = copy.deepcopy(_job())
            candidate[forbidden] = "private"
            candidate["record_hash"] = canonical_payload_hash(candidate, hash_field="record_hash")
            with self.assertRaisesRegex(ValueError, "invalid fields"):
                validate_studio_creation_job(candidate)

        request = {
            "format": "world-forge.studio_creation_worker",
            "format_version": 1,
            "kind": "request",
            "job_id": "job_compile_01",
            "operation": "creation.compile",
            "request_locator": "request_job_compile_01",
            "request_sha256": _HASH_A,
        }
        self.assertEqual("request", validate_studio_creation_worker_envelope(request)["kind"])
        response = {
            "format": "world-forge.studio_creation_worker",
            "format_version": 1,
            "kind": "response",
            "job_id": "job_compile_01",
            "operation": "creation.compile",
            "ok": True,
            "outputs": [
                {
                    "locator": "output_0001",
                    "subject": _subject(),
                    "size": 123,
                    "sha256": _HASH_A,
                }
            ],
            "metadata": {"analysis_status": "passed"},
        }
        self.assertEqual("response", validate_studio_creation_worker_envelope(response)["kind"])
        leaked = {**request, "request_path": "/private/request.json"}
        with self.assertRaisesRegex(ValueError, "invalid fields"):
            validate_studio_creation_worker_envelope(leaked)

    def test_protocol_v4_adds_creation_jobs_without_broadening_v3(self) -> None:
        from worldforge.studio.contracts import METHODS_V3, METHODS_V4

        expected = {
            "creation_job.create",
            "creation_job.get",
            "creation_job.list",
            "creation_job.cancel",
            "creation_job.recover",
            "creation_event.list",
        }
        self.assertTrue(expected <= METHODS_V4)
        self.assertTrue(expected.isdisjoint(METHODS_V3))

        root = Path(__file__).resolve().parents[1]
        catalog = json.loads((root / "contracts/catalog.json").read_text(encoding="utf-8"))
        by_id = {entry["id"]: entry for entry in catalog["contracts"]}
        by_format = {entry["format"]: entry for entry in catalog["contracts"]}
        self.assertEqual(
            "schemas/studio-creation-job.schema.json",
            by_id["studio-creation-job"]["schema"],
        )
        self.assertEqual(
            "schemas/studio-creation-worker.schema.json",
            by_format["world-forge.studio_creation_worker"]["schema"],
        )

    def test_v4_creation_job_requests_and_responses_are_exact_and_v3_rejects_them(self) -> None:
        from worldforge.studio.contracts import validate_studio_protocol_envelope

        create = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "request",
            "request_id": "request_create_job",
            "method": "creation_job.create",
            "params": {
                "job_id": "job_compile_01",
                "workspace_id": "workspace_01",
                "operation": "creation.compile",
                "expected_root_generation": 4,
                "expected_source_revision": _HASH_A,
                "expected_workflow_status_hash": _HASH_B,
                "expected_artifact_snapshot_hash": _HASH_C,
            },
        }
        self.assertEqual(
            "creation_job.create",
            validate_studio_protocol_envelope(create)["method"],
        )
        legacy = copy.deepcopy(create)
        legacy["protocol_version"] = 3
        with self.assertRaisesRegex(ValueError, "not available in protocol v3"):
            validate_studio_protocol_envelope(legacy)

        response = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "response",
            "request_id": "request_create_job",
            "method": "creation_job.create",
            "result": {"job": _job()},
        }
        self.assertEqual(
            "queued",
            validate_studio_protocol_envelope(response)["result"]["job"]["state"],
        )

        events = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "response",
            "request_id": "request_events",
            "method": "creation_event.list",
            "result": {
                "events": [
                    {
                        "event_id": 1,
                        "workspace_id": "workspace_01",
                        "topic": "creation_job.queued",
                        "entity_type": "creation_job",
                        "entity_id": "job_compile_01",
                        "payload": {"generation": 0, "operation": "creation.compile"},
                        "created_at": "2026-08-02T00:00:00.000000Z",
                    }
                ]
            },
        }
        self.assertEqual(
            1,
            validate_studio_protocol_envelope(events)["result"]["events"][0]["event_id"],
        )


class StudioCreationJobV4StorageTests(unittest.TestCase):
    def test_same_authority_compile_submission_is_idempotent_while_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                first = _queue_compile(service, workspace, "job_compile_first")
                second = _queue_compile(service, workspace, "job_compile_second")

                self.assertEqual(first["job_id"], second["job_id"])
                self.assertEqual(
                    1,
                    service.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_jobs "
                        "WHERE workspace_id = ? AND operation = 'creation.compile' "
                        "AND state IN ('queued', 'running')",
                        (workspace["workspace_id"],),
                    ).fetchone()[0],
                )
                self.assertEqual(
                    1,
                    service.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_events "
                        "WHERE workspace_id = ? AND topic = 'creation_job.queued' "
                        "AND entity_type = 'creation_job'",
                        (workspace["workspace_id"],),
                    ).fetchone()[0],
                )
            finally:
                service.close()
                service.store.close()

    def test_concurrent_same_authority_compile_submission_creates_one_job(self) -> None:
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            primary, workspace = _prepared_creation_service(base)
            evidence = primary.creation_evidence.list(
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
            start = threading.Barrier(2)
            snapshots = threading.Barrier(2)
            snapshot_serial = threading.Lock()
            results: list[dict[str, object] | None] = [None, None]
            errors: list[BaseException] = []

            def submit(index: int) -> None:
                store = StudioStore(base / "studio", mode="secondary")
                service = StudioService(store)
                real_snapshot = service.creation_evidence._snapshot  # noqa: SLF001

                def synchronized_snapshot(params: object):
                    with snapshot_serial:
                        result = real_snapshot(params)
                    if not store.connection.in_transaction:
                        snapshots.wait(timeout=5.0)
                    return result

                try:
                    with patch.object(
                        service.creation_evidence,
                        "_snapshot",
                        side_effect=synchronized_snapshot,
                    ):
                        start.wait(timeout=5.0)
                        results[index] = service.creation_jobs.create_compile(
                            {
                                "job_id": f"job_compile_concurrent_{index}",
                                "workspace_id": workspace["workspace_id"],
                                "expected_root_generation": workspace["root_generation"],
                                "expected_source_revision": workspace["source_revision"],
                                "expected_workflow_status_hash": workspace["workflow_status_hash"],
                                "expected_artifact_snapshot_hash": evidence[
                                    "artifact_snapshot_hash"
                                ],
                            }
                        )
                except BaseException as exc:  # pragma: no cover - surfaced below
                    errors.append(exc)
                finally:
                    service.close()
                    store.close()

            threads = [threading.Thread(target=submit, args=(index,)) for index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10.0)

            try:
                self.assertTrue(all(not thread.is_alive() for thread in threads))
                if errors:
                    raise errors[0]
                self.assertIsNotNone(results[0])
                self.assertIsNotNone(results[1])
                self.assertEqual(results[0]["job_id"], results[1]["job_id"])
                self.assertEqual(
                    1,
                    primary.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_jobs "
                        "WHERE workspace_id = ? AND operation = 'creation.compile' "
                        "AND state IN ('queued', 'running')",
                        (workspace["workspace_id"],),
                    ).fetchone()[0],
                )
            finally:
                primary.close()
                primary.store.close()

    def test_compile_drift_between_snapshots_is_a_contractual_conflict(self) -> None:
        from worldforge.creation_contracts import load_creation_project
        from worldforge.creation_workflow import initial_creation_workflow_status
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
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
            project_root = base / "project"
            real_snapshot = service.creation_evidence._snapshot  # noqa: SLF001
            snapshot_count = 0

            def snapshot_then_drift(params: object):
                nonlocal snapshot_count
                result = real_snapshot(params)
                snapshot_count += 1
                if snapshot_count == 1:
                    project_path = project_root / "project.json"
                    project = json.loads(project_path.read_text(encoding="utf-8"))
                    project["title"] = "Abstract Puzzle Changed"
                    project["content_hash"] = canonical_payload_hash(
                        project,
                        hash_field="content_hash",
                    )
                    project_path.write_bytes(canonical_json_bytes(project))
                    loaded = load_creation_project(project_path)
                    status = initial_creation_workflow_status(loaded)
                    internal = project_root / ".worldforge"
                    (internal / "status.json").write_bytes(canonical_json_bytes(status))
                    history = internal / "artifact_history"
                    for document in (loaded.project, status):
                        (history / f"{document['content_hash']}.json").write_bytes(
                            canonical_json_bytes(document)
                        )
                return result

            try:
                with (
                    patch.object(
                        service.creation_evidence,
                        "_snapshot",
                        side_effect=snapshot_then_drift,
                    ),
                    self.assertRaises(StudioError) as raised,
                ):
                    service.creation_jobs.create_compile(
                        {
                            "job_id": "job_compile_drift",
                            "workspace_id": workspace["workspace_id"],
                            "expected_root_generation": workspace["root_generation"],
                            "expected_source_revision": workspace["source_revision"],
                            "expected_workflow_status_hash": workspace["workflow_status_hash"],
                            "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
                        }
                    )
                self.assertEqual("conflict", raised.exception.code)
                self.assertEqual(
                    (0, 0),
                    (
                        service.store.connection.execute(
                            "SELECT COUNT(*) FROM creation_jobs"
                        ).fetchone()[0],
                        service.store.connection.execute(
                            "SELECT COUNT(*) FROM creation_events "
                            "WHERE topic = 'creation_job.queued'"
                        ).fetchone()[0],
                    ),
                )
            finally:
                service.close()
                service.store.close()

    def test_workspace_scoped_artifact_migration_debris_fails_before_live_tables_change(
        self,
    ) -> None:
        import sqlite3

        from worldforge.studio.errors import StudioError
        from worldforge.studio.storage import DATABASE_NAME, StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            _seed_global_creation_artifact_registry(data_dir)
            connection = sqlite3.connect(data_dir / DATABASE_NAME)
            try:
                connection.execute(
                    "CREATE TABLE creation_artifacts_workspace_scoped (sentinel TEXT)"
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(StudioError, "migration debris"):
                StudioStore(data_dir)

            connection = sqlite3.connect(data_dir / DATABASE_NAME)
            try:
                self.assertEqual(
                    1,
                    connection.execute("SELECT COUNT(*) FROM creation_artifacts").fetchone()[0],
                )
                self.assertEqual(
                    "3",
                    connection.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()[0],
                )
            finally:
                connection.close()

    def test_workspace_scoped_artifact_migration_rolls_back_every_statement(self) -> None:
        import sqlite3

        from worldforge.studio import storage as storage_module

        step_count = storage_module._CREATION_ARTIFACT_SCOPE_MIGRATION_STEP_COUNT  # noqa: SLF001
        for failure_step in range(1, step_count + 1):
            with (
                self.subTest(failure_step=failure_step),
                tempfile.TemporaryDirectory() as temporary,
            ):
                data_dir = Path(temporary)
                _seed_global_creation_artifact_registry(data_dir)
                executed = 0
                real_execute = (
                    storage_module.StudioStore._execute_creation_artifact_scope_migration_statement
                )

                def fail_after_statement(
                    store: object,
                    statement: str,
                    *,
                    _real_execute=real_execute,
                    _failure_step: int = failure_step,
                ):
                    nonlocal executed
                    result = _real_execute(store, statement)
                    executed += 1
                    if executed == _failure_step:
                        raise RuntimeError("simulated migration crash")
                    return result

                with (
                    patch.object(
                        storage_module.StudioStore,
                        "_execute_creation_artifact_scope_migration_statement",
                        fail_after_statement,
                    ),
                    self.assertRaisesRegex(RuntimeError, "simulated migration crash"),
                ):
                    storage_module.StudioStore(data_dir)

                connection = sqlite3.connect(data_dir / storage_module.DATABASE_NAME)
                try:
                    artifact_pk = {
                        row[1]: row[5]
                        for row in connection.execute("PRAGMA table_info(creation_artifacts)")
                        if row[5]
                    }
                    dependency_columns = {
                        row[1]
                        for row in connection.execute(
                            "PRAGMA table_info(creation_artifact_dependencies)"
                        )
                    }
                    debris = connection.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                        "AND name LIKE 'creation_artifact%workspace_scoped'"
                    ).fetchone()[0]
                    self.assertEqual({"artifact_id": 1}, artifact_pk)
                    self.assertNotIn("workspace_id", dependency_columns)
                    self.assertEqual(
                        (1, 1),
                        (
                            connection.execute(
                                "SELECT COUNT(*) FROM creation_artifacts"
                            ).fetchone()[0],
                            connection.execute(
                                "SELECT COUNT(*) FROM creation_artifact_dependencies"
                            ).fetchone()[0],
                        ),
                    )
                    self.assertEqual(0, debris)
                    self.assertEqual(
                        "3",
                        connection.execute(
                            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                        ).fetchone()[0],
                    )
                finally:
                    connection.close()

                migrated = storage_module.StudioStore(data_dir)
                try:
                    artifact_pk = {
                        row["name"]: row["pk"]
                        for row in migrated.connection.execute(
                            "PRAGMA table_info(creation_artifacts)"
                        )
                        if row["pk"]
                    }
                    dependency = migrated.connection.execute(
                        "SELECT workspace_id, artifact_id, dependency_artifact_id "
                        "FROM creation_artifact_dependencies"
                    ).fetchone()
                    self.assertEqual({"workspace_id": 1, "artifact_id": 2}, artifact_pk)
                    self.assertEqual(
                        ("workspace_legacy", "artifact_legacy", "artifact_source"),
                        tuple(dependency),
                    )
                    self.assertEqual(
                        "5",
                        migrated.connection.execute(
                            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                        ).fetchone()[0],
                    )
                finally:
                    migrated.close()

    def test_secondary_rejects_v4_artifact_tables_with_global_primary_key(self) -> None:
        import sqlite3

        from worldforge.studio.errors import StudioError
        from worldforge.studio.storage import DATABASE_NAME, StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            StudioStore(data_dir).close()
            connection = sqlite3.connect(data_dir / DATABASE_NAME)
            try:
                artifact_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'creation_artifacts'"
                ).fetchone()[0]
                dependency_sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'creation_artifact_dependencies'"
                ).fetchone()[0]
                global_sql = artifact_sql.replace(
                    "PRIMARY KEY (workspace_id, artifact_id)",
                    "PRIMARY KEY (artifact_id)",
                )
                self.assertNotEqual(artifact_sql, global_sql)
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("DROP TABLE creation_artifact_dependencies")
                connection.execute("DROP TABLE creation_artifacts")
                connection.execute(global_sql)
                connection.execute(dependency_sql)
                connection.commit()
            finally:
                connection.close()

            secondary = None
            try:
                with self.assertRaisesRegex(StudioError, "relational shape"):
                    secondary = StudioStore(data_dir, mode="secondary")
            finally:
                if secondary is not None:
                    secondary.close()

    def test_secondary_rejects_missing_v4_artifact_index_or_foreign_key(self) -> None:
        import sqlite3

        from worldforge.studio.errors import StudioError
        from worldforge.studio.storage import DATABASE_NAME, StudioStore

        for mutation in ("index", "foreign_key"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                data_dir = Path(temporary)
                StudioStore(data_dir).close()
                connection = sqlite3.connect(data_dir / DATABASE_NAME)
                try:
                    if mutation == "index":
                        connection.execute("DROP INDEX creation_artifacts_workspace_idx")
                    else:
                        connection.execute("PRAGMA foreign_keys = OFF")
                        connection.execute("DROP TABLE creation_artifact_dependencies")
                        connection.executescript(
                            """
                            CREATE TABLE creation_artifact_dependencies (
                                workspace_id TEXT NOT NULL,
                                artifact_id TEXT NOT NULL,
                                position INTEGER NOT NULL,
                                dependency_artifact_id TEXT NOT NULL,
                                subject_format TEXT NOT NULL,
                                subject_version INTEGER NOT NULL,
                                subject_id TEXT NOT NULL,
                                content_hash TEXT NOT NULL,
                                PRIMARY KEY (workspace_id, artifact_id, position),
                                UNIQUE (
                                    workspace_id, artifact_id, dependency_artifact_id
                                )
                            );
                            CREATE INDEX creation_artifact_dependencies_input_idx
                                ON creation_artifact_dependencies(
                                    workspace_id, dependency_artifact_id, artifact_id
                                );
                            """
                        )
                    connection.commit()
                finally:
                    connection.close()

                secondary = None
                try:
                    with self.assertRaisesRegex(StudioError, "relational shape"):
                        secondary = StudioStore(data_dir, mode="secondary")
                finally:
                    if secondary is not None:
                        secondary.close()

    def test_primary_and_secondary_reject_non_exact_v4_relational_shape(self) -> None:
        import sqlite3

        from worldforge.studio.errors import StudioError
        from worldforge.studio.storage import DATABASE_NAME, StudioStore

        dependency_table_with_extra_foreign_key = """
            CREATE TABLE creation_artifact_dependencies (
                workspace_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                dependency_artifact_id TEXT NOT NULL,
                subject_format TEXT NOT NULL,
                subject_version INTEGER NOT NULL,
                subject_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                PRIMARY KEY (workspace_id, artifact_id, position),
                UNIQUE (workspace_id, artifact_id, dependency_artifact_id),
                FOREIGN KEY (workspace_id, artifact_id)
                    REFERENCES creation_artifacts(workspace_id, artifact_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (workspace_id, dependency_artifact_id)
                    REFERENCES creation_artifacts(workspace_id, artifact_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX creation_artifact_dependencies_input_idx
                ON creation_artifact_dependencies(
                    workspace_id, dependency_artifact_id, artifact_id
                );
        """
        mutations = {
            "unique_named_index": (
                "DROP INDEX creation_artifacts_subject_idx; "
                "CREATE UNIQUE INDEX creation_artifacts_subject_idx "
                "ON creation_artifacts("
                "subject_format, subject_version, subject_id, content_hash)"
            ),
            "partial_named_index": (
                "DROP INDEX creation_artifacts_workspace_idx; "
                "CREATE INDEX creation_artifacts_workspace_idx "
                "ON creation_artifacts(workspace_id, lifecycle, artifact_id) "
                "WHERE lifecycle = 'candidate'"
            ),
            "extra_unique_index": (
                "CREATE UNIQUE INDEX creation_artifacts_unexpected_unique_idx "
                "ON creation_artifacts(workspace_id, artifact_id, lifecycle)"
            ),
            "extra_foreign_key": (
                "PRAGMA foreign_keys = OFF; "
                "DROP TABLE creation_artifact_dependencies; "
                + dependency_table_with_extra_foreign_key
            ),
        }
        for mode in ("primary", "secondary"):
            for mutation, statement in mutations.items():
                with (
                    self.subTest(mode=mode, mutation=mutation),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    data_dir = Path(temporary)
                    StudioStore(data_dir).close()
                    connection = sqlite3.connect(data_dir / DATABASE_NAME)
                    try:
                        connection.executescript(statement)
                        connection.commit()
                    finally:
                        connection.close()

                    reopened = None
                    try:
                        with self.assertRaisesRegex(StudioError, "relational shape"):
                            reopened = StudioStore(data_dir, mode=mode)
                    finally:
                        if reopened is not None:
                            reopened.close()

    def test_primary_and_secondary_reject_non_exact_v4_job_table_shape(self) -> None:
        import sqlite3

        from worldforge.studio.errors import StudioError
        from worldforge.studio.storage import DATABASE_NAME, StudioStore

        output_table_without_constraints = """
            PRAGMA foreign_keys = OFF;
            DROP TABLE creation_job_outputs;
            CREATE TABLE creation_job_outputs (
                job_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                artifact_id TEXT NOT NULL,
                subject_format TEXT NOT NULL,
                subject_version INTEGER NOT NULL,
                subject_id TEXT NOT NULL,
                content_hash TEXT NOT NULL
            );
            CREATE INDEX creation_job_outputs_artifact_idx
                ON creation_job_outputs(artifact_id, job_id);
        """
        mutations = {
            "outputs_without_constraints": output_table_without_constraints,
            "outputs_extra_unique": (
                "CREATE UNIQUE INDEX creation_job_outputs_unexpected_unique_idx "
                "ON creation_job_outputs(job_id, position, subject_id)"
            ),
            "jobs_extra_unique": (
                "CREATE UNIQUE INDEX creation_jobs_unexpected_unique_idx "
                "ON creation_jobs(workspace_id, job_id)"
            ),
            "jobs_extra_partial": (
                "CREATE INDEX creation_jobs_unexpected_partial_idx "
                "ON creation_jobs(workspace_id, sequence) WHERE state = 'queued'"
            ),
        }
        for mode in ("primary", "secondary"):
            for mutation, statement in mutations.items():
                with (
                    self.subTest(mode=mode, mutation=mutation),
                    tempfile.TemporaryDirectory() as temporary,
                ):
                    data_dir = Path(temporary)
                    StudioStore(data_dir).close()
                    connection = sqlite3.connect(data_dir / DATABASE_NAME)
                    try:
                        connection.executescript(statement)
                        connection.commit()
                    finally:
                        connection.close()

                    reopened = None
                    try:
                        with self.assertRaisesRegex(StudioError, "relational shape"):
                            reopened = StudioStore(data_dir, mode=mode)
                    finally:
                        if reopened is not None:
                            reopened.close()

    def test_secondary_rejects_creation_attempts_without_worker_identity_projection(self) -> None:
        import sqlite3

        from worldforge.studio.errors import StudioError
        from worldforge.studio.storage import DATABASE_NAME, StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary)
            StudioStore(data_dir).close()
            connection = sqlite3.connect(data_dir / DATABASE_NAME)
            try:
                connection.execute(
                    "ALTER TABLE creation_job_attempts DROP COLUMN worker_identity_json"
                )
                connection.commit()
            finally:
                connection.close()

            secondary = None
            try:
                with self.assertRaisesRegex(StudioError, "creation_job_attempts"):
                    secondary = StudioStore(data_dir, mode="secondary")
            finally:
                if secondary is not None:
                    secondary.close()

    def test_v3_to_v4_migration_is_additive_and_preserves_legacy_bytes(self) -> None:
        from worldforge.studio.storage import SCHEMA_VERSION, StudioStore

        self.assertEqual(5, SCHEMA_VERSION)
        with tempfile.TemporaryDirectory() as temporary:
            store = StudioStore(Path(temporary))
            legacy_payload = '{"legacy":"bytes-preserved"}'
            with store.connection:
                store.connection.execute(
                    "INSERT INTO events "
                    "(workspace_id, topic, entity_type, entity_id, payload_json, created_at) "
                    "VALUES (NULL, 'legacy', 'legacy', 'legacy_01', ?, ?)",
                    (legacy_payload, "2026-08-02T00:00:00.000000Z"),
                )
                store.connection.execute(
                    "UPDATE schema_meta SET value = '3' WHERE key = 'schema_version'"
                )
                for table in (
                    "creation_artifact_dependencies",
                    "creation_artifacts",
                    "creation_job_attempts",
                    "creation_job_outputs",
                    "creation_job_payloads",
                    "creation_job_inputs",
                    "creation_jobs",
                ):
                    store.connection.execute(f"DROP TABLE IF EXISTS {table}")
            store.close()

            migrated = StudioStore(Path(temporary))
            try:
                version = migrated.connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()["value"]
                self.assertEqual("5", version)
                payload = migrated.connection.execute(
                    "SELECT payload_json FROM events WHERE entity_id = 'legacy_01'"
                ).fetchone()["payload_json"]
                self.assertEqual(legacy_payload, payload)
                tables = {
                    row["name"]
                    for row in migrated.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {
                        "creation_jobs",
                        "creation_job_inputs",
                        "creation_job_payloads",
                        "creation_job_outputs",
                        "creation_job_attempts",
                        "creation_artifacts",
                        "creation_artifact_dependencies",
                    }
                    <= tables
                )
            finally:
                migrated.close()

    def test_startup_orphans_only_running_creation_jobs_and_increments_generation(self) -> None:
        from worldforge.studio import storage as storage_module
        from worldforge.studio.storage import StudioStore, encode_json

        with tempfile.TemporaryDirectory() as temporary:
            store = StudioStore(Path(temporary))
            workspace = {
                "format": "world-forge.studio_creation_workspace",
                "format_version": 1,
                "workspace_id": "workspace_01",
                "project": {
                    "format": "world-forge.project",
                    "format_version": 1,
                    "id": "neutral_game",
                    "content_hash": _HASH_A,
                },
                "project_kind": "game",
                "source_revision": _HASH_A,
                "workflow_status_hash": _HASH_B,
                "root_generation": 4,
                "created_at": "2026-08-02T00:00:00.000000Z",
                "updated_at": "2026-08-02T00:00:00.000000Z",
            }
            running = _job()
            running.update(
                {
                    "state": "running",
                    "progress": "worker_started",
                    "started_at": "2026-08-02T00:00:01.000000Z",
                    "updated_at": "2026-08-02T00:00:01.000000Z",
                }
            )
            running["record_hash"] = canonical_payload_hash(running, hash_field="record_hash")
            queued = _job()
            queued["job_id"] = "job_compile_02"
            queued["record_hash"] = canonical_payload_hash(queued, hash_field="record_hash")
            with store.connection:
                store.connection.execute(
                    "INSERT INTO creation_workspaces "
                    "(workspace_id, record_json, absolute_root, root_dev, root_ino, generation) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "workspace_01",
                        encode_json(workspace),
                        str(Path(temporary)),
                        "1",
                        "1",
                        4,
                    ),
                )
                for sequence, record in enumerate((running, queued), 1):
                    store.connection.execute(
                        "INSERT INTO creation_jobs "
                        "(sequence, job_id, workspace_id, operation, state, progress, "
                        "generation, record_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            sequence,
                            record["job_id"],
                            record["workspace_id"],
                            record["operation"],
                            record["state"],
                            record["progress"],
                            record["generation"],
                            encode_json(record),
                        ),
                    )
                worker_identity = {
                    "platform": "linux",
                    "pid": 4242,
                    "process_group_id": 4242,
                    "session_id": 4242,
                    "start_time_ticks": 123456,
                }
                store.connection.execute(
                    "INSERT INTO creation_job_attempts "
                    "(job_id, phase, journal_name, journal_dev, journal_ino, stage_locator, "
                    "stage_dev, stage_ino, request_locator, request_sha256, worker_pid, "
                    "worker_identity_json, generation, created_at, updated_at) "
                    "VALUES (?, 'worker_started', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?)",
                    (
                        running["job_id"],
                        "creation_job_startup_reap.journal",
                        "10",
                        "20",
                        "stage_" + "1" * 32,
                        "30",
                        "40",
                        "request_" + "2" * 32,
                        _HASH_A,
                        4242,
                        encode_json(worker_identity),
                        running["updated_at"],
                        running["updated_at"],
                    ),
                )
            store.close()

            with patch.object(
                storage_module,
                "terminate_registered_creation_process",
            ) as terminate:
                reopened = StudioStore(Path(temporary))
            terminate.assert_called_once_with(4242, worker_identity)
            try:
                rows = reopened.connection.execute(
                    "SELECT job_id, state, generation, record_json FROM creation_jobs "
                    "ORDER BY sequence"
                ).fetchall()
                self.assertEqual(("orphaned", 1), (rows[0]["state"], rows[0]["generation"]))
                orphaned = json.loads(rows[0]["record_json"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                self.assertEqual("queued", rows[1]["state"])
                attempt = reopened.connection.execute(
                    "SELECT worker_pid, worker_identity_json FROM creation_job_attempts "
                    "WHERE job_id = ?",
                    (running["job_id"],),
                ).fetchone()
                self.assertIsNone(attempt["worker_pid"])
                self.assertIsNone(attempt["worker_identity_json"])
                event = reopened.connection.execute(
                    "SELECT topic, entity_id, payload_json FROM creation_events"
                ).fetchone()
                self.assertEqual("creation_job.orphaned", event["topic"])
                self.assertEqual("job_compile_01", event["entity_id"])
            finally:
                reopened.close()


class StudioCreationWorkerTests(unittest.TestCase):
    def test_coordinator_preserves_worker_recovery_evidence(self) -> None:
        from worldforge.studio.creation_executor import CreationWorkerExecutionError
        from worldforge.studio.creation_jobs import CreationJobCoordinator

        evidence = {
            "stage": {
                "locator": ".worldforge-retained-stage-abc",
                "identity": [7, 11],
                "retention": "active",
            }
        }
        jobs = MagicMock()
        jobs.claim_next.return_value = {"job_id": "job_recovery_evidence"}
        coordinator = CreationJobCoordinator(jobs)
        with (
            patch.object(
                coordinator,
                "_execute",
                side_effect=CreationWorkerExecutionError(
                    "recovery_required",
                    "Exact retained output requires recovery",
                    recovery_evidence=evidence,
                ),
            ),
            patch.object(coordinator, "_finish_after_error") as finish,
        ):
            self.assertEqual("job_recovery_evidence", coordinator.run_once())

        finish.assert_called_once_with(
            "job_recovery_evidence",
            "recovery_required",
            recovery_evidence=evidence,
        )

    def test_asset_release_wrap_preserves_lower_recovery_evidence(self) -> None:
        from worldforge.generic_assetpack import GenericAssetpackError
        from worldforge.studio.creation_executor import CreationWorkerExecutionError
        from worldforge.studio.creation_jobs import CreationJobCoordinator

        evidence = {
            "journal": {
                "locator": ".assetpack.assetpack-publication.journal.json",
                "identity": [13, 17],
                "retention": "active",
            }
        }
        coordinator = CreationJobCoordinator(MagicMock())
        outputs = (MagicMock(payload=b"{}"), MagicMock(payload=b"{}"))
        with (
            patch(
                "worldforge.studio.creation_jobs.decode_json_object",
                side_effect=GenericAssetpackError(
                    "assetpack_recovery_required",
                    "Exact assetpack journal requires recovery",
                    recovery_evidence=evidence,
                ),
            ),
            self.assertRaises(CreationWorkerExecutionError) as raised,
        ):
            coordinator._publish_asset_release(  # noqa: SLF001
                {"job_id": "job_asset_release", "operation": "asset.release.seal"},
                request={"operation": "asset.release.seal"},
                outputs=outputs,
                dependency_documents=(),
                artifact_root=Path("unused-artifact-root"),
            )

        self.assertEqual("recovery_required", raised.exception.code)
        self.assertEqual(evidence, raised.exception.recovery_evidence)

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "strict retained creation evidence is a native Linux policy",
    )
    def test_linux_failed_stage_reservation_never_uses_pathname_rmdir(self) -> None:
        from worldforge.studio import creation_executor

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)

            with (
                patch.object(
                    creation_executor,
                    "fsync_directory",
                    side_effect=OSError("simulated stage reservation interruption"),
                ),
                patch.object(Path, "rmdir") as pathname_rmdir,
                self.assertRaises(OSError),
            ):
                creation_executor.create_creation_stage(parent, "job_stage_retained")

            pathname_rmdir.assert_not_called()
            retained = list(parent.iterdir())
            self.assertEqual(1, len(retained))
            self.assertTrue(retained[0].is_dir())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "strict retained creation evidence is a native Linux policy",
    )
    def test_linux_failed_private_request_never_uses_pathname_unlink(self) -> None:
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio import creation_executor
        from worldforge.studio.creation_job_protocol import build_private_compile_request

        project = load_creation_project(_PUZZLE_ROOT / "project.json")
        request = build_private_compile_request(
            job_id="job_request_retained",
            workspace_id="workspace_01",
            authority=_authority(),
            project=project,
        )
        with tempfile.TemporaryDirectory() as temporary:
            stage, _identity = creation_executor.create_creation_stage(
                Path(temporary),
                "job_request_retained",
            )
            real_write = creation_executor.os.write
            calls = 0

            def interrupt_after_partial_write(descriptor: int, payload: object) -> int:
                nonlocal calls
                calls += 1
                if calls == 1:
                    return real_write(descriptor, memoryview(payload)[:1])
                raise OSError("simulated private request interruption")

            with (
                patch.object(
                    creation_executor.os,
                    "write",
                    side_effect=interrupt_after_partial_write,
                ),
                patch.object(Path, "unlink") as pathname_unlink,
                self.assertRaises(OSError),
            ):
                creation_executor.write_private_request(stage, request)

            pathname_unlink.assert_not_called()
            retained = list(stage.iterdir())
            self.assertEqual(1, len(retained))
            self.assertTrue(retained[0].is_file())

    def test_windows_process_identity_and_termination_use_pointer_safe_signatures(self) -> None:
        import ctypes
        from types import SimpleNamespace

        from worldforge.studio import creation_process

        class FakeFunction:
            def __init__(self, result: object) -> None:
                self.result = result
                self.calls: list[tuple[object, ...]] = []

            def __call__(self, *args: object) -> object:
                self.calls.append(args)
                return self.result

        class GetTimes(FakeFunction):
            def __call__(self, *args: object) -> object:
                args[1]._obj.value = 123456  # type: ignore[attr-defined]
                args[2]._obj.value = 0  # type: ignore[attr-defined]
                return super().__call__(*args)

        open_process = FakeFunction(0x1_0000_0001)
        get_times = GetTimes(1)
        close_handle = FakeFunction(1)
        identity_kernel = SimpleNamespace(
            OpenProcess=open_process,
            GetProcessTimes=get_times,
            CloseHandle=close_handle,
        )
        with patch.object(
            creation_process.ctypes,
            "WinDLL",
            return_value=identity_kernel,
            create=True,
        ):
            identity = creation_process._windows_process_identity(42)  # noqa: SLF001
        self.assertEqual(123456, identity["creation_time"])
        self.assertEqual([ctypes.c_void_p], close_handle.argtypes)

        terminate_open = FakeFunction(0x1_0000_0001)
        terminate = FakeFunction(1)
        wait = FakeFunction(0)
        terminate_close = FakeFunction(1)
        termination_kernel = SimpleNamespace(
            OpenProcess=terminate_open,
            TerminateProcess=terminate,
            WaitForSingleObject=wait,
            CloseHandle=terminate_close,
        )
        expected = {"platform": "windows", "pid": 42, "creation_time": 123456}
        with (
            patch.object(creation_process, "sys_platform_linux", return_value=False),
            patch.object(creation_process.os, "name", "nt"),
            patch.object(creation_process, "_windows_process_identity", return_value=expected),
            patch.object(
                creation_process.ctypes,
                "WinDLL",
                return_value=termination_kernel,
                create=True,
            ),
        ):
            creation_process.terminate_registered_creation_process(42, expected)
        self.assertEqual(
            [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32],
            terminate_open.argtypes,
        )
        self.assertEqual([ctypes.c_void_p, ctypes.c_uint32], terminate.argtypes)
        self.assertEqual([ctypes.c_void_p, ctypes.c_uint32], wait.argtypes)
        self.assertEqual([ctypes.c_void_p], terminate_close.argtypes)

    def test_stage_reservation_failure_retains_the_exact_empty_directory(self) -> None:
        from worldforge.studio import creation_executor

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            real_fsync = creation_executor.fsync_directory

            def interrupt_reservation(path: Path, *, context: str) -> None:
                if context == "creation worker stage parent":
                    raise OSError("simulated stage reservation interruption")
                real_fsync(path, context=context)

            with (
                patch.object(
                    creation_executor,
                    "fsync_directory",
                    side_effect=interrupt_reservation,
                ),
                self.assertRaisesRegex(OSError, "simulated stage reservation interruption"),
            ):
                creation_executor.create_creation_stage(parent, "job_stage_failure")
            retained = list(parent.iterdir())
            self.assertEqual(1, len(retained))
            self.assertTrue(retained[0].is_dir())
            self.assertEqual([], list(retained[0].iterdir()))

    def test_private_request_partial_write_retains_the_exact_temporary_file(self) -> None:
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio import creation_executor
        from worldforge.studio.creation_job_protocol import build_private_compile_request

        project = load_creation_project(_PUZZLE_ROOT / "project.json")
        request = build_private_compile_request(
            job_id="job_partial_request",
            workspace_id="workspace_01",
            authority=_authority(),
            project=project,
        )
        with tempfile.TemporaryDirectory() as temporary:
            stage, _identity = creation_executor.create_creation_stage(
                Path(temporary),
                "job_partial_request",
            )
            real_write = creation_executor.os.write
            calls = 0

            def interrupt_after_partial_write(descriptor: int, payload: object) -> int:
                nonlocal calls
                calls += 1
                if calls == 1:
                    return real_write(descriptor, memoryview(payload)[:1])
                raise OSError("simulated private request interruption")

            with (
                patch.object(
                    creation_executor.os,
                    "write",
                    side_effect=interrupt_after_partial_write,
                ),
                self.assertRaisesRegex(OSError, "simulated private request interruption"),
            ):
                creation_executor.write_private_request(stage, request)
            retained = list(stage.iterdir())
            self.assertEqual(1, len(retained))
            self.assertTrue(retained[0].is_file())
            self.assertEqual(1, retained[0].stat().st_size)

    def test_compile_worker_is_deterministic_and_reports_unsupported_as_success(self) -> None:
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio.creation_job_protocol import (
            build_private_compile_request,
            execute_private_creation_request,
        )

        for root, expected_status in (
            (_PUZZLE_ROOT, "passed"),
            (_SIMULATION_ROOT, "unsupported"),
        ):
            with self.subTest(project=root.name):
                project = load_creation_project(root / "project.json")
                request = build_private_compile_request(
                    job_id=f"job_{root.name.replace('-', '_')}",
                    workspace_id="workspace_01",
                    authority=_authority(),
                    project=project,
                )
                first = execute_private_creation_request(request)
                second = execute_private_creation_request(copy.deepcopy(request))
                self.assertEqual(
                    [output.payload for output in first.outputs],
                    [output.payload for output in second.outputs],
                )
                self.assertEqual(
                    [
                        "world-forge.gamepack",
                        "world-forge.mechanic_capability_ledger",
                        "world-forge.game_analysis",
                    ],
                    [output.subject["format"] for output in first.outputs],
                )
                self.assertEqual(expected_status, first.analysis_status)

    def test_compile_worker_request_is_closed_and_contains_no_native_locator(self) -> None:
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio.creation_job_protocol import (
            build_private_compile_request,
            validate_private_creation_request,
        )

        project = load_creation_project(_PUZZLE_ROOT / "project.json")
        request = build_private_compile_request(
            job_id="job_compile_01",
            workspace_id="workspace_01",
            authority=_authority(),
            project=project,
        )
        self.assertEqual(
            "creation.compile",
            validate_private_creation_request(request)["operation"],
        )
        for field in ("path", "cwd", "command", "env", "provider", "prompt"):
            hostile = copy.deepcopy(request)
            hostile[field] = "forbidden"
            with self.assertRaisesRegex(ValueError, "invalid fields"):
                validate_private_creation_request(hostile)

    def test_isolated_worker_uses_fixed_bootstrap_and_locator_only_protocol(self) -> None:
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio.creation_executor import (
            create_creation_stage,
            run_isolated_creation_worker,
            worker_command,
            worker_environment,
            write_private_request,
        )
        from worldforge.studio.creation_job_protocol import build_private_compile_request

        project = load_creation_project(_PUZZLE_ROOT / "project.json")
        request = build_private_compile_request(
            job_id="job_compile_01",
            workspace_id="workspace_01",
            authority=_authority(),
            project=project,
        )
        with tempfile.TemporaryDirectory() as temporary:
            stage, identity = create_creation_stage(Path(temporary), "job_compile_01")
            locator, request_sha256 = write_private_request(stage, request)
            envelope = {
                "format": "world-forge.studio_creation_worker",
                "format_version": 1,
                "kind": "request",
                "job_id": "job_compile_01",
                "operation": "creation.compile",
                "request_locator": locator,
                "request_sha256": request_sha256,
            }
            execution = run_isolated_creation_worker(
                stage,
                identity,
                envelope,
                timeout_seconds=10.0,
            )

            self.assertTrue(execution.response["ok"])
            self.assertEqual(3, len(execution.outputs))
            self.assertNotIn(str(stage), json.dumps(execution.response))
            for output in execution.outputs:
                self.assertEqual(hashlib.sha256(output.payload).hexdigest(), output.sha256)
                self.assertEqual(output.size, len(output.payload))
            self.assertEqual("-I", worker_command()[1])
            environment = worker_environment()
            self.assertNotIn("PYTHONPATH", environment)
            self.assertNotIn("HOME", environment)
            self.assertNotIn("PATH", environment)

    def test_isolated_worker_reports_identity_bound_process_lifecycle(self) -> None:
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio.creation_executor import (
            create_creation_stage,
            run_isolated_creation_worker,
            worker_environment,
            write_private_request,
        )
        from worldforge.studio.creation_job_protocol import build_private_compile_request

        project = load_creation_project(_PUZZLE_ROOT / "project.json")
        request = build_private_compile_request(
            job_id="job_process_identity",
            workspace_id="workspace_01",
            authority=_authority(),
            project=project,
        )
        with tempfile.TemporaryDirectory() as temporary:
            stage, identity = create_creation_stage(Path(temporary), "job_process_identity")
            locator, request_sha256 = write_private_request(stage, request)
            envelope = {
                "format": "world-forge.studio_creation_worker",
                "format_version": 1,
                "kind": "request",
                "job_id": "job_process_identity",
                "operation": "creation.compile",
                "request_locator": locator,
                "request_sha256": request_sha256,
            }
            started: list[tuple[int, dict[str, object]]] = []
            stopped: list[tuple[int, dict[str, object]]] = []
            execution = run_isolated_creation_worker(
                stage,
                identity,
                envelope,
                timeout_seconds=10.0,
                process_started=lambda pid, proof: started.append((pid, proof)),
                process_stopped=lambda pid, proof: stopped.append((pid, proof)),
            )
            self.assertTrue(execution.response["ok"])
            self.assertEqual(1, len(started))
            self.assertEqual(started, stopped)
            self.assertEqual(started[0][0], started[0][1]["pid"])
            if sys.platform.startswith("linux"):
                self.assertEqual(
                    str(os.getpid()),
                    worker_environment()["WORLD_FORGE_STUDIO_PARENT_PID"],
                )

    def test_isolated_worker_closes_process_containment_when_stop_callback_fails(self) -> None:
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio import creation_executor
        from worldforge.studio.creation_job_protocol import build_private_compile_request

        project = load_creation_project(_PUZZLE_ROOT / "project.json")
        request = build_private_compile_request(
            job_id="job_process_callback_failure",
            workspace_id="workspace_01",
            authority=_authority(),
            project=project,
        )
        with tempfile.TemporaryDirectory() as temporary:
            stage, identity = creation_executor.create_creation_stage(
                Path(temporary), "job_process_callback_failure"
            )
            locator, request_sha256 = creation_executor.write_private_request(stage, request)
            envelope = {
                "format": "world-forge.studio_creation_worker",
                "format_version": 1,
                "kind": "request",
                "job_id": "job_process_callback_failure",
                "operation": "creation.compile",
                "request_locator": locator,
                "request_sha256": request_sha256,
            }
            containment = MagicMock()

            with (
                patch.object(creation_executor, "_WindowsJob", return_value=containment),
                self.assertRaisesRegex(RuntimeError, "stop callback failed"),
            ):
                creation_executor.run_isolated_creation_worker(
                    stage,
                    identity,
                    envelope,
                    timeout_seconds=10.0,
                    process_stopped=lambda _pid, _proof: (_ for _ in ()).throw(
                        RuntimeError("stop callback failed")
                    ),
                )

            containment.close.assert_called_once_with()


class StudioCreationJobCoordinatorTests(unittest.TestCase):
    def test_cleanup_wrap_preserves_evidence_for_lower_failure(self) -> None:
        from worldforge.studio.creation_jobs import CreationJobCoordinator
        from worldforge.studio.errors import StudioError
        from worldforge.studio.service import _sanitized_error

        evidence = {
            "stage": {
                "locator": ".worldforge-retained-stage-abc",
                "identity": [7, 11],
                "retention": "active",
            }
        }
        coordinator = CreationJobCoordinator(MagicMock())
        with (
            patch.object(
                coordinator,
                "_recover_cleanup",
                side_effect=OSError("simulated lower cleanup failure"),
            ),
            patch.object(
                coordinator,
                "_attempt_recovery_evidence",
                return_value=evidence,
            ),
            self.assertRaises(StudioError) as raised,
        ):
            coordinator._recover_cleanup_with_evidence(  # noqa: SLF001
                {"job_id": "job_cleanup_evidence"}
            )

        public_error = _sanitized_error(raised.exception, "Creation job recovery failed")
        self.assertEqual("conflict", public_error.code)
        self.assertEqual("Creation job cleanup requires explicit recovery", public_error.message)
        self.assertEqual("cleanup_recovery_failed", public_error.details["reason_code"])
        self.assertEqual(evidence, public_error.details["recovery_evidence"])
        self.assertNotIn("simulated lower cleanup failure", public_error.message)

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "retained cleanup evidence is a native Linux test",
    )
    def test_partial_stage_conflict_exposes_pathless_recovery_evidence(self) -> None:
        from worldforge.studio.creation_executor import create_creation_stage
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            service, _workspace = _prepared_creation_service(Path(temporary))
            try:
                stage, identity = create_creation_stage(
                    service.store.creation_jobs_dir,
                    "job_direct_partial_stage",
                )
                with self.assertRaisesRegex(StudioError, "recovery_required") as raised:
                    service.creation_job_coordinator._cleanup_empty_stage(  # noqa: SLF001
                        stage,
                        identity,
                        allow_retained_terminal=False,
                    )
                self.assertEqual(
                    stage.name,
                    raised.exception.details["recovery_evidence"]["stage"]["locator"],
                )
                self.assertEqual(
                    list(identity),
                    raised.exception.details["recovery_evidence"]["stage"]["identity"],
                )
            finally:
                service.close()
                service.store.close()

    def test_restart_recovers_a_hard_stop_after_durable_stage_reservation(self) -> None:
        from worldforge.studio import creation_jobs as creation_jobs_module
        from worldforge.studio.errors import StudioError
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            job = _queue_compile(service, workspace, "job_hard_stage_stop")
            data_dir = service.store.data_dir
            try:
                with (
                    patch.object(
                        creation_jobs_module,
                        "write_private_request",
                        side_effect=SystemExit("simulated process death"),
                    ),
                    self.assertRaisesRegex(SystemExit, "simulated process death"),
                ):
                    service.creation_job_coordinator.run_once()
                self.assertEqual(
                    1,
                    service.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_job_attempts WHERE job_id = ?",
                        (job["job_id"],),
                    ).fetchone()[0],
                )
            finally:
                service.close()
                service.store.close()

            reopened_store = StudioStore(data_dir)
            reopened = StudioService(reopened_store)
            try:
                orphaned = reopened.creation_jobs.get(job["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                with reopened_store.connection:
                    reopened_store.connection.execute(
                        "UPDATE creation_job_attempts SET generation = 99 WHERE job_id = ?",
                        (job["job_id"],),
                    )
                with self.assertRaisesRegex(StudioError, "attempt generation"):
                    reopened.creation_jobs.recover(
                        orphaned["job_id"],
                        mode="rollback",
                        expected_generation=orphaned["generation"],
                        expected_record_hash=orphaned["record_hash"],
                    )
                with reopened_store.connection:
                    reopened_store.connection.execute(
                        "UPDATE creation_job_attempts SET generation = 0 WHERE job_id = ?",
                        (job["job_id"],),
                    )
                if sys.platform.startswith("linux") and os.name == "posix":
                    with self.assertRaisesRegex(StudioError, "recovery_required|retained"):
                        reopened.creation_jobs.recover(
                            orphaned["job_id"],
                            mode="rollback",
                            expected_generation=orphaned["generation"],
                            expected_record_hash=orphaned["record_hash"],
                        )
                    _assert_linux_recovery_required(self, reopened, orphaned["job_id"])
                else:
                    rolled_back = reopened.creation_jobs.recover(
                        orphaned["job_id"],
                        mode="rollback",
                        expected_generation=orphaned["generation"],
                        expected_record_hash=orphaned["record_hash"],
                    )
                    self.assertEqual("failed", rolled_back["state"])
                    self.assertEqual([], list(reopened_store.creation_jobs_dir.iterdir()))
                    self.assertEqual(
                        [],
                        list(reopened_store.creation_job_journals_dir.iterdir()),
                    )
            finally:
                reopened.close()
                reopened_store.close()

    def test_cancel_during_partial_fixed_output_publication_cleans_the_stage(self) -> None:
        from worldforge.studio import creation_jobs as creation_jobs_module
        from worldforge.studio.creation_executor import CreationWorkerExecutionError

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                job = _queue_compile(service, workspace, "job_partial_output_cancel")

                def partial_then_cancel(stage: Path, *_args: object, **_kwargs: object):
                    output = stage / "output_0001.json"
                    output.write_bytes(b'{"partial":')
                    current = service.creation_jobs.get(job["job_id"])
                    service.creation_jobs.cancel(
                        current["job_id"],
                        expected_generation=current["generation"],
                        expected_record_hash=current["record_hash"],
                    )
                    raise CreationWorkerExecutionError("canceled", "simulated cancellation")

                with patch.object(
                    creation_jobs_module,
                    "run_isolated_creation_worker",
                    side_effect=partial_then_cancel,
                ):
                    self.assertEqual(job["job_id"], service.creation_job_coordinator.run_once())
                canceled = service.creation_jobs.get(job["job_id"])
                if sys.platform.startswith("linux") and os.name == "posix":
                    _assert_linux_recovery_required(self, service, job["job_id"])
                else:
                    self.assertEqual("canceled", canceled["state"])
                    self.assertEqual([], list(service.store.creation_jobs_dir.iterdir()))
                    self.assertEqual(
                        [],
                        list(service.store.creation_job_journals_dir.iterdir()),
                    )
            finally:
                service.close()
                service.store.close()

    def test_scheduler_shutdown_signal_is_part_of_worker_cancellation(self) -> None:
        import threading

        from worldforge.studio import creation_jobs as creation_jobs_module

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                job = _queue_compile(service, workspace, "job_service_shutdown")
                stop = threading.Event()
                coordinator = creation_jobs_module.CreationJobCoordinator(
                    service.creation_jobs,
                    timeout_seconds=20.0,
                    shutdown_requested=stop.is_set,
                )
                stop.set()
                self.assertEqual(job["job_id"], coordinator.run_once())
                interrupted = service.creation_jobs.get(job["job_id"])
                self.assertEqual("running", interrupted["state"])
                self.assertEqual("worker_started", interrupted["progress"])
            finally:
                service.close()
                service.store.close()

    def test_recovery_journal_requires_contiguous_phases_and_generations(self) -> None:
        from worldforge.studio import creation_jobs as creation_jobs_module

        job = _job()
        job.update(
            {
                "state": "running",
                "progress": "worker_started",
                "generation": 2,
                "started_at": "2026-08-02T00:00:01.000000Z",
                "updated_at": "2026-08-02T00:00:02.000000Z",
            }
        )
        job["record_hash"] = canonical_payload_hash(job, hash_field="record_hash")
        row = {
            "stage_locator": "stage_" + "1" * 32,
            "stage_dev": "10",
            "stage_ino": "20",
            "request_locator": "request_" + "2" * 32,
            "request_sha256": _HASH_A,
            "phase": "worker_started",
            "generation": 2,
        }

        def payload(phase: str, generation: int) -> bytes:
            journal_job = dict(job)
            journal_job["generation"] = generation
            return creation_jobs_module._journal_payload(  # noqa: SLF001
                job=journal_job,
                phase=phase,
                stage_locator=row["stage_locator"],
                stage_identity=(10, 20),
                request_locator=row["request_locator"],
                request_sha256=row["request_sha256"],
                outputs=(),
            )

        with self.assertRaisesRegex(Exception, "order"):
            creation_jobs_module.CreationJobCoordinator._validated_recovery_history(  # noqa: SLF001
                job,
                row,
                (payload("reserved", 1), payload("output_published", 2)),
            )
        with self.assertRaisesRegex(Exception, "generation"):
            creation_jobs_module.CreationJobCoordinator._validated_recovery_history(  # noqa: SLF001
                job,
                row,
                (payload("reserved", 1), payload("worker_started", 99)),
            )
        altered_row = dict(row)
        altered_row["generation"] = 99
        with self.assertRaisesRegex(Exception, "attempt generation"):
            creation_jobs_module.CreationJobCoordinator._validated_recovery_history(  # noqa: SLF001
                job,
                altered_row,
                (payload("reserved", 1), payload("worker_started", 2)),
            )

    def test_torn_journal_tail_is_truncated_to_its_validated_complete_prefix(self) -> None:
        import os

        from isoworld.content.publication_journal import journal_frame
        from isoworld.runtime_io import decode_json_object
        from worldforge.studio import creation_jobs as creation_jobs_module

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                job = _queue_compile(service, workspace, "job_torn_journal")
                real_append = creation_jobs_module.append_append_only_journal
                torn = False

                def tear_output_publication(*args: object, **kwargs: object):
                    nonlocal torn
                    updated = decode_json_object(
                        kwargs["updated_payload"], source="creation journal tear test"
                    )
                    if not torn and updated["phase"] == "output_published":
                        torn = True
                        frame = journal_frame(kwargs["updated_payload"])
                        descriptor = os.open(args[0], os.O_WRONLY | os.O_APPEND)
                        try:
                            os.write(descriptor, frame[:37])
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                        raise OSError("simulated torn journal append")
                    return real_append(*args, **kwargs)

                with patch.object(
                    creation_jobs_module,
                    "append_append_only_journal",
                    side_effect=tear_output_publication,
                ):
                    self.assertEqual(job["job_id"], service.creation_job_coordinator.run_once())
                failed = service.creation_jobs.get(job["job_id"])
                if sys.platform.startswith("linux") and os.name == "posix":
                    _assert_linux_recovery_required(self, service, job["job_id"])
                else:
                    self.assertEqual("failed", failed["state"])
                    self.assertEqual([], list(service.store.creation_jobs_dir.iterdir()))
                    self.assertEqual(
                        [],
                        list(service.store.creation_job_journals_dir.iterdir()),
                    )
            finally:
                service.close()
                service.store.close()

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "retained cleanup evidence is a native Linux test",
    )
    def test_cleanup_recovery_verifies_retained_stage_and_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                job = _queue_compile(service, workspace, "job_cleanup_db_window")
                coordinator = service.creation_job_coordinator
                with patch.object(
                    coordinator,
                    "_complete_cleanup",
                    side_effect=OSError("simulated DB cleanup completion interruption"),
                ):
                    self.assertEqual(job["job_id"], coordinator.run_once())
                pending = service.creation_jobs.get(job["job_id"])
                self.assertEqual("cleanup_pending", pending["progress"])
                attempt = service.store.connection.execute(
                    "SELECT * FROM creation_job_attempts WHERE job_id = ?",
                    (job["job_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)
                retained_stage = coordinator._retained_stage_evidence_path(  # noqa: SLF001
                    service.store.creation_jobs_dir / attempt["stage_locator"],
                    (int(attempt["stage_dev"]), int(attempt["stage_ino"])),
                )
                from worldforge.directory_publish import retained_journal_evidence_path

                retained_journal = retained_journal_evidence_path(
                    service.store.creation_job_journals_dir / attempt["journal_name"],
                    (int(attempt["journal_dev"]), int(attempt["journal_ino"])),
                )
                self.assertTrue(retained_stage.is_dir())
                self.assertTrue(retained_journal.is_file())

                recovered = service.creation_jobs.recover(
                    pending["job_id"],
                    mode="cleanup",
                    expected_generation=pending["generation"],
                    expected_record_hash=pending["record_hash"],
                )
                self.assertEqual("committed", recovered["progress"])
            finally:
                service.close()
                service.store.close()

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "retained cleanup evidence is a native Linux test",
    )
    def test_cleanup_recovery_rejects_coherently_rewritten_retained_evidence(self) -> None:
        from isoworld.content.publication_journal import journal_frame
        from isoworld.runtime_io import decode_json_object
        from worldforge.directory_publish import (
            read_append_only_journal_history_state,
            retained_journal_evidence_path,
        )
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                job = _queue_compile(service, workspace, "job_cleanup_mutated_evidence")
                coordinator = service.creation_job_coordinator
                with patch.object(
                    coordinator,
                    "_complete_cleanup",
                    side_effect=OSError("simulated DB cleanup completion interruption"),
                ):
                    self.assertEqual(job["job_id"], coordinator.run_once())
                pending = service.creation_jobs.get(job["job_id"])
                attempt = service.store.connection.execute(
                    "SELECT * FROM creation_job_attempts WHERE job_id = ?",
                    (job["job_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)
                stage_identity = (int(attempt["stage_dev"]), int(attempt["stage_ino"]))
                journal_identity = (int(attempt["journal_dev"]), int(attempt["journal_ino"]))
                retained_stage = coordinator._retained_stage_evidence_path(  # noqa: SLF001
                    service.store.creation_jobs_dir / attempt["stage_locator"],
                    stage_identity,
                )
                retained_journal = retained_journal_evidence_path(
                    service.store.creation_job_journals_dir / attempt["journal_name"],
                    journal_identity,
                )
                loaded = read_append_only_journal_history_state(
                    retained_journal,
                    max_record_bytes=256 * 1024,
                    max_file_bytes=2 * 1024 * 1024,
                )
                self.assertIsNotNone(loaded)
                history, confirmed_journal_identity, partial_tail = loaded
                self.assertEqual(journal_identity, confirmed_journal_identity)
                self.assertFalse(partial_tail)
                records = [
                    decode_json_object(item, source="mutated retained journal") for item in history
                ]
                victim_descriptor = records[-1]["outputs"][0]
                victim = retained_stage / f"{victim_descriptor['locator']}.json"
                original = victim.read_bytes()
                mutated = bytes([original[0] ^ 1]) + original[1:]
                with victim.open("wb") as stream:
                    stream.write(mutated)
                    stream.flush()
                    os.fsync(stream.fileno())
                mutated_hash = hashlib.sha256(mutated).hexdigest()
                for record in records:
                    for output in record["outputs"]:
                        if output["locator"] == victim_descriptor["locator"]:
                            output["sha256"] = mutated_hash
                            output["size"] = len(mutated)
                rewritten = [canonical_json_bytes(record) for record in records]
                with retained_journal.open("wb") as stream:
                    stream.write(rewritten[0])
                    for item in rewritten[1:]:
                        stream.write(journal_frame(item))
                    stream.flush()
                    os.fsync(stream.fileno())

                with self.assertRaisesRegex(StudioError, "trusted|projection|output"):
                    service.creation_jobs.recover(
                        pending["job_id"],
                        mode="cleanup",
                        expected_generation=pending["generation"],
                        expected_record_hash=pending["record_hash"],
                    )
                unchanged = service.creation_jobs.get(job["job_id"])
                self.assertEqual("cleanup_pending", unchanged["progress"])
            finally:
                service.close()
                service.store.close()

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "retained cleanup evidence is a native Linux test",
    )
    def test_cleanup_recovery_rejects_missing_retained_journal(self) -> None:
        from worldforge.directory_publish import retained_journal_evidence_path
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                job = _queue_compile(service, workspace, "job_cleanup_missing_journal")
                coordinator = service.creation_job_coordinator
                with patch.object(
                    coordinator,
                    "_complete_cleanup",
                    side_effect=OSError("simulated DB cleanup completion interruption"),
                ):
                    self.assertEqual(job["job_id"], coordinator.run_once())
                pending = service.creation_jobs.get(job["job_id"])
                attempt = service.store.connection.execute(
                    "SELECT * FROM creation_job_attempts WHERE job_id = ?",
                    (job["job_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)
                retained_journal = retained_journal_evidence_path(
                    service.store.creation_job_journals_dir / attempt["journal_name"],
                    (int(attempt["journal_dev"]), int(attempt["journal_ino"])),
                )
                retained_stage = coordinator._retained_stage_evidence_path(  # noqa: SLF001
                    service.store.creation_jobs_dir / attempt["stage_locator"],
                    (int(attempt["stage_dev"]), int(attempt["stage_ino"])),
                )
                retained_journal.rename(retained_journal.with_suffix(".missing"))

                with self.assertRaisesRegex(StudioError, "retained journal evidence") as raised:
                    service.creation_jobs.recover(
                        pending["job_id"],
                        mode="cleanup",
                        expected_generation=pending["generation"],
                        expected_record_hash=pending["record_hash"],
                    )
                self.assertEqual(
                    retained_stage.name,
                    raised.exception.details["recovery_evidence"]["stage"]["locator"],
                )
                self.assertEqual(
                    retained_journal.name,
                    raised.exception.details["recovery_evidence"]["journal"]["locator"],
                )
                unchanged = service.creation_jobs.get(job["job_id"])
                self.assertEqual("cleanup_pending", unchanged["progress"])
            finally:
                service.close()
                service.store.close()

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "retained cleanup evidence is a native Linux test",
    )
    def test_cleanup_recovery_rejects_incomplete_retained_stage(self) -> None:
        from worldforge.studio import creation_jobs as creation_jobs_module
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                job = _queue_compile(service, workspace, "job_cleanup_incomplete_stage")
                coordinator = service.creation_job_coordinator
                with patch.object(
                    creation_jobs_module,
                    "remove_append_only_journal",
                    side_effect=OSError("simulated pre-journal-archive interruption"),
                ):
                    self.assertEqual(job["job_id"], coordinator.run_once())
                pending = service.creation_jobs.get(job["job_id"])
                self.assertEqual("cleanup_pending", pending["progress"])
                attempt = service.store.connection.execute(
                    "SELECT * FROM creation_job_attempts WHERE job_id = ?",
                    (job["job_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)
                retained_stage = coordinator._retained_stage_evidence_path(  # noqa: SLF001
                    service.store.creation_jobs_dir / attempt["stage_locator"],
                    (int(attempt["stage_dev"]), int(attempt["stage_ino"])),
                )
                victim = next(
                    item for item in retained_stage.iterdir() if item.name.startswith("output_")
                )
                victim.unlink()

                with self.assertRaisesRegex(StudioError, "output is unavailable"):
                    service.creation_jobs.recover(
                        pending["job_id"],
                        mode="cleanup",
                        expected_generation=pending["generation"],
                        expected_record_hash=pending["record_hash"],
                    )
                unchanged = service.creation_jobs.get(job["job_id"])
                self.assertEqual("cleanup_pending", unchanged["progress"])
            finally:
                service.close()
                service.store.close()

    def test_request_stage_fsync_failure_is_recovered_from_a_durable_reservation(self) -> None:
        from worldforge.studio import creation_executor

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                job = _queue_compile(service, workspace, "job_request_stage_fsync")
                real_fsync = creation_executor.fsync_directory

                def fail_request_stage(path: Path, *, context: str) -> None:
                    if context == "creation worker request stage":
                        raise OSError("simulated request stage fsync interruption")
                    real_fsync(path, context=context)

                with patch.object(
                    creation_executor,
                    "fsync_directory",
                    side_effect=fail_request_stage,
                ):
                    self.assertEqual(job["job_id"], service.creation_job_coordinator.run_once())
                failed = service.creation_jobs.get(job["job_id"])
                if sys.platform.startswith("linux") and os.name == "posix":
                    _assert_linux_recovery_required(self, service, job["job_id"])
                else:
                    self.assertEqual("failed", failed["state"])
                    self.assertEqual([], list(service.store.creation_jobs_dir.iterdir()))
                    self.assertEqual(
                        [],
                        list(service.store.creation_job_journals_dir.iterdir()),
                    )
                    self.assertEqual(
                        0,
                        service.store.connection.execute(
                            "SELECT COUNT(*) FROM creation_job_attempts WHERE job_id = ?",
                            (job["job_id"],),
                        ).fetchone()[0],
                    )
            finally:
                service.close()
                service.store.close()

    def test_stored_candidate_record_is_bound_to_all_database_projections(self) -> None:
        from worldforge.studio.storage import encode_json

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                job = _queue_compile(service, workspace, "job_candidate_projection")
                self.assertEqual(job["job_id"], service.creation_job_coordinator.run_once())
                completed = service.creation_jobs.get(job["job_id"])
                artifact_id = completed["result"]["output_artifact_ids"][0]
                row = service.store.connection.execute(
                    "SELECT root_generation, input_artifact_snapshot_hash, record_json "
                    "FROM creation_artifacts "
                    "WHERE workspace_id = ? AND artifact_id = ?",
                    (workspace["workspace_id"], artifact_id),
                ).fetchone()

                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_artifacts SET root_generation = root_generation + 1 "
                        "WHERE workspace_id = ? AND artifact_id = ?",
                        (workspace["workspace_id"], artifact_id),
                    )
                with self.assertRaisesRegex(Exception, "DB projection diverged"):
                    service.creation_artifacts.list_stored(workspace["workspace_id"])
                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_artifacts SET root_generation = ? "
                        "WHERE workspace_id = ? AND artifact_id = ?",
                        (row["root_generation"], workspace["workspace_id"], artifact_id),
                    )

                forged_authority = json.loads(row["record_json"])
                forged_authority["authority"]["root_generation"] += 1
                forged_authority["record_hash"] = canonical_payload_hash(
                    forged_authority, hash_field="record_hash"
                )
                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_artifacts SET root_generation = root_generation + 1, "
                        "record_json = ? WHERE workspace_id = ? AND artifact_id = ?",
                        (
                            encode_json(forged_authority),
                            workspace["workspace_id"],
                            artifact_id,
                        ),
                    )
                with self.assertRaisesRegex(Exception, "producer projection diverged"):
                    service.creation_artifacts.list_stored(workspace["workspace_id"])
                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_artifacts SET root_generation = ?, record_json = ? "
                        "WHERE workspace_id = ? AND artifact_id = ?",
                        (
                            row["root_generation"],
                            row["record_json"],
                            workspace["workspace_id"],
                            artifact_id,
                        ),
                    )

                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_artifacts SET input_artifact_snapshot_hash = ? "
                        "WHERE workspace_id = ? AND artifact_id = ?",
                        ("f" * 64, workspace["workspace_id"], artifact_id),
                    )
                with self.assertRaisesRegex(Exception, "producer projection diverged"):
                    service.creation_artifacts.list_stored(workspace["workspace_id"])
                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_artifacts SET input_artifact_snapshot_hash = ? "
                        "WHERE workspace_id = ? AND artifact_id = ?",
                        (
                            row["input_artifact_snapshot_hash"],
                            workspace["workspace_id"],
                            artifact_id,
                        ),
                    )

                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_jobs SET operation = 'artifact.admit' WHERE job_id = ?",
                        (job["job_id"],),
                    )
                with self.assertRaisesRegex(Exception, "producer projection diverged"):
                    service.creation_artifacts.list_stored(workspace["workspace_id"])
                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_jobs SET operation = 'creation.compile' WHERE job_id = ?",
                        (job["job_id"],),
                    )

                producer_row = service.store.connection.execute(
                    "SELECT record_json FROM creation_jobs WHERE job_id = ?",
                    (job["job_id"],),
                ).fetchone()
                for mutation in ("output_artifact_ids", "artifact_snapshot_hash"):
                    with self.subTest(producer_result=mutation):
                        forged_producer = json.loads(producer_row["record_json"])
                        if mutation == "output_artifact_ids":
                            forged_producer["result"]["output_artifact_ids"][0] = "artifact_forged"
                        else:
                            forged_producer["result"]["artifact_snapshot_hash"] = "e" * 64
                        forged_producer["record_hash"] = canonical_payload_hash(
                            forged_producer,
                            hash_field="record_hash",
                        )
                        with service.store.connection:
                            service.store.connection.execute(
                                "UPDATE creation_jobs SET record_json = ? WHERE job_id = ?",
                                (encode_json(forged_producer), job["job_id"]),
                            )
                        with self.assertRaisesRegex(Exception, "result projection diverged"):
                            service.creation_jobs.get(job["job_id"])
                        with self.assertRaisesRegex(Exception, "result projection diverged"):
                            service.creation_artifacts.list_stored(workspace["workspace_id"])
                        with service.store.connection:
                            service.store.connection.execute(
                                "UPDATE creation_jobs SET record_json = ? WHERE job_id = ?",
                                (producer_row["record_json"], job["job_id"]),
                            )

                actual_evidence = service.creation_evidence.list(
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
                forged_snapshot_hash = "e" * 64
                self.assertNotEqual(
                    forged_snapshot_hash,
                    actual_evidence["artifact_snapshot_hash"],
                )
                forged_producer = json.loads(producer_row["record_json"])
                forged_producer["result"]["artifact_snapshot_hash"] = forged_snapshot_hash
                forged_producer["record_hash"] = canonical_payload_hash(
                    forged_producer,
                    hash_field="record_hash",
                )
                event_row = service.store.connection.execute(
                    "SELECT event_id, payload_json FROM creation_events "
                    "WHERE topic = 'creation_job.succeeded' "
                    "AND entity_type = 'creation_job' AND entity_id = ?",
                    (job["job_id"],),
                ).fetchone()
                forged_event = json.loads(event_row["payload_json"])
                forged_event["artifact_snapshot_hash"] = forged_snapshot_hash
                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_jobs SET record_json = ? WHERE job_id = ?",
                        (encode_json(forged_producer), job["job_id"]),
                    )
                    service.store.connection.execute(
                        "UPDATE creation_events SET payload_json = ? WHERE event_id = ?",
                        (encode_json(forged_event), event_row["event_id"]),
                    )
                service.creation_jobs.get(job["job_id"])
                service.creation_artifacts.list_stored(workspace["workspace_id"])
                with self.assertRaisesRegex(Exception, "recomputed snapshot diverged"):
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
                    )
                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_jobs SET record_json = ? WHERE job_id = ?",
                        (producer_row["record_json"], job["job_id"]),
                    )
                    service.store.connection.execute(
                        "UPDATE creation_events SET payload_json = ? WHERE event_id = ?",
                        (event_row["payload_json"], event_row["event_id"]),
                    )

                record = json.loads(row["record_json"])
                record["producer"]["reference_id"] = "job_forged_reference"
                record["references"]["dependency_count"] += 1
                record["record_hash"] = canonical_payload_hash(record, hash_field="record_hash")
                with service.store.connection:
                    service.store.connection.execute(
                        "UPDATE creation_artifacts SET record_json = ? "
                        "WHERE workspace_id = ? AND artifact_id = ?",
                        (encode_json(record), workspace["workspace_id"], artifact_id),
                    )
                with self.assertRaisesRegex(Exception, "DB projection diverged"):
                    service.creation_artifacts.list_stored(workspace["workspace_id"])
            finally:
                service.close()
                service.store.close()

    def test_identical_candidate_artifacts_are_scoped_by_workspace(self) -> None:
        from worldforge.creation_contracts import load_creation_project

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, first = _prepared_creation_service(base)
            second_root = base / "project_second"
            shutil.copytree(base / "project", second_root)
            project = load_creation_project(second_root / "project.json")
            try:
                grant = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 3,
                        "kind": "request",
                        "request_id": "grant_second",
                        "method": "creation_root_grant.create",
                        "params": {
                            "grant_id": "grant_puzzle_second",
                            "role": "existing_root",
                            "display_name": "Puzzle second",
                            "path": str(second_root),
                            "expected_project_hash": project.project["content_hash"],
                        },
                    }
                )["result"]["grant"]
                second = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 3,
                        "kind": "request",
                        "request_id": "workspace_second",
                        "method": "creation_workspace.register",
                        "params": {
                            "workspace_id": "workspace_puzzle_second",
                            "grant_id": grant["grant_id"],
                            "expected_grant_generation": grant["generation"],
                            "expected_project_hash": project.project["content_hash"],
                        },
                    }
                )["result"]["workspace"]

                first_job = _queue_compile(service, first, "job_compile_first_workspace")
                self.assertEqual(first_job["job_id"], service.creation_job_coordinator.run_once())
                second_job = _queue_compile(service, second, "job_compile_second_workspace")
                self.assertEqual(second_job["job_id"], service.creation_job_coordinator.run_once())
                first_done = service.creation_jobs.get(first_job["job_id"])
                second_done = service.creation_jobs.get(second_job["job_id"])
                self.assertEqual("succeeded", first_done["state"])
                self.assertEqual("succeeded", second_done["state"])
                self.assertEqual(
                    first_done["result"]["output_artifact_ids"],
                    second_done["result"]["output_artifact_ids"],
                )
                self.assertEqual(
                    3,
                    len(service.creation_artifacts.list_stored(first["workspace_id"])),
                )
                self.assertEqual(
                    3,
                    len(service.creation_artifacts.list_stored(second["workspace_id"])),
                )
            finally:
                service.close()
                service.store.close()

    def test_accepted_running_cancellation_cannot_commit_after_worker_exit(self) -> None:
        from worldforge.studio import creation_jobs as creation_jobs_module

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                job = _queue_compile(service, workspace, "job_cancel_after_worker")
                real_run = creation_jobs_module.run_isolated_creation_worker

                def cancel_after_worker(*args: object, **kwargs: object):
                    execution = real_run(*args, **kwargs)
                    current = service.creation_jobs.get(job["job_id"])
                    accepted = service.creation_jobs.cancel(
                        current["job_id"],
                        expected_generation=current["generation"],
                        expected_record_hash=current["record_hash"],
                    )
                    self.assertEqual("running", accepted["state"])
                    return execution

                with patch.object(
                    creation_jobs_module,
                    "run_isolated_creation_worker",
                    side_effect=cancel_after_worker,
                ):
                    self.assertEqual(job["job_id"], service.creation_job_coordinator.run_once())
                canceled = service.creation_jobs.get(job["job_id"])
                if sys.platform.startswith("linux") and os.name == "posix":
                    _assert_linux_recovery_required(self, service, job["job_id"])
                else:
                    self.assertEqual("canceled", canceled["state"])
                self.assertEqual(
                    0,
                    service.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_artifacts WHERE workspace_id = ?",
                        (workspace["workspace_id"],),
                    ).fetchone()[0],
                )
            finally:
                service.close()
                service.store.close()

    def test_cleanup_recovery_accepts_only_missing_files_from_a_pending_exact_set(self) -> None:
        from worldforge.directory_publish import fsync_directory
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
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
                job = service.creation_jobs.create_compile(
                    {
                        "job_id": "job_partial_cleanup",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
                    }
                )
                coordinator = service.creation_job_coordinator

                def interrupt_after_one_output(
                    stage: Path,
                    _stage_identity: object,
                    _request_locator: str,
                    _request_sha256: str,
                    outputs: object,
                    **_kwargs: object,
                ) -> None:
                    first = tuple(outputs)[0]
                    (stage / f"{first.locator}.json").unlink()
                    fsync_directory(stage, context="creation job partial cleanup test")
                    raise OSError("simulated partial cleanup interruption")

                with patch.object(
                    coordinator,
                    "_cleanup_stage",
                    side_effect=interrupt_after_one_output,
                ):
                    self.assertEqual(job["job_id"], coordinator.run_once())
                pending = service.creation_jobs.get(job["job_id"])
                self.assertEqual("cleanup_pending", pending["progress"])
                attempt = service.store.connection.execute(
                    "SELECT stage_locator, journal_name FROM creation_job_attempts "
                    "WHERE job_id = ?",
                    (job["job_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)

                if sys.platform.startswith("linux") and os.name == "posix":
                    with self.assertRaisesRegex(StudioError, "output is unavailable"):
                        service.creation_jobs.recover(
                            pending["job_id"],
                            mode="cleanup",
                            expected_generation=pending["generation"],
                            expected_record_hash=pending["record_hash"],
                        )
                    unchanged = service.creation_jobs.get(job["job_id"])
                    self.assertEqual("cleanup_pending", unchanged["progress"])
                    self.assertTrue(
                        (service.store.creation_jobs_dir / attempt["stage_locator"]).is_dir()
                    )
                    self.assertTrue(
                        (
                            service.store.creation_job_journals_dir / attempt["journal_name"]
                        ).is_file()
                    )
                else:
                    recovered = service.creation_jobs.recover(
                        pending["job_id"],
                        mode="cleanup",
                        expected_generation=pending["generation"],
                        expected_record_hash=pending["record_hash"],
                    )
                    self.assertEqual("committed", recovered["progress"])
                    self.assertEqual([], list(service.store.creation_jobs_dir.iterdir()))
                    self.assertEqual(
                        [],
                        list(service.store.creation_job_journals_dir.iterdir()),
                    )
            finally:
                service.close()
                service.store.close()

    def test_journal_one_phase_ahead_of_attempt_is_safely_reconciled(self) -> None:
        from worldforge.studio import creation_jobs as creation_jobs_module

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
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
                job = service.creation_jobs.create_compile(
                    {
                        "job_id": "job_journal_ahead",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
                    }
                )
                coordinator = service.creation_job_coordinator
                real_advance = coordinator._advance_journal  # noqa: SLF001
                interrupted = False

                def append_without_attempt_update(
                    current_job: object,
                    **kwargs: object,
                ):
                    nonlocal interrupted
                    if not interrupted and kwargs["phase"] == "worker_started":
                        interrupted = True
                        payload = creation_jobs_module._journal_payload(  # noqa: SLF001
                            job=current_job,
                            phase="worker_started",
                            stage_locator=kwargs["stage"].name,
                            stage_identity=kwargs["stage_identity"],
                            request_locator=kwargs["request_locator"],
                            request_sha256=kwargs["request_sha256"],
                            outputs=(),
                        )
                        creation_jobs_module.append_append_only_journal(
                            kwargs["path"],
                            expected_identity=kwargs["identity"],
                            expected_payload=kwargs["current"][-1],
                            expected_history=kwargs["current"],
                            updated_payload=payload,
                            max_record_bytes=256 * 1024,
                            max_file_bytes=2 * 1024 * 1024,
                        )
                        raise OSError("simulated attempt projection interruption")
                    return real_advance(current_job, **kwargs)

                with patch.object(
                    coordinator,
                    "_advance_journal",
                    side_effect=append_without_attempt_update,
                ):
                    self.assertEqual(job["job_id"], coordinator.run_once())

                failed = service.creation_jobs.get(job["job_id"])
                if sys.platform.startswith("linux") and os.name == "posix":
                    _assert_linux_recovery_required(self, service, job["job_id"])
                else:
                    self.assertEqual("failed", failed["state"])
                    self.assertEqual([], list(service.store.creation_jobs_dir.iterdir()))
                    self.assertEqual(
                        [],
                        list(service.store.creation_job_journals_dir.iterdir()),
                    )
            finally:
                service.close()
                service.store.close()

    def test_post_registry_journal_failure_is_publicly_cleanup_recoverable(self) -> None:
        from isoworld.runtime_io import decode_json_object
        from worldforge.studio import creation_jobs as creation_jobs_module

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
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
                job = service.creation_jobs.create_compile(
                    {
                        "job_id": "job_post_registry_failure",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
                    }
                )
                coordinator = service.creation_job_coordinator
                real_append = creation_jobs_module.append_append_only_journal

                def fail_committed_record(*args: object, **kwargs: object):
                    updated = decode_json_object(
                        kwargs["updated_payload"],
                        source="creation job append test",
                    )
                    if updated["phase"] == "committed":
                        raise OSError("simulated post-registry journal interruption")
                    return real_append(*args, **kwargs)

                with patch.object(
                    creation_jobs_module,
                    "append_append_only_journal",
                    side_effect=fail_committed_record,
                ):
                    self.assertEqual(job["job_id"], service.creation_job_coordinator.run_once())

                pending = service.creation_jobs.get(job["job_id"])
                self.assertEqual("succeeded", pending["state"])
                self.assertEqual("cleanup_pending", pending["progress"])
                self.assertTrue(pending["result"]["cleanup_pending"])
                attempt = service.store.connection.execute(
                    "SELECT * FROM creation_job_attempts WHERE job_id = ?",
                    (job["job_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)
                recovered = service.creation_jobs.recover(
                    pending["job_id"],
                    mode="cleanup",
                    expected_generation=pending["generation"],
                    expected_record_hash=pending["record_hash"],
                )
                self.assertEqual("committed", recovered["progress"])
                if sys.platform.startswith("linux") and os.name == "posix":
                    from worldforge.directory_publish import retained_journal_evidence_path

                    retained_stage = coordinator._retained_stage_evidence_path(  # noqa: SLF001
                        service.store.creation_jobs_dir / attempt["stage_locator"],
                        (int(attempt["stage_dev"]), int(attempt["stage_ino"])),
                    )
                    retained_journal = retained_journal_evidence_path(
                        service.store.creation_job_journals_dir / attempt["journal_name"],
                        (int(attempt["journal_dev"]), int(attempt["journal_ino"])),
                    )
                    self.assertTrue(retained_stage.is_dir())
                    self.assertTrue(retained_journal.is_file())
                else:
                    self.assertEqual([], list(service.store.creation_jobs_dir.iterdir()))
                    self.assertEqual(
                        [],
                        list(service.store.creation_job_journals_dir.iterdir()),
                    )
            finally:
                service.close()
                service.store.close()

    def test_private_request_failure_removes_the_empty_identity_bound_stage(self) -> None:
        from worldforge.studio import creation_jobs as creation_jobs_module

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
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
                job = service.creation_jobs.create_compile(
                    {
                        "job_id": "job_request_failure",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
                    }
                )
                with patch.object(
                    creation_jobs_module,
                    "write_private_request",
                    side_effect=OSError("simulated private request interruption"),
                ):
                    self.assertEqual(job["job_id"], service.creation_job_coordinator.run_once())

                failed = service.creation_jobs.get(job["job_id"])
                if sys.platform.startswith("linux") and os.name == "posix":
                    _assert_linux_recovery_required(self, service, job["job_id"])
                else:
                    self.assertEqual("failed", failed["state"])
                    self.assertEqual([], list(service.store.creation_jobs_dir.iterdir()))
                    self.assertEqual(
                        [],
                        list(service.store.creation_job_journals_dir.iterdir()),
                    )
            finally:
                service.close()
                service.store.close()

    def test_initial_journal_registration_failure_removes_exact_stage_and_journal(self) -> None:
        from worldforge.studio import creation_jobs as creation_jobs_module

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
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
                job = service.creation_jobs.create_compile(
                    {
                        "job_id": "job_journal_failure",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
                    }
                )
                real_fsync = creation_jobs_module.fsync_directory

                def fail_journal_parent(path: Path, *, context: str) -> None:
                    if context == "creation job journal parent":
                        raise OSError("simulated journal registration interruption")
                    real_fsync(path, context=context)

                with patch.object(
                    creation_jobs_module,
                    "fsync_directory",
                    side_effect=fail_journal_parent,
                ):
                    self.assertEqual(job["job_id"], service.creation_job_coordinator.run_once())

                failed = service.creation_jobs.get(job["job_id"])
                if sys.platform.startswith("linux") and os.name == "posix":
                    _assert_linux_recovery_required(self, service, job["job_id"])
                else:
                    self.assertEqual("failed", failed["state"])
                    self.assertEqual([], list(service.store.creation_jobs_dir.iterdir()))
                    self.assertEqual(
                        [],
                        list(service.store.creation_job_journals_dir.iterdir()),
                    )
            finally:
                service.close()
                service.store.close()

    def test_scheduler_executes_a_queued_compile_with_its_thread_owned_store(self) -> None:
        from worldforge.studio.creation_jobs import CreationJobScheduler

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            scheduler = CreationJobScheduler(service.store.data_dir, timeout_seconds=20.0)
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
                job = service.creation_jobs.create_compile(
                    {
                        "job_id": "job_scheduled_compile",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
                    }
                )
                scheduler.start()
                scheduler.notify()
                deadline = time.monotonic() + 20.0
                current = service.creation_jobs.get(job["job_id"])
                while not (
                    (current["state"] == "succeeded" and current["progress"] == "committed")
                    or current["state"] in {"failed", "orphaned"}
                ):
                    if time.monotonic() >= deadline:
                        self.fail("Creation job scheduler did not reach a terminal state")
                    time.sleep(0.05)
                    current = service.creation_jobs.get(job["job_id"])
                self.assertEqual("succeeded", current["state"])
                self.assertEqual("committed", current["progress"])
                self.assertEqual(3, len(current["result"]["output_artifact_ids"]))
            finally:
                scheduler.shutdown()
                service.close()
                service.store.close()

    def test_scheduler_setup_failure_signals_ready_and_closes_an_open_store(self) -> None:
        from worldforge.studio import creation_jobs as creation_jobs_module

        scheduler = creation_jobs_module.CreationJobScheduler(Path("unused"))
        store = MagicMock()
        with (
            patch.object(creation_jobs_module, "StudioStore", return_value=store),
            patch.object(
                creation_jobs_module,
                "CreationRootGrantManager",
                side_effect=RuntimeError("simulated setup failure"),
            ),
        ):
            scheduler._run()  # noqa: SLF001
        self.assertTrue(scheduler._ready.is_set())  # noqa: SLF001
        self.assertIsInstance(scheduler._startup_error, RuntimeError)  # noqa: SLF001
        store.close.assert_called_once_with()

    def test_artifact_admission_survives_restart_and_seals_an_integral_candidate(self) -> None:
        from tests.test_studio_creation_evidence_v4 import _register_root
        from worldforge.creation_contracts import load_creation_project
        from worldforge.creation_workflow import initial_creation_workflow_status
        from worldforge.gamepack import build_gamepack
        from worldforge.phase_report_v3 import document_identity
        from worldforge.studio.creation_artifacts import artifact_id_for_identity
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project_root = base / "project"
            shutil.copytree(_PUZZLE_ROOT, project_root)
            project = load_creation_project(project_root / "project.json")
            internal = project_root / ".worldforge"
            history = internal / "artifact_history"
            (internal / "phase_reports").mkdir(parents=True)
            history.mkdir()
            (internal / "status.json").write_bytes(
                canonical_json_bytes(initial_creation_workflow_status(project))
            )
            for document in (
                project.project,
                project.profile,
                project.manifest,
                *project.world_modules,
                *project.activity_modules,
                *project.narrative_modules,
                *project.system_modules,
                *project.logic_modules,
            ):
                (history / f"{document['content_hash']}.json").write_bytes(
                    canonical_json_bytes(document)
                )
            service, workspace = _register_root(base, project_root)
            before = service.creation_evidence.list(
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
            gamepack = build_gamepack(project)
            queued = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 4,
                    "kind": "request",
                    "request_id": "admit_gamepack",
                    "method": "creation_job.create",
                    "params": {
                        "job_id": "job_admit_gamepack",
                        "workspace_id": workspace["workspace_id"],
                        "operation": "artifact.admit",
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": before["artifact_snapshot_hash"],
                        "document": gamepack,
                        "dependency_artifact_ids": [],
                    },
                }
            )["result"]["job"]
            self.assertEqual("queued", queued["state"])
            self.assertNotIn("document", queued)
            public_record = service.store.connection.execute(
                "SELECT record_json FROM creation_jobs WHERE job_id = ?",
                (queued["job_id"],),
            ).fetchone()["record_json"]
            self.assertNotIn('"document"', public_record)
            self.assertNotIn(gamepack["content_hash"], public_record)
            self.assertEqual(
                1,
                service.store.connection.execute(
                    "SELECT COUNT(*) FROM creation_job_payloads WHERE job_id = ?",
                    (queued["job_id"],),
                ).fetchone()[0],
            )
            data_dir = service.store.data_dir
            service.close()
            service.store.close()

            reopened_store = StudioStore(data_dir)
            reopened = StudioService(reopened_store)
            try:
                self.assertEqual("job_admit_gamepack", reopened.creation_job_coordinator.run_once())
                completed = reopened.creation_jobs.get("job_admit_gamepack")
                self.assertEqual("succeeded", completed["state"])
                self.assertEqual("not_applicable", completed["result"]["analysis_status"])
                self.assertEqual(
                    [artifact_id_for_identity(document_identity(gamepack))],
                    completed["result"]["output_artifact_ids"],
                )
                stored = reopened.creation_artifacts.get_document(
                    workspace["workspace_id"], completed["result"]["output_artifact_ids"][0]
                )
                self.assertEqual(gamepack, stored)
            finally:
                reopened.close()
                reopened_store.close()

    def test_compile_job_traverses_fifo_worker_registry_and_candidate_evidence(self) -> None:
        from tests.test_studio_creation_evidence_v4 import _register_root
        from worldforge.creation_contracts import load_creation_project
        from worldforge.creation_workflow import initial_creation_workflow_status
        from worldforge.gamepack import build_gamepack

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project_root = base / "project"
            shutil.copytree(_PUZZLE_ROOT, project_root)
            source_project = load_creation_project(project_root / "project.json")
            internal = project_root / ".worldforge"
            history = internal / "artifact_history"
            (internal / "phase_reports").mkdir(parents=True)
            history.mkdir()
            status = initial_creation_workflow_status(source_project)
            (internal / "status.json").write_bytes(canonical_json_bytes(status))
            for document in (
                source_project.project,
                source_project.profile,
                source_project.manifest,
                *source_project.world_modules,
                *source_project.activity_modules,
                *source_project.narrative_modules,
                *source_project.system_modules,
                *source_project.logic_modules,
            ):
                (history / f"{document['content_hash']}.json").write_bytes(
                    canonical_json_bytes(document)
                )
            service, workspace = _register_root(base, project_root)
            evidence = service.creation_evidence
            jobs = service.creation_jobs
            coordinator = service.creation_job_coordinator
            authority_params = {
                "workspace_id": workspace["workspace_id"],
                "expected_root_generation": workspace["root_generation"],
                "expected_source_revision": workspace["source_revision"],
                "expected_workflow_status_hash": workspace["workflow_status_hash"],
                "expected_artifact_snapshot_hash": None,
                "lifecycle": None,
                "cursor": None,
                "limit": 64,
            }
            before = evidence.list(authority_params)
            queued = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 4,
                    "kind": "request",
                    "request_id": "create_compile",
                    "method": "creation_job.create",
                    "params": {
                        "job_id": "job_compile_puzzle",
                        "operation": "creation.compile",
                        **{
                            key: value
                            for key, value in authority_params.items()
                            if key
                            in {
                                "workspace_id",
                                "expected_root_generation",
                                "expected_source_revision",
                                "expected_workflow_status_hash",
                            }
                        },
                        "expected_artifact_snapshot_hash": before["artifact_snapshot_hash"],
                    },
                }
            )["result"]["job"]
            self.assertEqual("queued", queued["state"])
            self.assertGreaterEqual(len(queued["inputs"]), 4)
            with service.store.connection:
                service.store.connection.execute(
                    "UPDATE creation_job_inputs SET content_hash = ? "
                    "WHERE job_id = ? AND position = 0",
                    (_HASH_C, queued["job_id"]),
                )
            with self.assertRaisesRegex(Exception, "input projection diverged"):
                jobs.get(queued["job_id"])
            with service.store.connection:
                service.store.connection.execute(
                    "UPDATE creation_job_inputs SET content_hash = ? "
                    "WHERE job_id = ? AND position = 0",
                    (queued["inputs"][0]["subject"]["content_hash"], queued["job_id"]),
                )

            with patch.object(
                coordinator,
                "_cleanup_stage",
                side_effect=OSError("simulated cleanup interruption"),
            ):
                self.assertEqual("job_compile_puzzle", coordinator.run_once())
            completed = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 4,
                    "kind": "request",
                    "request_id": "get_compile",
                    "method": "creation_job.get",
                    "params": {"job_id": "job_compile_puzzle"},
                }
            )["result"]["job"]
            self.assertEqual("succeeded", completed["state"])
            self.assertEqual("cleanup_pending", completed["progress"])
            self.assertTrue(completed["result"]["cleanup_pending"])
            self.assertEqual("passed", completed["result"]["analysis_status"])
            self.assertEqual(3, len(completed["result"]["output_artifact_ids"]))
            from isoworld.runtime_io import decode_json_object
            from worldforge.directory_publish import (
                read_append_only_journal_history_state,
            )

            attempt = service.store.connection.execute(
                "SELECT journal_name FROM creation_job_attempts WHERE job_id = ?",
                (completed["job_id"],),
            ).fetchone()
            journal_state = read_append_only_journal_history_state(
                service.store.creation_job_journals_dir / attempt["journal_name"],
                max_record_bytes=256 * 1024,
                max_file_bytes=2 * 1024 * 1024,
            )
            self.assertIsNotNone(journal_state)
            journal_history, _journal_identity, partial_tail = journal_state
            self.assertFalse(partial_tail)
            journal_records = [
                decode_json_object(item, source="creation job test journal")
                for item in journal_history
            ]
            self.assertEqual("cleanup_pending", journal_records[-1]["phase"])
            self.assertEqual(completed["generation"], journal_records[-1]["job_generation"])
            completed = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 4,
                    "kind": "request",
                    "request_id": "recover_cleanup",
                    "method": "creation_job.recover",
                    "params": {
                        "job_id": completed["job_id"],
                        "mode": "cleanup",
                        "expected_generation": completed["generation"],
                        "expected_record_hash": completed["record_hash"],
                    },
                }
            )["result"]["job"]
            self.assertEqual("committed", completed["progress"])
            self.assertFalse(completed["result"]["cleanup_pending"])
            self.assertEqual(
                0,
                service.store.connection.execute(
                    "SELECT COUNT(*) FROM creation_job_attempts WHERE job_id = ?",
                    (completed["job_id"],),
                ).fetchone()[0],
            )

            after = evidence.list(
                {
                    **authority_params,
                    "expected_artifact_snapshot_hash": None,
                    "lifecycle": "candidate",
                }
            )
            self.assertEqual(3, after["counts"]["candidate"])
            self.assertEqual(before["counts"]["active"], after["counts"]["active"])
            self.assertNotEqual(before["artifact_snapshot_hash"], after["artifact_snapshot_hash"])
            inspected = evidence.inspect(
                {
                    "workspace_id": workspace["workspace_id"],
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": after["artifact_snapshot_hash"],
                    "artifact_id": completed["result"]["output_artifact_ids"][0],
                }
            )
            self.assertEqual("candidate", inspected["artifact"]["lifecycle"])
            self.assertNotIn("document", inspected["projection"])
            expected_gamepack = build_gamepack(load_creation_project(project_root / "project.json"))
            self.assertEqual(
                expected_gamepack["content_hash"],
                inspected["artifact"]["subject"]["content_hash"],
            )
            listed = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 4,
                    "kind": "request",
                    "request_id": "list_compile",
                    "method": "creation_job.list",
                    "params": {
                        "workspace_id": workspace["workspace_id"],
                        "state": "succeeded",
                        "after_sequence": 0,
                        "limit": 8,
                    },
                }
            )["result"]
            self.assertEqual(["job_compile_puzzle"], [item["job_id"] for item in listed["jobs"]])
            events = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 4,
                    "kind": "request",
                    "request_id": "list_events",
                    "method": "creation_event.list",
                    "params": {
                        "workspace_id": workspace["workspace_id"],
                        "after_id": 0,
                        "limit": 100,
                    },
                }
            )["result"]["events"]
            self.assertEqual(
                [
                    "creation_job.queued",
                    "creation_job.running",
                    "creation_job.succeeded",
                    "creation_job.cleanup_completed",
                ],
                [event["topic"] for event in events if event["entity_id"] == "job_compile_puzzle"],
            )

            duplicate = jobs.create_compile(
                {
                    "job_id": "job_duplicate_puzzle",
                    "workspace_id": workspace["workspace_id"],
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": after["artifact_snapshot_hash"],
                }
            )
            self.assertEqual(duplicate["job_id"], coordinator.run_once())
            duplicate = jobs.get(duplicate["job_id"])
            if sys.platform.startswith("linux") and os.name == "posix":
                _assert_linux_recovery_required(self, service, duplicate["job_id"])
            else:
                self.assertEqual("failed", duplicate["state"])
                self.assertEqual("invalid_artifact", duplicate["error"]["code"])
                self.assertEqual(
                    0,
                    service.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_job_attempts WHERE job_id = ?",
                        (duplicate["job_id"],),
                    ).fetchone()[0],
                )
                self.assertEqual([], list(service.store.creation_jobs_dir.iterdir()))
                self.assertEqual(
                    [],
                    list(service.store.creation_job_journals_dir.iterdir()),
                )

            next_job = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 4,
                    "kind": "request",
                    "request_id": "create_cancel",
                    "method": "creation_job.create",
                    "params": {
                        "job_id": "job_cancel_puzzle",
                        "operation": "creation.compile",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": after["artifact_snapshot_hash"],
                    },
                }
            )["result"]["job"]
            canceled = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 4,
                    "kind": "request",
                    "request_id": "cancel_compile",
                    "method": "creation_job.cancel",
                    "params": {
                        "job_id": next_job["job_id"],
                        "expected_generation": next_job["generation"],
                        "expected_record_hash": next_job["record_hash"],
                    },
                }
            )["result"]["job"]
            self.assertEqual("canceled", canceled["state"])
            cancel_topics = [
                row["topic"]
                for row in service.store.connection.execute(
                    "SELECT topic FROM creation_events WHERE entity_id = ? ORDER BY event_id",
                    (next_job["job_id"],),
                ).fetchall()
            ]
            self.assertEqual(
                ["creation_job.queued", "creation_job.canceled"],
                cancel_topics,
            )
            self.assertIsNone(coordinator.run_once())
            service.close()
            service.store.close()


if __name__ == "__main__":
    unittest.main()
