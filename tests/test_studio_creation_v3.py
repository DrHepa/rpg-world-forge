from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

from isoworld.content.file_stat import FileStat, WindowsFileStat, path_file_stat
from worldforge.creation_contracts import canonical_creation_hash, load_creation_project
from worldforge.creation_scaffold import CreationScaffoldError, create_creation_project
from worldforge.creation_workflow import (
    complete_creation_phase,
    load_creation_workflow_status,
    reconcile_creation_workflow,
)
from worldforge.directory_publish import DirectoryPublishError
from worldforge.phase_report_v3 import (
    build_phase_output_evidence_v2,
    build_phase_report_v3,
    document_identity,
)
from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.contracts import (
    METHODS,
    METHODS_V2,
    METHODS_V3,
    validate_studio_creation_root_grant,
    validate_studio_creation_workspace,
    validate_studio_protocol_envelope,
)
from worldforge.studio.creation_grants import CreationRootGrantManager
from worldforge.studio.creation_workspaces import CreationWorkspaceManager
from worldforge.studio.errors import StudioError
from worldforge.studio.external_grants import ExternalGrantManager
from worldforge.studio.service import StudioService
from worldforge.studio.storage import SCHEMA_VERSION, StudioStore, decode_object, encode_json
from worldforge.studio.workspaces import WorkspaceManager

_HASH_A = "a" * 64
_TIMESTAMP = "2026-08-01T00:00:00Z"


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _complete_brief_phase(root: Path) -> dict[str, object]:
    loaded = load_creation_project(root / "project.json")
    reviewer_id = "lead_reviewer"
    reviewer_role = "validation_analyst"
    output = build_phase_output_evidence_v2(
        evidence_id="p00_brief_output",
        phase="p00_brief",
        role="project_brief",
        subject=document_identity(loaded.project),
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        source_project=loaded,
    )
    report = build_phase_report_v3(
        loaded,
        phase="p00_brief",
        status="ready",
        rationale_code="phase_ready",
        rationale_message="The project brief was reviewed.",
        evidence=(
            {
                "evidence_id": "reviewed_project",
                "claim": "The exact creation project was reviewed.",
                "subject": document_identity(loaded.project),
            },
        ),
        output_evidence=output,
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        invalidation_dependencies=None,
    )
    report_path = root / "p00-brief-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return complete_creation_phase(
        root,
        report_path,
        expected_status_hash=load_creation_workflow_status(root)["content_hash"],
    )


def _contains_native_path(value: object, path: Path) -> bool:
    if isinstance(value, dict):
        return any(_contains_native_path(item, path) for item in value.values())
    if isinstance(value, list):
        return any(_contains_native_path(item, path) for item in value)
    return isinstance(value, str) and str(path) in value


def _grant_record(*, role: str = "existing_root") -> dict[str, object]:
    return {
        "format": "world-forge.studio_creation_root_grant",
        "format_version": 1,
        "grant_id": "grant_01",
        "role": role,
        "display_name": "Neutral project",
        "state": "ready",
        "expected_target_state": "existing_project" if role == "existing_root" else "absent",
        "expected_project": (
            {
                "format": "world-forge.project",
                "format_version": 1,
                "id": "neutral_project",
                "content_hash": _HASH_A,
            }
            if role == "existing_root"
            else None
        ),
        "generation": 0,
        "created_at": _TIMESTAMP,
        "updated_at": _TIMESTAMP,
    }


def _workspace_record() -> dict[str, object]:
    return {
        "format": "world-forge.studio_creation_workspace",
        "format_version": 1,
        "workspace_id": "workspace_01",
        "project": {
            "format": "world-forge.project",
            "format_version": 1,
            "id": "neutral_project",
            "content_hash": _HASH_A,
        },
        "project_kind": "universe_library",
        "source_revision": "b" * 64,
        "workflow_status_hash": None,
        "root_generation": 0,
        "created_at": _TIMESTAMP,
        "updated_at": _TIMESTAMP,
    }


class StudioCreationV3ContractTests(unittest.TestCase):
    def test_creation_records_are_closed_pathless_and_conditionally_exact(self) -> None:
        existing = _grant_record()
        self.assertEqual(existing, validate_studio_creation_root_grant(existing))
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_studio_creation_root_grant({**existing, "path": "/private/root"})
        with self.assertRaisesRegex(ValueError, "expected_project"):
            validate_studio_creation_root_grant({**existing, "expected_project": None})

        target = _grant_record(role="new_target")
        self.assertEqual(target, validate_studio_creation_root_grant(target))
        with self.assertRaisesRegex(ValueError, "target state"):
            validate_studio_creation_root_grant(
                {**target, "expected_target_state": "existing_project"}
            )

        workspace = _workspace_record()
        self.assertEqual(workspace, validate_studio_creation_workspace(workspace))
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_studio_creation_workspace({**workspace, "root": "/private/root"})
        with self.assertRaisesRegex(ValueError, "project"):
            validate_studio_creation_workspace(
                {
                    **workspace,
                    "project": {**workspace["project"], "format_version": 2},  # type: ignore[dict-item]
                }
            )

    def test_protocol_v3_is_closed_and_does_not_broaden_v1_or_v2(self) -> None:
        self.assertNotIn("creation_workspace.open", METHODS)
        self.assertNotIn("creation_workspace.open", METHODS_V2)
        self.assertEqual(
            {
                "service.initialize",
                "creation_root_grant.create",
                "creation_root_grant.get",
                "creation_root_grant.revoke",
                "creation_workspace.create",
                "creation_workspace.recover",
                "creation_workspace.register",
                "creation_workspace.get",
                "creation_workspace.list",
                "creation_workspace.open",
                "creation_document.list",
                "creation_document.read",
                "creation_changeset.create",
                "creation_changeset.get",
                "creation_changeset.list",
                "creation_changeset.diff",
                "creation_changeset.approve",
                "creation_changeset.reject",
                "creation_changeset.apply",
                "creation_changeset.recover",
                "creation_workflow.get",
                "creation_workflow.reconcile",
                "creation_phase.read",
                "creation_phase.validate",
                "creation_phase.complete",
                "creation_phase.reopen",
                "creation_readiness.inspect",
            },
            set(METHODS_V3),
        )
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 3,
            "kind": "request",
            "request_id": "request_01",
            "method": "creation_document.read",
            "params": {
                "workspace_id": "workspace_01",
                "expected_source_revision": "b" * 64,
                "path": "profile.json",
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        with self.assertRaisesRegex(ValueError, "not available"):
            validate_studio_protocol_envelope({**request, "protocol_version": 2})
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_studio_protocol_envelope(
                {**request, "params": {**request["params"], "native_path": "/tmp/x"}}
            )
        with self.assertRaisesRegex(ValueError, "expected_source_revision"):
            validate_studio_protocol_envelope(
                {
                    **request,
                    "params": {
                        "workspace_id": "workspace_01",
                        "path": "profile.json",
                    },
                }
            )

    def test_protocol_v3_response_shapes_are_method_specific(self) -> None:
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 3,
            "kind": "response",
            "request_id": "request_01",
            "method": "creation_workspace.get",
            "result": {"workspace": _workspace_record()},
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_studio_protocol_envelope(
                {**request, "result": {**request["result"], "path": "/private/root"}}
            )

        initialize = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 3,
            "kind": "response",
            "request_id": "request_02",
            "method": "service.initialize",
            "result": {
                "service": "world-forge.studio",
                "service_version": 3,
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": 3,
                "methods": sorted(METHODS_V3),
                "capabilities": {
                    "generic_creation": True,
                    "safe_project_creation": True,
                    "read_only_documents": True,
                    "profile_editing": True,
                    "generic_jobs": False,
                    "reviewed_changesets": True,
                    "workflow_mutations": True,
                    "inline_phase_reports": True,
                },
            },
        }
        self.assertEqual(initialize, validate_studio_protocol_envelope(initialize))
        with self.assertRaisesRegex(ValueError, "capabilities"):
            validate_studio_protocol_envelope(
                {
                    **initialize,
                    "result": {
                        **initialize["result"],
                        "capabilities": {
                            **initialize["result"]["capabilities"],  # type: ignore[index]
                            "generic_creation": False,
                        },
                    },
                }
            )


class StudioCreationV3StorageTests(unittest.TestCase):
    def test_v2_to_v3_migration_is_additive_idempotent_and_preserves_legacy_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "studio"
            with StudioStore(data) as store:
                self.assertEqual(5, SCHEMA_VERSION)
                store.connection.execute(
                    "UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'"
                )
                legacy = (
                    "workspace_legacy",
                    '{"format":"rpg-world-forge.forge_workspace","sentinel":"raw"}',
                    "1",
                    "2",
                    "3",
                    "4",
                    None,
                    None,
                    None,
                    None,
                )
                store.connection.execute(
                    "INSERT INTO workspaces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    legacy,
                )
                store.connection.commit()
            database = data / "studio.sqlite3"
            before = (
                sqlite3.connect(database)
                .execute(
                    "SELECT record_json FROM workspaces WHERE workspace_id = 'workspace_legacy'"
                )
                .fetchone()[0]
            )
            with StudioStore(data) as migrated:
                version = migrated.connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                self.assertEqual("5", version)
                tables = {
                    row[0]
                    for row in migrated.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertLessEqual(
                    {
                        "creation_root_grants",
                        "creation_workspace_attempts",
                        "creation_workspaces",
                        "creation_events",
                    },
                    tables,
                )
            with StudioStore(data):
                pass
            after = (
                sqlite3.connect(database)
                .execute(
                    "SELECT record_json FROM workspaces WHERE workspace_id = 'workspace_legacy'"
                )
                .fetchone()[0]
            )
            self.assertEqual(before, after)

    def test_secondary_store_requires_exact_v3(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "studio"
            with StudioStore(data):
                pass
            with StudioStore(data, mode="secondary"):
                pass
            database = sqlite3.connect(data / "studio.sqlite3")
            database.execute("UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'")
            database.commit()
            database.close()
            with self.assertRaisesRegex(StudioError, "schema version 5"):
                StudioStore(data, mode="secondary")


class StudioCreationV3WorkspaceTests(unittest.TestCase):
    def test_existing_root_registration_is_pathless_revision_bound_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="neutral_project",
                title="Neutral project",
            )
            project_bytes = (root / "project.json").read_bytes()
            expected_project = document_identity(
                load_creation_project(root / "project.json").project
            )
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_existing",
                        "role": "existing_root",
                        "display_name": "Neutral project",
                        "path": str(root),
                        "expected_project_hash": expected_project["content_hash"],
                    }
                )
                self.assertFalse(_contains_native_path(grant, root))
                manager = CreationWorkspaceManager(store, grants=grants)
                workspace = manager.register(
                    {
                        "workspace_id": "workspace_neutral",
                        "grant_id": grant["grant_id"],
                        "expected_grant_generation": grant["generation"],
                        "expected_project_hash": expected_project["content_hash"],
                    }
                )
                self.assertFalse(_contains_native_path(workspace, root))
                opened = manager.open("workspace_neutral")
                self.assertEqual("generic", opened["route"])
                self.assertEqual("universe_library", opened["project_kind"])
                self.assertEqual(workspace["source_revision"], opened["source_revision"])
                self.assertEqual("p00_brief", opened["current_phase"])
                self.assertEqual("consumed", grants.get("grant_existing")["state"])
                self.assertNotIn("project_document", opened)
                self.assertEqual(project_bytes, (root / "project.json").read_bytes())

    def test_document_allowlist_and_revision_cas(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="neutral_project",
                title="Neutral project",
            )
            project_hash = load_creation_project(root / "project.json").project["content_hash"]
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_existing",
                        "role": "existing_root",
                        "display_name": "Neutral project",
                        "path": str(root),
                        "expected_project_hash": project_hash,
                    }
                )
                manager = CreationWorkspaceManager(store, grants=grants)
                workspace = manager.register(
                    {
                        "workspace_id": "workspace_neutral",
                        "grant_id": "grant_existing",
                        "expected_grant_generation": grant["generation"],
                        "expected_project_hash": project_hash,
                    }
                )
                listed = manager.list_documents(
                    "workspace_neutral",
                    expected_source_revision=workspace["source_revision"],
                )
                self.assertEqual(
                    ["profile.json", "project.json", "source/manifest.json"],
                    [item["path"] for item in listed],
                )
                document = manager.read_document(
                    "workspace_neutral",
                    "profile.json",
                    expected_source_revision=workspace["source_revision"],
                )
                self.assertEqual("world-forge.creation_profile", document["format"])
                self.assertEqual(document["content_hash"], document["document"]["content_hash"])
                self.assertEqual(64, len(document["file_sha256"]))
                self.assertNotIn("path", document["document"])
                with self.assertRaisesRegex(StudioError, "source revision"):
                    manager.read_document(
                        "workspace_neutral",
                        ".worldforge/status.json",
                        expected_source_revision=workspace["source_revision"],
                    )
                with self.assertRaisesRegex(StudioError, "source revision"):
                    manager.list_documents(
                        "workspace_neutral",
                        expected_source_revision=_HASH_A,
                    )

    def test_open_workflow_and_readiness_refresh_legitimate_workflow_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="refresh_project",
                title="Refresh project",
            )
            project_hash = load_creation_project(root / "project.json").project["content_hash"]
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_refresh",
                        "role": "existing_root",
                        "display_name": "Refresh project",
                        "path": str(root),
                        "expected_project_hash": project_hash,
                    }
                )
                manager = CreationWorkspaceManager(store, grants=grants)
                registered = manager.register(
                    {
                        "workspace_id": "workspace_refresh",
                        "grant_id": grant["grant_id"],
                        "expected_grant_generation": grant["generation"],
                        "expected_project_hash": project_hash,
                    }
                )
                completed = _complete_brief_phase(root)
                expected_tree = _tree_bytes(root)

                opened = manager.open("workspace_refresh")
                workflow = manager.workflow("workspace_refresh")
                readiness = manager.readiness("workspace_refresh")

                self.assertEqual("p01_genre_style", opened["current_phase"])
                self.assertEqual(completed["content_hash"], opened["workflow_status_hash"])
                self.assertEqual("active", workflow["state"])
                self.assertEqual(1, workflow["revision"])
                self.assertEqual("blocked", readiness["state"])
                self.assertIn("workflow_incomplete", readiness["blocker_reason_codes"])
                refreshed = manager.get("workspace_refresh")
                self.assertEqual(completed["content_hash"], refreshed["workflow_status_hash"])
                self.assertEqual(registered["root_generation"] + 1, refreshed["root_generation"])
                self.assertEqual(expected_tree, _tree_bytes(root))
                topics = [
                    row["topic"]
                    for row in store.connection.execute(
                        "SELECT topic FROM creation_events "
                        "WHERE workspace_id = 'workspace_refresh' ORDER BY event_id"
                    )
                ]
                self.assertEqual(
                    ["creation_workspace.registered", "creation_workspace.refreshed"],
                    topics,
                )

    def test_source_reconciliation_refreshes_revision_and_preserves_document_cas(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="edited_project",
                title="Before edit",
            )
            loaded = load_creation_project(root / "project.json")
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_edit",
                        "role": "existing_root",
                        "display_name": "Edited project",
                        "path": str(root),
                        "expected_project_hash": loaded.project["content_hash"],
                    }
                )
                manager = CreationWorkspaceManager(store, grants=grants)
                registered = manager.register(
                    {
                        "workspace_id": "workspace_edit",
                        "grant_id": grant["grant_id"],
                        "expected_grant_generation": grant["generation"],
                        "expected_project_hash": loaded.project["content_hash"],
                    }
                )
                status = json.loads((root / ".worldforge/status.json").read_text(encoding="utf-8"))
                project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                project["title"] = "After edit"
                project["content_hash"] = ""
                project["content_hash"] = canonical_creation_hash(project)
                (root / "project.json").write_text(
                    json.dumps(project, indent=2) + "\n",
                    encoding="utf-8",
                )
                reconciled = reconcile_creation_workflow(
                    root,
                    expected_status_hash=status["content_hash"],
                )
                expected_tree = _tree_bytes(root)

                opened = manager.open("workspace_edit")
                self.assertNotEqual(registered["source_revision"], opened["source_revision"])
                self.assertEqual(
                    project["content_hash"], opened["workspace"]["project"]["content_hash"]
                )
                self.assertEqual(reconciled["content_hash"], opened["workflow_status_hash"])
                with self.assertRaisesRegex(StudioError, "source revision"):
                    manager.list_documents(
                        "workspace_edit",
                        expected_source_revision=registered["source_revision"],
                    )
                documents = manager.list_documents(
                    "workspace_edit",
                    expected_source_revision=opened["source_revision"],
                )
                by_path = {document["path"]: document for document in documents}
                self.assertEqual(
                    project["content_hash"],
                    by_path["project.json"]["content_hash"],
                )
                self.assertEqual(expected_tree, _tree_bytes(root))

    def test_invalid_workflow_is_distinct_from_invalid_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="invalid_workflow",
                title="Invalid workflow",
            )
            loaded = load_creation_project(root / "project.json")
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_invalid_workflow",
                        "role": "existing_root",
                        "display_name": "Invalid workflow",
                        "path": str(root),
                        "expected_project_hash": loaded.project["content_hash"],
                    }
                )
                manager = CreationWorkspaceManager(store, grants=grants)
                registered = manager.register(
                    {
                        "workspace_id": "workspace_invalid_workflow",
                        "grant_id": grant["grant_id"],
                        "expected_grant_generation": grant["generation"],
                        "expected_project_hash": loaded.project["content_hash"],
                    }
                )
                invalid_bytes = b'{"broken":true}\n'
                (root / ".worldforge/status.json").write_bytes(invalid_bytes)
                expected_tree = _tree_bytes(root)

                workflow = manager.workflow("workspace_invalid_workflow")
                readiness = manager.readiness("workspace_invalid_workflow")

                invalid_hash = hashlib.sha256(invalid_bytes).hexdigest()
                self.assertEqual("invalid", workflow["state"])
                self.assertEqual(invalid_hash, workflow["status_hash"])
                self.assertEqual(registered["source_revision"], workflow["source_revision"])
                self.assertEqual("invalid", readiness["state"])
                self.assertIn("workflow_invalid", readiness["blocker_reason_codes"])
                self.assertNotIn("source_invalid", readiness["blocker_reason_codes"])
                self.assertEqual(invalid_hash, readiness["workflow_status_hash"])
                self.assertEqual(expected_tree, _tree_bytes(root))

    def test_snapshot_refresh_retries_a_generation_race_without_sleeping(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="refresh_race",
                title="Refresh race",
            )
            loaded = load_creation_project(root / "project.json")
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_refresh_race",
                        "role": "existing_root",
                        "display_name": "Refresh race",
                        "path": str(root),
                        "expected_project_hash": loaded.project["content_hash"],
                    }
                )
                plain = CreationWorkspaceManager(store, grants=grants)
                plain.register(
                    {
                        "workspace_id": "workspace_refresh_race",
                        "grant_id": grant["grant_id"],
                        "expected_grant_generation": grant["generation"],
                        "expected_project_hash": loaded.project["content_hash"],
                    }
                )
                _complete_brief_phase(root)
                fired = False

                def race(phase: str, context: dict[str, object]) -> None:
                    nonlocal fired
                    if phase != "snapshot_scanned" or fired:
                        return
                    fired = True
                    row = store.connection.execute(
                        "SELECT * FROM creation_workspaces "
                        "WHERE workspace_id = 'workspace_refresh_race'"
                    ).fetchone()
                    assert row is not None
                    record = decode_object(row["record_json"], context="test workspace")
                    record["root_generation"] += 1
                    store.connection.execute(
                        "UPDATE creation_workspaces SET record_json = ?, generation = ? "
                        "WHERE workspace_id = ?",
                        (
                            encode_json(record),
                            record["root_generation"],
                            record["workspace_id"],
                        ),
                    )
                    store.connection.commit()

                manager = CreationWorkspaceManager(
                    store,
                    grants=grants,
                    transition_hook=race,
                )
                opened = manager.open("workspace_refresh_race")
                self.assertTrue(fired)
                self.assertEqual("p01_genre_style", opened["current_phase"])
                self.assertEqual(2, opened["workspace"]["root_generation"])

    def test_snapshot_refresh_rejects_a_torn_unchanged_source_workflow_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(root, project_id="torn_pair", title="Torn pair")
            loaded = load_creation_project(root / "project.json")
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_torn_pair",
                        "role": "existing_root",
                        "display_name": "Torn pair",
                        "path": str(root),
                        "expected_project_hash": loaded.project["content_hash"],
                    }
                )
                plain = CreationWorkspaceManager(store, grants=grants)
                registered = plain.register(
                    {
                        "workspace_id": "workspace_torn_pair",
                        "grant_id": grant["grant_id"],
                        "expected_grant_generation": grant["generation"],
                        "expected_project_hash": loaded.project["content_hash"],
                    }
                )
                initial_status = json.loads(
                    (root / ".worldforge" / "status.json").read_text(encoding="utf-8")
                )
                fired = False
                expected_hash: str | None = None

                def mutate_between_pairs(phase: str, _context: dict[str, object]) -> None:
                    nonlocal expected_hash, fired
                    if phase != "snapshot_scanned" or fired:
                        return
                    fired = True
                    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
                    project["title"] = "Torn pair refreshed"
                    project["content_hash"] = ""
                    project["content_hash"] = canonical_creation_hash(project)
                    (root / "project.json").write_text(
                        json.dumps(project, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    reconcile_creation_workflow(
                        root,
                        expected_status_hash=initial_status["content_hash"],
                    )
                    expected_hash = project["content_hash"]

                manager = CreationWorkspaceManager(
                    store,
                    grants=grants,
                    transition_hook=mutate_between_pairs,
                )
                opened = manager.open("workspace_torn_pair")
                self.assertTrue(fired)
                self.assertIsNotNone(expected_hash)
                self.assertNotEqual(registered["source_revision"], opened["source_revision"])
                self.assertEqual(expected_hash, opened["workspace"]["project"]["content_hash"])

    def test_new_target_creation_consumes_grant_without_phantom_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            target = base / "new-project"
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_target",
                        "role": "new_target",
                        "display_name": "New project",
                        "path": str(target),
                        "expected_project_hash": None,
                    }
                )
                manager = CreationWorkspaceManager(store, grants=grants)
                with mock.patch(
                    "worldforge.studio.creation_workspaces.create_creation_project",
                    side_effect=CreationScaffoldError("interrupted"),
                ):
                    with self.assertRaisesRegex(StudioError, "Creation failed"):
                        manager.create(
                            {
                                "workspace_id": "workspace_new",
                                "grant_id": "grant_target",
                                "expected_grant_generation": grant["generation"],
                                "project_kind": "universe_library",
                                "project_id": "new_project",
                                "title": "New project",
                                "default_locale": "en",
                                "project_version": "0.1.0",
                            }
                        )
                self.assertIsNone(
                    store.connection.execute(
                        "SELECT 1 FROM creation_workspaces WHERE workspace_id = 'workspace_new'"
                    ).fetchone()
                )
                retried_grant = grants.get("grant_target")
                self.assertEqual("ready", retried_grant["state"])
                workspace = manager.create(
                    {
                        "workspace_id": "workspace_new",
                        "grant_id": "grant_target",
                        "expected_grant_generation": retried_grant["generation"],
                        "project_kind": "universe_library",
                        "project_id": "new_project",
                        "title": "New project",
                        "default_locale": "en",
                        "project_version": "0.1.0",
                    }
                )
                self.assertTrue(target.is_dir())
                self.assertEqual("new_project", workspace["project"]["id"])
                self.assertEqual("consumed", grants.get("grant_target")["state"])

    def test_windows_workspace_ancestry_shares_delete_only_for_external_anchor(self) -> None:
        from worldforge.studio import workspaces

        api = object.__new__(workspaces._WindowsRelativeDirectoryApi)
        api._invalid_handle = ctypes.c_void_p(-1).value
        create_file_shares: list[int] = []
        relative_shares: list[int] = []

        def fake_state(handle: int, *, context: str, directory: bool) -> FileStat:
            del context, directory
            return WindowsFileStat(
                st_mode=stat.S_IFDIR,
                st_dev=23,
                st_ino=handle,
                st_nlink=1,
                st_size=0,
                st_mtime_ns=0,
                st_ctime_ns=0,
                st_file_attributes=0,
            )

        def create_file(
            _path: str,
            _access: int,
            share: int,
            _security: object,
            _disposition: int,
            _flags: int,
            _template: object,
        ) -> int:
            create_file_shares.append(share)
            return 41

        def nt_create_file(
            opened: object,
            _access: int,
            _attributes: object,
            _io_status: object,
            _allocation: object,
            _file_attributes: int,
            share: int,
            _disposition: int,
            _options: int,
            _ea_buffer: object,
            _ea_length: int,
        ) -> int:
            relative_shares.append(share)
            handle = 50 + len(relative_shares)
            ctypes.cast(opened, ctypes.POINTER(ctypes.c_void_p)).contents.value = handle
            return 0

        api.state = fake_state
        api._create_file = create_file
        api._nt_create_file = nt_create_file

        handles, identities = workspaces._open_windows_ancestry(
            api,
            PureWindowsPath("X:/forge/project"),
            context="creation root",
        )

        self.assertEqual([41, 51, 52], handles)
        self.assertEqual(((23, 41), (23, 51), (23, 52)), identities)
        self.assertEqual([api._FILE_SHARE_ALL], create_file_shares)
        self.assertEqual(
            [api._FILE_SHARE_READ | api._FILE_SHARE_WRITE] * 2,
            relative_shares,
        )

    def test_windows_workspace_ancestry_rejects_root_only_directory(self) -> None:
        from worldforge.studio import workspaces

        api = object.__new__(workspaces._WindowsRelativeDirectoryApi)
        api.open_anchor = lambda *_args, **_kwargs: self.fail("root-only anchor must not be opened")

        with self.assertRaisesRegex(ValueError, "filesystem root"):
            workspaces._open_windows_ancestry(
                api,
                PureWindowsPath("X:/"),
                context="creation root",
            )

    def test_visible_created_target_requires_exact_recovery_and_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            target = base / "recovered-project"
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_recovery",
                        "role": "new_target",
                        "display_name": "Recovered project",
                        "path": str(target),
                        "expected_project_hash": None,
                    }
                )
                manager = CreationWorkspaceManager(store, grants=grants)
                real_create = create_creation_project

                def publish_then_interrupt(*args: object, **kwargs: object) -> Path:
                    real_create(*args, **kwargs)
                    raise CreationScaffoldError("interrupted after publication")

                request = {
                    "workspace_id": "workspace_recovery",
                    "grant_id": "grant_recovery",
                    "expected_grant_generation": grant["generation"],
                    "project_kind": "universe_library",
                    "project_id": "recovered_project",
                    "title": "Recovered project",
                    "default_locale": "en",
                    "project_version": "0.1.0",
                }
                with mock.patch(
                    "worldforge.studio.creation_workspaces.create_creation_project",
                    side_effect=publish_then_interrupt,
                ):
                    with self.assertRaisesRegex(StudioError, "Creation failed"):
                        manager.create(request)
                self.assertTrue(target.is_dir())
                self.assertIsNone(
                    store.connection.execute(
                        "SELECT 1 FROM creation_workspaces "
                        "WHERE workspace_id = 'workspace_recovery'"
                    ).fetchone()
                )
                recovery = grants.get("grant_recovery")
                self.assertEqual("recovery_required", recovery["state"])
                recovered = manager.create(
                    {**request, "expected_grant_generation": recovery["generation"]}
                )
                self.assertEqual("recovered_project", recovered["project"]["id"])
                self.assertEqual("consumed", grants.get("grant_recovery")["state"])
                self.assertEqual([], list(store.journals_dir.glob("creation-*.journal")))

    def test_cleanup_failure_after_commit_is_successful_and_idempotently_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            target = base / "cleanup-project"
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_cleanup",
                        "role": "new_target",
                        "display_name": "Cleanup project",
                        "path": str(target),
                        "expected_project_hash": None,
                    }
                )
                manager = CreationWorkspaceManager(store, grants=grants)
                request = {
                    "workspace_id": "workspace_cleanup",
                    "grant_id": grant["grant_id"],
                    "expected_grant_generation": grant["generation"],
                    "project_kind": "universe_library",
                    "project_id": "cleanup_project",
                    "title": "Cleanup project",
                    "default_locale": "en",
                    "project_version": "0.1.0",
                }
                with mock.patch(
                    "worldforge.studio.creation_workspaces.remove_append_only_journal",
                    side_effect=DirectoryPublishError("interrupted cleanup"),
                ):
                    workspace = manager.create(request)

                self.assertEqual("cleanup_project", workspace["project"]["id"])
                self.assertEqual("consumed", grants.get(grant["grant_id"])["state"])
                attempt = store.connection.execute(
                    "SELECT phase FROM creation_workspace_attempts WHERE workspace_id = ?",
                    (workspace["workspace_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)
                self.assertEqual("cleanup_authorized", attempt["phase"])
                self.assertEqual(1, len(list(store.journals_dir.glob("creation-*.journal"))))

                first = manager.recover(
                    workspace["workspace_id"],
                    expected_root_generation=workspace["root_generation"],
                )
                second = manager.recover(
                    workspace["workspace_id"],
                    expected_root_generation=workspace["root_generation"],
                )
                self.assertEqual("complete", first["state"])
                self.assertEqual(first, second)
                self.assertEqual(workspace, first["workspace"])
                self.assertEqual([], list(store.journals_dir.glob("creation-*.journal")))
                self.assertIsNone(
                    store.connection.execute(
                        "SELECT 1 FROM creation_workspace_attempts WHERE workspace_id = ?",
                        (workspace["workspace_id"],),
                    ).fetchone()
                )

    def test_database_cleanup_failure_after_commit_remains_successful_and_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            target = base / "database-cleanup-project"
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_database_cleanup",
                        "role": "new_target",
                        "display_name": "Database cleanup",
                        "path": str(target),
                        "expected_project_hash": None,
                    }
                )
                manager = CreationWorkspaceManager(store, grants=grants)
                request = {
                    "workspace_id": "workspace_database_cleanup",
                    "grant_id": grant["grant_id"],
                    "expected_grant_generation": grant["generation"],
                    "project_kind": "universe_library",
                    "project_id": "database_cleanup",
                    "title": "Database cleanup",
                    "default_locale": "en",
                    "project_version": "0.1.0",
                }
                with mock.patch.object(
                    manager,
                    "_finish_attempt_cleanup",
                    side_effect=sqlite3.OperationalError("interrupted cleanup transaction"),
                ):
                    workspace = manager.create(request)

                self.assertEqual("database_cleanup", workspace["project"]["id"])
                self.assertEqual("consumed", grants.get(grant["grant_id"])["state"])
                self.assertEqual([], list(store.journals_dir.glob("creation-*.journal")))
                self.assertIsNotNone(
                    store.connection.execute(
                        "SELECT 1 FROM creation_workspace_attempts WHERE workspace_id = ?",
                        (workspace["workspace_id"],),
                    ).fetchone()
                )
                recovered = manager.recover(
                    workspace["workspace_id"],
                    expected_root_generation=workspace["root_generation"],
                )
                self.assertEqual("complete", recovered["state"])
                self.assertEqual(workspace, recovered["workspace"])

    def test_creation_crash_boundaries_roll_forward_once_without_phantom_workspaces(self) -> None:
        class SimulatedCrash(BaseException):
            pass

        for phase in (
            "reservation_committed",
            "before_publication",
            "target_published",
            "workspace_committed",
            "grant_consumed",
            "cleanup_authorized",
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                target = base / f"crash-{phase}"
                with StudioStore(base / "studio") as store:
                    grants = CreationRootGrantManager(store)
                    grant = grants.create(
                        {
                            "grant_id": "grant_crash",
                            "role": "new_target",
                            "display_name": "Crash boundary",
                            "path": str(target),
                            "expected_project_hash": None,
                        }
                    )
                    request = {
                        "workspace_id": "workspace_crash",
                        "grant_id": grant["grant_id"],
                        "expected_grant_generation": grant["generation"],
                        "project_kind": "universe_library",
                        "project_id": "crash_project",
                        "title": "Crash project",
                        "default_locale": "en",
                        "project_version": "0.1.0",
                    }
                    fired = False

                    def crash(
                        observed: str,
                        _context: dict[str, object],
                        expected_phase: str = phase,
                    ) -> None:
                        nonlocal fired
                        if observed == expected_phase and not fired:
                            fired = True
                            raise SimulatedCrash(expected_phase)

                    manager = CreationWorkspaceManager(
                        store,
                        grants=grants,
                        transition_hook=crash,
                    )
                    with self.assertRaises(SimulatedCrash):
                        manager.create(request)
                    self.assertTrue(fired)

                    current = grants.get(grant["grant_id"])
                    recovered = CreationWorkspaceManager(store, grants=grants).create(
                        {
                            **request,
                            "expected_grant_generation": current["generation"],
                        }
                    )
                    self.assertEqual("crash_project", recovered["project"]["id"])
                    self.assertEqual("consumed", grants.get(grant["grant_id"])["state"])
                    self.assertEqual(
                        recovered,
                        CreationWorkspaceManager(store, grants=grants).recover(
                            recovered["workspace_id"],
                            expected_root_generation=recovered["root_generation"],
                        )["workspace"],
                    )
                    self.assertEqual([], list(store.journals_dir.glob("creation-*.journal")))

    def test_reserved_create_state_resumes_after_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                manager = CreationWorkspaceManager(store, grants=grants)
                for visible in (False, True):
                    with self.subTest(visible=visible):
                        suffix = "visible" if visible else "absent"
                        target = base / f"restart-{suffix}"
                        grant_id = f"grant_restart_{suffix}"
                        workspace_id = f"workspace_restart_{suffix}"
                        project_id = f"restart_{suffix}"
                        grant = grants.create(
                            {
                                "grant_id": grant_id,
                                "role": "new_target",
                                "display_name": f"Restart {suffix}",
                                "path": str(target),
                                "expected_project_hash": None,
                            }
                        )
                        request = {
                            "workspace_id": workspace_id,
                            "grant_id": grant_id,
                            "expected_grant_generation": grant["generation"],
                            "project_kind": "universe_library",
                            "project_id": project_id,
                            "title": f"Restart {suffix}",
                            "default_locale": "en",
                            "project_version": "0.1.0",
                        }
                        with store.connection:
                            reserved, _binding = grants.reserve(
                                grant_id,
                                workspace_id=workspace_id,
                                expected_generation=grant["generation"],
                                role="new_target",
                                creation_spec=request,
                            )
                        if visible:
                            create_creation_project(
                                target,
                                project_id=project_id,
                                title=f"Restart {suffix}",
                            )
                        recovered = manager.create(
                            {
                                **request,
                                "expected_grant_generation": reserved["generation"],
                            }
                        )
                        self.assertEqual(project_id, recovered["project"]["id"])
                        self.assertEqual("consumed", grants.get(grant_id)["state"])

    def test_prepublication_journal_collision_preserves_bytes_and_releases_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            target = base / "journal-conflict"
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_journal_conflict",
                        "role": "new_target",
                        "display_name": "Journal conflict",
                        "path": str(target),
                        "expected_project_hash": None,
                    }
                )
                request = {
                    "workspace_id": "workspace_journal_conflict",
                    "grant_id": "grant_journal_conflict",
                    "expected_grant_generation": grant["generation"],
                    "project_kind": "universe_library",
                    "project_id": "journal_conflict",
                    "title": "Journal conflict",
                    "default_locale": "en",
                    "project_version": "0.1.0",
                }
                journal_path: Path | None = None

                def collide(phase: str, context: dict[str, object]) -> None:
                    nonlocal journal_path
                    if phase == "reservation_committed" and journal_path is None:
                        candidate = context["journal_path"]
                        assert isinstance(candidate, Path)
                        journal_path = candidate
                        candidate.write_bytes(b"{}")

                manager = CreationWorkspaceManager(
                    store,
                    grants=grants,
                    transition_hook=collide,
                )
                with self.assertRaises(StudioError) as captured:
                    manager.create(request)
                self.assertEqual("recovery_ambiguous", captured.exception.code)
                self.assertEqual("ready", grants.get(grant["grant_id"])["state"])
                self.assertIsNotNone(journal_path)
                assert journal_path is not None
                self.assertEqual(b"{}", journal_path.read_bytes())
                self.assertFalse(target.exists())

                retry_grant = grants.get(grant["grant_id"])
                workspace = CreationWorkspaceManager(store, grants=grants).create(
                    {
                        **request,
                        "expected_grant_generation": retry_grant["generation"],
                    }
                )
                self.assertEqual("journal_conflict", workspace["project"]["id"])
                self.assertEqual(b"{}", journal_path.read_bytes())

    def test_recovery_never_adopts_a_matching_but_unbound_creation_journal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            target = base / "unbound-journal"
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_unbound_journal",
                        "role": "new_target",
                        "display_name": "Unbound journal",
                        "path": str(target),
                        "expected_project_hash": None,
                    }
                )
                request = {
                    "workspace_id": "workspace_unbound_journal",
                    "grant_id": grant["grant_id"],
                    "expected_grant_generation": grant["generation"],
                    "project_kind": "universe_library",
                    "project_id": "unbound_journal",
                    "title": "Unbound journal",
                    "default_locale": "en",
                    "project_version": "0.1.0",
                }
                manager = CreationWorkspaceManager(store, grants=grants)
                original_update = manager._update_attempt

                def interrupt_identity_binding(
                    attempt: sqlite3.Row,
                    *,
                    phase: str,
                    journal_identity: tuple[int, int] | None = None,
                    root_identity: tuple[int, int] | None = None,
                    commit: bool = True,
                ) -> sqlite3.Row:
                    if phase == "before_publication" and journal_identity is not None:
                        raise sqlite3.OperationalError("interrupted journal identity binding")
                    return original_update(
                        attempt,
                        phase=phase,
                        journal_identity=journal_identity,
                        root_identity=root_identity,
                        commit=commit,
                    )

                with mock.patch.object(
                    manager,
                    "_update_attempt",
                    side_effect=interrupt_identity_binding,
                ):
                    with self.assertRaises(sqlite3.OperationalError):
                        manager.create(request)

                attempt = store.connection.execute(
                    "SELECT * FROM creation_workspace_attempts WHERE workspace_id = ?",
                    (request["workspace_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)
                assert attempt is not None
                self.assertIsNone(attempt["journal_dev"])
                journal_path = store.journals_dir / attempt["journal_name"]
                matching_bytes = journal_path.read_bytes()
                reserved = grants.get(grant["grant_id"])
                with self.assertRaises(StudioError) as captured:
                    manager.create(
                        {
                            **request,
                            "expected_grant_generation": reserved["generation"],
                        }
                    )
                self.assertEqual("recovery_ambiguous", captured.exception.code)
                self.assertEqual("ready", grants.get(grant["grant_id"])["state"])
                self.assertEqual(matching_bytes, journal_path.read_bytes())
                self.assertFalse(target.exists())

    def test_grant_is_recensused_after_later_legacy_authority_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            legacy_manifest = create_world_project(
                base / "legacy",
                world_id="legacy_authority",
                title="Legacy authority",
                language="en",
            )
            legacy = legacy_manifest.parents[1]
            target = legacy / "generic-child"
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_later_overlap",
                        "role": "new_target",
                        "display_name": "Later overlap",
                        "path": str(target),
                        "expected_project_hash": None,
                    }
                )
                WorkspaceManager(store).register(
                    {
                        "workspace_id": "workspace_legacy_authority",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(legacy),
                    }
                )
                with self.assertRaisesRegex(StudioError, "overlap"):
                    CreationWorkspaceManager(store, grants=grants).create(
                        {
                            "workspace_id": "workspace_later_overlap",
                            "grant_id": grant["grant_id"],
                            "expected_grant_generation": grant["generation"],
                            "project_kind": "universe_library",
                            "project_id": "later_overlap",
                            "title": "Later overlap",
                            "default_locale": "en",
                            "project_version": "0.1.0",
                        }
                    )
                self.assertFalse(target.exists())
                self.assertEqual("ready", grants.get(grant["grant_id"])["state"])
                self.assertIsNone(
                    store.connection.execute("SELECT 1 FROM creation_workspace_attempts").fetchone()
                )

    def test_publication_boundary_recensus_blocks_late_authority_before_database_commit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            legacy_manifest = create_world_project(
                base / "late-legacy",
                world_id="late_authority",
                title="Late authority",
                language="en",
            )
            legacy = legacy_manifest.parents[1]
            target = base / "generic-target"
            with StudioStore(base / "studio") as store:
                legacy_workspace = WorkspaceManager(store).register(
                    {
                        "workspace_id": "workspace_late_authority",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(legacy),
                    }
                )
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_publish_overlap",
                        "role": "new_target",
                        "display_name": "Publish overlap",
                        "path": str(target),
                        "expected_project_hash": None,
                    }
                )
                fired = False

                def register_authority(phase: str, _context: dict[str, object]) -> None:
                    nonlocal fired
                    if phase == "target_published" and not fired:
                        fired = True
                        ExternalGrantManager(store).create(
                            {
                                "grant_id": "grant_late_external",
                                "workspace_id": legacy_workspace["workspace_id"],
                                "operation": "game.package",
                                "role": "target",
                                "artifact_kind": "game_package",
                                "display_name": "Late external target",
                                "path": str(target / "late-package.zip"),
                                "expected_content_hash": None,
                            }
                        )

                manager = CreationWorkspaceManager(
                    store,
                    grants=grants,
                    transition_hook=register_authority,
                )
                with self.assertRaisesRegex(StudioError, "overlap"):
                    manager.create(
                        {
                            "workspace_id": "workspace_publish_overlap",
                            "grant_id": grant["grant_id"],
                            "expected_grant_generation": grant["generation"],
                            "project_kind": "universe_library",
                            "project_id": "publish_overlap",
                            "title": "Publish overlap",
                            "default_locale": "en",
                            "project_version": "0.1.0",
                        }
                    )
                self.assertTrue(fired)
                self.assertTrue(target.is_dir())
                self.assertEqual("recovery_required", grants.get(grant["grant_id"])["state"])
                self.assertIsNone(
                    store.connection.execute(
                        "SELECT 1 FROM creation_workspaces "
                        "WHERE workspace_id = 'workspace_publish_overlap'"
                    ).fetchone()
                )

    def test_database_commit_recensus_rejects_replaced_visible_target_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            target = base / "identity-target"
            replacement = base / "identity-replacement"
            displaced = base / "identity-displaced"
            create_creation_project(
                replacement,
                project_id="identity_project",
                title="Identity project",
            )
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_identity_target",
                        "role": "new_target",
                        "display_name": "Identity target",
                        "path": str(target),
                        "expected_project_hash": None,
                    }
                )
                request = {
                    "workspace_id": "workspace_identity_target",
                    "grant_id": grant["grant_id"],
                    "expected_grant_generation": grant["generation"],
                    "project_kind": "universe_library",
                    "project_id": "identity_project",
                    "title": "Identity project",
                    "default_locale": "en",
                    "project_version": "0.1.0",
                }
                original_recensus = grants.recensus
                visible_calls = 0

                def replace_before_database_commit(
                    *args: object,
                    **kwargs: object,
                ) -> dict[str, object]:
                    nonlocal visible_calls
                    if kwargs.get("allow_visible_target") is True:
                        visible_calls += 1
                        if visible_calls == 2:
                            target.rename(displaced)
                            replacement.rename(target)
                    return original_recensus(*args, **kwargs)

                with mock.patch.object(
                    grants,
                    "recensus",
                    side_effect=replace_before_database_commit,
                ):
                    with self.assertRaises(StudioError):
                        CreationWorkspaceManager(store, grants=grants).create(request)
                self.assertEqual(2, visible_calls)
                self.assertIsNone(
                    store.connection.execute(
                        "SELECT 1 FROM creation_workspaces WHERE workspace_id = ?",
                        (request["workspace_id"],),
                    ).fetchone()
                )
                self.assertEqual("recovery_required", grants.get(grant["grant_id"])["state"])

    def test_unsafe_workflow_status_is_workflow_invalid_not_source_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "unsafe-workflow"
            create_creation_project(
                root,
                project_id="unsafe_workflow",
                title="Unsafe workflow",
            )
            loaded = load_creation_project(root / "project.json")
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_unsafe_workflow",
                        "role": "existing_root",
                        "display_name": "Unsafe workflow",
                        "path": str(root),
                        "expected_project_hash": loaded.project["content_hash"],
                    }
                )
                manager = CreationWorkspaceManager(store, grants=grants)
                manager.register(
                    {
                        "workspace_id": "workspace_unsafe_workflow",
                        "grant_id": grant["grant_id"],
                        "expected_grant_generation": grant["generation"],
                        "expected_project_hash": loaded.project["content_hash"],
                    }
                )
                status_path = root / ".worldforge" / "status.json"
                unsafe_copy = root / ".worldforge" / "unsafe-status.json"
                unsafe_copy.write_bytes(status_path.read_bytes())
                status_path.unlink()
                os.link(unsafe_copy, status_path)

                readiness = manager.readiness("workspace_unsafe_workflow")
                self.assertEqual("invalid", readiness["state"])
                self.assertIn("workflow_invalid", readiness["blocker_reason_codes"])
                self.assertNotIn("source_invalid", readiness["blocker_reason_codes"])

    def test_windows_reparse_attribute_seam_rejects_project_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="neutral_project",
                title="Neutral project",
            )
            project_hash = load_creation_project(root / "project.json").project["content_hash"]
            observed = path_file_stat(root / "project.json")
            reparse = WindowsFileStat(
                st_mode=stat.S_IFREG | stat.S_IRUSR,
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_nlink=1,
                st_size=observed.st_size,
                st_mtime_ns=observed.st_mtime_ns,
                st_ctime_ns=observed.st_ctime_ns,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )

            def marker_reparse_stat(path: Path) -> FileStat:
                if path == root / "project.json":
                    return reparse
                return path_file_stat(path)

            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                with mock.patch(
                    "worldforge.studio.creation_grants.path_file_stat",
                    side_effect=marker_reparse_stat,
                ):
                    with self.assertRaisesRegex(StudioError, "standalone regular file"):
                        grants.create(
                            {
                                "grant_id": "grant_windows_reparse",
                                "role": "existing_root",
                                "display_name": "Windows reparse project",
                                "path": str(root),
                                "expected_project_hash": project_hash,
                            }
                        )

    def test_windows_reparse_attribute_seam_rejects_project_root_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="neutral_project",
                title="Neutral project",
            )
            project_hash = load_creation_project(root / "project.json").project["content_hash"]
            observed = path_file_stat(root)
            reparse = WindowsFileStat(
                st_mode=stat.S_IFDIR | stat.S_IRUSR,
                st_dev=observed.st_dev,
                st_ino=observed.st_ino,
                st_nlink=observed.st_nlink,
                st_size=observed.st_size,
                st_mtime_ns=observed.st_mtime_ns,
                st_ctime_ns=observed.st_ctime_ns,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )

            def root_reparse_stat(path: Path) -> FileStat:
                if path == root:
                    return reparse
                return path_file_stat(path)

            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                with mock.patch(
                    "worldforge.studio.creation_grants.path_file_stat",
                    side_effect=root_reparse_stat,
                ):
                    with self.assertRaisesRegex(
                        StudioError,
                        "project root contains a symbolic link or reparse point",
                    ):
                        grants.create(
                            {
                                "grant_id": "grant_windows_root_reparse",
                                "role": "existing_root",
                                "display_name": "Windows reparse project root",
                                "path": str(root),
                                "expected_project_hash": project_hash,
                            }
                        )

    def test_existing_grant_rejects_hardlinked_project_marker_and_target_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="neutral_project",
                title="Neutral project",
            )
            project_hash = load_creation_project(root / "project.json").project["content_hash"]
            marker_alias = base / "project-marker-alias.json"
            try:
                os.link(root / "project.json", marker_alias)
            except OSError:
                self.skipTest("hardlinks unavailable")
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                with self.assertRaises(StudioError):
                    grants.create(
                        {
                            "grant_id": "grant_hardlink",
                            "role": "existing_root",
                            "display_name": "Hardlinked project",
                            "path": str(root),
                            "expected_project_hash": project_hash,
                        }
                    )
                sibling = base / "Taken"
                sibling.mkdir()
                with self.assertRaisesRegex(StudioError, "absent"):
                    grants.create(
                        {
                            "grant_id": "grant_casefold",
                            "role": "new_target",
                            "display_name": "Casefold target",
                            "path": str(base / "taken"),
                            "expected_project_hash": None,
                        }
                    )

    def test_grants_reject_unsafe_overlap_aliases_links_and_stale_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="neutral_project",
                title="Neutral project",
            )
            project_hash = load_creation_project(root / "project.json").project["content_hash"]
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                first = grants.create(
                    {
                        "grant_id": "grant_existing",
                        "role": "existing_root",
                        "display_name": "Neutral project",
                        "path": str(root),
                        "expected_project_hash": project_hash,
                    }
                )
                with self.assertRaisesRegex(StudioError, "overlap"):
                    grants.create(
                        {
                            "grant_id": "grant_nested",
                            "role": "new_target",
                            "display_name": "Nested",
                            "path": str(root / "child"),
                            "expected_project_hash": None,
                        }
                    )
                link = base / "project-link"
                try:
                    link.symlink_to(root, target_is_directory=True)
                except OSError:
                    pass
                else:
                    with self.assertRaises(StudioError):
                        grants.create(
                            {
                                "grant_id": "grant_link",
                                "role": "existing_root",
                                "display_name": "Link",
                                "path": str(link),
                                "expected_project_hash": project_hash,
                            }
                        )
                with self.assertRaisesRegex(StudioError, "generation"):
                    grants.revoke(first["grant_id"], expected_generation=99)

    def test_generic_and_legacy_workspace_routes_remain_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            generic = base / "generic"
            create_creation_project(
                generic,
                project_id="neutral_project",
                title="Neutral project",
            )
            legacy = create_world_project(
                base / "legacy",
                world_id="legacy_world",
                title="Legacy world",
                language="en",
            )
            generic_hash = load_creation_project(generic / "project.json").project["content_hash"]
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                with self.assertRaises(StudioError):
                    grants.create(
                        {
                            "grant_id": "grant_legacy",
                            "role": "existing_root",
                            "display_name": "Legacy",
                            "path": str(legacy),
                            "expected_project_hash": generic_hash,
                        }
                    )
                with self.assertRaises(StudioError):
                    WorkspaceManager(store).register(
                        {
                            "workspace_id": "legacy_from_generic",
                            "forge_root": str(FORGE_ROOT),
                            "world_root": str(generic),
                        }
                    )


class StudioCreationV3ServiceTests(unittest.TestCase):
    def test_service_revoke_is_durable_across_store_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            data_dir = base / "studio"
            with StudioStore(data_dir) as store:
                service = StudioService(store)
                grant = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 3,
                        "kind": "request",
                        "request_id": "request_grant",
                        "method": "creation_root_grant.create",
                        "params": {
                            "grant_id": "grant_revoke",
                            "role": "new_target",
                            "display_name": "Revoked target",
                            "path": str(base / "revoked-target"),
                            "expected_project_hash": None,
                        },
                    }
                )["result"]["grant"]
                revoked = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 3,
                        "kind": "request",
                        "request_id": "request_revoke",
                        "method": "creation_root_grant.revoke",
                        "params": {
                            "grant_id": grant["grant_id"],
                            "expected_generation": grant["generation"],
                        },
                    }
                )["result"]["grant"]
                self.assertEqual("revoked", revoked["state"])
                service.close()
            with StudioStore(data_dir) as reopened:
                persisted = CreationRootGrantManager(reopened).get(grant["grant_id"])
                self.assertEqual(revoked, persisted)

    def test_service_exposes_pathless_generic_read_only_methods(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="neutral_project",
                title="Neutral project",
            )
            project_hash = load_creation_project(root / "project.json").project["content_hash"]
            with StudioStore(base / "studio") as store:
                service = StudioService(store)
                try:
                    initialize = service.handle(
                        {
                            "protocol": "rpg-world-forge.studio_protocol",
                            "protocol_version": 3,
                            "kind": "request",
                            "request_id": "request_init",
                            "method": "service.initialize",
                            "params": {},
                        }
                    )
                    self.assertEqual(sorted(METHODS_V3), initialize["result"]["methods"])
                    grant = service.handle(
                        {
                            "protocol": "rpg-world-forge.studio_protocol",
                            "protocol_version": 3,
                            "kind": "request",
                            "request_id": "request_grant",
                            "method": "creation_root_grant.create",
                            "params": {
                                "grant_id": "grant_existing",
                                "role": "existing_root",
                                "display_name": "Neutral project",
                                "path": str(root),
                                "expected_project_hash": project_hash,
                            },
                        }
                    )["result"]["grant"]
                    registered = service.handle(
                        {
                            "protocol": "rpg-world-forge.studio_protocol",
                            "protocol_version": 3,
                            "kind": "request",
                            "request_id": "request_register",
                            "method": "creation_workspace.register",
                            "params": {
                                "workspace_id": "workspace_neutral",
                                "grant_id": grant["grant_id"],
                                "expected_grant_generation": grant["generation"],
                                "expected_project_hash": project_hash,
                            },
                        }
                    )["result"]["workspace"]
                    readiness = service.handle(
                        {
                            "protocol": "rpg-world-forge.studio_protocol",
                            "protocol_version": 3,
                            "kind": "request",
                            "request_id": "request_readiness",
                            "method": "creation_readiness.inspect",
                            "params": {
                                "workspace_id": "workspace_neutral",
                            },
                        }
                    )["result"]["readiness"]
                    self.assertEqual("not_started", readiness["state"])
                    self.assertEqual("blocked", readiness["release"])
                    for response in (initialize, grant, registered, readiness):
                        self.assertFalse(_contains_native_path(response, root))
                finally:
                    service.close()


if __name__ == "__main__":
    unittest.main()
