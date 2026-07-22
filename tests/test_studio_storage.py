from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from worldforge.studio.errors import StudioError
from worldforge.studio.storage import StudioStore, encode_json


class StudioStorageTests(unittest.TestCase):
    def test_creates_hardened_schema_and_rejects_future_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "studio"
            with StudioStore(data_dir) as store:
                tables = {
                    row[0]
                    for row in store.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {"schema_meta", "workspaces", "changesets", "jobs", "events"} <= tables
                )
                self.assertEqual(1, store.connection.execute("PRAGMA foreign_keys").fetchone()[0])
                self.assertEqual(2, store.connection.execute("PRAGMA synchronous").fetchone()[0])
                self.assertEqual(
                    "wal", store.connection.execute("PRAGMA journal_mode").fetchone()[0]
                )

            connection = sqlite3.connect(data_dir / "studio.sqlite3")
            connection.execute("UPDATE schema_meta SET value = '999' WHERE key = 'schema_version'")
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(StudioError, "newer schema"):
                StudioStore(data_dir)

    def test_startup_orphans_running_jobs_and_records_an_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "studio"
            with StudioStore(data_dir) as store:
                timestamp = "2026-07-22T12:00:00Z"
                workspace = {
                    "format": "rpg-world-forge.forge_workspace",
                    "format_version": 1,
                    "workspace_id": "workspace_01",
                    "forge_root": "/forge",
                    "world_root": "/world",
                    "game_root": None,
                    "bundle_root": None,
                    "created_at": timestamp,
                }
                store.connection.execute(
                    "INSERT INTO workspaces "
                    "(workspace_id, record_json, forge_dev, forge_ino, world_dev, world_ino, "
                    "game_dev, game_ino, bundle_dev, bundle_ino) "
                    "VALUES (?, ?, 1, 1, 2, 2, NULL, NULL, NULL, NULL)",
                    ("workspace_01", encode_json(workspace)),
                )
                job = {
                    "format": "rpg-world-forge.studio_job",
                    "format_version": 1,
                    "job_id": "job_01",
                    "workspace_id": "workspace_01",
                    "operation": "forge.validate",
                    "state": "running",
                    "input": {},
                    "result": None,
                    "error": None,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                store.connection.execute(
                    "INSERT INTO jobs "
                    "(job_id, workspace_id, state, record_json) VALUES (?, ?, ?, ?)",
                    ("job_01", "workspace_01", "running", encode_json(job)),
                )
                store.connection.commit()

            with StudioStore(data_dir) as reopened:
                row = reopened.connection.execute(
                    "SELECT state, record_json FROM jobs WHERE job_id = 'job_01'"
                ).fetchone()
                self.assertEqual("orphaned", row["state"])
                events = reopened.list_events(workspace_id="workspace_01")
                self.assertEqual("job.orphaned", events[0]["topic"])


if __name__ == "__main__":
    unittest.main()
