from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.errors import StudioError
from worldforge.studio.jobs import JobManager
from worldforge.studio.storage import StudioStore
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
                        "operation": "forge.validate",
                        "input": {"profile": "draft"},
                    }
                )
                running = jobs.transition(job["job_id"], {"state": "running"})
                self.assertEqual("running", running["state"])
                with self.assertRaisesRegex(StudioError, "transition"):
                    jobs.transition(job["job_id"], {"state": "queued"})

            with StudioStore(data_dir) as reopened:
                orphaned = JobManager(reopened).get(job["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                canceled = JobManager(reopened).cancel(job["job_id"])
                self.assertEqual("canceled", canceled["state"])


if __name__ == "__main__":
    unittest.main()
