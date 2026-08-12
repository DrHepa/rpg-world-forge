from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from isoworld.content.publication_journal import canonical_journal_record
from worldforge.asset_io import AssetContractError
from worldforge.directory_publish import (
    create_append_only_journal,
    read_append_only_journal_state,
)
from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.service import StudioService
from worldforge.studio.storage import StudioStore
from worldforge.workflow import WorkflowError
from worldforge.world_lifecycle import (
    bump_world_version,
    clone_world_project,
    inspect_world_project,
    migrate_world_project,
    upgrade_legacy_world_project,
)


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_bytes())


def _write(path: Path, value: object) -> None:
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _set_project_identity(root: Path, version: int, repository: str) -> bytes:
    path = root / ".worldforge/project.json"
    project = _read(path)
    project["format_version"] = version
    project["tool_repository"] = repository
    _write(path, project)
    return path.read_bytes()


def _make_v2(root: Path, *, world_id: str = "legacy_source") -> bytes:
    create_world_project(
        root,
        world_id=world_id,
        title="Legacy Source",
        language="en",
        version="1.2.3",
    )
    return _set_project_identity(root, 2, "rpg-world-forge")


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _append_top_level_member(path: Path, name: str, raw_value: bytes) -> None:
    payload = path.read_bytes()
    if not payload.endswith(b"}\n"):
        raise AssertionError(f"expected an object document: {path}")
    path.write_bytes(payload[:-2] + f',\n  "{name}": '.encode("ascii") + raw_value + b"\n}\n")


def _run_cli(*arguments: object) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "worldforge", *(str(argument) for argument in arguments)],
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )


class WorldProjectV3ContractTests(unittest.TestCase):
    def test_new_reader_and_clone_use_coherent_v3_identity_without_mutating_v2(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = root / "current"
            create_world_project(
                current,
                world_id="current_world",
                title="Current World",
                language="en",
            )
            current_project = _read(current / ".worldforge/project.json")
            self.assertEqual(
                (3, "world-forge"),
                (
                    current_project["format_version"],
                    current_project["tool_repository"],
                ),
            )
            before = _tree_snapshot(current)
            self.assertEqual("current_world", inspect_world_project(current).world_id)
            self.assertEqual(before, _tree_snapshot(current))

            legacy = root / "legacy"
            legacy_bytes = _make_v2(legacy)
            before = _tree_snapshot(legacy)
            self.assertEqual("legacy_source", inspect_world_project(legacy).world_id)
            self.assertEqual(before, _tree_snapshot(legacy))
            self.assertEqual(legacy_bytes, (legacy / ".worldforge/project.json").read_bytes())

            clone = root / "clone"
            clone_world_project(
                legacy,
                clone,
                world_id="current_clone",
                title="Current Clone",
            )
            clone_project = _read(clone / ".worldforge/project.json")
            self.assertEqual(
                (3, "world-forge"),
                (
                    clone_project["format_version"],
                    clone_project["tool_repository"],
                ),
            )

    def test_v2_and_v3_repository_pairs_are_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2(root)
            cases = (
                (2, "world-forge"),
                (3, "rpg-world-forge"),
                (3, "another-forge"),
            )
            for version, repository in cases:
                _set_project_identity(root, version, repository)
                with self.subTest(version=version, repository=repository):
                    with self.assertRaisesRegex(WorkflowError, "tool_repository"):
                        inspect_world_project(root)

    def test_legacy_upgrade_stays_v2_while_bump_preserves_v2_or_v3_and_manifest_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "v1"
            _make_v2(legacy, world_id="legacy_v1")
            project = _read(legacy / ".worldforge/project.json")
            project["format_version"] = 1
            project.pop("project_kind")
            project.pop("world_version")
            _write(legacy / ".worldforge/project.json", project)
            world = _read(legacy / "source/world.json")
            world.pop("version")
            _write(legacy / "source/world.json", world)
            status = _read(legacy / ".worldforge/status.json")
            status.pop("world_version")
            _write(legacy / ".worldforge/status.json", status)

            upgrade_legacy_world_project(
                legacy,
                version="0.2.0",
                reason="Preserve the published v1 to v2 bridge",
                approved_by="test",
            )
            upgraded = _read(legacy / ".worldforge/project.json")
            self.assertEqual(
                (2, "rpg-world-forge"),
                (
                    upgraded["format_version"],
                    upgraded["tool_repository"],
                ),
            )

            current = root / "v3"
            create_world_project(
                current,
                world_id="current_v3",
                title="Current V3",
                language="en",
                version="2.0.0",
            )
            for project_root, expected, pair in (
                (legacy, "0.2.0", (2, "rpg-world-forge")),
                (current, "2.0.0", (3, "world-forge")),
            ):
                manifest_path = project_root / "source/manifest.json"
                manifest_bytes = manifest_path.read_bytes()
                manifest_identity = os.stat(manifest_path, follow_symlinks=False)
                bump_world_version(
                    project_root,
                    expected_version=expected,
                    part="patch",
                    reason="Identity-preserving bump",
                    approved_by="test",
                )
                project = _read(project_root / ".worldforge/project.json")
                after_identity = os.stat(manifest_path, follow_symlinks=False)
                self.assertEqual(pair, (project["format_version"], project["tool_repository"]))
                self.assertEqual(manifest_bytes, manifest_path.read_bytes())
                self.assertEqual(
                    (manifest_identity.st_dev, manifest_identity.st_ino),
                    (after_identity.st_dev, after_identity.st_ino),
                )

    def test_studio_registers_and_reads_v3_without_migration_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world = root / "world"
            create_world_project(
                world,
                world_id="studio_v3",
                title="Studio V3",
                language="en",
            )
            before = _tree_snapshot(world)
            store = StudioStore(root / "studio-data")
            self.addCleanup(store.close)
            service = StudioService(store)
            response = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 1,
                    "kind": "request",
                    "request_id": "register-v3",
                    "method": "workspace.register",
                    "params": {
                        "workspace_id": "workspace_v3",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(world),
                    },
                }
            )
            self.assertEqual("workspace_v3", response["result"]["workspace"]["workspace_id"])
            self.assertEqual(before, _tree_snapshot(world))

    def test_schema_and_catalog_publish_the_closed_v2_v3_pair(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        schema = json.loads((repository / "schemas/world-project.schema.json").read_bytes())
        pairs = {
            (
                variant["properties"]["format_version"]["const"],
                variant["properties"]["tool_repository"]["const"],
            )
            for variant in schema["oneOf"]
        }
        self.assertEqual({(2, "rpg-world-forge"), (3, "world-forge")}, pairs)

        catalog = json.loads((repository / "contracts/catalog.json").read_bytes())
        entry = next(item for item in catalog["contracts"] if item["id"] == "world-project")
        self.assertEqual(3, entry["version"])
        self.assertIn("migrate-world-project", entry["cli_commands"])
        self.assertIn(
            "worldforge.world_lifecycle:migrate_world_project",
            entry["python_symbols"],
        )
        migration_contracts = {
            item["id"]: item
            for item in catalog["contracts"]
            if item["id"].startswith("world-project-migration-")
        }
        self.assertEqual(
            {
                "world-project-migration-backup",
                "world-project-migration-evidence",
                "world-project-migration-journal",
            },
            set(migration_contracts),
        )
        for contract_id, migration_entry in migration_contracts.items():
            self.assertEqual(1, migration_entry["version"])
            self.assertEqual(
                f"world-forge.{contract_id.replace('-', '_')}",
                migration_entry["format"],
            )
            self.assertTrue((repository / migration_entry["schema"]).is_file())


class WorldProjectMigrationTests(unittest.TestCase):
    def test_recovery_shape_rejects_every_transition_impossible_combination(self) -> None:
        from worldforge import world_project_migration as migration

        allowed = (
            (2, None, False, None, False),
            (3, None, False, None, True),
            (2, None, True, None, False),
            (2, "prepared", True, None, False),
            (2, "prepared", True, "target", False),
            (3, "prepared", True, "source", False),
            (3, "replaced", True, None, False),
            (3, "replaced", True, "source", True),
            (3, "verified", True, None, True),
            (3, "cleanup_authorized", True, None, True),
            (3, "cleanup_authorized", False, None, True),
        )
        impossible = (
            (3, None, True, None, False),
            (3, None, True, "source", False),
            (2, None, True, "target", False),
            (2, None, True, None, True),
            (2, "prepared", False, None, False),
            (2, "prepared", True, None, True),
            (3, "prepared", True, None, False),
            (3, "prepared", True, "target", False),
            (2, "replaced", True, None, False),
            (3, "replaced", False, None, False),
            (3, "replaced", True, "target", False),
            (2, "verified", True, None, True),
            (3, "verified", True, "source", True),
            (3, "verified", True, None, False),
            (2, "cleanup_authorized", True, None, True),
            (3, "cleanup_authorized", False, "source", True),
            (3, "cleanup_authorized", False, None, False),
        )

        for values in allowed:
            with self.subTest(kind="allowed", values=values):
                self.assertIsNone(
                    migration._validate_recovery_shape(
                        project_version=values[0],
                        journal_state=values[1],
                        backup_present=values[2],
                        staged_role=values[3],
                        evidence_present=values[4],
                    )
                )
        for values in impossible:
            with self.subTest(kind="impossible", values=values):
                with self.assertRaisesRegex(
                    migration.WorldProjectMigrationError,
                    "transition-impossible",
                ) as raised:
                    migration._validate_recovery_shape(
                        project_version=values[0],
                        journal_state=values[1],
                        backup_present=values[2],
                        staged_role=values[3],
                        evidence_present=values[4],
                    )
                self.assertEqual(
                    "world_project_migration_state_diverged",
                    raised.exception.reason_code,
                )

    def test_v3_target_and_staged_source_cannot_forge_a_missing_prepared_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            source = _make_v2(root)
            expected = _sha256(source)

            def interrupt(event: str) -> None:
                if event == "after_backup_created":
                    raise OSError("interrupt after backup")

            with patch(
                "worldforge.world_project_migration._migration_transition_hook",
                side_effect=interrupt,
            ):
                with self.assertRaisesRegex(WorkflowError, "interrupt after backup"):
                    migrate_world_project(root, expected_source_hash=expected, mode="apply")

            backup_path = root / ".worldforge/project-migration.backup.json"
            loaded = read_append_only_journal_state(
                backup_path,
                max_record_bytes=8 * 1024 * 1024,
                max_file_bytes=8 * 1024 * 1024,
            )
            self.assertIsNotNone(loaded)
            backup_payload, _backup_identity, partial = loaded
            self.assertFalse(partial)
            operation_id = json.loads(backup_payload)["operation_id"]
            project_path = root / ".worldforge/project.json"
            target = _read(project_path)
            target["format_version"] = 3
            target["tool_repository"] = "world-forge"
            staged_source = (
                root / ".worldforge" / f".project.json.migration.{operation_id}.exchange"
            )
            os.link(project_path, staged_source)
            replacement = project_path.with_suffix(".forged-target")
            _write(replacement, target)
            os.replace(replacement, project_path)
            target_before = project_path.read_bytes()

            with self.assertRaisesRegex(WorkflowError, "transition-impossible") as raised:
                migrate_world_project(root, expected_source_hash=expected, mode="apply")

            self.assertEqual(
                "world_project_migration_state_diverged",
                raised.exception.reason_code,
            )
            self.assertEqual(target_before, project_path.read_bytes())
            self.assertEqual(source, staged_source.read_bytes())
            self.assertFalse((root / ".worldforge/project-migration.journal.json").exists())

    def test_dry_run_is_deterministic_and_has_no_filesystem_effects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            source = _make_v2(root)
            expected = _sha256(source)
            before = _tree_snapshot(root)

            first = migrate_world_project(root, expected_source_hash=expected, mode="dry-run")
            second = migrate_world_project(root, expected_source_hash=expected, mode="dry-run")

            self.assertEqual(first, second)
            self.assertEqual("would_migrate", first["status"])
            self.assertEqual("dry-run", first["mode"])
            self.assertEqual(2, first["from_format_version"])
            self.assertEqual(3, first["to_format_version"])
            self.assertEqual(expected, first["source_sha256"])
            self.assertRegex(first["target_sha256"], r"^[0-9a-f]{64}$")
            self.assertNotEqual(first["source_sha256"], first["target_sha256"])
            self.assertTrue(first["apply_supported"])
            self.assertIsNone(first["apply_capability_reason"])
            self.assertEqual(before, _tree_snapshot(root))

    def test_dry_run_truthfully_reports_apply_capability_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            source = _make_v2(root)
            before = _tree_snapshot(root)

            with patch(
                "worldforge.world_lock.world_project_migration_apply_capability",
                return_value=(False, "local_ntfs_required"),
            ):
                result = migrate_world_project(
                    root,
                    expected_source_hash=_sha256(source),
                    mode="dry-run",
                )

            self.assertEqual("would_migrate", result["status"])
            self.assertFalse(result["apply_supported"])
            self.assertEqual("local_ntfs_required", result["apply_capability_reason"])
            self.assertEqual(before, _tree_snapshot(root))

    def test_dry_run_rejects_untrusted_migration_artifacts(self) -> None:
        for artifact_name in (
            "project-migration.backup.json",
            "project-migration.journal.json",
        ):
            with (
                self.subTest(artifact_name=artifact_name),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory) / "world"
                source = _make_v2(root)
                artifact = root / ".worldforge" / artifact_name
                artifact.write_bytes(b"untrusted migration state\n")

                with self.assertRaises(WorkflowError) as raised:
                    migrate_world_project(
                        root,
                        expected_source_hash=_sha256(source),
                        mode="dry-run",
                    )

                self.assertEqual(
                    "world_project_migration_state_diverged",
                    raised.exception.reason_code,
                )

    def test_dry_run_reports_recovery_required_for_a_valid_interrupted_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            source = _make_v2(root)
            expected = _sha256(source)

            def interrupt(event: str) -> None:
                if event == "after_backup_created":
                    raise OSError("interrupt after backup")

            with patch(
                "worldforge.world_project_migration._migration_transition_hook",
                side_effect=interrupt,
            ):
                with self.assertRaisesRegex(WorkflowError, "interrupt after backup"):
                    migrate_world_project(root, expected_source_hash=expected, mode="apply")

            before = _tree_snapshot(root)
            with self.assertRaises(WorkflowError) as raised:
                migrate_world_project(root, expected_source_hash=expected, mode="dry-run")

            self.assertEqual(
                "world_project_migration_recovery_required",
                raised.exception.reason_code,
            )
            self.assertEqual(before, _tree_snapshot(root))

    def test_apply_migrates_exact_bytes_and_is_idempotent_with_original_source_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            source = _make_v2(root)
            expected = _sha256(source)
            manifest_path = root / "source/manifest.json"
            manifest_bytes = manifest_path.read_bytes()

            result = migrate_world_project(root, expected_source_hash=expected, mode="apply")

            self.assertEqual("migrated", result["status"])
            self.assertEqual(expected, result["source_sha256"])
            project_bytes = (root / ".worldforge/project.json").read_bytes()
            self.assertEqual(_sha256(project_bytes), result["target_sha256"])
            self.assertEqual(
                (3, "world-forge"),
                (
                    _read(root / ".worldforge/project.json")["format_version"],
                    _read(root / ".worldforge/project.json")["tool_repository"],
                ),
            )
            self.assertEqual(manifest_bytes, manifest_path.read_bytes())
            self.assertFalse((root / ".worldforge/project-migration.backup.json").exists())
            self.assertFalse((root / ".worldforge/project-migration.journal.json").exists())
            evidence_path = root / ".worldforge/project-migration-v3.evidence.json"
            self.assertTrue(evidence_path.is_file())
            evidence_before = evidence_path.read_bytes()
            evidence = json.loads(evidence_before)
            self.assertRegex(evidence["operation_id"], r"^[0-9a-f]{64}$")
            self.assertNotEqual(
                _sha256(f"{expected}:{result['target_sha256']}".encode("ascii")),
                evidence["operation_id"],
            )

            repeated = migrate_world_project(root, expected_source_hash=expected, mode="apply")
            self.assertEqual("already_current", repeated["status"])
            self.assertEqual(3, repeated["from_format_version"])
            self.assertEqual(3, repeated["to_format_version"])
            self.assertEqual(result["target_sha256"], repeated["source_sha256"])
            self.assertEqual(result["target_sha256"], repeated["target_sha256"])
            self.assertIsNone(repeated["evidence_sha256"])
            self.assertEqual(evidence_before, evidence_path.read_bytes())

    def test_native_v3_with_forged_predictable_evidence_is_only_already_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            create_world_project(
                root,
                world_id="native_current",
                title="Native Current",
                language="en",
            )
            project_path = root / ".worldforge/project.json"
            target_payload = project_path.read_bytes()
            target_hash = _sha256(target_payload)
            arbitrary_source_hash = "1" * 64
            target_info = os.stat(project_path, follow_symlinks=False)
            predictable_operation = _sha256(
                f"{arbitrary_source_hash}:{target_hash}".encode("ascii")
            )
            forged = canonical_journal_record(
                {
                    "format": "world-forge.world_project_migration_evidence",
                    "format_version": 1,
                    "operation_id": predictable_operation,
                    "from_format_version": 2,
                    "to_format_version": 3,
                    "source_sha256": arbitrary_source_hash,
                    "target_sha256": target_hash,
                    "target_identity": {
                        "device": target_info.st_dev,
                        "inode": target_info.st_ino,
                    },
                    "status": "verified",
                }
            )
            create_append_only_journal(
                root / ".worldforge/project-migration-v3.evidence.json",
                forged,
                max_record_bytes=8 * 1024 * 1024,
            )

            result = migrate_world_project(
                root,
                expected_source_hash=arbitrary_source_hash,
                mode="apply",
            )

            self.assertEqual(
                {
                    "status": "already_current",
                    "mode": "apply",
                    "format": "rpg-world-forge.project",
                    "from_format_version": 3,
                    "to_format_version": 3,
                    "source_sha256": target_hash,
                    "target_sha256": target_hash,
                    "evidence_sha256": None,
                },
                result,
            )

    def test_rejects_malformed_stale_and_raced_source_hashes_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            source = _make_v2(root)
            path = root / ".worldforge/project.json"
            for expected in ("A" * 64, "0" * 63, "0" * 65, "z" * 64):
                with self.subTest(expected=expected):
                    with self.assertRaisesRegex(WorkflowError, "lowercase SHA-256"):
                        migrate_world_project(root, expected_source_hash=expected, mode="dry-run")

            with self.assertRaisesRegex(WorkflowError, "expected source hash"):
                migrate_world_project(root, expected_source_hash="0" * 64, mode="apply")
            self.assertEqual(source, path.read_bytes())

            expected = _sha256(source)

            def race(event: str) -> None:
                if event == "before_replacement":
                    mutated = _read(path)
                    mutated["title"] = "Raced title"
                    _write(path, mutated)

            with patch(
                "worldforge.world_project_migration._migration_transition_hook",
                side_effect=race,
            ):
                with self.assertRaisesRegex(WorkflowError, "changed before replacement"):
                    migrate_world_project(root, expected_source_hash=expected, mode="apply")
            self.assertEqual("Raced title", _read(path)["title"])

    def test_v1_requires_upgrade_world_and_native_v3_is_already_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy"
            source = _make_v2(legacy)
            project = _read(legacy / ".worldforge/project.json")
            project["format_version"] = 1
            project.pop("project_kind")
            project.pop("world_version")
            _write(legacy / ".worldforge/project.json", project)
            with self.assertRaisesRegex(WorkflowError, "upgrade-world"):
                migrate_world_project(
                    legacy,
                    expected_source_hash=_sha256(
                        (legacy / ".worldforge/project.json").read_bytes()
                    ),
                    mode="apply",
                )

            current = root / "current"
            create_world_project(
                current,
                world_id="new_current",
                title="New Current",
                language="en",
            )
            current_hash = _sha256((current / ".worldforge/project.json").read_bytes())
            result = migrate_world_project(
                current,
                expected_source_hash=current_hash,
                mode="apply",
            )
            self.assertEqual("already_current", result["status"])
            self.assertEqual(3, result["from_format_version"])
            self.assertEqual(3, result["to_format_version"])
            self.assertIsNone(result["evidence_sha256"])
            self.assertEqual(source, source)

    def test_interrupted_apply_recovers_each_persistent_transition(self) -> None:
        events = (
            "after_backup_created",
            "after_journal_prepared",
            "after_identity_exchange",
            "after_replacement",
            "after_journal_replaced",
            "after_evidence_created",
            "after_journal_verified",
            "after_cleanup_authorized",
            "after_backup_removed",
        )
        for event in events:
            with self.subTest(event=event), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "world"
                source = _make_v2(root)
                expected = _sha256(source)
                triggered = False

                def interrupt(observed: str, expected_event: str = event) -> None:
                    nonlocal triggered
                    if observed == expected_event and not triggered:
                        triggered = True
                        raise OSError(f"interrupted at {expected_event}")

                with patch(
                    "worldforge.world_project_migration._migration_transition_hook",
                    side_effect=interrupt,
                ):
                    with self.assertRaisesRegex(WorkflowError, event) as raised:
                        migrate_world_project(root, expected_source_hash=expected, mode="apply")
                self.assertEqual(
                    "world_project_migration_io_failed",
                    raised.exception.reason_code,
                )
                self.assertTrue(triggered)
                recovered = migrate_world_project(
                    root,
                    expected_source_hash=expected,
                    mode="apply",
                )
                self.assertIn(recovered["status"], {"migrated", "already_migrated"})
                self.assertEqual(3, _read(root / ".worldforge/project.json")["format_version"])
                self.assertFalse((root / ".worldforge/project-migration.backup.json").exists())
                self.assertFalse((root / ".worldforge/project-migration.journal.json").exists())

    def test_recovery_rejects_replaced_backup_and_journal_identities(self) -> None:
        for interrupted_event, artifact_name in (
            ("after_backup_created", "project-migration.backup.json"),
            ("after_journal_prepared", "project-migration.journal.json"),
        ):
            with (
                self.subTest(artifact=artifact_name),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory) / "world"
                expected = _sha256(_make_v2(root))

                def interrupt(event: str, expected_event: str = interrupted_event) -> None:
                    if event == expected_event:
                        raise OSError("injected interruption")

                with patch(
                    "worldforge.world_project_migration._migration_transition_hook",
                    side_effect=interrupt,
                ):
                    with self.assertRaises(WorkflowError):
                        migrate_world_project(root, expected_source_hash=expected, mode="apply")
                artifact = root / ".worldforge" / artifact_name
                displaced = artifact.with_suffix(".displaced")
                artifact.rename(displaced)
                artifact.write_text('{"replacement":true}\n', encoding="utf-8")
                with self.assertRaisesRegex(WorkflowError, "migration .* diverged"):
                    migrate_world_project(root, expected_source_hash=expected, mode="apply")
                self.assertTrue(displaced.is_file())

    def test_recovery_rejects_same_bytes_with_a_different_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            expected = _sha256(_make_v2(root))
            project = root / ".worldforge/project.json"

            with patch(
                "worldforge.world_project_migration._migration_transition_hook",
                side_effect=lambda event: (
                    (_ for _ in ()).throw(OSError("interrupt after backup"))
                    if event == "after_backup_created"
                    else None
                ),
            ):
                with self.assertRaisesRegex(WorkflowError, "interrupt after backup"):
                    migrate_world_project(root, expected_source_hash=expected, mode="apply")

            replacement = project.with_suffix(".replacement")
            replacement.write_bytes(project.read_bytes())
            os.replace(replacement, project)
            replacement_identity = os.stat(project, follow_symlinks=False)

            with self.assertRaisesRegex(WorkflowError, "source identity diverged"):
                migrate_world_project(root, expected_source_hash=expected, mode="apply")
            after = os.stat(project, follow_symlinks=False)
            self.assertEqual(
                (replacement_identity.st_dev, replacement_identity.st_ino),
                (after.st_dev, after.st_ino),
            )

    def test_replacement_between_cas_check_and_exchange_rolls_back_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            expected = _sha256(_make_v2(root))
            project = root / ".worldforge/project.json"
            raced_identity: tuple[int, int] | None = None

            def replace_between_stages(event: str) -> None:
                nonlocal raced_identity
                if event != "before_identity_exchange":
                    return
                replacement = project.with_suffix(".raced")
                replacement.write_bytes(project.read_bytes())
                os.replace(replacement, project)
                info = os.stat(project, follow_symlinks=False)
                raced_identity = info.st_dev, info.st_ino

            with patch(
                "worldforge.world_project_migration._migration_transition_hook",
                side_effect=replace_between_stages,
            ):
                with self.assertRaisesRegex(WorkflowError, "source identity diverged"):
                    migrate_world_project(root, expected_source_hash=expected, mode="apply")

            self.assertIsNotNone(raced_identity)
            after = os.stat(project, follow_symlinks=False)
            self.assertEqual(raced_identity, (after.st_dev, after.st_ino))
            self.assertEqual(2, _read(project)["format_version"])

    def test_root_replacement_before_exchange_fails_without_touching_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / "world"
            expected = _sha256(_make_v2(root, world_id="retained_source"))
            displaced = parent / "displaced"
            replacement_bytes: bytes | None = None

            def replace_root(event: str) -> None:
                nonlocal replacement_bytes
                if event != "before_identity_exchange":
                    return
                root.rename(displaced)
                replacement_bytes = _make_v2(root, world_id="replacement_world")

            with patch(
                "worldforge.world_project_migration._migration_transition_hook",
                side_effect=replace_root,
            ):
                with self.assertRaisesRegex(WorkflowError, "root.*changed"):
                    migrate_world_project(root, expected_source_hash=expected, mode="apply")

            self.assertIsNotNone(replacement_bytes)
            self.assertEqual(replacement_bytes, (root / ".worldforge/project.json").read_bytes())
            self.assertEqual(2, _read(root / ".worldforge/project.json")["format_version"])
            self.assertEqual(
                1,
                os.stat(
                    displaced / ".worldforge/lifecycle.lock",
                    follow_symlinks=False,
                ).st_size,
            )

    def test_parent_ancestry_replacement_before_exchange_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            container = Path(directory)
            parent = container / "projects"
            root = parent / "world"
            expected = _sha256(_make_v2(root, world_id="retained_source"))
            displaced_parent = container / "displaced-projects"
            replacement_bytes: bytes | None = None

            def replace_parent(event: str) -> None:
                nonlocal replacement_bytes
                if event != "before_identity_exchange":
                    return
                parent.rename(displaced_parent)
                replacement_bytes = _make_v2(
                    parent / "world",
                    world_id="replacement_world",
                )

            with patch(
                "worldforge.world_project_migration._migration_transition_hook",
                side_effect=replace_parent,
            ):
                with self.assertRaisesRegex(WorkflowError, "root ancestry.*changed"):
                    migrate_world_project(root, expected_source_hash=expected, mode="apply")

            self.assertIsNotNone(replacement_bytes)
            self.assertEqual(replacement_bytes, (root / ".worldforge/project.json").read_bytes())
            self.assertEqual(2, _read(root / ".worldforge/project.json")["format_version"])
            self.assertEqual(
                1,
                os.stat(
                    displaced_parent / "world/.worldforge/lifecycle.lock",
                    follow_symlinks=False,
                ).st_size,
            )

    def test_captured_invalid_project_cannot_be_sanitized_by_swap_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2(root)
            project_path = root / ".worldforge/project.json"
            project = _read(project_path)
            project["unknown_control"] = True
            _write(project_path, project)
            invalid_bytes = project_path.read_bytes()
            clean = dict(project)
            clean.pop("unknown_control")
            expected = _sha256(invalid_bytes)

            from worldforge import world_lifecycle

            original_read = world_lifecycle._read_object

            def sanitize_during_path_read(path: Path, *, error_type: type[ValueError]):
                if path.name != "project.json":
                    return original_read(path, error_type=error_type)
                _write(path, clean)
                try:
                    return original_read(path, error_type=error_type)
                finally:
                    path.write_bytes(invalid_bytes)

            with patch.object(
                world_lifecycle,
                "_read_object",
                side_effect=sanitize_during_path_read,
            ):
                with self.assertRaisesRegex(WorkflowError, "unknown fields"):
                    migrate_world_project(root, expected_source_hash=expected, mode="dry-run")
            self.assertEqual(invalid_bytes, project_path.read_bytes())

    def test_every_control_rejects_ambiguous_or_non_finite_json(self) -> None:
        cases = (
            (".worldforge/project.json", "runtime_ai", b"false"),
            (".worldforge/status.json", "world_id", b'"legacy_source"'),
            ("source/manifest.json", "format_version", b"1"),
            ("source/world.json", "id", b'"legacy_source"'),
        )
        for relative, duplicate_key, duplicate_value in cases:
            for label, mutate in (
                (
                    "duplicate",
                    lambda path, key=duplicate_key, value=duplicate_value: _append_top_level_member(
                        path, key, value
                    ),
                ),
                ("nan", lambda path: _append_top_level_member(path, "strict_probe", b"NaN")),
                (
                    "infinity",
                    lambda path: _append_top_level_member(path, "strict_probe", b"Infinity"),
                ),
                (
                    "overflow",
                    lambda path: _append_top_level_member(path, "strict_probe", b"1e999"),
                ),
                ("non_object", lambda path: path.write_bytes(b"[]\n")),
                ("invalid_utf8", lambda path: path.write_bytes(path.read_bytes() + b"\xff")),
            ):
                with (
                    self.subTest(control=relative, mutation=label),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory) / "world"
                    _make_v2(root)
                    path = root / relative
                    mutate(path)
                    expected = _sha256((root / ".worldforge/project.json").read_bytes())
                    with self.assertRaises(WorkflowError):
                        migrate_world_project(
                            root,
                            expected_source_hash=expected,
                            mode="dry-run",
                        )

    def test_closed_controls_reject_unknown_properties(self) -> None:
        for relative in (
            ".worldforge/project.json",
            ".worldforge/status.json",
            "source/manifest.json",
            "source/world.json",
        ):
            with self.subTest(control=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "world"
                _make_v2(root)
                _append_top_level_member(root / relative, "unknown_control", b"true")
                expected = _sha256((root / ".worldforge/project.json").read_bytes())
                with self.assertRaisesRegex(WorkflowError, "unknown"):
                    migrate_world_project(root, expected_source_hash=expected, mode="dry-run")

    def test_dangling_journal_symlink_is_divergence_not_absence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world = root / "world"
            create_world_project(
                world,
                world_id="native_v3",
                title="Native V3",
                language="en",
            )
            journal = world / ".worldforge/project-migration.journal.json"
            try:
                journal.symlink_to(root / "missing-journal")
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are unavailable")
            expected = _sha256((world / ".worldforge/project.json").read_bytes())
            with self.assertRaisesRegex(WorkflowError, "journal diverged"):
                migrate_world_project(world, expected_source_hash=expected, mode="apply")

    def test_verified_journal_does_not_authorize_unrecorded_backup_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            expected = _sha256(_make_v2(root))

            def interrupt(event: str) -> None:
                if event == "after_journal_verified":
                    raise OSError("interrupt before cleanup authorization")

            with patch(
                "worldforge.world_project_migration._migration_transition_hook",
                side_effect=interrupt,
            ):
                with self.assertRaisesRegex(WorkflowError, "cleanup authorization"):
                    migrate_world_project(root, expected_source_hash=expected, mode="apply")
            (root / ".worldforge/project-migration.backup.json").unlink()

            with self.assertRaisesRegex(WorkflowError, "backup.*absent"):
                migrate_world_project(root, expected_source_hash=expected, mode="apply")

    def test_verified_journal_never_recreates_missing_bound_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            expected = _sha256(_make_v2(root))

            def interrupt(event: str) -> None:
                if event == "after_journal_verified":
                    raise OSError("interrupt after verified evidence")

            with patch(
                "worldforge.world_project_migration._migration_transition_hook",
                side_effect=interrupt,
            ):
                with self.assertRaisesRegex(WorkflowError, "verified evidence"):
                    migrate_world_project(root, expected_source_hash=expected, mode="apply")
            evidence = root / ".worldforge/project-migration-v3.evidence.json"
            evidence.unlink()

            with self.assertRaisesRegex(WorkflowError, "evidence.*absent"):
                migrate_world_project(root, expected_source_hash=expected, mode="apply")

            self.assertFalse(evidence.exists())
            self.assertTrue((root / ".worldforge/project-migration.backup.json").is_file())
            self.assertTrue((root / ".worldforge/project-migration.journal.json").is_file())

    def test_permanent_stale_lifecycle_lock_is_reacquired_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            expected = _sha256(_make_v2(root))
            lock = root / ".worldforge/lifecycle.lock"
            lock.write_bytes(b"\x00")
            identity = os.stat(lock, follow_symlinks=False)

            migrated = migrate_world_project(
                root,
                expected_source_hash=expected,
                mode="apply",
            )

            after = os.stat(lock, follow_symlinks=False)
            self.assertEqual("migrated", migrated["status"])
            self.assertEqual(1, after.st_size)
            self.assertEqual(
                (identity.st_dev, identity.st_ino),
                (after.st_dev, after.st_ino),
            )

    @unittest.skipUnless(os.name == "posix", "requires POSIX process locks")
    def test_process_exit_releases_permanent_lock_at_every_persistent_transition(self) -> None:
        events = (
            "after_backup_created",
            "after_journal_prepared",
            "after_identity_exchange",
            "after_replacement",
            "after_journal_replaced",
            "after_evidence_created",
            "after_journal_verified",
            "after_cleanup_authorized",
            "after_backup_removed",
        )
        program = """
import os
import sys
from pathlib import Path
import worldforge.world_project_migration as migration

root = Path(sys.argv[1])
expected = sys.argv[2]
event = sys.argv[3]
def interrupt(observed):
    if observed == event:
        os._exit(73)
migration._migration_transition_hook = interrupt
migration.migrate_world_project(root, expected_source_hash=expected, mode="apply")
raise SystemExit(74)
"""
        for event in events:
            with self.subTest(event=event), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "world"
                expected = _sha256(_make_v2(root))
                environment = os.environ.copy()
                environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
                crashed = subprocess.run(
                    [sys.executable, "-c", program, str(root), expected, event],
                    env=environment,
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(73, crashed.returncode, crashed.stdout + crashed.stderr)
                lock = root / ".worldforge/lifecycle.lock"
                self.assertEqual(1, os.stat(lock, follow_symlinks=False).st_size)
                lock_identity = os.stat(lock, follow_symlinks=False)

                recovered = migrate_world_project(
                    root,
                    expected_source_hash=expected,
                    mode="apply",
                )

                self.assertIn(recovered["status"], {"migrated", "already_migrated"})
                after = os.stat(lock, follow_symlinks=False)
                self.assertEqual(
                    (lock_identity.st_dev, lock_identity.st_ino),
                    (after.st_dev, after.st_ino),
                )
                self.assertEqual(1, after.st_size)

    def test_apply_fails_closed_without_retained_platform_capability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            expected = _sha256(_make_v2(root))
            before = _tree_snapshot(root)

            with patch(
                "worldforge.world_lock.retained_world_lifecycle_supported",
                return_value=False,
            ):
                with self.assertRaisesRegex(
                    WorkflowError,
                    "Retained world lifecycle primitives are unavailable",
                ) as raised:
                    migrate_world_project(
                        root,
                        expected_source_hash=expected,
                        mode="apply",
                    )

            self.assertEqual(
                "world_project_migration_capability_unavailable",
                raised.exception.reason_code,
            )
            self.assertEqual(before, _tree_snapshot(root))

    def test_apply_reports_missing_exchange_capability_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            source = _make_v2(root)
            expected = _sha256(source)
            project = root / ".worldforge/project.json"

            with patch(
                "worldforge.asset_io._linux_exchange_names",
                side_effect=AssetContractError("identity-atomic replacement is unavailable"),
            ):
                with self.assertRaisesRegex(
                    WorkflowError,
                    "identity-atomic replacement is unavailable",
                ) as raised:
                    migrate_world_project(
                        root,
                        expected_source_hash=expected,
                        mode="apply",
                    )

            self.assertEqual(
                "world_project_migration_capability_unavailable",
                raised.exception.reason_code,
            )
            self.assertEqual(source, project.read_bytes())
            self.assertEqual(
                1,
                os.stat(root / ".worldforge/lifecycle.lock", follow_symlinks=False).st_size,
            )
            self.assertEqual(
                [],
                list((root / ".worldforge").glob(".project.json.migration.*.exchange")),
            )

    def test_project_hardlinks_and_migration_artifact_symlinks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world = root / "world"
            expected = _sha256(_make_v2(world))
            project = world / ".worldforge/project.json"
            hardlink = root / "project-hardlink.json"
            try:
                os.link(project, hardlink)
            except (OSError, NotImplementedError):
                self.skipTest("hard links are unavailable")
            with self.assertRaisesRegex(WorkflowError, "standalone regular file"):
                migrate_world_project(world, expected_source_hash=expected, mode="apply")
            hardlink.unlink()

            external = root / "external-backup.json"
            external.write_text('{"external":true}\n', encoding="utf-8")
            backup = world / ".worldforge/project-migration.backup.json"
            try:
                os.symlink(external, backup)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are unavailable")
            with self.assertRaisesRegex(WorkflowError, "migration backup diverged"):
                migrate_world_project(world, expected_source_hash=expected, mode="apply")
            self.assertEqual('{"external":true}\n', external.read_text(encoding="utf-8"))

    def test_every_validated_control_rejects_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world = root / "world"
            source = _make_v2(world)
            status = world / ".worldforge/status.json"
            hardlink = root / "status-hardlink.json"
            try:
                os.link(status, hardlink)
            except (OSError, NotImplementedError):
                self.skipTest("hard links are unavailable")

            with self.assertRaisesRegex(WorkflowError, "standalone regular file"):
                migrate_world_project(
                    world,
                    expected_source_hash=_sha256(source),
                    mode="dry-run",
                )


class WorldProjectMigrationCliTests(unittest.TestCase):
    def test_cli_frames_indeterminate_cleanup_as_json_stderr(self) -> None:
        import worldforge.__main__ as cli
        from worldforge.world_project_migration import WorldProjectMigrationError

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(
                cli,
                "migrate_world_project",
                side_effect=WorldProjectMigrationError(
                    "world_project_migration_outcome_indeterminate",
                    "migration backup cleanup became indeterminate",
                ),
            ),
            patch.object(
                sys,
                "argv",
                [
                    "worldforge",
                    "migrate-world-project",
                    "C:/world",
                    "--expected-source-hash",
                    "a" * 64,
                    "--mode",
                    "apply",
                ],
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            return_code = cli.main()

        self.assertEqual(1, return_code)
        self.assertEqual("", stdout.getvalue())
        error = json.loads(stderr.getvalue())
        self.assertEqual("error", error["status"])
        self.assertEqual(
            "world_project_migration_outcome_indeterminate",
            error["reason_code"],
        )
        self.assertEqual(
            "migration backup cleanup became indeterminate",
            error["detail"],
        )
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_dry_run_apply_and_idempotent_results_are_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            expected = _sha256(_make_v2(root))
            dry_run = _run_cli(
                "migrate-world-project",
                root,
                "--expected-source-hash",
                expected,
                "--mode",
                "dry-run",
            )
            self.assertEqual(0, dry_run.returncode, dry_run.stdout + dry_run.stderr)
            self.assertEqual("would_migrate", json.loads(dry_run.stdout)["status"])
            self.assertEqual("", dry_run.stderr)

            applied = _run_cli(
                "migrate-world-project",
                root,
                "--expected-source-hash",
                expected,
                "--mode",
                "apply",
            )
            self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
            self.assertEqual("migrated", json.loads(applied.stdout)["status"])
            repeated = _run_cli(
                "migrate-world-project",
                root,
                "--expected-source-hash",
                expected,
                "--mode",
                "apply",
            )
            self.assertEqual(0, repeated.returncode, repeated.stdout + repeated.stderr)
            self.assertEqual("already_current", json.loads(repeated.stdout)["status"])

    def test_cli_contract_error_is_stderr_exit_one_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2(root)
            result = _run_cli(
                "migrate-world-project",
                root,
                "--expected-source-hash",
                "0" * 64,
                "--mode",
                "apply",
            )
            self.assertEqual(1, result.returncode)
            self.assertEqual("", result.stdout)
            error = json.loads(result.stderr)
            self.assertEqual("error", error["status"])
            self.assertEqual(
                "world_project_migration_expected_hash_mismatch",
                error["reason_code"],
            )
            self.assertNotIn("Traceback", result.stderr)

    def test_lock_and_semantic_failures_are_json_stderr_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            expected = _sha256(_make_v2(root))
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
            holder_program = """
import sys
from pathlib import Path
from worldforge.workflow import WorkflowError
from worldforge.world_lock import exclusive_world_lifecycle
with exclusive_world_lifecycle(Path(sys.argv[1]), error_type=WorkflowError):
    print("locked", flush=True)
    sys.stdin.read(1)
"""
            holder = subprocess.Popen(
                [sys.executable, "-c", holder_program, str(root)],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertIsNotNone(holder.stdout)
            self.assertEqual("locked", holder.stdout.readline().strip())
            try:
                locked = _run_cli(
                    "migrate-world-project",
                    root,
                    "--expected-source-hash",
                    expected,
                    "--mode",
                    "apply",
                )
            finally:
                self.assertIsNotNone(holder.stdin)
                remaining_stdout, holder_stderr = holder.communicate("x", timeout=10)
                self.assertEqual("", remaining_stdout)
                self.assertEqual("", holder_stderr)
                self.assertEqual(0, holder.returncode)
            self.assertEqual(1, locked.returncode)
            self.assertEqual("", locked.stdout)
            lock_error = json.loads(locked.stderr)
            self.assertEqual("world_project_migration_lock_unavailable", lock_error["reason_code"])
            self.assertNotIn("Traceback", locked.stderr)

            project = _read(root / ".worldforge/project.json")
            project["unknown_control"] = True
            _write(root / ".worldforge/project.json", project)
            semantic = _run_cli(
                "migrate-world-project",
                root,
                "--expected-source-hash",
                _sha256((root / ".worldforge/project.json").read_bytes()),
                "--mode",
                "apply",
            )
            self.assertEqual(1, semantic.returncode)
            self.assertEqual("", semantic.stdout)
            semantic_error = json.loads(semantic.stderr)
            self.assertEqual(
                "world_project_migration_project_invalid",
                semantic_error["reason_code"],
            )
            self.assertNotIn("Traceback", semantic.stderr)


if __name__ == "__main__":
    unittest.main()
