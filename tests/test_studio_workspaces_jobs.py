from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.errors import StudioError
from worldforge.studio.jobs import JobManager
from worldforge.studio.storage import StudioStore, encode_json
from worldforge.studio.workspaces import WorkspaceManager


class StudioWorkspacesAndJobsTests(unittest.TestCase):
    def test_registers_canonical_roots_and_rejects_duplicates_and_nesting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world = temp / "world"
            create_world_project(world, world_id="studio_world", title="Studio", language="en")
            with StudioStore(temp / "data") as store:
                manager = WorkspaceManager(store)
                record = manager.register(
                    {
                        "workspace_id": "workspace_01",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(world),
                    }
                )
                self.assertEqual(str(world.resolve()), record["world_root"])
                self.assertEqual(record, manager.get("workspace_01"))
                self.assertEqual([record], manager.list())

                generated = manager.register(
                    {
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(
                            self._make_second_world(temp, world_id="generated_world")
                        ),
                    }
                )
                self.assertRegex(generated["workspace_id"], r"^[a-z][a-z0-9_-]{1,63}$")

                with self.assertRaisesRegex(StudioError, "already registered"):
                    manager.register(
                        {
                            "workspace_id": "workspace_02",
                            "forge_root": str(FORGE_ROOT),
                            "world_root": str(world),
                        }
                    )

                (world / "game").mkdir()
                with self.assertRaisesRegex(StudioError, "overlap"):
                    manager.register(
                        {
                            "workspace_id": "workspace_03",
                            "forge_root": str(FORGE_ROOT),
                            "world_root": str(world),
                            "game_root": str(world / "game"),
                        }
                    )

            with StudioStore(world / ".studio-data") as nested_store:
                with self.assertRaisesRegex(StudioError, "data directory"):
                    WorkspaceManager(nested_store).register(
                        {
                            "workspace_id": "workspace_04",
                            "forge_root": str(FORGE_ROOT),
                            "world_root": str(world),
                        }
                    )

    @staticmethod
    def _make_second_world(temp: Path, *, world_id: str) -> Path:
        world = temp / world_id
        create_world_project(world, world_id=world_id, title="Generated", language="en")
        return world

    def test_job_state_machine_and_startup_orphaning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world = temp / "world"
            create_world_project(world, world_id="studio_world", title="Studio", language="en")
            data_dir = temp / "data"
            with StudioStore(data_dir) as store:
                workspace = WorkspaceManager(store).register(
                    {
                        "workspace_id": "workspace_01",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(world),
                    }
                )
                jobs = JobManager(store)
                job = jobs.create(
                    {
                        "workspace_id": workspace["workspace_id"],
                        "operation": "runtime.headless",
                        "input": {"worldpack": "build/worldpack.json", "ticks": 0},
                    }
                )
                self.assertEqual(2, job["format_version"])
                running = jobs.claim_next()
                self.assertIsNotNone(running)
                assert running is not None
                self.assertEqual("running", running["state"])
                with self.assertRaisesRegex(StudioError, "owned by the Studio executor"):
                    jobs.transition(job["job_id"], {"state": "succeeded", "result": {}})

            with StudioStore(data_dir) as reopened:
                orphaned = JobManager(reopened).get(job["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                canceled = JobManager(reopened).cancel(job["job_id"])
                self.assertEqual("canceled", canceled["state"])

    def test_claim_skips_legacy_v1_and_preserves_managed_v2_fifo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world = temp / "world"
            create_world_project(world, world_id="studio_world", title="Studio", language="en")
            with StudioStore(temp / "data") as store:
                WorkspaceManager(store).register(
                    {
                        "workspace_id": "workspace_01",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(world),
                    }
                )
                jobs = JobManager(store)
                timestamp = "2026-07-22T12:00:00Z"

                def insert_legacy(
                    job_id: str,
                    operation: str,
                    job_input: dict[str, object],
                    *,
                    state: str = "queued",
                ) -> None:
                    record = {
                        "format": "rpg-world-forge.studio_job",
                        "format_version": 1,
                        "job_id": job_id,
                        "workspace_id": "workspace_01",
                        "operation": operation,
                        "state": state,
                        "input": job_input,
                        "result": None,
                        "error": None,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    }
                    store.connection.execute(
                        "INSERT INTO jobs "
                        "(job_id, workspace_id, state, record_json) VALUES (?, ?, ?, ?)",
                        (job_id, "workspace_01", state, encode_json(record)),
                    )
                    store.connection.commit()

                insert_legacy(
                    "legacy_running",
                    "forge.validate",
                    {"profile": "release"},
                    state="running",
                )
                self.assertEqual("canceled", jobs.cancel("legacy_running")["state"])
                insert_legacy("legacy_first", "forge.validate", {"profile": "release"})
                managed_first = jobs.create(
                    {
                        "job_id": "managed_first",
                        "workspace_id": "workspace_01",
                        "operation": "runtime.headless",
                        "input": {"worldpack": "build/first.json", "ticks": 0},
                    }
                )
                insert_legacy(
                    "legacy_managed_name",
                    "runtime.headless",
                    {"legacy_command": "headless --old-contract"},
                )
                managed_second = jobs.create(
                    {
                        "job_id": "managed_second",
                        "workspace_id": "workspace_01",
                        "operation": "runtime.headless",
                        "input": {"worldpack": "build/second.json", "ticks": 0},
                    }
                )
                store.connection.commit()

                first_claim = jobs.claim_next()
                self.assertIsNotNone(first_claim)
                assert first_claim is not None
                self.assertEqual(managed_first["job_id"], first_claim["job_id"])
                jobs.finish(first_claim["job_id"], "canceled")
                second_claim = jobs.claim_next()
                self.assertIsNotNone(second_claim)
                assert second_claim is not None
                self.assertEqual(managed_second["job_id"], second_claim["job_id"])
                jobs.finish(second_claim["job_id"], "canceled")
                self.assertIsNone(jobs.claim_next())

                self.assertEqual("queued", jobs.get("legacy_first")["state"])
                self.assertEqual("queued", jobs.get("legacy_managed_name")["state"])
                self.assertEqual("canceled", jobs.cancel("legacy_first")["state"])
                self.assertEqual("canceled", jobs.cancel("legacy_managed_name")["state"])


if __name__ == "__main__":
    unittest.main()
