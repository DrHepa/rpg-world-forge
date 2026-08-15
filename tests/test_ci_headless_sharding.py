from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import verify_headless_suite as headless
from scripts import verify_multigenre_release as release_gate
from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".github/headless-suite-shards-v1.json"


class HeadlessSuiteShardingTests(unittest.TestCase):
    def test_checked_in_manifest_is_canonical_and_covers_current_modules_once(self) -> None:
        payload = MANIFEST.read_bytes()
        manifest = headless.load_manifest(MANIFEST)
        discovery = headless.discover_suite(ROOT, manifest)

        self.assertEqual(payload, canonical_json_bytes(manifest))
        self.assertEqual(
            {"calibration", "discovery", "format", "format_version", "modules", "shards"},
            set(manifest),
        )
        self.assertEqual(
            [f"s{index:02d}" for index in range(16)],
            [shard["id"] for shard in manifest["shards"]],
        )
        assigned = [module for shard in manifest["shards"] for module in shard["modules"]]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(sorted(manifest["modules"]), sorted(assigned))
        self.assertEqual(set(manifest["modules"]), set(discovery.modules))
        self.assertEqual(len(discovery.test_ids), len(set(discovery.test_ids)))
        self.assertIn("test_ci_headless_sharding", discovery.modules)

        calibration = manifest["calibration"]
        self.assertEqual(2205, calibration["discovered_test_count"])
        self.assertEqual(110, calibration["discovered_module_count"])
        self.assertEqual(
            "d56a1cd5f2b6207f5850ce898ea761f2b8a1163fbec4adff1eedb7a6d557a30f",
            calibration["run_log_sha256"],
        )
        states = {shard["id"]: shard["calibration_state"] for shard in manifest["shards"]}
        self.assertEqual("complete", states["s00"])
        self.assertEqual("partial", states["s01"])
        self.assertEqual("partial", states["s08"])
        self.assertEqual({"unmeasured"}, {states[f"s{index:02d}"] for index in range(9, 16)})

    def test_manifest_preserves_exact_reviewed_assignment_and_calibration(self) -> None:
        manifest = headless.load_manifest(MANIFEST)
        assignment = {shard["id"]: shard["modules"] for shard in manifest["shards"]}
        self.assertEqual(
            "312a391b4e8d319aec3cd664fbd890c24bd09212f838b5fb524d7d585a14aae3",
            hashlib.sha256(canonical_json_bytes(assignment)).hexdigest(),
        )
        self.assertEqual(
            [
                ("s00", "complete", 30, 3454),
                ("s01", "partial", 128, 3170),
                ("s02", "complete", 275, 3170),
                ("s03", "complete", 446, 3170),
                ("s04", "complete", 88, 3188),
                ("s05", "complete", 204, 3170),
                ("s06", "complete", 257, 3170),
                ("s07", "complete", 199, 3170),
                ("s08", "partial", 5, 1771),
                ("s09", "unmeasured", 48, None),
                ("s10", "unmeasured", 59, None),
                ("s11", "unmeasured", 53, None),
                ("s12", "unmeasured", 47, None),
                ("s13", "unmeasured", 103, None),
                ("s14", "unmeasured", 188, None),
                ("s15", "unmeasured", 75, None),
            ],
            [
                (
                    shard["id"],
                    shard["calibration_state"],
                    shard["test_count"],
                    shard["weight_seconds"],
                )
                for shard in manifest["shards"]
            ],
        )
        self.assertEqual(2205, sum(shard["test_count"] for shard in manifest["shards"]))

    def test_existing_module_growth_is_planned_but_a_new_module_fails_closed(self) -> None:
        manifest = headless.load_manifest(MANIFEST)
        discovery = headless.discover_suite(ROOT, manifest)
        added_id = "test_runtime.RuntimeGrowthTests.test_new_case"
        modules = dict(discovery.modules)
        modules["test_runtime"] = (*modules["test_runtime"], added_id)
        grown = headless.DiscoveredSuite((*discovery.test_ids, added_id), modules)

        plan = headless.build_plan(
            manifest,
            grown,
            manifest_sha256="c" * 64,
            commit="a" * 40,
            source_tree="b" * 40,
            runner_os="Linux",
            runner_image="ubuntu24:20260810.1",
            python_full_version="3.12.3",
            python_minor="3.12",
        )
        runtime_shard = next(shard for shard in plan["shards"] if shard["id"] == "s01")
        self.assertIn(added_id, runtime_shard["expected_test_ids"])

        new_module = dict(modules)
        new_module["test_unreviewed_module"] = ("test_unreviewed_module.NewTests.test_case",)
        with self.assertRaisesRegex(headless.HeadlessSuiteError, "discovered modules"):
            headless.validate_manifest(
                manifest,
                discovery=headless.DiscoveredSuite(grown.test_ids, new_module),
            )

    def test_manifest_rejects_module_gaps_extras_duplicates_and_dynamic_shard_ids(self) -> None:
        manifest = headless.load_manifest(MANIFEST)
        discovery = headless.discover_suite(ROOT, manifest)
        mutations: list[tuple[str, dict[str, object], str]] = []

        missing = copy.deepcopy(manifest)
        missing["shards"][0]["modules"].pop()
        mutations.append(("missing", missing, "module assignment"))

        duplicate = copy.deepcopy(manifest)
        duplicate["shards"][1]["modules"].append(duplicate["shards"][0]["modules"][0])
        mutations.append(("duplicate", duplicate, "module assignment"))

        extra = copy.deepcopy(manifest)
        extra["modules"].append("test_unreviewed_new_module")
        extra["shards"][0]["modules"].append("test_unreviewed_new_module")
        extra["modules"].sort()
        extra["shards"][0]["modules"].sort()
        mutations.append(("extra", extra, "discovered modules"))

        shard_id = copy.deepcopy(manifest)
        shard_id["shards"][0]["id"] = "s16"
        mutations.append(("shard-id", shard_id, "shard IDs"))

        count_drift = copy.deepcopy(manifest)
        count_drift["shards"][0]["test_count"] += 1
        mutations.append(("count-drift", count_drift, "calibration"))

        for name, mutated, reason in mutations:
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    headless.HeadlessSuiteError,
                    reason,
                ),
            ):
                headless.validate_manifest(mutated, discovery=discovery)

    def test_discovery_rejects_loader_errors_failed_tests_duplicate_and_malformed_ids(self) -> None:
        valid = SimpleNamespace(
            id=lambda: "test_example.ExampleTests.test_valid",
            __class__=SimpleNamespace(__name__="ExampleTests"),
        )
        cases = (
            ("loader", (valid,), ("import exploded",), "loader errors"),
            (
                "failed-test",
                (unittest.loader._FailedTest("test_broken", ImportError("broken")),),
                (),
                "_FailedTest",
            ),
            ("duplicate", (valid, valid), (), "duplicate test ID"),
            (
                "malformed",
                (SimpleNamespace(id=lambda: "tests/test_bad.py::test_bad"),),
                (),
                "noncanonical test ID",
            ),
        )
        for name, tests, errors, reason in cases:
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    headless.HeadlessSuiteError,
                    reason,
                ),
            ):
                headless.collect_discovered_tests(tests, loader_errors=errors)

    def test_plan_binds_axis_source_manifest_and_ordered_test_stream_hashes(self) -> None:
        manifest = headless.load_manifest(MANIFEST)
        discovery = headless.discover_suite(ROOT, manifest)
        manifest_payload = canonical_json_bytes(manifest)
        plan = headless.build_plan(
            manifest,
            discovery,
            manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
            commit="a" * 40,
            source_tree="b" * 40,
            runner_os="Linux",
            runner_image="ubuntu24:20260810.1",
            python_full_version="3.12.3",
            python_minor="3.12",
        )

        stream = b"".join(test_id.encode("utf-8") + b"\n" for test_id in discovery.test_ids)
        self.assertEqual(hashlib.sha256(stream).hexdigest(), plan["discovery"]["test_ids_sha256"])
        self.assertEqual(len(stream), plan["discovery"]["test_ids_size_bytes"])
        self.assertEqual("a" * 40, plan["source"]["commit"])
        self.assertEqual("b" * 40, plan["source"]["tree"])
        self.assertEqual("3.12.3", plan["axis"]["python_full_version"])
        self.assertEqual("Linux", plan["axis"]["runner_os"])
        self.assertEqual(16, len(plan["shards"]))
        planned_ids = [
            test_id for shard in plan["shards"] for test_id in shard["expected_test_ids"]
        ]
        self.assertEqual(
            list(discovery.test_ids), sorted(planned_ids, key=discovery.test_ids.index)
        )
        self.assertEqual(set(discovery.test_ids), set(planned_ids))

    def test_git_source_binding_rejects_unverified_github_sha(self) -> None:
        completed = (
            SimpleNamespace(stdout=""),
            SimpleNamespace(stdout="a" * 40 + "\n"),
        )
        with mock.patch.object(headless.subprocess, "run", side_effect=completed) as run:
            with self.assertRaisesRegex(headless.HeadlessSuiteError, "GITHUB_SHA"):
                headless.resolve_git_source(ROOT, github_sha="b" * 40)
        self.assertEqual(2, run.call_count)

    def test_git_source_binding_requires_clean_tracked_and_untracked_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-clean-git-") as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "headless-tests@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Headless Tests"],
                cwd=root,
                check=True,
            )
            tracked = root / "tracked.txt"
            tracked.write_text("baseline\n", encoding="utf-8")
            (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

            self.assertEqual(head, headless.resolve_git_source(root, github_sha=head)[0])
            (root / "ignored.txt").write_text("allowed\n", encoding="utf-8")
            self.assertEqual(head, headless.resolve_git_source(root, github_sha=head)[0])

            dirty_cases = ("staged", "unstaged", "untracked")
            for dirty_case in dirty_cases:
                with self.subTest(dirty_case=dirty_case):
                    if dirty_case == "untracked":
                        dirty_path = root / "untracked.txt"
                        dirty_path.write_text("dirty\n", encoding="utf-8")
                    else:
                        dirty_path = tracked
                        dirty_path.write_text(f"{dirty_case}\n", encoding="utf-8")
                        if dirty_case == "staged":
                            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
                    with self.assertRaisesRegex(headless.HeadlessSuiteError, "worktree"):
                        headless.resolve_git_source(root, github_sha=head)
                    if dirty_case == "untracked":
                        dirty_path.unlink()
                    else:
                        subprocess.run(
                            ["git", "reset", "--hard", "-q", "HEAD"], cwd=root, check=True
                        )

    def test_current_plan_checks_source_cleanliness_before_discovery(self) -> None:
        with (
            mock.patch.object(
                headless,
                "resolve_git_source",
                side_effect=headless.HeadlessSuiteError("worktree is dirty"),
            ),
            mock.patch.object(headless, "discover_suite") as discover,
            self.assertRaisesRegex(headless.HeadlessSuiteError, "worktree"),
        ):
            headless._current_plan(
                source_root=ROOT,
                manifest_path=MANIFEST,
                github_sha="a" * 40,
                runner_os="Linux",
                runner_image="ubuntu24:20260810.1",
                python_minor=f"{sys.version_info.major}.{sys.version_info.minor}",
            )
        discover.assert_not_called()

    def test_result_coalesces_subtests_and_accepts_only_documented_successes(self) -> None:
        class Outcomes(unittest.TestCase):
            def test_subtests(self) -> None:
                with self.subTest(slot="ok"):
                    self.assertEqual(1, 1)
                with self.subTest(slot="failed"):
                    self.assertEqual(1, 2)

            @unittest.skip("bounded skip")
            def test_skipped(self) -> None:
                self.fail("unreachable")

            @unittest.expectedFailure
            def test_expected_failure(self) -> None:
                self.assertEqual("actual", "expected")

            @unittest.expectedFailure
            def test_unexpected_success(self) -> None:
                pass

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Outcomes)
        planned = tuple(test.id() for test in suite)
        result = headless.HeadlessTestResult(planned)
        suite.run(result)
        events = {event["test_id"]: event for event in result.terminal_events()}

        self.assertEqual(len(planned), len(events))
        self.assertEqual("failure", events[Outcomes("test_subtests").id()]["outcome"])
        self.assertEqual("skipped", events[Outcomes("test_skipped").id()]["outcome"])
        self.assertEqual(
            "expected_failure",
            events[Outcomes("test_expected_failure").id()]["outcome"],
        )
        self.assertEqual(
            "unexpected_success",
            events[Outcomes("test_unexpected_success").id()]["outcome"],
        )
        self.assertFalse(headless.terminal_events_accepted(tuple(events.values())))
        accepted = tuple(
            event
            for event in events.values()
            if event["outcome"] in {"passed", "skipped", "expected_failure"}
        )
        self.assertTrue(headless.terminal_events_accepted(accepted))

    def test_result_maps_class_fixture_errors_to_each_planned_parent(self) -> None:
        class BrokenFixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls) -> None:
                raise RuntimeError("fixture failed")

            def test_one(self) -> None:
                pass

            def test_two(self) -> None:
                pass

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(BrokenFixture)
        planned = tuple(test.id() for test in suite)
        result = headless.HeadlessTestResult(planned)
        suite.run(result)

        self.assertEqual(
            [(test_id, "error") for test_id in planned],
            [(event["test_id"], event["outcome"]) for event in result.terminal_events()],
        )

        module_planned = (
            "test_example.FirstTests.test_one",
            "test_example.SecondTests.test_two",
        )
        module_result = headless.HeadlessTestResult(module_planned)
        holder = unittest.suite._ErrorHolder("setUpModule (test_example)")
        try:
            raise RuntimeError("module fixture failed")
        except RuntimeError:
            module_result.addError(holder, sys.exc_info())
        self.assertEqual(
            [(test_id, "error") for test_id in module_planned],
            [(event["test_id"], event["outcome"]) for event in module_result.terminal_events()],
        )

    def test_result_maps_class_and_module_fixture_skips_to_planned_parents(self) -> None:
        class SkippedFixture(unittest.TestCase):
            @classmethod
            def setUpClass(cls) -> None:
                raise unittest.SkipTest("fixture unavailable")

            def test_one(self) -> None:
                self.fail("unreachable")

            def test_two(self) -> None:
                self.fail("unreachable")

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SkippedFixture)
        planned = tuple(test.id() for test in suite)
        result = headless.HeadlessTestResult(planned)
        suite.run(result)
        self.assertEqual(
            [(test_id, "skipped") for test_id in planned],
            [(event["test_id"], event["outcome"]) for event in result.terminal_events()],
        )
        self.assertTrue(headless.terminal_events_accepted(result.terminal_events()))

        module_planned = (
            "test_example.FirstTests.test_one",
            "test_example.SecondTests.test_two",
        )
        module_result = headless.HeadlessTestResult(module_planned)
        holder = unittest.suite._ErrorHolder("setUpModule (test_example)")
        module_result.addSkip(holder, "module unavailable")
        self.assertEqual(
            [(test_id, "skipped") for test_id in module_planned],
            [(event["test_id"], event["outcome"]) for event in module_result.terminal_events()],
        )
        self.assertTrue(headless.terminal_events_accepted(module_result.terminal_events()))

    def test_terminal_event_stream_rejects_noncanonical_and_unbounded_input(self) -> None:
        event = headless.terminal_event(
            "test_example.ExampleTests.test_valid",
            "passed",
        )
        canonical = headless.encode_terminal_events((event,))
        duplicate_key = canonical.replace(
            b'{"format":',
            b'{"format":"duplicate","format":',
            1,
        )
        cases = (
            ("missing-newline", canonical.rstrip(b"\n")),
            ("noncanonical", canonical.replace(b'":"', b'": "', 1)),
            ("duplicate-key", duplicate_key),
            ("lone-surrogate", canonical.replace(b"test_valid", b"test_\\ud800")),
            ("oversize", b"x" * (headless._EVENTS_LIMIT + 1)),
        )
        for name, payload in cases:
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    headless.HeadlessSuiteError,
                    "terminal test event",
                ),
            ):
                headless.parse_terminal_events(payload)

    def test_worker_evidence_marks_timeout_crash_duplicate_extra_and_missing_incomplete(
        self,
    ) -> None:
        plan = _synthetic_plan(("test_example.ExampleTests.test_one",))
        passed = headless.encode_terminal_events(
            (
                headless.terminal_event(
                    "test_example.ExampleTests.test_one",
                    "passed",
                ),
            )
        )
        cases = (
            ("timeout", b"", None, True, "timeout"),
            ("crash", b"", 7, False, "crash"),
            ("missing", b"", 0, False, "missing"),
            ("duplicate", passed + passed, 0, False, "duplicate"),
            (
                "extra",
                headless.encode_terminal_events(
                    (
                        headless.terminal_event(
                            "test_other.OtherTests.test_extra",
                            "passed",
                        ),
                    )
                ),
                0,
                False,
                "missing",
            ),
        )
        for name, raw, return_code, timed_out, expected_outcome in cases:
            with self.subTest(name=name):
                events_payload, receipt = headless.finalize_worker_evidence(
                    plan,
                    shard_id="s00",
                    raw_events=raw,
                    return_code=return_code,
                    timed_out=timed_out,
                )
                events = headless.parse_terminal_events(events_payload)
                self.assertEqual(1, len(events))
                self.assertEqual(expected_outcome, events[0]["outcome"])
                self.assertEqual("incomplete", receipt["state"])
                self.assertEqual("failed", receipt["status"])
                self.assertEqual(
                    hashlib.sha256(events_payload).hexdigest(),
                    receipt["events_sha256"],
                )
                self.assertEqual(
                    hashlib.sha256(canonical_json_bytes(plan)).hexdigest(),
                    receipt["plan_sha256"],
                )

    def test_worker_evidence_is_complete_only_for_exact_acceptable_events(self) -> None:
        identifiers = (
            "test_example.ExampleTests.test_passed",
            "test_example.ExampleTests.test_skipped",
            "test_example.ExampleTests.test_expected",
        )
        plan = _synthetic_plan(identifiers)
        raw = headless.encode_terminal_events(
            tuple(
                headless.terminal_event(test_id, outcome)
                for test_id, outcome in zip(
                    identifiers,
                    ("passed", "skipped", "expected_failure"),
                    strict=True,
                )
            )
        )
        events_payload, receipt = headless.finalize_worker_evidence(
            plan,
            shard_id="s00",
            raw_events=raw,
            return_code=0,
            timed_out=False,
        )

        self.assertEqual(raw, events_payload)
        self.assertEqual("complete", receipt["state"])
        self.assertEqual("passed", receipt["status"])
        self.assertEqual(
            {"expected_failure": 1, "passed": 1, "skipped": 1},
            receipt["outcome_counts"],
        )

    def test_worker_evidence_rejects_every_terminal_failure_outcome(self) -> None:
        test_id = "test_example.ExampleTests.test_rejected"
        plan = _synthetic_plan((test_id,))
        for outcome in ("error", "failure", "unexpected_success"):
            raw = headless.encode_terminal_events((headless.terminal_event(test_id, outcome),))
            events, receipt = headless.finalize_worker_evidence(
                plan,
                shard_id="s00",
                raw_events=raw,
                return_code=1,
                timed_out=False,
            )
            with self.subTest(outcome=outcome):
                self.assertEqual(outcome, headless.parse_terminal_events(events)[0]["outcome"])
                self.assertEqual("complete", receipt["state"])
                self.assertEqual("failed", receipt["status"])

    def test_worker_evidence_marks_operational_terminal_outcomes_incomplete(self) -> None:
        test_id = "test_example.ExampleTests.test_incomplete"
        plan = _synthetic_plan((test_id,))
        for outcome in ("crash", "duplicate", "incomplete", "missing", "timeout"):
            raw = headless.encode_terminal_events((headless.terminal_event(test_id, outcome),))
            _events, receipt = headless.finalize_worker_evidence(
                plan,
                shard_id="s00",
                raw_events=raw,
                return_code=1,
                timed_out=False,
            )
            with self.subTest(outcome=outcome):
                self.assertEqual("incomplete", receipt["state"])
                self.assertEqual("failed", receipt["status"])
                self.assertIn(f"terminal_{outcome}", receipt["violations"])

    def test_plan_and_worker_publication_are_bounded_no_replace_and_external(self) -> None:
        plan = _synthetic_plan(("test_example.ExampleTests.test_one",))
        with tempfile.TemporaryDirectory(prefix="wf-headless-publish-") as temporary:
            output = Path(temporary) / "evidence"
            output.mkdir()
            plan_path = headless.publish_plan(output, plan, source_root=ROOT)
            self.assertEqual(output / "plan.json", plan_path)
            self.assertEqual(canonical_json_bytes(plan), plan_path.read_bytes())
            with self.assertRaisesRegex(headless.HeadlessSuiteError, "publish"):
                headless.publish_plan(output, plan, source_root=ROOT)

            events = headless.encode_terminal_events(
                (headless.terminal_event("test_example.ExampleTests.test_one", "passed"),)
            )
            _normalized, receipt = headless.finalize_worker_evidence(
                plan,
                shard_id="s00",
                raw_events=events,
                return_code=0,
                timed_out=False,
            )
            headless.publish_worker_evidence(
                output,
                events,
                receipt,
                source_root=ROOT,
            )
            self.assertEqual(events, (output / "events.jsonl").read_bytes())
            self.assertEqual(canonical_json_bytes(receipt), (output / "receipt.json").read_bytes())

        inside = ROOT / ".headless-forbidden-output"
        with self.assertRaisesRegex(headless.HeadlessSuiteError, "outside the source tree"):
            headless.publish_plan(inside, plan, source_root=ROOT)

    def test_worker_controller_always_publishes_incomplete_timeout_and_crash_receipts(self) -> None:
        plan = _synthetic_plan(("test_example.ExampleTests.test_one",))
        cases = (
            ("timeout", headless.ChildRun(b"", None, True), "timeout"),
            ("crash", headless.ChildRun(b"", 9, False), "crash"),
        )
        with tempfile.TemporaryDirectory(prefix="wf-headless-controller-") as temporary:
            root = Path(temporary)
            for name, child, expected in cases:
                output = root / name
                output.mkdir()
                with self.subTest(name=name):
                    status = headless.run_worker_controller(
                        plan,
                        shard_id="s00",
                        output_dir=output,
                        source_root=ROOT,
                        child_runner=mock.Mock(return_value=child),
                    )
                    self.assertEqual(1, status)
                    receipt = json.loads((output / "receipt.json").read_bytes())
                    events = headless.parse_terminal_events((output / "events.jsonl").read_bytes())
                    self.assertEqual("incomplete", receipt["state"])
                    self.assertEqual(expected, events[0]["outcome"])

            raised = root / "raised"
            raised.mkdir()
            self.assertEqual(
                1,
                headless.run_worker_controller(
                    plan,
                    shard_id="s00",
                    output_dir=raised,
                    source_root=ROOT,
                    child_runner=mock.Mock(side_effect=headless.HeadlessSuiteError("child")),
                ),
            )
            self.assertEqual(
                "incomplete",
                json.loads((raised / "receipt.json").read_bytes())["state"],
            )

    def test_worker_loads_only_exact_planned_ids_and_rejects_loader_drift(self) -> None:
        class Planned(unittest.TestCase):
            def id(self) -> str:
                return f"test_example.PlannedTests.{self._testMethodName}"

            def test_one(self) -> None:
                pass

            def test_two(self) -> None:
                pass

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(Planned)
        identifiers = tuple(test.id() for test in suite)
        plan = _synthetic_plan(identifiers)
        loader = mock.Mock()
        loader.errors = []
        loader.loadTestsFromNames.return_value = suite
        events, return_code = headless.execute_planned_tests(
            plan,
            shard_id="s00",
            loader=loader,
        )
        loader.loadTestsFromNames.assert_called_once_with(list(identifiers))
        self.assertEqual(0, return_code)
        self.assertEqual(
            [(test_id, "passed") for test_id in identifiers],
            [(event["test_id"], event["outcome"]) for event in events],
        )

        drifted_loader = mock.Mock()
        drifted_loader.errors = ["loader drift"]
        drifted_loader.loadTestsFromNames.return_value = suite
        with self.assertRaisesRegex(headless.HeadlessSuiteError, "loader errors"):
            headless.execute_planned_tests(
                plan,
                shard_id="s00",
                loader=drifted_loader,
            )

    def test_worker_rejects_any_plan_hash_or_axis_drift(self) -> None:
        plan = _synthetic_plan(("test_example.ExampleTests.test_one",))
        for name, mutate in (
            (
                "source",
                lambda document: document["source"].__setitem__("tree", "d" * 40),
            ),
            (
                "axis",
                lambda document: document["axis"].__setitem__("runner_image", "changed"),
            ),
            (
                "ids",
                lambda document: document["shards"][0]["expected_test_ids"].clear(),
            ),
        ):
            mutated = copy.deepcopy(plan)
            mutate(mutated)
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    headless.HeadlessSuiteError,
                    "plan drift",
                ),
            ):
                headless.require_exact_plan(mutated, expected=plan)

    def test_child_process_executes_only_the_planned_parent_with_fixed_deadline(self) -> None:
        test_id = (
            "test_ci_headless_sharding.HeadlessSuiteShardingTests."
            "test_git_source_binding_rejects_unverified_github_sha"
        )
        plan = _synthetic_plan((test_id,))
        child = headless.run_child_process(plan, shard_id="s00", source_root=ROOT)

        self.assertFalse(child.timed_out)
        self.assertEqual(0, child.return_code)
        self.assertEqual(
            [(test_id, "passed")],
            [
                (event["test_id"], event["outcome"])
                for event in headless.parse_terminal_events(child.raw_events)
            ],
        )
        source = inspect.getsource(headless.run_child_process)
        self.assertEqual(6000, headless._WORKER_TIMEOUT_SECONDS)
        self.assertIn("_WORKER_TIMEOUT_SECONDS", source)
        self.assertNotIn(
            "timeout_seconds", inspect.signature(headless.run_child_process).parameters
        )

    def test_child_process_reuses_reviewed_containment_with_headless_bounds(self) -> None:
        test_id = (
            "test_ci_headless_sharding.HeadlessSuiteShardingTests."
            "test_git_source_binding_rejects_unverified_github_sha"
        )
        plan = _synthetic_plan((test_id,))
        result = release_gate._BoundedProcessResult(
            return_code=0,
            stdout=headless.encode_terminal_events((headless.terminal_event(test_id, "passed"),)),
            stderr=b"",
            timed_out=False,
            stdout_overflow=False,
            stderr_overflow=False,
        )
        with (
            mock.patch.object(
                release_gate,
                "_run_bounded_subprocess_execution",
                create=True,
                return_value=result,
            ) as contained,
            mock.patch.dict(
                os.environ,
                {
                    "GITHUB_TOKEN": "must-not-reach-worker",
                    "RUNNER_TEMP": "must-not-reach-worker",
                },
            ),
        ):
            child = headless.run_child_process(plan, shard_id="s00", source_root=ROOT)

        self.assertEqual(result.stdout, child.raw_events)
        self.assertEqual(0, child.return_code)
        self.assertFalse(child.timed_out)
        self.assertEqual(1, contained.call_count)
        call = contained.call_args
        self.assertEqual(6000, call.kwargs["timeout_seconds"])
        self.assertEqual(headless._EVENTS_LIMIT, call.kwargs["output_limit"])
        self.assertNotIn("GITHUB_TOKEN", call.kwargs["environment"])
        self.assertNotIn("RUNNER_TEMP", call.kwargs["environment"])
        source = inspect.getsource(headless.run_child_process)
        self.assertNotIn("subprocess.Popen", source)
        self.assertNotIn("process.kill", source)
        self.assertIn("_run_bounded_subprocess_execution", source)

        with self.assertRaises(ValueError):
            release_gate._run_bounded_subprocess(
                (sys.executable, "-I", "-c", "pass"),
                cwd=ROOT,
                environment={},
                timeout_seconds=release_gate._PROCESS_TIMEOUT_SECONDS + 0.001,
            )
        with self.assertRaises(ValueError):
            release_gate._run_bounded_subprocess(
                (sys.executable, "-I", "-c", "pass"),
                cwd=ROOT,
                environment={},
                output_limit=release_gate._MAX_PROCESS_OUTPUT_BYTES + 1,
            )

    @unittest.skipUnless(os.name == "posix", "POSIX descendant containment")
    def test_child_process_kills_live_grandchild_after_success(self) -> None:
        test_id = "test_example.ExampleTests.test_passed"
        plan = _synthetic_plan((test_id,))
        events = headless.encode_terminal_events((headless.terminal_event(test_id, "passed"),))
        with tempfile.TemporaryDirectory(prefix="wf-headless-success-tree-") as temporary:
            marker = Path(temporary) / "late-grandchild.bin"
            grandchild = (
                "import pathlib,time\n"
                "time.sleep(0.6)\n"
                f"pathlib.Path({str(marker)!r}).write_bytes(b'escaped')\n"
            )
            source = (
                "import subprocess,sys\n"
                "subprocess.Popen(\n"
                f"    [sys.executable, '-I', '-c', {grandchild!r}],\n"
                "    stdin=subprocess.DEVNULL,\n"
                "    stdout=subprocess.DEVNULL,\n"
                "    stderr=subprocess.DEVNULL,\n"
                ")\n"
                f"sys.stdout.buffer.write({events!r})\n"
                "sys.stdout.buffer.flush()\n"
            )
            with mock.patch.object(
                headless,
                "_child_command",
                return_value=[sys.executable, "-I", "-c", source],
            ):
                child = headless.run_child_process(plan, shard_id="s00", source_root=ROOT)
            self.assertEqual(0, child.return_code)
            self.assertEqual(events, child.raw_events)
            time.sleep(0.75)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX descendant containment")
    def test_child_process_timeout_kills_sleeping_grandchild(self) -> None:
        test_id = "test_example.ExampleTests.test_timeout"
        plan = _synthetic_plan((test_id,))
        with tempfile.TemporaryDirectory(prefix="wf-headless-timeout-tree-") as temporary:
            marker = Path(temporary) / "late-grandchild.bin"
            grandchild = (
                "import pathlib,time\n"
                "time.sleep(0.6)\n"
                f"pathlib.Path({str(marker)!r}).write_bytes(b'escaped')\n"
            )
            source = (
                "import subprocess,sys,time\n"
                "subprocess.Popen(\n"
                f"    [sys.executable, '-I', '-c', {grandchild!r}],\n"
                "    stdin=subprocess.DEVNULL,\n"
                "    stdout=subprocess.DEVNULL,\n"
                "    stderr=subprocess.DEVNULL,\n"
                ")\n"
                "time.sleep(30)\n"
            )
            with (
                mock.patch.object(headless, "_WORKER_TIMEOUT_SECONDS", 0.15),
                mock.patch.object(
                    headless,
                    "_child_command",
                    return_value=[sys.executable, "-I", "-c", source],
                ),
            ):
                child = headless.run_child_process(plan, shard_id="s00", source_root=ROOT)
            self.assertTrue(child.timed_out)
            time.sleep(0.75)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux descendant containment")
    def test_child_process_kills_setsid_double_fork_after_success(self) -> None:
        test_id = "test_example.ExampleTests.test_passed"
        plan = _synthetic_plan((test_id,))
        events = headless.encode_terminal_events((headless.terminal_event(test_id, "passed"),))
        with tempfile.TemporaryDirectory(prefix="wf-headless-double-fork-") as temporary:
            marker = Path(temporary) / "late-detached.bin"
            source = (
                "import os,pathlib,sys,time\n"
                "first = os.fork()\n"
                "if first == 0:\n"
                "    os.setsid()\n"
                "    second = os.fork()\n"
                "    if second == 0:\n"
                "        time.sleep(0.6)\n"
                f"        pathlib.Path({str(marker)!r}).write_bytes(b'escaped')\n"
                "        os._exit(0)\n"
                "    os._exit(0)\n"
                "os.waitpid(first, 0)\n"
                f"sys.stdout.buffer.write({events!r})\n"
                "sys.stdout.buffer.flush()\n"
            )
            with mock.patch.object(
                headless,
                "_child_command",
                return_value=[sys.executable, "-I", "-c", source],
            ):
                child = headless.run_child_process(plan, shard_id="s00", source_root=ROOT)
            self.assertEqual(0, child.return_code)
            self.assertEqual(events, child.raw_events)
            time.sleep(0.75)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(
        sys.platform.startswith("linux")
        and hasattr(release_gate, "_run_bounded_subprocess_execution"),
        "Linux broker containment implementation",
    )
    def test_child_process_broker_death_is_incomplete_and_fail_closed(self) -> None:
        test_id = "test_example.ExampleTests.test_passed"
        plan = _synthetic_plan((test_id,))
        events = headless.encode_terminal_events((headless.terminal_event(test_id, "passed"),))
        with tempfile.TemporaryDirectory(prefix="wf-headless-broker-death-") as temporary:
            pid_path = Path(temporary) / "target.pid"
            source = (
                "import os,pathlib,signal,sys,time\n"
                f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()))\n"
                f"sys.stdout.buffer.write({events!r})\n"
                "sys.stdout.buffer.flush()\n"
                "os.kill(os.getppid(), signal.SIGKILL)\n"
                "time.sleep(30)\n"
            )
            target_pid: int | None = None
            try:
                with mock.patch.object(
                    headless,
                    "_child_command",
                    return_value=[sys.executable, "-I", "-c", source],
                ):
                    child = headless.run_child_process(plan, shard_id="s00", source_root=ROOT)
                target_pid = int(pid_path.read_text(encoding="utf-8"))
                self.assertIsNone(child.return_code)
                self.assertEqual(b"", child.raw_events)
                normalized, receipt = headless.finalize_worker_evidence(
                    plan,
                    shard_id="s00",
                    raw_events=child.raw_events,
                    return_code=child.return_code,
                    timed_out=child.timed_out,
                )
                self.assertEqual("crash", headless.parse_terminal_events(normalized)[0]["outcome"])
                self.assertEqual("incomplete", receipt["state"])
                self.assertEqual("failed", receipt["status"])
            finally:
                if target_pid is not None:
                    try:
                        os.kill(target_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_plan_cli_publishes_one_current_canonical_axis_plan(self) -> None:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        with tempfile.TemporaryDirectory(prefix="wf-headless-plan-cli-") as temporary:
            output = Path(temporary) / "evidence"
            output.mkdir()
            tree = subprocess.run(
                ["git", "rev-parse", "HEAD^{tree}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            with mock.patch.object(headless, "resolve_git_source", return_value=(head, tree)):
                status = headless.main(
                    (
                        "plan",
                        "--source-root",
                        str(ROOT),
                        "--manifest",
                        str(MANIFEST),
                        "--output-dir",
                        str(output),
                        "--github-sha",
                        head,
                        "--runner-os",
                        "Linux",
                        "--runner-image",
                        "ubuntu24:20260810.1",
                        "--python-minor",
                        f"{sys.version_info.major}.{sys.version_info.minor}",
                    )
                )
            self.assertEqual(0, status)
            plan = headless.load_plan(output / "plan.json")
            self.assertEqual(head, plan["source"]["commit"])
            self.assertEqual(
                len(headless.discover_suite(ROOT, headless.load_manifest(MANIFEST)).test_ids),
                plan["discovery"]["test_count"],
            )

    def test_aggregate_requires_exact_four_by_sixteen_artifact_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-aggregate-") as temporary:
            artifacts = Path(temporary) / "artifacts"
            artifacts.mkdir()
            _write_aggregate_artifacts(artifacts)
            aggregate = headless.aggregate_artifacts(artifacts)

        self.assertEqual("world-forge.headless_suite_aggregate", aggregate["format"])
        self.assertEqual("passed", aggregate["status"])
        self.assertNotIn("supported", aggregate)
        self.assertEqual(4, len(aggregate["axes"]))
        self.assertEqual(64, sum(len(axis["shards"]) for axis in aggregate["axes"]))
        self.assertEqual(
            {("Linux", "3.11"), ("Linux", "3.12"), ("Windows", "3.11"), ("Windows", "3.12")},
            {(axis["runner_os"], axis["python_minor"]) for axis in aggregate["axes"]},
        )

    def test_aggregate_rejects_gaps_extras_plan_hash_drift_and_failed_outcomes(self) -> None:
        mutations = ("missing", "extra", "plan-drift", "hash-drift", "failed-outcome")
        for mutation in mutations:
            with tempfile.TemporaryDirectory(prefix=f"wf-headless-{mutation}-") as temporary:
                artifacts = Path(temporary) / "artifacts"
                artifacts.mkdir()
                _write_aggregate_artifacts(artifacts)
                target = artifacts / "headless-suite-Linux-py3.12-s00"
                if mutation == "missing":
                    shutil.rmtree(target)
                elif mutation == "extra":
                    extra = artifacts / "headless-suite-Linux-py3.12-s16"
                    shutil.copytree(target, extra)
                elif mutation == "plan-drift":
                    plan = json.loads((target / "plan.json").read_bytes())
                    plan["axis"]["runner_image"] = "drifted"
                    (target / "plan.json").write_bytes(canonical_json_bytes(plan))
                    receipt = json.loads((target / "receipt.json").read_bytes())
                    receipt["plan_sha256"] = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
                    (target / "receipt.json").write_bytes(canonical_json_bytes(receipt))
                elif mutation == "hash-drift":
                    receipt = json.loads((target / "receipt.json").read_bytes())
                    receipt["events_sha256"] = "f" * 64
                    (target / "receipt.json").write_bytes(canonical_json_bytes(receipt))
                else:
                    plan = json.loads((target / "plan.json").read_bytes())
                    test_id = plan["shards"][0]["expected_test_ids"][0]
                    events = headless.encode_terminal_events(
                        (headless.terminal_event(test_id, "failure"),)
                    )
                    (target / "events.jsonl").write_bytes(events)
                    _events, receipt = headless.finalize_worker_evidence(
                        plan,
                        shard_id="s00",
                        raw_events=events,
                        return_code=1,
                        timed_out=False,
                    )
                    (target / "receipt.json").write_bytes(canonical_json_bytes(receipt))
                with (
                    self.subTest(mutation=mutation),
                    self.assertRaisesRegex(
                        headless.HeadlessSuiteError,
                        "aggregate",
                    ),
                ):
                    headless.aggregate_artifacts(artifacts)

    def test_aggregate_rejects_consistent_cross_axis_test_id_substitution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-cross-axis-drift-") as temporary:
            artifacts = Path(temporary) / "artifacts"
            artifacts.mkdir()
            _write_aggregate_artifacts(artifacts)
            replacement = "test_injected.InjectedTests.test_unreviewed"
            replacement_stream = replacement.encode("utf-8") + b"\n"
            for index in range(16):
                shard_id = f"s{index:02d}"
                directory = artifacts / f"headless-suite-Windows-py3.12-{shard_id}"
                plan = json.loads((directory / "plan.json").read_bytes())
                substituted = plan["shards"][0]
                substituted["expected_test_ids"] = [replacement]
                substituted["expected_test_ids_sha256"] = hashlib.sha256(
                    replacement_stream
                ).hexdigest()
                substituted["expected_test_ids_size_bytes"] = len(replacement_stream)
                ordered = plan["discovery"]["ordered_test_ids"]
                ordered[0] = replacement
                ordered_stream = b"".join(test_id.encode("utf-8") + b"\n" for test_id in ordered)
                plan["discovery"]["test_ids_sha256"] = hashlib.sha256(ordered_stream).hexdigest()
                plan["discovery"]["test_ids_size_bytes"] = len(ordered_stream)
                plan_payload = canonical_json_bytes(plan)
                (directory / "plan.json").write_bytes(plan_payload)

                receipt = json.loads((directory / "receipt.json").read_bytes())
                receipt["plan_sha256"] = hashlib.sha256(plan_payload).hexdigest()
                if shard_id == "s00":
                    events = headless.encode_terminal_events(
                        (headless.terminal_event(replacement, "passed"),)
                    )
                    (directory / "events.jsonl").write_bytes(events)
                    receipt["events_sha256"] = hashlib.sha256(events).hexdigest()
                (directory / "receipt.json").write_bytes(canonical_json_bytes(receipt))

            with self.assertRaisesRegex(headless.HeadlessSuiteError, "aggregate.*drift"):
                headless.aggregate_artifacts(artifacts)

    def test_aggregate_rejects_consistently_forged_discovery_digest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-discovery-drift-") as temporary:
            artifacts = Path(temporary) / "artifacts"
            artifacts.mkdir()
            _write_aggregate_artifacts(artifacts)
            for directory in artifacts.iterdir():
                plan = json.loads((directory / "plan.json").read_bytes())
                plan["discovery"]["test_ids_sha256"] = "f" * 64
                plan_payload = canonical_json_bytes(plan)
                (directory / "plan.json").write_bytes(plan_payload)
                receipt = json.loads((directory / "receipt.json").read_bytes())
                receipt["plan_sha256"] = hashlib.sha256(plan_payload).hexdigest()
                (directory / "receipt.json").write_bytes(canonical_json_bytes(receipt))

            with self.assertRaisesRegex(headless.HeadlessSuiteError, "discovery"):
                headless.aggregate_artifacts(artifacts)

    def test_aggregate_rejects_rogue_symlink_hardlink_and_oversize_row_files(self) -> None:
        mutations = ["rogue", "hardlink", "oversize"]
        if os.name == "posix":
            mutations.append("symlink")
        for mutation in mutations:
            with tempfile.TemporaryDirectory(prefix=f"wf-headless-row-{mutation}-") as temporary:
                artifacts = Path(temporary) / "artifacts"
                artifacts.mkdir()
                _write_aggregate_artifacts(artifacts)
                target = artifacts / "headless-suite-Linux-py3.11-s00"
                if mutation == "rogue":
                    (target / "rogue.bin").write_bytes(b"not evidence")
                elif mutation == "hardlink":
                    events = target / "events.jsonl"
                    events.unlink()
                    os.link(target / "plan.json", events)
                elif mutation == "oversize":
                    (target / "events.jsonl").write_bytes(b"x" * (headless._EVENTS_LIMIT + 1))
                else:
                    receipt = target / "receipt.json"
                    receipt.unlink()
                    receipt.symlink_to("plan.json")
                with (
                    self.subTest(mutation=mutation),
                    self.assertRaises(headless.HeadlessSuiteError),
                ):
                    headless.aggregate_artifacts(artifacts)

    @unittest.skipUnless(os.name == "posix", "POSIX symlink fixture")
    def test_aggregate_rejects_artifact_root_symlink_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-root-link-") as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            _write_aggregate_artifacts(artifacts)
            alias = root / "artifact-alias"
            alias.symlink_to(artifacts, target_is_directory=True)
            with self.assertRaisesRegex(headless.HeadlessSuiteError, "artifact root"):
                headless.aggregate_artifacts(alias)

    def test_aggregate_root_scan_stops_at_the_bounded_extra_entry(self) -> None:
        class ChurningEntries:
            def __init__(self) -> None:
                self.count = 0

            def __enter__(self) -> ChurningEntries:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def __iter__(self) -> ChurningEntries:
                return self

            def __next__(self) -> object:
                self.count += 1
                if self.count > 65:
                    raise AssertionError("aggregate traversal exceeded its 64-row bound")
                return SimpleNamespace(name=f"rogue-{self.count:02d}")

        with tempfile.TemporaryDirectory(prefix="wf-headless-root-bound-") as temporary:
            root = Path(temporary)
            entries = ChurningEntries()
            with (
                mock.patch.object(headless.os, "scandir", return_value=entries),
                self.assertRaisesRegex(headless.HeadlessSuiteError, "bound"),
            ):
                headless.aggregate_artifacts(root)
            self.assertEqual(65, entries.count)

    def test_aggregate_publishes_attempt_always_and_result_only_on_complete_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-aggregate-publish-") as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            _write_aggregate_artifacts(artifacts)
            success = root / "success"
            success.mkdir()
            self.assertEqual(
                0,
                headless.run_aggregate_controller(
                    artifacts,
                    output_dir=success,
                    source_root=ROOT,
                ),
            )
            self.assertTrue((success / "aggregate.json").is_file())
            successful_attempt = json.loads((success / "aggregate-attempt.json").read_bytes())
            self.assertEqual("awaiting_aggregate", successful_attempt["state"])
            self.assertIs(successful_attempt["authoritative"], False)

            shutil.rmtree(artifacts / "headless-suite-Windows-py3.11-s15")
            failure = root / "failure"
            failure.mkdir()
            self.assertEqual(
                1,
                headless.run_aggregate_controller(
                    artifacts,
                    output_dir=failure,
                    source_root=ROOT,
                ),
            )
            self.assertFalse((failure / "aggregate.json").exists())
            failed_attempt = json.loads((failure / "aggregate-attempt.json").read_bytes())
            self.assertEqual("failed", failed_attempt["state"])
            self.assertIs(failed_attempt["authoritative"], False)

    def test_aggregate_publication_is_attempt_first_and_authority_last(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-authority-order-") as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            output = root / "output"
            artifacts.mkdir()
            output.mkdir()
            _write_aggregate_artifacts(artifacts)
            publications: list[tuple[str, dict[str, object]]] = []

            def record_publication(
                _directory: Path,
                name: str,
                payload: bytes,
                **_kwargs: object,
            ) -> None:
                publications.append((name, json.loads(payload)))

            with mock.patch.object(
                headless,
                "publish_bytes_noreplace",
                side_effect=record_publication,
            ):
                status = headless.run_aggregate_controller(
                    artifacts,
                    output_dir=output,
                    source_root=ROOT,
                )

            self.assertEqual(0, status)
            self.assertEqual(
                ["aggregate-attempt.json", "aggregate.json"],
                [name for name, _payload in publications],
            )
            attempt = publications[0][1]
            self.assertEqual(
                {
                    "authoritative": False,
                    "format": "world-forge.headless_suite_aggregate_attempt",
                    "format_version": 1,
                    "reason_code": None,
                    "state": "awaiting_aggregate",
                },
                attempt,
            )
            self.assertEqual("passed", publications[1][1]["status"])

    def test_aggregate_preclaims_and_partial_publish_never_authorize_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-preclaim-") as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            _write_aggregate_artifacts(artifacts)

            attempt_preclaim = root / "attempt-preclaim"
            attempt_preclaim.mkdir()
            (attempt_preclaim / "aggregate-attempt.json").write_bytes(b"rogue")
            self.assertEqual(
                1,
                headless.run_aggregate_controller(
                    artifacts,
                    output_dir=attempt_preclaim,
                    source_root=ROOT,
                ),
            )
            self.assertFalse((attempt_preclaim / "aggregate.json").exists())

            aggregate_preclaim = root / "aggregate-preclaim"
            aggregate_preclaim.mkdir()
            rogue_aggregate = canonical_json_bytes({"status": "passed"})
            (aggregate_preclaim / "aggregate.json").write_bytes(rogue_aggregate)
            self.assertEqual(
                1,
                headless.run_aggregate_controller(
                    artifacts,
                    output_dir=aggregate_preclaim,
                    source_root=ROOT,
                ),
            )
            self.assertEqual(rogue_aggregate, (aggregate_preclaim / "aggregate.json").read_bytes())
            aggregate_attempt = json.loads(
                (aggregate_preclaim / "aggregate-attempt.json").read_bytes()
            )
            self.assertEqual("awaiting_aggregate", aggregate_attempt["state"])
            self.assertIs(aggregate_attempt["authoritative"], False)

            partial = root / "partial"
            partial.mkdir()
            real_publish = headless.publish_bytes_noreplace

            def fail_authority(
                directory: Path,
                name: str,
                payload: bytes,
                **kwargs: object,
            ) -> None:
                if name == "aggregate.json":
                    raise OSError("simulated aggregate publication failure")
                real_publish(directory, name, payload, **kwargs)

            with mock.patch.object(
                headless,
                "publish_bytes_noreplace",
                side_effect=fail_authority,
            ):
                self.assertEqual(
                    1,
                    headless.run_aggregate_controller(
                        artifacts,
                        output_dir=partial,
                        source_root=ROOT,
                    ),
                )
            self.assertFalse((partial / "aggregate.json").exists())
            partial_attempt = json.loads((partial / "aggregate-attempt.json").read_bytes())
            self.assertEqual("awaiting_aggregate", partial_attempt["state"])
            self.assertIs(partial_attempt["authoritative"], False)

    def test_concurrent_aggregate_publication_has_one_authoritative_winner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-concurrent-") as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            output = root / "output"
            artifacts.mkdir()
            output.mkdir()
            _write_aggregate_artifacts(artifacts)
            barrier = threading.Barrier(2)
            statuses: list[int] = []

            def publish() -> None:
                barrier.wait(timeout=5)
                statuses.append(
                    headless.run_aggregate_controller(
                        artifacts,
                        output_dir=output,
                        source_root=ROOT,
                    )
                )

            threads = [threading.Thread(target=publish) for _index in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual([0, 1], sorted(statuses))
            attempt = json.loads((output / "aggregate-attempt.json").read_bytes())
            aggregate = json.loads((output / "aggregate.json").read_bytes())
            self.assertEqual("awaiting_aggregate", attempt["state"])
            self.assertIs(attempt["authoritative"], False)
            self.assertEqual("passed", aggregate["status"])

    def test_aggregate_cli_validates_exact_rows_and_publishes_canonical_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-headless-aggregate-cli-") as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            output = root / "output"
            artifacts.mkdir()
            output.mkdir()
            _write_aggregate_artifacts(artifacts)

            result = headless.main(
                [
                    "aggregate",
                    "--source-root",
                    str(ROOT),
                    "--artifacts-root",
                    str(artifacts),
                    "--output-dir",
                    str(output),
                ]
            )

            self.assertEqual(0, result)
            self.assertEqual(
                "passed",
                json.loads((output / "aggregate.json").read_bytes())["status"],
            )
            self.assertEqual(
                "awaiting_aggregate",
                json.loads((output / "aggregate-attempt.json").read_bytes())["state"],
            )

    def test_workflow_declares_static_bounded_matrix_artifacts_and_readiness_guard(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        _assert_headless_workflow_contract(workflow)

        shard_block = _workflow_job_block(workflow, "headless-suite-shards")
        aggregate_block = _workflow_job_block(workflow, "headless-suite-aggregate")
        for block in (shard_block, aggregate_block):
            for script in _literal_run_scripts(block):
                completed = subprocess.run(
                    ["bash", "-n"],
                    input=script,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual("", completed.stderr)
                self.assertEqual(0, completed.returncode)

    def test_workflow_contract_is_mutation_sensitive(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        mutations = (
            ("dynamic", workflow.replace("          - s15\n", "", 1)),
            ("timeout", workflow.replace("    timeout-minutes: 115\n", "", 1)),
            (
                "aggregate-timeout",
                workflow.replace("    timeout-minutes: 15\n", "", 1),
            ),
            (
                "max-parallel",
                workflow.replace("      max-parallel: 16\n", "", 1),
            ),
            (
                "windows-shell",
                workflow.replace(
                    "    defaults:\n      run:\n        shell: bash\n",
                    "",
                    1,
                ),
            ),
            (
                "upload",
                workflow.replace(
                    "        if: always()\n        uses: actions/upload-artifact@",
                    "        if: success()\n        uses: actions/upload-artifact@",
                    1,
                ),
            ),
            (
                "dependency",
                workflow.replace("    needs: headless-suite-aggregate\n", "", 1),
            ),
            (
                "aggregate-if",
                workflow.replace(
                    "      - name: Validate exact hosted headless suite\n"
                    "        id: headless-aggregate-validation\n"
                    "        if: always()\n",
                    "      - name: Validate exact hosted headless suite\n"
                    "        id: headless-aggregate-validation\n"
                    "        if: success()\n",
                    1,
                ),
            ),
            (
                "merge",
                workflow.replace("          merge-multiple: false\n", "", 1),
            ),
            (
                "axis",
                workflow.replace(
                    '        python-version: ["3.11", "3.12"]\n',
                    '        python-version: ["3.11", "3.12", "3.13"]\n',
                    1,
                ),
            ),
            (
                "unpinned",
                workflow.replace(
                    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
                    "actions/checkout@main",
                    1,
                ),
            ),
            (
                "wrong-pinned-sha",
                workflow.replace(
                    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
                    "actions/upload-artifact@" + "f" * 40,
                    1,
                ),
            ),
            (
                "authority-always-uploaded",
                workflow.replace(
                    "        if: success() && steps.headless-aggregate-validation.outcome == "
                    "'success'\n",
                    "        if: always()\n",
                    1,
                ),
            ),
            (
                "serial",
                workflow.replace(
                    "      - name: Validate foundation release profile\n",
                    "      - name: Forbidden serial rerun\n"
                    "        run: python -m unittest discover -s tests -v\n"
                    "      - name: Validate foundation release profile\n",
                    1,
                ),
            ),
        )
        for name, mutated in mutations:
            with self.subTest(name=name), self.assertRaises(AssertionError):
                _assert_headless_workflow_contract(mutated)


def _synthetic_plan(test_ids: tuple[str, ...]) -> dict[str, object]:
    stream = b"".join(test_id.encode("utf-8") + b"\n" for test_id in test_ids)
    shards = []
    for index in range(16):
        expected = test_ids if index == 0 else ()
        expected_stream = b"".join(test_id.encode("utf-8") + b"\n" for test_id in expected)
        shards.append(
            {
                "expected_test_count": len(expected),
                "expected_test_ids": list(expected),
                "expected_test_ids_sha256": hashlib.sha256(expected_stream).hexdigest(),
                "expected_test_ids_size_bytes": len(expected_stream),
                "id": f"s{index:02d}",
            }
        )
    return {
        "axis": {
            "python_full_version": "3.12.3",
            "python_minor": "3.12",
            "runner_image": "ubuntu24:20260810.1",
            "runner_os": "Linux",
        },
        "discovery": {
            "module_count": 1,
            "ordered_test_ids": list(test_ids),
            "test_count": len(test_ids),
            "test_ids_sha256": hashlib.sha256(stream).hexdigest(),
            "test_ids_size_bytes": len(stream),
        },
        "format": "world-forge.headless_suite_plan",
        "format_version": 1,
        "manifest_sha256": "c" * 64,
        "shards": shards,
        "source": {"commit": "a" * 40, "tree": "b" * 40},
    }


def _aggregate_plan(runner_os: str, python_minor: str) -> dict[str, object]:
    identifiers = tuple(f"test_shard_{index:02d}.ShardTests.test_axis" for index in range(16))
    plan = _synthetic_plan(identifiers)
    plan["discovery"]["module_count"] = 16
    plan["axis"] = {
        "python_full_version": "3.11.9" if python_minor == "3.11" else "3.12.3",
        "python_minor": python_minor,
        "runner_image": "windows2022:20260810.1"
        if runner_os == "Windows"
        else "ubuntu24:20260810.1",
        "runner_os": runner_os,
    }
    shards = []
    for index, test_id in enumerate(identifiers):
        stream = test_id.encode("utf-8") + b"\n"
        shards.append(
            {
                "expected_test_count": 1,
                "expected_test_ids": [test_id],
                "expected_test_ids_sha256": hashlib.sha256(stream).hexdigest(),
                "expected_test_ids_size_bytes": len(stream),
                "id": f"s{index:02d}",
            }
        )
    plan["shards"] = shards
    return plan


def _write_aggregate_artifacts(root: Path) -> None:
    for runner_os in ("Linux", "Windows"):
        for python_minor in ("3.11", "3.12"):
            plan = _aggregate_plan(runner_os, python_minor)
            for index in range(16):
                shard_id = f"s{index:02d}"
                directory = root / f"headless-suite-{runner_os}-py{python_minor}-{shard_id}"
                directory.mkdir()
                test_id = plan["shards"][index]["expected_test_ids"][0]
                events = headless.encode_terminal_events(
                    (headless.terminal_event(test_id, "passed"),)
                )
                _events, receipt = headless.finalize_worker_evidence(
                    plan,
                    shard_id=shard_id,
                    raw_events=events,
                    return_code=0,
                    timed_out=False,
                )
                (directory / "plan.json").write_bytes(canonical_json_bytes(plan))
                (directory / "events.jsonl").write_bytes(events)
                (directory / "receipt.json").write_bytes(canonical_json_bytes(receipt))


def _workflow_job_block(workflow: str, job_id: str) -> str:
    marker = f"  {job_id}:\n"
    if workflow.count(marker) != 1:
        raise AssertionError(f"workflow job {job_id} is not exact")
    remainder = workflow.split(marker, 1)[1]
    lines = remainder.splitlines(keepends=True)
    end = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("  ") and not line.startswith("    ")
        ),
        len(lines),
    )
    return "".join(lines[:end])


def _literal_run_scripts(job_block: str) -> tuple[str, ...]:
    lines = job_block.splitlines()
    scripts = []
    index = 0
    while index < len(lines):
        if lines[index] != "        run: |":
            index += 1
            continue
        index += 1
        payload = []
        while index < len(lines) and lines[index].startswith("          "):
            payload.append(lines[index][10:])
            index += 1
        if not payload:
            raise AssertionError("workflow literal run block is empty")
        scripts.append("\n".join(payload) + "\n")
    return tuple(scripts)


def _yaml_sequence(job_block: str, key: str, *, indent: int) -> tuple[str, ...]:
    marker = " " * indent + f"{key}:"
    lines = job_block.splitlines()
    if lines.count(marker) != 1:
        raise AssertionError(f"workflow matrix key {key} is not exact")
    index = lines.index(marker) + 1
    prefix = " " * (indent + 2) + "- "
    values = []
    while index < len(lines) and lines[index].startswith(prefix):
        values.append(lines[index].removeprefix(prefix))
        index += 1
    if not values:
        raise AssertionError(f"workflow matrix key {key} is empty")
    return tuple(values)


def _assert_pinned_actions(job_block: str) -> None:
    uses = re.findall(r"^        uses: ([^\s]+)(?:\s+#.*)?$", job_block, re.MULTILINE)
    if not uses:
        raise AssertionError("workflow job has no actions")
    exact_actions = {
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    }
    if any(action not in exact_actions for action in uses):
        raise AssertionError("workflow action pin drifted from the reviewed commit")


def _assert_headless_workflow_contract(workflow: str) -> None:
    shard = _workflow_job_block(workflow, "headless-suite-shards")
    aggregate = _workflow_job_block(workflow, "headless-suite-aggregate")
    readiness = _workflow_job_block(workflow, "release-readiness")
    if "python -m unittest discover -s tests -v" in workflow:
        raise AssertionError("workflow must not rerun the serial headless suite")
    required_shard_fragments = (
        "    timeout-minutes: 115\n",
        "    defaults:\n      run:\n        shell: bash\n",
        "      fail-fast: false\n",
        "      max-parallel: 16\n",
        "          - ubuntu-24.04\n          - windows-2022\n",
        '        python-version: ["3.11", "3.12"]\n',
        "python -m scripts.verify_headless_suite plan",
        "python -m scripts.verify_headless_suite worker",
        "python -m pip install --requirement requirements-m5.lock",
        "python -m pip install --no-build-isolation --no-deps -e .",
        ': "${ImageOS:?missing hosted runner image OS}"',
        ': "${ImageVersion:?missing hosted runner image version}"',
        "        if: always()\n        uses: actions/upload-artifact@",
        "          if-no-files-found: error\n",
        "          retention-days: 90\n",
        "plan.json\n",
        "events.jsonl\n",
        "receipt.json\n",
    )
    if any(fragment not in shard for fragment in required_shard_fragments):
        raise AssertionError("headless shard workflow contract drifted")
    operating_systems = _yaml_sequence(shard, "os", indent=8)
    shard_ids = _yaml_sequence(shard, "shard", indent=8)
    python_versions_match = re.search(
        r"^        python-version: (\[[^\n]+\])$", shard, re.MULTILINE
    )
    if python_versions_match is None:
        raise AssertionError("headless Python matrix is not a closed inline sequence")
    python_versions = json.loads(python_versions_match.group(1))
    if (
        operating_systems != ("ubuntu-24.04", "windows-2022")
        or python_versions != ["3.11", "3.12"]
        or shard_ids != tuple(f"s{index:02d}" for index in range(16))
        or len(operating_systems) * len(python_versions) * len(shard_ids) != 64
    ):
        raise AssertionError("headless shard matrix is not static and exact")
    if "fromJSON(" in shard or "include:" in shard:
        raise AssertionError("headless shard matrix must not be dynamic")
    required_aggregate_fragments = (
        "    needs: headless-suite-shards\n",
        "    if: always()\n",
        "    timeout-minutes: 15\n",
        "          pattern: headless-suite-*-py*-s*\n",
        "          merge-multiple: false\n",
        "python -m scripts.verify_headless_suite aggregate",
        "      - name: Upload non-authoritative headless aggregate attempt\n"
        "        if: always()\n"
        "        uses: actions/upload-artifact@",
        "          name: headless-suite-aggregate-attempt\n",
        "          path: ${{ runner.temp }}/world-forge-headless-suite-aggregate/"
        "aggregate-attempt.json\n",
        "      - name: Upload authoritative passing headless aggregate\n"
        "        if: success() && steps.headless-aggregate-validation.outcome == 'success'\n"
        "        uses: actions/upload-artifact@",
        "          name: headless-suite-aggregate\n",
        "          path: ${{ runner.temp }}/world-forge-headless-suite-aggregate/aggregate.json\n",
        "          if-no-files-found: error\n",
        "          retention-days: 90\n",
    )
    if any(fragment not in aggregate for fragment in required_aggregate_fragments):
        raise AssertionError("headless aggregate workflow contract drifted")
    if (
        "      - name: Validate exact hosted headless suite\n"
        "        id: headless-aggregate-validation\n"
        "        if: always()\n"
        not in aggregate
        or aggregate.count("uses: actions/upload-artifact@") != 2
        or aggregate.count("aggregate-attempt.json") != 1
        or aggregate.count("/aggregate.json") != 1
        or "continue-on-error:" in shard + aggregate
        or "unknown" in shard
    ):
        raise AssertionError("headless aggregate failure handling drifted")
    if (
        "    needs: headless-suite-aggregate\n" not in readiness
        or "    if: always()\n" not in readiness
        or "${{ needs.headless-suite-aggregate.result }}" not in readiness
        or "      - name: Enforce complete hosted headless suite\n        if: always()\n"
        not in readiness
        or "Release readiness (${{ matrix.os }}, Python ${{ matrix.python-version }})"
        not in readiness
        or "Run native Windows world-project migration gate" not in readiness
        or "Lint and formatting" not in readiness
        or "Compile Python" not in readiness
        or "Validate foundation release profile" not in readiness
        or "Audit source contracts" not in readiness
        or "Audit runtime AI boundary" not in readiness
        or "Verify neutral standalone and reproducible releases" not in readiness
    ):
        raise AssertionError("release readiness headless dependency drifted")
    for block in (shard, aggregate):
        _assert_pinned_actions(block)
        if "\t" in block or any(line.endswith(" ") for line in block.splitlines()):
            raise AssertionError("headless workflow YAML whitespace is invalid")


if __name__ == "__main__":
    unittest.main()
