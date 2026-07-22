from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from isoworld.content.loader import load_worldpack
from isoworld.core.app import GameApp
from isoworld.persistence import write_replay
from worldforge.assetpack import build_assetpack
from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.executor import JobScheduler
from worldforge.studio.jobs import JobManager
from worldforge.studio.storage import StudioStore
from worldforge.studio.workspaces import WorkspaceManager

ROOT = Path(__file__).resolve().parents[1]
TERMINAL_STATES = {"succeeded", "failed", "canceled", "orphaned"}


class StudioExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name)
        cls.world = cls.root / "world"
        create_world_project(
            cls.world,
            world_id="studio_world",
            title="Studio",
            language="en",
        )
        (cls.world / "build").mkdir()
        cls.worldpack = cls.world / "build/worldpack.json"
        shutil.copy2(ROOT / "content/compiled/foundation.worldpack.json", cls.worldpack)
        shutil.copytree(ROOT / "examples/m5-neutral/assetpack", cls.world / "assets")
        cls.assetpack = cls.world / "packages/assetpack.json"
        build_assetpack(cls.world / "assets/manifest.json", cls.worldpack, cls.assetpack)
        cls.replay = cls.world / "replays/empty.json"
        cls.replay.parent.mkdir()
        pack = load_worldpack(cls.worldpack)
        state = GameApp(pack).run_headless(0)
        write_replay(cls.replay, [], state, pack)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def setUp(self) -> None:
        self.data_dir = self.root / f"data-{time.time_ns()}"
        self.store = StudioStore(self.data_dir)
        WorkspaceManager(self.store).register(
            {
                "workspace_id": "workspace_01",
                "forge_root": str(FORGE_ROOT),
                "world_root": str(self.world),
            }
        )

    def tearDown(self) -> None:
        self.store.close()

    def _create(self, operation: str, job_input: dict[str, object]) -> dict[str, object]:
        return JobManager(self.store).create(
            {
                "workspace_id": "workspace_01",
                "operation": operation,
                "input": job_input,
            }
        )

    def _wait(self, job_id: str, *, timeout: float = 15.0) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = JobManager(self.store).get(job_id)
            if job["state"] in TERMINAL_STATES:
                return job
            time.sleep(0.025)
        self.fail(f"job {job_id} did not reach a terminal state")

    def _wait_for_state(self, job_id: str, state: str, *, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if JobManager(self.store).get(job_id)["state"] == state:
                return
            time.sleep(0.01)
        self.fail(f"job {job_id} did not reach {state}")

    def test_executes_closed_allowlist_in_durable_fifo_order(self) -> None:
        requests = (
            (
                "asset.receipt.validate",
                {"receipt": "receipts/neutral_actor_3d.json"},
            ),
            (
                "assetpack.verify",
                {"assetpack": "packages/assetpack.json", "worldpack": "build/worldpack.json"},
            ),
            ("runtime.headless", {"worldpack": "build/worldpack.json", "ticks": 0}),
            (
                "runtime.replay",
                {"worldpack": "build/worldpack.json", "replay": "replays/empty.json"},
            ),
        )
        created = [self._create(operation, job_input) for operation, job_input in requests]
        scheduler = JobScheduler(self.data_dir)
        scheduler.start()
        try:
            scheduler.notify()
            completed = [self._wait(str(job["job_id"])) for job in created]
        finally:
            scheduler.shutdown()

        self.assertEqual(["succeeded"] * 4, [job["state"] for job in completed])
        self.assertEqual(
            [operation for operation, _ in requests],
            [job["result"]["operation"] for job in completed],  # type: ignore[index]
        )
        self.assertTrue(completed[0]["result"]["valid"])  # type: ignore[index]
        self.assertEqual(0, completed[2]["result"]["ticks"])  # type: ignore[index]
        self.assertEqual(0, completed[3]["result"]["action_count"])  # type: ignore[index]
        events = self.store.list_events(limit=1000)
        running = [
            event["entity_id"]
            for event in events
            if event["topic"] == "job.transitioned" and event["payload"].get("state") == "running"
        ]
        self.assertEqual([job["job_id"] for job in created], running)
        for job in created:
            values = [
                event["payload"]["progress"]
                for event in events
                if event["entity_id"] == job["job_id"] and event["topic"] == "job.progress"
            ]
            self.assertEqual(sorted(set(values)), values)
            self.assertEqual([0, 20, 50], values)

    def test_two_schedulers_cannot_double_claim_one_job(self) -> None:
        job = self._create("runtime.headless", {"worldpack": "build/worldpack.json", "ticks": 1})
        schedulers = [JobScheduler(self.data_dir), JobScheduler(self.data_dir)]
        for scheduler in schedulers:
            scheduler.start()
            scheduler.notify()
        try:
            completed = self._wait(str(job["job_id"]))
        finally:
            for scheduler in schedulers:
                scheduler.shutdown()
        self.assertEqual("succeeded", completed["state"])
        transitions = [
            event
            for event in self.store.list_events(limit=1000)
            if event["entity_id"] == job["job_id"]
            and event["topic"] == "job.transitioned"
            and event["payload"].get("state") == "running"
        ]
        self.assertEqual(1, len(transitions))

    def test_queued_and_running_cancellation_are_durable(self) -> None:
        queued = self._create("runtime.headless", {"worldpack": "build/worldpack.json", "ticks": 0})
        canceled = JobManager(self.store).cancel(queued["job_id"])
        self.assertEqual("canceled", canceled["state"])

        running = self._create(
            "runtime.headless", {"worldpack": "build/worldpack.json", "ticks": 0}
        )
        command = (
            sys.executable,
            "-I",
            "-c",
            "import sys,time;sys.stdin.buffer.read();time.sleep(30)",
        )
        with patch("worldforge.studio.executor._worker_command", return_value=command):
            scheduler = JobScheduler(self.data_dir)
            scheduler.start()
            scheduler.notify()
            self._wait_for_state(str(running["job_id"]), "running")
            request = JobManager(self.store).cancel(running["job_id"])
            self.assertEqual("running", request["state"])
            completed = self._wait(str(running["job_id"]))
            scheduler.shutdown()
        self.assertEqual("canceled", completed["state"])
        topics = [
            event["topic"]
            for event in self.store.list_events(limit=1000)
            if event["entity_id"] == running["job_id"]
        ]
        self.assertIn("job.cancel_requested", topics)

    def test_timeout_bad_output_crash_and_shutdown_are_structured(self) -> None:
        cases = (
            (
                (
                    sys.executable,
                    "-I",
                    "-c",
                    "import sys,time;sys.stdin.buffer.read();time.sleep(30)",
                ),
                0.05,
                "failed",
                "timeout",
                False,
            ),
            (
                (sys.executable, "-I", "-c", "import sys;sys.stdin.buffer.read();print('bad')"),
                5.0,
                "failed",
                "worker_protocol",
                False,
            ),
            (
                (
                    sys.executable,
                    "-I",
                    "-c",
                    "import sys;sys.stdin.buffer.read();sys.stdout.buffer.write(b'x'*1048577)",
                ),
                5.0,
                "failed",
                "worker_protocol",
                False,
            ),
            (
                (
                    sys.executable,
                    "-I",
                    "-c",
                    "import sys;sys.stdin.buffer.read();"
                    "sys.stderr.write('boom');raise SystemExit(3)",
                ),
                5.0,
                "failed",
                "worker_crashed",
                False,
            ),
            (
                (
                    sys.executable,
                    "-I",
                    "-c",
                    "import sys,time;sys.stdin.buffer.read();time.sleep(30)",
                ),
                5.0,
                "orphaned",
                None,
                True,
            ),
        )
        for index, (command, timeout, state, code, stop) in enumerate(cases):
            with self.subTest(index=index):
                if index:
                    self.tearDown()
                    self.setUp()
                job = self._create(
                    "runtime.headless", {"worldpack": "build/worldpack.json", "ticks": 0}
                )
                with patch("worldforge.studio.executor._worker_command", return_value=command):
                    scheduler = JobScheduler(self.data_dir, timeout_seconds=timeout)
                    scheduler.start()
                    scheduler.notify()
                    if stop:
                        self._wait_for_state(str(job["job_id"]), "running")
                        scheduler.shutdown()
                    completed = self._wait(str(job["job_id"]))
                    if not stop:
                        scheduler.shutdown()
                self.assertEqual(state, completed["state"])
                if code is not None:
                    self.assertEqual(code, completed["error"]["code"])  # type: ignore[index]
                    self.assertNotIn(str(self.root), str(completed["error"]))
                else:
                    self.assertIsNone(completed["error"])

    def test_rejects_symlink_and_hardlink_inputs_before_spawn(self) -> None:
        attacks = self.world / "attacks"
        attacks.mkdir(exist_ok=True)
        source = attacks / f"source-{time.time_ns()}.json"
        shutil.copy2(self.worldpack, source)
        hardlink = attacks / f"hard-{time.time_ns()}.json"
        os.link(source, hardlink)
        paths = [hardlink.relative_to(self.world).as_posix()]
        symlink = attacks / f"link-{time.time_ns()}.json"
        try:
            symlink.symlink_to(self.worldpack)
        except OSError:
            pass
        else:
            paths.append(symlink.relative_to(self.world).as_posix())
        for index, path in enumerate(paths):
            with self.subTest(path=path):
                if index:
                    self.tearDown()
                    self.setUp()
                job = self._create("runtime.headless", {"worldpack": path, "ticks": 0})
                scheduler = JobScheduler(self.data_dir)
                scheduler.start()
                scheduler.notify()
                completed = self._wait(str(job["job_id"]))
                scheduler.shutdown()
                self.assertEqual("failed", completed["state"])
                self.assertEqual("invalid_workspace", completed["error"]["code"])  # type: ignore[index]

    def test_receipt_findings_are_bounded_and_do_not_leak_the_workspace_root(self) -> None:
        receipt = self.world / f"assets/receipts/invalid-{time.time_ns()}.json"
        receipt.write_bytes(b'{"format":"bad","secret":"\xff"}')
        job = self._create(
            "asset.receipt.validate",
            {"receipt": receipt.relative_to(self.world / "assets").as_posix()},
        )
        scheduler = JobScheduler(self.data_dir)
        scheduler.start()
        scheduler.notify()
        completed = self._wait(str(job["job_id"]))
        scheduler.shutdown()
        self.assertEqual("succeeded", completed["state"])
        self.assertFalse(completed["result"]["valid"])  # type: ignore[index]
        self.assertNotIn(str(self.world), str(completed["result"]))


if __name__ == "__main__":
    unittest.main()
