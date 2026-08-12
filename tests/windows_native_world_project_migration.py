from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from worldforge.scaffold import create_world_project
from worldforge.world_project_migration import migrate_world_project

if os.name != "nt":
    raise SystemExit("native Windows world-project migration gate requires Windows")


def _make_v2(root: Path, *, world_id: str) -> bytes:
    create_world_project(
        root,
        world_id=world_id,
        title="Native Windows Migration",
        language="en",
        version="1.2.3",
    )
    project_path = root / ".worldforge/project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["format_version"] = 2
    project["tool_repository"] = "rpg-world-forge"
    project_path.write_text(
        json.dumps(project, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return project_path.read_bytes()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class NativeWindowsWorldProjectMigrationTests(unittest.TestCase):
    def test_local_ntfs_apply_is_durable_idempotent_and_cleans_retention(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            source = _make_v2(root, world_id="native_windows_apply")
            expected = _sha256(source)

            dry_run = migrate_world_project(
                root,
                expected_source_hash=expected,
                mode="dry-run",
            )
            self.assertEqual("would_migrate", dry_run["status"])
            self.assertTrue(
                dry_run["apply_supported"],
                dry_run["apply_capability_reason"],
            )

            migrated = migrate_world_project(
                root,
                expected_source_hash=expected,
                mode="apply",
            )
            self.assertEqual("migrated", migrated["status"])
            project_path = root / ".worldforge/project.json"
            self.assertEqual(migrated["target_sha256"], _sha256(project_path.read_bytes()))
            self.assertEqual(1, (root / ".worldforge/lifecycle.lock").stat().st_size)
            self.assertFalse(tuple((root / ".worldforge").glob(".project.json.migration.*")))
            self.assertFalse((root / ".worldforge/project-migration.backup.json").exists())
            self.assertFalse((root / ".worldforge/project-migration.journal.json").exists())
            self.assertTrue((root / ".worldforge/project-migration-v3.evidence.json").is_file())

            repeated = migrate_world_project(
                root,
                expected_source_hash=expected,
                mode="apply",
            )
            self.assertEqual("already_current", repeated["status"])

    def test_process_exit_recovers_after_retention_and_after_rename(self) -> None:
        child = r"""
import os
import sys
from pathlib import Path
import worldforge.world_project_migration as migration

event = sys.argv[3]
def interrupt(observed):
    if observed == event:
        os._exit(73)
migration._migration_transition_hook = interrupt
migration.migrate_world_project(
    Path(sys.argv[1]),
    expected_source_hash=sys.argv[2],
    mode="apply",
)
raise SystemExit(91)
"""
        for boundary in ("after_windows_retention_link", "after_windows_rename"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "world"
                source = _make_v2(root, world_id=f"native_{boundary}")
                expected = _sha256(source)
                crashed = subprocess.run(
                    [sys.executable, "-c", child, str(root), expected, boundary],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(73, crashed.returncode, crashed.stdout + crashed.stderr)
                self.assertEqual(1, (root / ".worldforge/lifecycle.lock").stat().st_size)

                recovered = migrate_world_project(
                    root,
                    expected_source_hash=expected,
                    mode="apply",
                )
                self.assertEqual("migrated", recovered["status"])
                self.assertFalse(tuple((root / ".worldforge").glob(".project.json.migration.*")))
                self.assertEqual(
                    recovered["target_sha256"],
                    _sha256((root / ".worldforge/project.json").read_bytes()),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
