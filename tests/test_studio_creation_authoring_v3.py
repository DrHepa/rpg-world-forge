from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import worldforge.studio.creation_authoring as creation_authoring_module
from isoworld.content.publication_journal import journal_frame
from worldforge.creation_contracts import canonical_creation_hash, load_creation_project
from worldforge.creation_scaffold import create_creation_project
from worldforge.directory_publish import (
    read_append_only_journal_history_state,
    retained_journal_evidence_path,
)
from worldforge.file_stat import file_identity, path_file_stat
from worldforge.integrity import canonical_json_bytes
from worldforge.studio.contracts import (
    CREATION_CHANGESET_FORMAT,
    MAX_CHANGE_FILE_BYTES,
    creation_changeset_record_hash,
    validate_studio_creation_changeset,
)
from worldforge.studio.creation_authoring import CreationAuthoringManager
from worldforge.studio.creation_grants import CreationRootGrantManager
from worldforge.studio.creation_workspaces import CreationWorkspaceManager
from worldforge.studio.errors import StudioContractError, StudioError
from worldforge.studio.storage import SCHEMA_VERSION, StudioStore

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_TIMESTAMP = "2026-08-01T00:00:00Z"


def _assert_retired_creation_journal(
    testcase: unittest.TestCase,
    store: StudioStore,
    approved: dict[str, object],
    *,
    expected_phase: str,
    prior_entries: set[Path] | None = None,
) -> set[Path]:
    before = set() if prior_entries is None else set(prior_entries)
    entries = set(store.creation_changeset_journals_dir.iterdir())
    if not (sys.platform.startswith("linux") and os.name == "posix"):
        testcase.assertEqual(before, entries)
        return entries

    retired = entries - before
    testcase.assertEqual(1, len(retired))
    retained = next(iter(retired))
    testcase.assertRegex(
        retained.name,
        r"^\.worldforge-retained-journal-[0-9a-f]{64}\.json$",
    )
    loaded = read_append_only_journal_history_state(
        retained,
        max_record_bytes=creation_authoring_module._MAX_JOURNAL_RECORD_BYTES,  # noqa: SLF001
        max_file_bytes=creation_authoring_module._MAX_JOURNAL_FILE_BYTES,  # noqa: SLF001
    )
    testcase.assertIsNotNone(loaded)
    assert loaded is not None
    history, retained_identity, partial_tail = loaded
    testcase.assertFalse(partial_tail)
    testcase.assertTrue(history)
    retained_info = path_file_stat(retained)
    testcase.assertTrue(stat.S_ISREG(retained_info.st_mode))
    testcase.assertEqual(1, retained_info.st_nlink)
    testcase.assertEqual(retained_identity, file_identity(retained_info))

    documents = [
        creation_authoring_module.decode_json_object(
            payload,
            source="retired creation changeset journal",
        )
        for payload in history
    ]
    for payload, document in zip(history, documents, strict=True):
        testcase.assertEqual(payload, canonical_json_bytes(document))
    final = documents[-1]
    testcase.assertEqual(expected_phase, final["phase"])
    base = CreationAuthoringManager._journal_base_from_document(final)  # noqa: SLF001
    stage_identities = tuple(
        None if operation["stage_identity"] is None else tuple(operation["stage_identity"])
        for operation in final["operations"]
    )
    testcase.assertEqual(
        history,
        creation_authoring_module._expected_journal_history(  # noqa: SLF001
            base,
            through_phase=expected_phase,
            stage_identities=stage_identities,
        ),
    )
    testcase.assertEqual(approved["changeset_id"], base["changeset_id"])
    testcase.assertEqual(approved["workspace_id"], base["workspace_id"])
    testcase.assertEqual(approved["record_hash"], base["approved_record_hash"])
    testcase.assertEqual(approved["review_sha256"], base["review_sha256"])
    testcase.assertEqual(
        approved["expected_root_generation"],
        base["expected_root_generation"],
    )
    testcase.assertEqual(
        approved["expected_source_revision"],
        base["expected_source_revision"],
    )
    testcase.assertEqual(
        approved["proposed_source_revision"],
        base["proposed_source_revision"],
    )
    testcase.assertEqual(
        approved["expected_workflow_status_hash"],
        base["expected_workflow_status_hash"],
    )
    active = store.creation_changeset_journals_dir / (
        f"{base['changeset_id']}.{base['attempt_nonce']}.json"
    )
    testcase.assertFalse(active.exists())
    testcase.assertEqual(
        retained,
        retained_journal_evidence_path(active, retained_identity),
    )
    testcase.assertEqual(before | {retained}, entries)
    return entries


def _changeset_record(
    *,
    operation: str = "replace",
    status: str = "staged",
) -> dict[str, object]:
    base_hash: str | None = _HASH_A
    base_size: int | None = 120
    proposed_hash: str | None = _HASH_B
    proposed_size: int | None = 121
    if operation == "create":
        base_hash = None
        base_size = None
    elif operation == "delete":
        proposed_hash = None
        proposed_size = None
    record: dict[str, object] = {
        "format": CREATION_CHANGESET_FORMAT,
        "format_version": 1,
        "changeset_id": "creation_change_01",
        "workspace_id": "workspace_01",
        "status": status,
        "expected_root_generation": 0,
        "expected_source_revision": _HASH_A,
        "proposed_source_revision": _HASH_B,
        "expected_workflow_status_hash": None,
        "review_sha256": _HASH_A,
        "operations": [
            {
                "operation": operation,
                "path": "project.json",
                "expected_base_file_sha256": base_hash,
                "expected_base_size": base_size,
                "proposed_file_sha256": proposed_hash,
                "proposed_size": proposed_size,
            }
        ],
        "created_at": _TIMESTAMP,
        "updated_at": _TIMESTAMP,
        "record_hash": "",
    }
    record["record_hash"] = creation_changeset_record_hash(record)
    return record


def _portable_path_with_length(index: int, length: int, *, fill: str = "a") -> str:
    if len(fill) != 1:
        raise ValueError("path fill must be one Unicode codepoint")
    prefix = f"source/{index:03d}/"
    suffix = ".json"
    budget = length - len(prefix) - len(suffix)
    if budget < 1:
        raise ValueError("requested path length is too short")
    fill_bytes = len(fill.encode("utf-8"))
    component_capacity = 255 // fill_bytes
    final_component_capacity = (255 - len(suffix.encode("utf-8"))) // fill_bytes
    if component_capacity < 1 or final_component_capacity < 1:
        raise ValueError("path fill does not fit in a portable component")
    components: list[str] = []
    while budget > final_component_capacity:
        component_length = min(component_capacity, budget - 2)
        if component_length < 1:
            raise ValueError("requested path cannot be represented portably")
        components.append(fill * component_length)
        budget -= component_length + 1
    components.append(fill * budget)
    rendered = prefix + "/".join(components) + suffix
    assert len(rendered) == length
    assert all(len(component.encode("utf-8")) <= 255 for component in rendered.split("/"))
    return rendered


class StudioCreationChangesetV3ContractTests(unittest.TestCase):
    def test_creation_changeset_record_is_closed_pathless_and_hash_bound(self) -> None:
        record = _changeset_record()
        self.assertEqual(record, validate_studio_creation_changeset(record))
        self.assertFalse(any("/tmp/" in str(value) for value in record.values()))

        leaked = {**record, "native_root": "/private/project"}
        with self.assertRaises(StudioContractError):
            validate_studio_creation_changeset(leaked)
        tampered = {**record, "status": "approved"}
        with self.assertRaisesRegex(StudioContractError, "record_hash"):
            validate_studio_creation_changeset(tampered)

    def test_creation_changeset_operations_are_exact_for_create_replace_and_delete(self) -> None:
        for operation in ("create", "replace", "delete"):
            with self.subTest(operation=operation):
                record = _changeset_record(operation=operation)
                self.assertEqual(record, validate_studio_creation_changeset(record))

        invalid = _changeset_record(operation="create")
        invalid["operations"][0]["expected_base_file_sha256"] = _HASH_A
        invalid["record_hash"] = creation_changeset_record_hash(invalid)
        with self.assertRaises(StudioContractError):
            validate_studio_creation_changeset(invalid)

    def test_creation_changeset_state_vocabulary_includes_explicit_recovery(self) -> None:
        for status in (
            "staged",
            "approved",
            "applying",
            "applied",
            "rejected",
            "recovery_required",
        ):
            with self.subTest(status=status):
                record = _changeset_record(status=status)
                self.assertEqual(status, validate_studio_creation_changeset(record)["status"])

    def test_creation_changeset_python_bounds_match_the_schema_and_retained_bytes_policy(
        self,
    ) -> None:
        excessive_generation = _changeset_record()
        excessive_generation["expected_root_generation"] = 9_007_199_254_740_992
        excessive_generation["record_hash"] = creation_changeset_record_hash(excessive_generation)
        with self.assertRaisesRegex(StudioContractError, "at most"):
            validate_studio_creation_changeset(excessive_generation)

        excessive_retained = _changeset_record(operation="delete")
        excessive_retained["operations"] = [
            {
                "operation": "delete",
                "path": f"source/module_{index}.json",
                "expected_base_file_sha256": f"{index + 1:064x}",
                "expected_base_size": MAX_CHANGE_FILE_BYTES,
                "proposed_file_sha256": None,
                "proposed_size": None,
            }
            for index in range(5)
        ]
        excessive_retained["record_hash"] = creation_changeset_record_hash(excessive_retained)
        with self.assertRaisesRegex(StudioContractError, "retained bytes"):
            validate_studio_creation_changeset(excessive_retained)

    def test_creation_changeset_path_length_matches_schema_at_1024_codepoints(self) -> None:
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "schemas"
                / "studio-creation-changeset.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(1024, schema["$defs"]["operation"]["properties"]["path"]["maxLength"])

        legal = _changeset_record()
        legal["operations"][0]["path"] = _portable_path_with_length(0, 1024)
        legal["record_hash"] = creation_changeset_record_hash(legal)
        self.assertEqual(legal, validate_studio_creation_changeset(legal))

        excessive = _changeset_record()
        excessive["operations"][0]["path"] = _portable_path_with_length(0, 1025)
        excessive["record_hash"] = creation_changeset_record_hash(excessive)
        with self.assertRaisesRegex(StudioContractError, "1024"):
            validate_studio_creation_changeset(excessive)

    def test_maximum_legal_operation_metadata_fits_the_bounded_journal(self) -> None:
        record = _changeset_record()
        record["changeset_id"] = "c" * 128
        record["workspace_id"] = "w" * 64
        record["operations"] = [
            {
                "operation": "replace",
                "path": _portable_path_with_length(index, 1024, fill="😀"),
                "expected_base_file_sha256": _HASH_A,
                "expected_base_size": 0,
                "proposed_file_sha256": _HASH_B,
                "proposed_size": 0,
            }
            for index in range(256)
        ]
        record["record_hash"] = creation_changeset_record_hash(record)
        validate_studio_creation_changeset(record)

        native_identity = 9_223_372_036_854_775_807
        base = {
            "format": creation_authoring_module._JOURNAL_FORMAT,
            "format_version": creation_authoring_module._JOURNAL_VERSION,
            "attempt_nonce": "d" * 32,
            "changeset_id": record["changeset_id"],
            "workspace_id": record["workspace_id"],
            "approved_record_hash": _HASH_A,
            "applying_record_hash": record["record_hash"],
            "review_sha256": record["review_sha256"],
            "expected_root_generation": record["expected_root_generation"],
            "expected_source_revision": record["expected_source_revision"],
            "proposed_source_revision": record["proposed_source_revision"],
            "expected_workflow_status_hash": record["expected_workflow_status_hash"],
            "root": "C:\\" + "界" * 32_760,
            "root_identity": [native_identity, native_identity],
            "operations": [
                {
                    **operation,
                    "parent_identity": [native_identity, native_identity],
                    "base_identity": [native_identity, native_identity],
                    "stage_name": (
                        f".worldforge-creation-{record['changeset_id']}-{index}-{'e' * 32}.stage"
                    ),
                    "rollback_name": (
                        f".worldforge-creation-{record['changeset_id']}-{index}-{'f' * 32}.rollback"
                    ),
                }
                for index, operation in enumerate(record["operations"])
            ],
        }
        stage_identities = tuple(
            (native_identity, native_identity) for _operation in record["operations"]
        )
        maximum_record_size = 0
        encoded_size = 0
        for index, phase in enumerate(
            creation_authoring_module._journal_phases(len(record["operations"]))
        ):
            payload = creation_authoring_module._journal_payload(
                base,
                phase=phase,
                stage_identities=stage_identities,
            )
            maximum_record_size = max(maximum_record_size, len(payload))
            encoded_size += len(payload) if index == 0 else len(journal_frame(payload))
        self.assertLessEqual(
            maximum_record_size,
            creation_authoring_module._MAX_JOURNAL_RECORD_BYTES,
        )
        self.assertLessEqual(
            encoded_size,
            creation_authoring_module._MAX_JOURNAL_FILE_BYTES,
        )

    def test_additive_v3_storage_has_creation_changeset_and_attempt_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "studio"
            with StudioStore(data_dir) as store:
                tables = {
                    row["name"]
                    for row in store.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertEqual(5, SCHEMA_VERSION)
                self.assertIn("creation_changesets", tables)
                self.assertIn("creation_changeset_attempts", tables)
                operation_columns = {
                    row["name"]
                    for row in store.connection.execute(
                        "PRAGMA table_info(creation_changeset_operations)"
                    )
                }
                self.assertTrue({"base_size", "proposed_size"} <= operation_columns)
            with StudioStore(data_dir) as reopened:
                self.assertEqual(
                    "5",
                    reopened.connection.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()["value"],
                )


def _rehash(document: dict[str, object]) -> dict[str, object]:
    document["content_hash"] = ""
    document["content_hash"] = canonical_creation_hash(document)
    return document


def _replace_operation(root: Path, path: str, document: dict[str, object]) -> dict[str, object]:
    base = (root / path).read_bytes()
    proposed = canonical_json_bytes(document)
    return {
        "operation": "replace",
        "path": path,
        "expected_base_file_sha256": hashlib.sha256(base).hexdigest(),
        "expected_base_size": len(base),
        "proposed_file_sha256": hashlib.sha256(proposed).hexdigest(),
        "proposed_size": len(proposed),
        "document": document,
    }


def _create_operation(path: str, document: dict[str, object]) -> dict[str, object]:
    proposed = canonical_json_bytes(document)
    return {
        "operation": "create",
        "path": path,
        "expected_base_file_sha256": None,
        "expected_base_size": None,
        "proposed_file_sha256": hashlib.sha256(proposed).hexdigest(),
        "proposed_size": len(proposed),
        "document": document,
    }


def _delete_operation(root: Path, path: str) -> dict[str, object]:
    base = (root / path).read_bytes()
    return {
        "operation": "delete",
        "path": path,
        "expected_base_file_sha256": hashlib.sha256(base).hexdigest(),
        "expected_base_size": len(base),
        "proposed_file_sha256": None,
        "proposed_size": None,
    }


def _registered_creation_workspace(
    base: Path,
) -> tuple[StudioStore, CreationWorkspaceManager, dict[str, object], Path]:
    root = base / "project"
    create_creation_project(root, project_id="authoring_project", title="Authoring project")
    loaded = load_creation_project(root / "project.json")
    store = StudioStore(base / "studio")
    grants = CreationRootGrantManager(store)
    grant = grants.create(
        {
            "grant_id": "grant_authoring_project",
            "role": "existing_root",
            "display_name": "Authoring project",
            "path": str(root),
            "expected_project_hash": loaded.project["content_hash"],
        }
    )
    workspaces = CreationWorkspaceManager(store, grants=grants)
    workspace = workspaces.register(
        {
            "workspace_id": "workspace_authoring_project",
            "grant_id": grant["grant_id"],
            "expected_grant_generation": grant["generation"],
            "expected_project_hash": loaded.project["content_hash"],
        }
    )
    return store, workspaces, workspace, root


def _approved_title_change(
    store: StudioStore,
    workspaces: CreationWorkspaceManager,
    workspace: dict[str, object],
    root: Path,
    *,
    changeset_id: str,
    title: str,
    mutation_hook: object = None,
) -> tuple[CreationAuthoringManager, dict[str, object]]:
    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    project["title"] = title
    _rehash(project)
    manager = CreationAuthoringManager(
        store,
        workspaces=workspaces,
        mutation_hook=mutation_hook,
    )
    staged = manager.create(
        {
            "changeset_id": changeset_id,
            "workspace_id": workspace["workspace_id"],
            "expected_root_generation": workspace["root_generation"],
            "expected_source_revision": workspace["source_revision"],
            "expected_workflow_status_hash": workspace["workflow_status_hash"],
            "operations": [_replace_operation(root, "project.json", project)],
        }
    )
    approved = manager.approve(
        staged["changeset_id"],
        expected_record_hash=staged["record_hash"],
        expected_review_sha256=staged["review_sha256"],
    )
    return manager, approved


class _SimulatedCrash(BaseException):
    pass


class StudioCreationAuthoringV3Tests(unittest.TestCase):
    def test_profile_replace_requires_integral_link_updates_before_blob_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
                manifest = json.loads(
                    (root / "source" / "manifest.json").read_text(encoding="utf-8")
                )
                profile["experience"]["player_promise"] = "A reviewed professional promise."
                _rehash(profile)
                project["profile"]["content_hash"] = profile["content_hash"]
                manifest["profile"]["content_hash"] = profile["content_hash"]
                _rehash(manifest)
                project["source_manifest"]["content_hash"] = manifest["content_hash"]
                _rehash(project)
                operations = sorted(
                    [
                        _replace_operation(root, "project.json", project),
                        _replace_operation(root, "profile.json", profile),
                        _replace_operation(root, "source/manifest.json", manifest),
                    ],
                    key=lambda operation: operation["path"].encode("utf-8"),
                )
                manager = CreationAuthoringManager(store, workspaces=workspaces)
                with self.assertRaises(StudioError):
                    manager.create(
                        {
                            "changeset_id": "partial_profile_change",
                            "workspace_id": workspace["workspace_id"],
                            "expected_root_generation": workspace["root_generation"],
                            "expected_source_revision": workspace["source_revision"],
                            "expected_workflow_status_hash": workspace["workflow_status_hash"],
                            "operations": [operations[0]],
                        }
                    )
                self.assertEqual([], list(store.blobs_dir.rglob("*")))

                changeset = manager.create(
                    {
                        "changeset_id": "integral_profile_change",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "operations": operations,
                    }
                )
                self.assertEqual("staged", changeset["status"])
                self.assertNotEqual(
                    changeset["expected_source_revision"],
                    changeset["proposed_source_revision"],
                )
                self.assertEqual(
                    ["profile.json", "project.json", "source/manifest.json"],
                    [operation["path"] for operation in changeset["operations"]],
                )
                self.assertFalse("document" in json.dumps(changeset))
                self.assertEqual(
                    3,
                    store.connection.execute(
                        "SELECT COUNT(*) FROM creation_changeset_operations"
                    ).fetchone()[0],
                )

    def test_diff_and_review_actions_are_bounded_and_double_hash_cas_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                project["title"] = "Reviewed authoring project"
                _rehash(project)
                manager = CreationAuthoringManager(store, workspaces=workspaces)
                changeset = manager.create(
                    {
                        "changeset_id": "reviewed_change",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "operations": [_replace_operation(root, "project.json", project)],
                    }
                )
                diff = manager.diff(changeset["changeset_id"])
                self.assertEqual(changeset["review_sha256"], diff["review_sha256"])
                self.assertNotIn("document", json.dumps(diff))
                with self.assertRaises(StudioError):
                    manager.approve(
                        changeset["changeset_id"],
                        expected_record_hash=_HASH_B,
                        expected_review_sha256=changeset["review_sha256"],
                    )
                approved = manager.approve(
                    changeset["changeset_id"],
                    expected_record_hash=changeset["record_hash"],
                    expected_review_sha256=changeset["review_sha256"],
                )
                self.assertEqual("approved", approved["status"])
                self.assertNotEqual(changeset["record_hash"], approved["record_hash"])

    def test_review_and_load_fail_closed_on_retained_projection_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                project["title"] = "Tamper-resistant review"
                _rehash(project)
                manager = CreationAuthoringManager(store, workspaces=workspaces)
                staged = manager.create(
                    {
                        "changeset_id": "tamper_resistant_change",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "operations": [_replace_operation(root, "project.json", project)],
                    }
                )
                proposed_hash = staged["operations"][0]["proposed_file_sha256"]
                store.blob_path(proposed_hash).write_bytes(b"{}")
                with self.assertRaises(StudioError):
                    manager.diff(staged["changeset_id"])
                with self.assertRaises(StudioError):
                    manager.approve(
                        staged["changeset_id"],
                        expected_record_hash=staged["record_hash"],
                        expected_review_sha256=staged["review_sha256"],
                    )

                store.connection.execute(
                    "UPDATE creation_changeset_operations SET base_size = base_size + 1 "
                    "WHERE changeset_id = ?",
                    (staged["changeset_id"],),
                )
                with self.assertRaisesRegex(StudioError, "projection diverged"):
                    manager.get(staged["changeset_id"])
                store.connection.execute(
                    "UPDATE creation_changeset_operations SET base_size = ? WHERE changeset_id = ?",
                    (
                        staged["operations"][0]["expected_base_size"],
                        staged["changeset_id"],
                    ),
                )
                store.connection.execute(
                    "UPDATE creation_changeset_operations SET operation = 'delete' "
                    "WHERE changeset_id = ?",
                    (staged["changeset_id"],),
                )
                with self.assertRaisesRegex(StudioError, "projection diverged"):
                    manager.get(staged["changeset_id"])

    def test_unpublished_v3_operation_projection_backfills_sizes_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "studio"
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                project["title"] = "Backfilled operation sizes"
                _rehash(project)
                manager = CreationAuthoringManager(store, workspaces=workspaces)
                staged = manager.create(
                    {
                        "changeset_id": "backfilled_operation_sizes",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "operations": [_replace_operation(root, "project.json", project)],
                    }
                )
                store.connection.executescript(
                    """
                    ALTER TABLE creation_changeset_operations
                        RENAME TO creation_changeset_operations_with_sizes;
                    CREATE TABLE creation_changeset_operations (
                        changeset_id TEXT NOT NULL REFERENCES creation_changesets(changeset_id)
                            ON DELETE CASCADE,
                        path TEXT NOT NULL,
                        operation TEXT NOT NULL,
                        base_blob_sha256 TEXT,
                        proposed_blob_sha256 TEXT,
                        PRIMARY KEY (changeset_id, path)
                    );
                    INSERT INTO creation_changeset_operations
                        (changeset_id, path, operation, base_blob_sha256,
                         proposed_blob_sha256)
                    SELECT changeset_id, path, operation, base_blob_sha256,
                           proposed_blob_sha256
                    FROM creation_changeset_operations_with_sizes;
                    DROP TABLE creation_changeset_operations_with_sizes;
                    """
                )

            with self.assertRaisesRegex(StudioError, "required columns") as raised:
                StudioStore(data_dir, mode="secondary")
            self.assertEqual("invalid_state", raised.exception.code)
            with sqlite3.connect(data_dir / "studio.sqlite3") as raw:
                old_columns = {
                    row[1]
                    for row in raw.execute("PRAGMA table_info(creation_changeset_operations)")
                }
            self.assertFalse({"base_size", "proposed_size"} <= old_columns)

            with StudioStore(data_dir) as reopened:
                loaded = CreationAuthoringManager(reopened).get(staged["changeset_id"])
                projection = reopened.connection.execute(
                    "SELECT base_size, proposed_size FROM creation_changeset_operations "
                    "WHERE changeset_id = ?",
                    (staged["changeset_id"],),
                ).fetchone()
                self.assertEqual(
                    (
                        loaded["operations"][0]["expected_base_size"],
                        loaded["operations"][0]["proposed_size"],
                    ),
                    (projection["base_size"], projection["proposed_size"]),
                )
            with StudioStore(data_dir, mode="secondary") as secondary:
                self.assertEqual(
                    staged["changeset_id"],
                    CreationAuthoringManager(secondary).get(staged["changeset_id"])["changeset_id"],
                )

    def test_staging_rejects_a_graph_mutated_after_its_integral_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                proposed = json.loads((root / "project.json").read_text(encoding="utf-8"))
                proposed["title"] = "Proposed title"
                _rehash(proposed)

                def mutate(phase: str, _details: dict[str, object]) -> None:
                    if phase != "staging_snapshot_captured":
                        return
                    changed = json.loads((root / "project.json").read_text(encoding="utf-8"))
                    changed["title"] = "Concurrent title"
                    _rehash(changed)
                    (root / "project.json").write_bytes(canonical_json_bytes(changed))

                manager = CreationAuthoringManager(
                    store,
                    workspaces=workspaces,
                    mutation_hook=mutate,
                )
                with self.assertRaisesRegex(StudioError, "changed while staging"):
                    manager.create(
                        {
                            "changeset_id": "torn_snapshot_change",
                            "workspace_id": workspace["workspace_id"],
                            "expected_root_generation": workspace["root_generation"],
                            "expected_source_revision": workspace["source_revision"],
                            "expected_workflow_status_hash": workspace["workflow_status_hash"],
                            "operations": [_replace_operation(root, "project.json", proposed)],
                        }
                    )
                self.assertEqual(
                    0,
                    store.connection.execute("SELECT COUNT(*) FROM creation_changesets").fetchone()[
                        0
                    ],
                )

    def test_apply_recensuses_the_exact_published_creation_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                project["title"] = "Applied authoring project"
                _rehash(project)
                manager = CreationAuthoringManager(store, workspaces=workspaces)
                staged = manager.create(
                    {
                        "changeset_id": "applied_change",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "operations": [_replace_operation(root, "project.json", project)],
                    }
                )
                approved = manager.approve(
                    staged["changeset_id"],
                    expected_record_hash=staged["record_hash"],
                    expected_review_sha256=staged["review_sha256"],
                )

                result = manager.apply(
                    approved["changeset_id"],
                    expected_record_hash=approved["record_hash"],
                    expected_review_sha256=approved["review_sha256"],
                    expected_root_generation=approved["expected_root_generation"],
                )

                self.assertEqual("applied", result["changeset"]["status"])
                self.assertEqual(
                    approved["proposed_source_revision"],
                    result["workspace"]["source_revision"],
                )
                self.assertEqual(
                    "Applied authoring project",
                    load_creation_project(root / "project.json").project["title"],
                )
                _assert_retired_creation_journal(
                    self,
                    store,
                    approved,
                    expected_phase="database_committed",
                )
                self.assertEqual(
                    0,
                    store.connection.execute(
                        "SELECT COUNT(*) FROM creation_changeset_attempts"
                    ).fetchone()[0],
                )

    def test_crash_after_files_commit_is_targeted_and_idempotently_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                project["title"] = "Recovered authoring project"
                _rehash(project)

                def crash(phase: str, _details: dict[str, object]) -> None:
                    if phase == "files_committed":
                        raise _SimulatedCrash

                manager = CreationAuthoringManager(
                    store,
                    workspaces=workspaces,
                    mutation_hook=crash,
                )
                staged = manager.create(
                    {
                        "changeset_id": "recovered_change",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "operations": [_replace_operation(root, "project.json", project)],
                    }
                )
                approved = manager.approve(
                    staged["changeset_id"],
                    expected_record_hash=staged["record_hash"],
                    expected_review_sha256=staged["review_sha256"],
                )
                with self.assertRaises(_SimulatedCrash):
                    manager.apply(
                        approved["changeset_id"],
                        expected_record_hash=approved["record_hash"],
                        expected_review_sha256=approved["review_sha256"],
                        expected_root_generation=approved["expected_root_generation"],
                    )

                recovering = CreationAuthoringManager(store, workspaces=workspaces)
                pending = recovering.get(approved["changeset_id"])
                recovered = recovering.recover(
                    pending["changeset_id"],
                    mode="resume",
                    expected_record_hash=pending["record_hash"],
                    expected_review_sha256=pending["review_sha256"],
                    expected_root_generation=pending["expected_root_generation"],
                )
                self.assertEqual("committed", recovered["outcome"])
                self.assertEqual("applied", recovered["changeset"]["status"])
                self.assertEqual(
                    "Recovered authoring project",
                    load_creation_project(root / "project.json").project["title"],
                )

                repeated = recovering.recover(
                    pending["changeset_id"],
                    mode="resume",
                    expected_record_hash=recovered["changeset"]["record_hash"],
                    expected_review_sha256=pending["review_sha256"],
                    expected_root_generation=recovered["workspace"]["root_generation"],
                )
                self.assertEqual("not_needed", repeated["outcome"])

    def test_partial_multi_file_crash_rolls_back_only_owned_identities(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                paths = ("profile.json", "project.json", "source/manifest.json")
                original = {path: (root / path).read_bytes() for path in paths}
                profile = json.loads(original["profile.json"])
                project = json.loads(original["project.json"])
                manifest = json.loads(original["source/manifest.json"])
                profile["experience"]["player_promise"] = "Crash-safe promise."
                _rehash(profile)
                manifest["profile"]["content_hash"] = profile["content_hash"]
                _rehash(manifest)
                project["profile"]["content_hash"] = profile["content_hash"]
                project["source_manifest"]["content_hash"] = manifest["content_hash"]
                _rehash(project)
                operations = sorted(
                    [
                        _replace_operation(root, "profile.json", profile),
                        _replace_operation(root, "project.json", project),
                        _replace_operation(root, "source/manifest.json", manifest),
                    ],
                    key=lambda operation: operation["path"].encode("utf-8"),
                )

                def crash(phase: str, details: dict[str, object]) -> None:
                    if phase == "operation_applied" and details["operation_index"] == 0:
                        raise _SimulatedCrash

                manager = CreationAuthoringManager(
                    store,
                    workspaces=workspaces,
                    mutation_hook=crash,
                )
                staged = manager.create(
                    {
                        "changeset_id": "partial_crash_change",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "operations": operations,
                    }
                )
                approved = manager.approve(
                    staged["changeset_id"],
                    expected_record_hash=staged["record_hash"],
                    expected_review_sha256=staged["review_sha256"],
                )
                with self.assertRaises(_SimulatedCrash):
                    manager.apply(
                        approved["changeset_id"],
                        expected_record_hash=approved["record_hash"],
                        expected_review_sha256=approved["review_sha256"],
                        expected_root_generation=approved["expected_root_generation"],
                    )

                recovering = CreationAuthoringManager(store, workspaces=workspaces)
                pending = recovering.get(approved["changeset_id"])
                result = recovering.recover(
                    pending["changeset_id"],
                    mode="rollback",
                    expected_record_hash=pending["record_hash"],
                    expected_review_sha256=pending["review_sha256"],
                    expected_root_generation=pending["expected_root_generation"],
                )
                self.assertEqual("rolled_back", result["outcome"])
                self.assertEqual("approved", result["changeset"]["status"])
                self.assertEqual(original, {path: (root / path).read_bytes() for path in paths})
                _assert_retired_creation_journal(
                    self,
                    store,
                    approved,
                    expected_phase="operation_1_committed",
                )

    def test_cleanup_failure_after_database_commit_remains_successful_and_recoverable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                project["title"] = "Committed despite cleanup interruption"
                _rehash(project)
                manager = CreationAuthoringManager(store, workspaces=workspaces)
                staged = manager.create(
                    {
                        "changeset_id": "cleanup_pending_change",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "operations": [_replace_operation(root, "project.json", project)],
                    }
                )
                approved = manager.approve(
                    staged["changeset_id"],
                    expected_record_hash=staged["record_hash"],
                    expected_review_sha256=staged["review_sha256"],
                )
                with patch(
                    "worldforge.studio.creation_authoring.remove_append_only_journal",
                    side_effect=OSError("cleanup interrupted"),
                ):
                    result = manager.apply(
                        approved["changeset_id"],
                        expected_record_hash=approved["record_hash"],
                        expected_review_sha256=approved["review_sha256"],
                        expected_root_generation=approved["expected_root_generation"],
                    )
                self.assertEqual("applied", result["changeset"]["status"])
                self.assertEqual(
                    1,
                    store.connection.execute(
                        "SELECT COUNT(*) FROM creation_changeset_attempts"
                    ).fetchone()[0],
                )

                recovered = manager.recover(
                    result["changeset"]["changeset_id"],
                    mode="resume",
                    expected_record_hash=result["changeset"]["record_hash"],
                    expected_review_sha256=result["changeset"]["review_sha256"],
                    expected_root_generation=result["workspace"]["root_generation"],
                )
                self.assertEqual("committed", recovered["outcome"])
                _assert_retired_creation_journal(
                    self,
                    store,
                    approved,
                    expected_phase="database_committed",
                )

    def test_replace_crash_after_rollback_link_converges_for_resume_and_rollback(self) -> None:
        for mode in ("resume", "rollback"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
                with store:
                    original = (root / "project.json").read_bytes()
                    project = json.loads(original)
                    project["title"] = f"Recovered through {mode}"
                    _rehash(project)

                    def crash(phase: str, _details: dict[str, object]) -> None:
                        if phase == "rollback_linked":
                            raise _SimulatedCrash

                    manager = CreationAuthoringManager(
                        store,
                        workspaces=workspaces,
                        mutation_hook=crash,
                    )
                    staged = manager.create(
                        {
                            "changeset_id": f"linked_{mode}_change",
                            "workspace_id": workspace["workspace_id"],
                            "expected_root_generation": workspace["root_generation"],
                            "expected_source_revision": workspace["source_revision"],
                            "expected_workflow_status_hash": workspace["workflow_status_hash"],
                            "operations": [_replace_operation(root, "project.json", project)],
                        }
                    )
                    approved = manager.approve(
                        staged["changeset_id"],
                        expected_record_hash=staged["record_hash"],
                        expected_review_sha256=staged["review_sha256"],
                    )
                    with self.assertRaises(_SimulatedCrash):
                        manager.apply(
                            approved["changeset_id"],
                            expected_record_hash=approved["record_hash"],
                            expected_review_sha256=approved["review_sha256"],
                            expected_root_generation=approved["expected_root_generation"],
                        )

                    recovering = CreationAuthoringManager(store, workspaces=workspaces)
                    pending = recovering.get(approved["changeset_id"])
                    result = recovering.recover(
                        pending["changeset_id"],
                        mode=mode,
                        expected_record_hash=pending["record_hash"],
                        expected_review_sha256=pending["review_sha256"],
                        expected_root_generation=pending["expected_root_generation"],
                    )
                    if mode == "resume":
                        self.assertEqual("committed", result["outcome"])
                        self.assertEqual(
                            f"Recovered through {mode}",
                            load_creation_project(root / "project.json").project["title"],
                        )
                    else:
                        self.assertEqual("rolled_back", result["outcome"])
                        self.assertEqual(original, (root / "project.json").read_bytes())

    def test_blob_link_crash_is_reconciled_without_poisoning_the_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                project["title"] = "Blob link recovery"
                _rehash(project)
                operation = _replace_operation(root, "project.json", project)

                def crash(phase: str, _details: dict[str, object]) -> None:
                    if phase == "blob_linked":
                        raise _SimulatedCrash

                crashing = CreationAuthoringManager(
                    store,
                    workspaces=workspaces,
                    mutation_hook=crash,
                )
                params = {
                    "changeset_id": "blob_link_recovery_change",
                    "workspace_id": workspace["workspace_id"],
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "operations": [operation],
                }
                with self.assertRaises(_SimulatedCrash):
                    crashing.create(params)
                self.assertTrue(any(store.blobs_dir.rglob("*.tmp")))

                staged = CreationAuthoringManager(store, workspaces=workspaces).create(params)
                self.assertEqual("staged", staged["status"])
                self.assertEqual([], list(store.blobs_dir.rglob("*.tmp")))
                for blob in store.blobs_dir.glob("*/*"):
                    self.assertEqual(1, blob.stat().st_nlink)

    def test_concurrent_identical_blob_publishers_both_converge(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp) / "studio"
            payload = b'{"concurrent":"blob"}\n'
            digest = hashlib.sha256(payload).hexdigest()
            with StudioStore(data_dir) as winner_store, StudioStore(data_dir) as loser_store:
                staged = threading.Barrier(2)
                winner_linked = threading.Event()
                loser_done = threading.Event()
                errors: list[BaseException] = []

                def winner_hook(phase: str, _details: dict[str, object]) -> None:
                    if phase == "blob_staged":
                        staged.wait(timeout=5)
                    elif phase == "blob_linked":
                        winner_linked.set()
                        if not loser_done.wait(timeout=5):
                            raise RuntimeError("loser did not reconcile the linked blob")

                def loser_hook(phase: str, _details: dict[str, object]) -> None:
                    if phase == "blob_staged":
                        staged.wait(timeout=5)
                        if not winner_linked.wait(timeout=5):
                            raise RuntimeError("winner did not publish the blob link")

                winner = CreationAuthoringManager(winner_store, mutation_hook=winner_hook)
                loser = CreationAuthoringManager(loser_store, mutation_hook=loser_hook)

                def publish(manager: CreationAuthoringManager, *, is_loser: bool) -> None:
                    try:
                        manager._store_blob(payload, digest)
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        if is_loser:
                            loser_done.set()

                winner_thread = threading.Thread(
                    target=publish,
                    kwargs={"manager": winner, "is_loser": False},
                )
                loser_thread = threading.Thread(
                    target=publish,
                    kwargs={"manager": loser, "is_loser": True},
                )
                winner_thread.start()
                loser_thread.start()
                winner_thread.join(timeout=10)
                loser_thread.join(timeout=10)

                self.assertFalse(winner_thread.is_alive())
                self.assertFalse(loser_thread.is_alive())
                self.assertEqual([], errors)
                target = winner_store.blob_path(digest)
                self.assertEqual(payload, target.read_bytes())
                self.assertEqual(1, target.stat().st_nlink)
                self.assertEqual([], list(target.parent.glob("*.tmp")))

    def test_blob_convergence_requires_a_successful_post_link_directory_flush(self) -> None:
        def run_case(persistent_failure: bool) -> None:
            with tempfile.TemporaryDirectory() as temp:
                with StudioStore(Path(temp) / "studio") as store:
                    payload = b'{"durable":"blob"}\n'
                    digest = hashlib.sha256(payload).hexdigest()
                    armed = False
                    failed_flushes = 0
                    successful_retry_flushes = 0
                    original_flush = creation_authoring_module._PinnedParent.flush

                    def hook(phase: str, _details: dict[str, object]) -> None:
                        nonlocal armed
                        if phase == "blob_linked":
                            armed = True

                    def controlled_flush(parent: object) -> None:
                        nonlocal failed_flushes, successful_retry_flushes
                        if armed and (persistent_failure or failed_flushes == 0):
                            failed_flushes += 1
                            raise OSError("interrupted directory flush")
                        if armed:
                            successful_retry_flushes += 1
                        original_flush(parent)

                    manager = CreationAuthoringManager(store, mutation_hook=hook)
                    with patch.object(
                        creation_authoring_module._PinnedParent,
                        "flush",
                        new=controlled_flush,
                    ):
                        if persistent_failure:
                            with self.assertRaisesRegex(StudioError, "durably converge"):
                                manager._store_blob(payload, digest)
                        else:
                            manager._store_blob(payload, digest)
                    self.assertGreaterEqual(failed_flushes, 1)
                    if persistent_failure:
                        self.assertEqual(0, successful_retry_flushes)
                    else:
                        self.assertGreaterEqual(successful_retry_flushes, 1)
                        target = store.blob_path(digest)
                        self.assertEqual(payload, target.read_bytes())
                        self.assertEqual(1, target.stat().st_nlink)

        for persistent_failure in (False, True):
            with self.subTest(persistent_failure=persistent_failure):
                run_case(persistent_failure)

    def test_blob_retry_flushes_an_exact_target_after_prior_durability_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with StudioStore(Path(temp) / "studio") as store:
                payload = b'{"durable":"retry"}\n'
                digest = hashlib.sha256(payload).hexdigest()
                armed = False
                failures_remaining = 2
                successful_flushes = 0
                original_flush = creation_authoring_module._PinnedParent.flush

                def hook(phase: str, _details: dict[str, object]) -> None:
                    nonlocal armed
                    if phase == "blob_linked":
                        armed = True

                def controlled_flush(parent: object) -> None:
                    nonlocal failures_remaining, successful_flushes
                    if armed and failures_remaining:
                        failures_remaining -= 1
                        raise OSError("interrupted directory flush")
                    if armed:
                        successful_flushes += 1
                    original_flush(parent)

                manager = CreationAuthoringManager(store, mutation_hook=hook)
                with patch.object(
                    creation_authoring_module._PinnedParent,
                    "flush",
                    new=controlled_flush,
                ):
                    with self.assertRaisesRegex(StudioError, "durably converge"):
                        manager._store_blob(payload, digest)
                    self.assertEqual(0, failures_remaining)
                    self.assertEqual(0, successful_flushes)
                    manager._store_blob(payload, digest)

                self.assertGreaterEqual(successful_flushes, 1)
                target = store.blob_path(digest)
                self.assertEqual(payload, target.read_bytes())
                self.assertEqual(1, target.stat().st_nlink)

    def test_two_blob_crashes_ignore_standalone_orphans_and_reconcile_the_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                project["title"] = "Repeated blob crash recovery"
                _rehash(project)
                params = {
                    "changeset_id": "repeated_blob_crash_change",
                    "workspace_id": workspace["workspace_id"],
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "operations": [_replace_operation(root, "project.json", project)],
                }

                def crash_staged(phase: str, _details: dict[str, object]) -> None:
                    if phase == "blob_staged":
                        raise _SimulatedCrash

                with self.assertRaises(_SimulatedCrash):
                    CreationAuthoringManager(
                        store,
                        workspaces=workspaces,
                        mutation_hook=crash_staged,
                    ).create(params)
                standalone_orphans = list(store.blobs_dir.rglob("*.tmp"))
                self.assertEqual(1, len(standalone_orphans))
                self.assertEqual(1, standalone_orphans[0].stat().st_nlink)

                def crash_linked(phase: str, _details: dict[str, object]) -> None:
                    if phase == "blob_linked":
                        raise _SimulatedCrash

                with self.assertRaises(_SimulatedCrash):
                    CreationAuthoringManager(
                        store,
                        workspaces=workspaces,
                        mutation_hook=crash_linked,
                    ).create(params)
                self.assertEqual(2, len(list(store.blobs_dir.rglob("*.tmp"))))

                staged = CreationAuthoringManager(store, workspaces=workspaces).create(params)
                self.assertEqual("staged", staged["status"])
                ignored_orphans = list(store.blobs_dir.rglob("*.tmp"))
                self.assertEqual(1, len(ignored_orphans))
                self.assertEqual(1, ignored_orphans[0].stat().st_nlink)
                for blob in store.blobs_dir.glob("*/*"):
                    if blob.suffix == ".tmp":
                        continue
                    self.assertEqual(1, blob.stat().st_nlink)

    def test_blob_shard_symlink_is_rejected_without_writing_through_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                project["title"] = "Unsafe blob shard"
                _rehash(project)
                operation = _replace_operation(root, "project.json", project)
                base_digest = operation["expected_base_file_sha256"]
                attacker = Path(temp) / "attacker"
                attacker.mkdir()
                shard = store.blobs_dir / str(base_digest)[:2]
                try:
                    shard.symlink_to(attacker, target_is_directory=True)
                except OSError as exc:
                    self.skipTest(f"Directory symlinks are unavailable: {exc}")

                with self.assertRaises(StudioError):
                    CreationAuthoringManager(store, workspaces=workspaces).create(
                        {
                            "changeset_id": "unsafe_blob_shard_change",
                            "workspace_id": workspace["workspace_id"],
                            "expected_root_generation": workspace["root_generation"],
                            "expected_source_revision": workspace["source_revision"],
                            "expected_workflow_status_hash": workspace["workflow_status_hash"],
                            "operations": [operation],
                        }
                    )
                self.assertEqual([], list(attacker.iterdir()))

    def test_journal_is_published_before_atomic_claim_without_unbound_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:

                def crash(phase: str, _details: dict[str, object]) -> None:
                    if phase == "journal_created_unbound":
                        raise _SimulatedCrash

                manager, approved = _approved_title_change(
                    store,
                    workspaces,
                    workspace,
                    root,
                    changeset_id="journal_claim_gap_change",
                    title="Journal claim gap",
                    mutation_hook=crash,
                )
                with self.assertRaises(_SimulatedCrash):
                    manager.apply(
                        approved["changeset_id"],
                        expected_record_hash=approved["record_hash"],
                        expected_review_sha256=approved["review_sha256"],
                        expected_root_generation=approved["expected_root_generation"],
                    )
                self.assertEqual(
                    "approved",
                    CreationAuthoringManager(store, workspaces=workspaces).get(
                        approved["changeset_id"]
                    )["status"],
                )
                self.assertEqual(
                    0,
                    store.connection.execute(
                        "SELECT COUNT(*) FROM creation_changeset_attempts"
                    ).fetchone()[0],
                )
                orphan_entries = set(store.creation_changeset_journals_dir.iterdir())
                self.assertEqual(1, len(orphan_entries))

                applied = CreationAuthoringManager(store, workspaces=workspaces).apply(
                    approved["changeset_id"],
                    expected_record_hash=approved["record_hash"],
                    expected_review_sha256=approved["review_sha256"],
                    expected_root_generation=approved["expected_root_generation"],
                )
                self.assertEqual("applied", applied["changeset"]["status"])
                retired_entries = _assert_retired_creation_journal(
                    self,
                    store,
                    approved,
                    expected_phase="database_committed",
                    prior_entries=orphan_entries,
                )
                self.assertEqual(2, len(retired_entries))
                self.assertLessEqual(orphan_entries, retired_entries)

    def test_apply_reports_supported_recovery_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                manager, approved = _approved_title_change(
                    store,
                    workspaces,
                    workspace,
                    root,
                    changeset_id="supported_recovery_error_change",
                    title="Supported recovery error",
                )
                with (
                    patch.object(
                        manager,
                        "_prepare_stages",
                        side_effect=RuntimeError("staging failed"),
                    ),
                    patch.object(
                        manager,
                        "_rollback_journal",
                        side_effect=RuntimeError("rollback failed"),
                    ),
                    self.assertRaises(StudioError) as raised,
                ):
                    manager.apply(
                        approved["changeset_id"],
                        expected_record_hash=approved["record_hash"],
                        expected_review_sha256=approved["review_sha256"],
                        expected_root_generation=approved["expected_root_generation"],
                    )
                self.assertEqual("recovery_failed", raised.exception.code)

    def test_pending_recovery_generation_cas_rejects_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:

                def crash(phase: str, _details: dict[str, object]) -> None:
                    if phase == "files_committed":
                        raise _SimulatedCrash

                manager, approved = _approved_title_change(
                    store,
                    workspaces,
                    workspace,
                    root,
                    changeset_id="stale_recovery_generation_change",
                    title="Stale recovery generation",
                    mutation_hook=crash,
                )
                with self.assertRaises(_SimulatedCrash):
                    manager.apply(
                        approved["changeset_id"],
                        expected_record_hash=approved["record_hash"],
                        expected_review_sha256=approved["review_sha256"],
                        expected_root_generation=approved["expected_root_generation"],
                    )
                recovering = CreationAuthoringManager(store, workspaces=workspaces)
                pending = recovering.get(approved["changeset_id"])
                attempt_before = tuple(
                    store.connection.execute(
                        "SELECT * FROM creation_changeset_attempts WHERE changeset_id = ?",
                        (pending["changeset_id"],),
                    ).fetchone()
                )
                files_before = {
                    path: (root / path).read_bytes()
                    for path in ("project.json", "profile.json", "source/manifest.json")
                }
                journal_path = next(store.creation_changeset_journals_dir.iterdir())
                journal_before = journal_path.read_bytes()

                with self.assertRaisesRegex(StudioError, "generation changed"):
                    recovering.recover(
                        pending["changeset_id"],
                        mode="resume",
                        expected_record_hash=pending["record_hash"],
                        expected_review_sha256=pending["review_sha256"],
                        expected_root_generation=pending["expected_root_generation"] + 1,
                    )
                self.assertEqual(
                    attempt_before,
                    tuple(
                        store.connection.execute(
                            "SELECT * FROM creation_changeset_attempts WHERE changeset_id = ?",
                            (pending["changeset_id"],),
                        ).fetchone()
                    ),
                )
                self.assertEqual(journal_before, journal_path.read_bytes())
                self.assertEqual(
                    files_before,
                    {path: (root / path).read_bytes() for path in files_before},
                )

    def test_corrupt_attempt_journal_basename_is_rejected_before_dereference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:

                def crash(phase: str, _details: dict[str, object]) -> None:
                    if phase == "files_committed":
                        raise _SimulatedCrash

                manager, approved = _approved_title_change(
                    store,
                    workspaces,
                    workspace,
                    root,
                    changeset_id="journal_traversal_change",
                    title="Journal traversal",
                    mutation_hook=crash,
                )
                with self.assertRaises(_SimulatedCrash):
                    manager.apply(
                        approved["changeset_id"],
                        expected_record_hash=approved["record_hash"],
                        expected_review_sha256=approved["review_sha256"],
                        expected_root_generation=approved["expected_root_generation"],
                    )
                pending = CreationAuthoringManager(store, workspaces=workspaces).get(
                    approved["changeset_id"]
                )
                outside = store.journals_dir / "outside.json"
                outside.write_bytes(b"do-not-read")
                store.connection.execute(
                    "UPDATE creation_changeset_attempts SET journal_name = '../outside.json' "
                    "WHERE changeset_id = ?",
                    (pending["changeset_id"],),
                )
                with self.assertRaisesRegex(StudioError, "journal name"):
                    CreationAuthoringManager(store, workspaces=workspaces).recover(
                        pending["changeset_id"],
                        mode="resume",
                        expected_record_hash=pending["record_hash"],
                        expected_review_sha256=pending["review_sha256"],
                        expected_root_generation=pending["expected_root_generation"],
                    )
                self.assertEqual(b"do-not-read", outside.read_bytes())

    def test_exact_partial_journal_frames_repair_at_every_transition_boundary(self) -> None:
        phases = (
            "stages_prepared",
            "operation_1_committed",
            "files_committed",
            "database_committed",
        )
        for torn_phase in phases:
            with self.subTest(torn_phase=torn_phase), tempfile.TemporaryDirectory() as temp:
                store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
                with store:
                    manager, approved = _approved_title_change(
                        store,
                        workspaces,
                        workspace,
                        root,
                        changeset_id=f"partial_{torn_phase}_change",
                        title=f"Recovered {torn_phase}",
                    )
                    original_append = creation_authoring_module.append_append_only_journal
                    torn = False

                    def append_with_torn_frame(
                        path: Path,
                        *,
                        _torn_phase: str = torn_phase,
                        _original_append: object = original_append,
                        **kwargs: object,
                    ) -> object:
                        nonlocal torn
                        document = json.loads(bytes(kwargs["updated_payload"]))
                        if not torn and document["phase"] == _torn_phase:
                            frame = journal_frame(bytes(kwargs["updated_payload"]))
                            with path.open("ab") as output:
                                output.write(frame[: max(1, len(frame) // 2)])
                                output.flush()
                                os.fsync(output.fileno())
                            torn = True
                            raise _SimulatedCrash
                        return _original_append(path, **kwargs)

                    with (
                        patch(
                            "worldforge.studio.creation_authoring.append_append_only_journal",
                            side_effect=append_with_torn_frame,
                        ),
                        self.assertRaises(_SimulatedCrash),
                    ):
                        manager.apply(
                            approved["changeset_id"],
                            expected_record_hash=approved["record_hash"],
                            expected_review_sha256=approved["review_sha256"],
                            expected_root_generation=approved["expected_root_generation"],
                        )
                    self.assertTrue(torn)

                    recovering = CreationAuthoringManager(store, workspaces=workspaces)
                    pending = recovering.get(approved["changeset_id"])
                    current_workspace = workspaces.get(workspace["workspace_id"])
                    expected_generation = (
                        current_workspace["root_generation"]
                        if pending["status"] == "applied"
                        else pending["expected_root_generation"]
                    )
                    recovered = recovering.recover(
                        pending["changeset_id"],
                        mode="resume",
                        expected_record_hash=pending["record_hash"],
                        expected_review_sha256=pending["review_sha256"],
                        expected_root_generation=expected_generation,
                    )
                    self.assertEqual("committed", recovered["outcome"])
                    self.assertEqual("applied", recovered["changeset"]["status"])
                    self.assertEqual(
                        f"Recovered {torn_phase}",
                        load_creation_project(root / "project.json").project["title"],
                    )

    def test_replace_publish_crash_windows_resume_exact_proposed_identity(self) -> None:
        for crash_phase in ("stage_linked", "operation_published"):
            with self.subTest(crash_phase=crash_phase), tempfile.TemporaryDirectory() as temp:
                store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
                with store:
                    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                    project["title"] = f"Recovered after {crash_phase}"
                    _rehash(project)

                    def crash(
                        phase: str,
                        _details: dict[str, object],
                        expected_phase: str = crash_phase,
                    ) -> None:
                        if phase == expected_phase:
                            raise _SimulatedCrash

                    manager = CreationAuthoringManager(
                        store,
                        workspaces=workspaces,
                        mutation_hook=crash,
                    )
                    staged = manager.create(
                        {
                            "changeset_id": f"{crash_phase}_change",
                            "workspace_id": workspace["workspace_id"],
                            "expected_root_generation": workspace["root_generation"],
                            "expected_source_revision": workspace["source_revision"],
                            "expected_workflow_status_hash": workspace["workflow_status_hash"],
                            "operations": [_replace_operation(root, "project.json", project)],
                        }
                    )
                    approved = manager.approve(
                        staged["changeset_id"],
                        expected_record_hash=staged["record_hash"],
                        expected_review_sha256=staged["review_sha256"],
                    )
                    with self.assertRaises(_SimulatedCrash):
                        manager.apply(
                            approved["changeset_id"],
                            expected_record_hash=approved["record_hash"],
                            expected_review_sha256=approved["review_sha256"],
                            expected_root_generation=approved["expected_root_generation"],
                        )
                    recovering = CreationAuthoringManager(store, workspaces=workspaces)
                    pending = recovering.get(approved["changeset_id"])
                    recovered = recovering.recover(
                        pending["changeset_id"],
                        mode="resume",
                        expected_record_hash=pending["record_hash"],
                        expected_review_sha256=pending["review_sha256"],
                        expected_root_generation=pending["expected_root_generation"],
                    )
                    self.assertEqual("committed", recovered["outcome"])
                    self.assertEqual(
                        f"Recovered after {crash_phase}",
                        load_creation_project(root / "project.json").project["title"],
                    )

    def test_module_create_and_delete_require_manifest_project_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, workspaces, workspace, root = _registered_creation_workspace(Path(temp))
            with store:
                module = _rehash(
                    {
                        "format": "world-forge.system_module",
                        "format_version": 1,
                        "module_id": "auxiliary_rules",
                        "project_id": "authoring_project",
                        "title": "Auxiliary rules",
                        "systems": [
                            {
                                "id": "auxiliary_rule",
                                "system_type": "rule",
                                "title": "Auxiliary rule",
                                "precondition_ids": [],
                                "effect_ids": [],
                                "event_ids": [],
                                "asset_binding_ids": [],
                            }
                        ],
                        "extensions": [],
                        "content_hash": "",
                    }
                )
                manifest = json.loads(
                    (root / "source" / "manifest.json").read_text(encoding="utf-8")
                )
                manifest["modules"]["system_modules"].append(
                    {
                        "format": module["format"],
                        "format_version": module["format_version"],
                        "id": module["module_id"],
                        "path": "auxiliary.json",
                        "content_hash": module["content_hash"],
                    }
                )
                _rehash(manifest)
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                project["source_manifest"]["content_hash"] = manifest["content_hash"]
                _rehash(project)
                create_operations = sorted(
                    [
                        _create_operation("source/auxiliary.json", module),
                        _replace_operation(root, "source/manifest.json", manifest),
                        _replace_operation(root, "project.json", project),
                    ],
                    key=lambda operation: operation["path"].encode("utf-8"),
                )
                manager = CreationAuthoringManager(store, workspaces=workspaces)
                staged_create = manager.create(
                    {
                        "changeset_id": "create_module_change",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "operations": create_operations,
                    }
                )
                approved_create = manager.approve(
                    staged_create["changeset_id"],
                    expected_record_hash=staged_create["record_hash"],
                    expected_review_sha256=staged_create["review_sha256"],
                )
                created = manager.apply(
                    approved_create["changeset_id"],
                    expected_record_hash=approved_create["record_hash"],
                    expected_review_sha256=approved_create["review_sha256"],
                    expected_root_generation=approved_create["expected_root_generation"],
                )
                self.assertTrue((root / "source/auxiliary.json").is_file())

                manifest["modules"]["system_modules"] = []
                _rehash(manifest)
                project["source_manifest"]["content_hash"] = manifest["content_hash"]
                _rehash(project)
                delete_operations = sorted(
                    [
                        _delete_operation(root, "source/auxiliary.json"),
                        _replace_operation(root, "source/manifest.json", manifest),
                        _replace_operation(root, "project.json", project),
                    ],
                    key=lambda operation: operation["path"].encode("utf-8"),
                )
                current_workspace = created["workspace"]
                staged_delete = manager.create(
                    {
                        "changeset_id": "delete_module_change",
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": current_workspace["root_generation"],
                        "expected_source_revision": current_workspace["source_revision"],
                        "expected_workflow_status_hash": current_workspace["workflow_status_hash"],
                        "operations": delete_operations,
                    }
                )
                approved_delete = manager.approve(
                    staged_delete["changeset_id"],
                    expected_record_hash=staged_delete["record_hash"],
                    expected_review_sha256=staged_delete["review_sha256"],
                )

                def crash_delete_link(phase: str, details: dict[str, object]) -> None:
                    if phase == "rollback_linked" and details["source_name"] == "auxiliary.json":
                        raise _SimulatedCrash

                deleting = CreationAuthoringManager(
                    store,
                    workspaces=workspaces,
                    mutation_hook=crash_delete_link,
                )
                with self.assertRaises(_SimulatedCrash):
                    deleting.apply(
                        approved_delete["changeset_id"],
                        expected_record_hash=approved_delete["record_hash"],
                        expected_review_sha256=approved_delete["review_sha256"],
                        expected_root_generation=approved_delete["expected_root_generation"],
                    )
                recovering = CreationAuthoringManager(store, workspaces=workspaces)
                pending_delete = recovering.get(approved_delete["changeset_id"])
                deleted = recovering.recover(
                    pending_delete["changeset_id"],
                    mode="resume",
                    expected_record_hash=pending_delete["record_hash"],
                    expected_review_sha256=pending_delete["review_sha256"],
                    expected_root_generation=pending_delete["expected_root_generation"],
                )
                self.assertEqual("applied", deleted["changeset"]["status"])
                self.assertFalse((root / "source/auxiliary.json").exists())


if __name__ == "__main__":
    unittest.main()
