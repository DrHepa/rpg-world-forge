from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import worldforge.workflow as workflow
from isoworld.content.loader import load_worldpack
from worldforge.assets import init_asset_manifest, validate_asset_manifest
from worldforge.claims import validate_claims
from worldforge.compiler import build_worldpack, compile_project
from worldforge.integrity import canonical_payload_hash
from worldforge.project import SourceProject, load_source_project
from worldforge.scaffold import create_world_project
from worldforge.validation import validate_project
from worldforge.workflow import WorkflowError, complete_phase, describe_status, reopen_phase

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples/foundation/source/manifest.json"
COMPILED = ROOT / "content/compiled/foundation.worldpack.json"


class ContentPipelineTests(unittest.TestCase):
    def test_phase_report_schema_uses_portable_project_paths(self) -> None:
        schema = json.loads((ROOT / "schemas/phase-report.schema.json").read_text(encoding="utf-8"))
        project_path = schema["$defs"]["projectPath"]
        pattern = re.compile(project_path["pattern"])
        for value in (
            "source/design/brief.md",
            "build/world.worldpack.json",
        ):
            with self.subTest(valid=value):
                self.assertIsNotNone(pattern.fullmatch(value))
        for value in (
            "/absolute/file.json",
            "source\\brief.md",
            "source//brief.md",
            "source/../brief.md",
            "build/CON.json",
            "build/trailing.",
            "build/bad:name.json",
        ):
            with self.subTest(invalid=value):
                self.assertIsNone(pattern.fullmatch(value))
        self.assertEqual(
            {"$ref": "#/$defs/projectPath"},
            schema["properties"]["deliverables"]["items"],
        )
        for field in (
            "asset_manifest_path",
            "handoff_path",
            "renderpack_path",
            "worldpack_path",
        ):
            self.assertEqual(
                {"$ref": "#/$defs/projectPath"},
                schema["properties"][field],
            )
        self.assertFalse(schema["properties"]["validations"]["items"]["additionalProperties"])

    def test_foundation_source_is_valid(self) -> None:
        project = load_source_project(MANIFEST)
        self.assertEqual([], validate_project(project))

    def test_compilation_is_reproducible(self) -> None:
        project = load_source_project(MANIFEST)
        first = build_worldpack(project)
        second = build_worldpack(project)
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(first, second)

    def test_checked_in_worldpack_matches_sources(self) -> None:
        expected = build_worldpack(load_source_project(MANIFEST))
        actual = json.loads(COMPILED.read_text(encoding="utf-8"))
        self.assertEqual(expected, actual)

    def test_compiled_pack_loads_without_authoring_package(self) -> None:
        pack = load_worldpack(COMPILED)
        self.assertEqual("foundation_slice", pack.world_id)
        self.assertEqual(("explorer", "maker"), pack.playable_actor_ids)

    def test_placeholder_is_rejected(self) -> None:
        original = load_source_project(MANIFEST)
        broken_world = dict(original.world)
        broken_world["title"] = "{{WORLD_TITLE}}"
        broken = SourceProject(original.manifest_path, broken_world, original.collections)
        issues = validate_project(broken)
        self.assertTrue(any("placeholder" in issue.message for issue in issues))

    def test_world_can_declare_an_arbitrary_playable_roster_policy(self) -> None:
        original = load_source_project(MANIFEST)
        strict_world = dict(original.world)
        strict_world["content_policy"] = {
            "exact_playable_actor_count": 7,
            "playable_requires_personal_arc": True,
        }
        strict = SourceProject(original.manifest_path, strict_world, original.collections)
        messages = [issue.message for issue in validate_project(strict)]
        self.assertTrue(any("expected 7 playable actors" in message for message in messages))
        self.assertEqual(
            2,
            sum("requires a personal arc" in message for message in messages),
        )

    def test_malformed_m1_locations_report_issues_without_crashing(self) -> None:
        original = load_source_project(MANIFEST)
        collections = {key: list(value) for key, value in original.collections.items()}
        collections["schedules"] = [
            {
                "id": "broken_schedule",
                "entries": [
                    {
                        "start_minute": 0,
                        "end_minute": 100,
                        "map_id": "missing_map",
                        "x": "bad",
                        "y": 0,
                        "activity": "wait",
                        "fallbacks": ["not_a_location"],
                    }
                ],
            }
        ]
        broken = SourceProject(original.manifest_path, original.world, collections)
        messages = [issue.message for issue in validate_project(broken)]
        self.assertTrue(any("unknown map" in message for message in messages))
        self.assertTrue(any("location object" in message for message in messages))

    def test_compiler_writes_loadable_pack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test.worldpack.json"
            compile_project(MANIFEST, output)
            self.assertEqual("foundation_slice", load_worldpack(output).world_id)

    def test_new_world_scaffold_is_generic_and_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "another_world"
            manifest = create_world_project(
                target,
                world_id="another_world",
                title="Another World",
                language="es",
                actor_id="first_actor",
                actor_name="First Actor",
            )
            project = load_source_project(manifest)
            self.assertEqual([], validate_project(project))
            self.assertEqual("another_world", project.world["id"])
            self.assertEqual("first_actor", project.collections["actors"][0]["id"])
            self.assertIn("interactions", project.collections)
            self.assertEqual(20, project.world["simulation"]["ticks_per_minute"])
            compiled = build_worldpack(project)
            self.assertEqual(5, compiled["format_version"])
            self.assertEqual("0.1.0", project.world["version"])
            self.assertEqual("es", project.world["default_locale"])
            self.assertEqual(["es"], project.world["supported_locales"])
            self.assertIn("consequences", compiled["collections"])
            self.assertIn("constructions", compiled["collections"])
            self.assertTrue((target / ".gitignore").is_file())
            self.assertTrue((target / "AGENTS.md").is_file())
            self.assertTrue((target / ".worldforge/status.json").is_file())
            self.assertTrue((target / "source/timeline/README.md").is_file())
            project_metadata = json.loads(
                (target / ".worldforge/project.json").read_text(encoding="utf-8")
            )
            self.assertEqual("world", project_metadata["project_kind"])
            self.assertEqual(2, project_metadata["format_version"])
            self.assertEqual("0.1.0", project_metadata["world_version"])
            self.assertIn(
                "world-authoring repository",
                (target / "AGENTS.md").read_text(encoding="utf-8"),
            )
            readme = (target / "README.md").read_text(encoding="utf-8")
            self.assertIn("World-authoring project", readme)
            self.assertIn("--output assets/release/renderpack.json", readme)
            self.assertNotIn("Game project created", readme)
            self.assertFalse((target / ".agents").exists())
            self.assertFalse((target / "src").exists())

    def test_new_world_can_begin_without_inventing_a_character(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "blank_world"
            manifest = create_world_project(
                target,
                world_id="blank_world",
                title="World in Design",
                language="es",
            )
            project = load_source_project(manifest)
            self.assertEqual([], validate_project(project, profile="draft"))
            release_messages = [
                issue.message for issue in validate_project(project, profile="release")
            ]
            self.assertIn("at least one playable actor is required", release_messages)
            self.assertIn("phase=p00_brief", describe_status(target))

    def test_phase_gate_advances_only_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "workflow_world"
            create_world_project(
                target,
                world_id="workflow_world",
                title="Workflow World",
                language="es",
            )
            deliverable = target / "source/design/experience_brief.md"
            deliverable.write_text(
                "# Brief\n\nExperiencia y restricciones aprobadas.\n", encoding="utf-8"
            )
            report = target / "phase_report.json"
            report.write_text(
                json.dumps(
                    {
                        "format": "rpg-world-forge.phase_report",
                        "format_version": 1,
                        "phase": "p00_brief",
                        "status": "ready",
                        "summary": "Brief approved",
                        "deliverables": ["source/design/experience_brief.md"],
                        "decisions": ["dec_001"],
                        "blockers": [],
                        "validations": [
                            {
                                "name": "brief_review",
                                "passed": True,
                                "evidence": "Lead-agent review",
                            }
                        ],
                        "reviewed_by": "gpt-lead",
                    }
                ),
                encoding="utf-8",
            )
            status = complete_phase(target, report)
            self.assertEqual("p01_genre_style", status["current_phase"])
            self.assertEqual(["p00_brief"], status["completed_phases"])
            self.assertTrue((target / ".worldforge/phase_reports/p00_brief.json").is_file())
            status.update(
                {
                    "compatibility_report": "build/compatibility.json",
                    "release_hash": "a" * 64,
                    "release_package": "dist/world.rwfworld",
                }
            )
            (target / ".worldforge/status.json").write_text(
                json.dumps(status),
                encoding="utf-8",
            )
            reopened = reopen_phase(
                target,
                "p00_brief",
                reason="Scope changed after review",
                approved_by="gpt-lead",
            )
            self.assertEqual("p00_brief", reopened["current_phase"])
            self.assertEqual([], reopened["completed_phases"])
            self.assertIsNone(reopened["compatibility_report"])
            self.assertIsNone(reopened["release_hash"])
            self.assertIsNone(reopened["release_package"])
            self.assertTrue((target / ".worldforge/reopen_log.json").is_file())

    def test_phase_mutations_share_the_lifecycle_lock_and_roll_back_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "locked_workflow"
            create_world_project(
                target,
                world_id="locked_workflow",
                title="Locked Workflow",
                language="en",
            )
            deliverable = target / "source/design/experience_brief.md"
            deliverable.write_text("# Approved brief\n", encoding="utf-8")
            report = target / "phase_report.json"
            report.write_text(
                json.dumps(
                    {
                        "format": "rpg-world-forge.phase_report",
                        "format_version": 1,
                        "phase": "p00_brief",
                        "status": "ready",
                        "summary": "Approved workflow lock fixture",
                        "deliverables": ["source/design/experience_brief.md"],
                        "decisions": [],
                        "blockers": [],
                        "validations": [
                            {
                                "name": "review",
                                "passed": True,
                                "evidence": "Lead review",
                            }
                        ],
                        "reviewed_by": "lead",
                    }
                ),
                encoding="utf-8",
            )
            status_path = target / ".worldforge/status.json"
            report_target = target / ".worldforge/phase_reports/p00_brief.json"
            before = status_path.read_bytes()
            lock = target / ".worldforge/lifecycle.lock"
            lock.write_text("owned elsewhere\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "already in progress"):
                complete_phase(target, report)
            self.assertEqual("owned elsewhere\n", lock.read_text(encoding="utf-8"))
            self.assertEqual(before, status_path.read_bytes())
            self.assertFalse(report_target.exists())
            lock.unlink()

            original_replace = workflow._replace_file
            calls = 0

            def fail_second(source: Path, destination: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("simulated publication failure")
                original_replace(source, destination)

            with patch("worldforge.workflow._replace_file", side_effect=fail_second):
                with self.assertRaisesRegex(OSError, "publication failure"):
                    complete_phase(target, report)
            self.assertEqual(before, status_path.read_bytes())
            self.assertFalse(report_target.exists())
            self.assertFalse(lock.exists())

            complete_phase(target, report)
            lock.write_text("owned elsewhere\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "already in progress"):
                reopen_phase(
                    target,
                    "p00_brief",
                    reason="New evidence",
                    approved_by="lead",
                )
            self.assertEqual("owned elsewhere\n", lock.read_text(encoding="utf-8"))

    def test_phase_status_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "duplicate_status"
            create_world_project(
                target,
                world_id="duplicate_status",
                title="Duplicate Status",
                language="en",
            )
            status_path = target / ".worldforge/status.json"
            original = status_path.read_text(encoding="utf-8")
            status_path.write_text(
                original.replace("{\n", '{\n  "revision": 999,\n', 1),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowError, "duplicate JSON object key"):
                workflow.load_status(target)
            self.assertFalse((target / ".worldforge/lifecycle.lock").exists())

    def test_workflow_entry_points_reject_forged_phase_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "forged_progress"
            create_world_project(
                target,
                world_id="forged_progress",
                title="Forged Progress",
                language="en",
            )
            status_path = target / ".worldforge/status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["completed_phases"] = []
            status["current_phase"] = "p14_handoff"
            status_path.write_text(json.dumps(status), encoding="utf-8")

            with self.assertRaisesRegex(WorkflowError, "must follow"):
                describe_status(target)
            with self.assertRaisesRegex(WorkflowError, "must follow"):
                complete_phase(target, target / "missing-phase-report.json")
            self.assertFalse((target / ".worldforge/lifecycle.lock").exists())

    def test_phase_entry_points_bind_status_to_canonical_v2_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "canonical_controls"
            create_world_project(
                target,
                world_id="canonical_controls",
                title="Canonical Controls",
                language="en",
            )
            missing_report = target / "missing.json"
            operations = (
                lambda: describe_status(target),
                lambda: complete_phase(target, missing_report),
                lambda: reopen_phase(target, "p00_brief", reason="test", approved_by="lead"),
            )

            project_path = target / ".worldforge/project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["project_kind"] = "game"
            project_path.write_text(json.dumps(project), encoding="utf-8")
            for operation in operations:
                with (
                    self.subTest(control="kind", operation=operation),
                    self.assertRaisesRegex(
                        WorkflowError,
                        "game repository",
                    ),
                ):
                    operation()

            project["project_kind"] = "world"
            project_path.write_text(json.dumps(project), encoding="utf-8")
            status_path = target / ".worldforge/status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status["world_version"] = "9.9.9"
            status_path.write_text(json.dumps(status), encoding="utf-8")
            for operation in operations:
                with (
                    self.subTest(control="version", operation=operation),
                    self.assertRaisesRegex(
                        WorkflowError,
                        "versions do not match",
                    ),
                ):
                    operation()
            self.assertFalse((target / ".worldforge/lifecycle.lock").exists())

    def test_phase_report_enforces_closed_contract_and_normalized_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "closed_report"
            create_world_project(
                target,
                world_id="closed_report",
                title="Closed Report",
                language="en",
            )
            deliverable = target / "source/design/experience_brief.md"
            deliverable.write_text("# Brief\n", encoding="utf-8")
            base = {
                "format": "rpg-world-forge.phase_report",
                "format_version": 1,
                "phase": "p00_brief",
                "status": "ready",
                "summary": "Approved brief",
                "deliverables": ["source/design/experience_brief.md"],
                "decisions": [],
                "blockers": [],
                "validations": [{"name": "review", "passed": True, "evidence": "Lead review"}],
                "reviewed_by": "lead",
            }
            variants = (
                ({key: value for key, value in base.items() if key != "summary"}, "missing fields"),
                (
                    {key: value for key, value in base.items() if key != "decisions"},
                    "missing fields",
                ),
                (dict(base, validations=[{"passed": True}]), "requires a name"),
                (dict(base, unexpected=True), "unknown fields"),
                (
                    dict(base, deliverables=[str(deliverable.resolve())]),
                    "normalized relative POSIX",
                ),
                (
                    dict(base, handoff_path="source/design/../design/experience_brief.md"),
                    "handoff_path must be a normalized relative POSIX",
                ),
            )
            report = target / "phase-report.json"
            for payload, message in variants:
                report.write_text(json.dumps(payload), encoding="utf-8")
                with (
                    self.subTest(message=message),
                    self.assertRaisesRegex(
                        WorkflowError,
                        message,
                    ),
                ):
                    complete_phase(target, report)

            outside = Path(directory) / "outside-evidence"
            outside.mkdir()
            (outside / "brief.md").write_text("# Outside\n", encoding="utf-8")
            try:
                os.symlink(outside, target / "artifacts", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are not available")
            report.write_text(
                json.dumps(dict(base, deliverables=["artifacts/brief.md"])),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowError, "unsafe path"):
                complete_phase(target, report)

    def test_phase_transaction_rejects_external_symlink_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "transaction_paths"
            create_world_project(
                target,
                world_id="transaction_paths",
                title="Transaction Paths",
                language="en",
            )
            deliverable = target / "source/design/experience_brief.md"
            deliverable.write_text("# Brief\n", encoding="utf-8")
            report = target / "phase-report.json"
            report.write_text(
                json.dumps(
                    {
                        "format": "rpg-world-forge.phase_report",
                        "format_version": 1,
                        "phase": "p00_brief",
                        "status": "ready",
                        "summary": "Approved brief",
                        "deliverables": ["source/design/experience_brief.md"],
                        "decisions": [],
                        "blockers": [],
                        "validations": [
                            {"name": "review", "passed": True, "evidence": "Lead review"}
                        ],
                        "reviewed_by": "lead",
                    }
                ),
                encoding="utf-8",
            )
            outside = Path(directory) / "outside"
            outside.mkdir()
            reports = target / ".worldforge/phase_reports"
            if reports.exists():
                (reports / "README.md").unlink()
                reports.rmdir()
            try:
                os.symlink(outside, reports, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are not available")
            status_path = target / ".worldforge/status.json"
            before = status_path.read_bytes()
            with self.assertRaisesRegex(WorkflowError, "symbolic link"):
                complete_phase(target, report)
            self.assertEqual(before, status_path.read_bytes())
            self.assertFalse((outside / "p00_brief.json").exists())
            reports.unlink()

            complete_phase(target, report)
            reopen_log = target / ".worldforge/reopen_log.json"
            outside_log = outside / "reopen.json"
            os.symlink(outside_log, reopen_log)
            before = status_path.read_bytes()
            with self.assertRaisesRegex(WorkflowError, "symbolic link"):
                reopen_phase(
                    target,
                    "p00_brief",
                    reason="New evidence",
                    approved_by="lead",
                )
            self.assertEqual(before, status_path.read_bytes())
            self.assertFalse(outside_log.exists())

    def test_p10_loads_worldpack_and_binds_world_identity_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "p10_world"
            manifest = create_world_project(
                target,
                world_id="p10_world",
                title="P10 World",
                language="en",
                actor_id="hero",
                actor_name="Hero",
            )
            worldpack_path = target / "build/p10.worldpack.json"
            canonical = compile_project(manifest, worldpack_path)
            status_path = target / ".worldforge/status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status.update(
                {
                    "completed_phases": [phase.id for phase in workflow.PHASES[:10]],
                    "current_phase": "p10_canon_lock",
                    "revision": 10,
                }
            )
            status_path.write_text(json.dumps(status), encoding="utf-8")
            report_path = target / "p10-report.json"

            def write_candidate(candidate: dict[str, object]) -> None:
                candidate["content_hash"] = canonical_payload_hash(candidate)
                worldpack_path.write_text(json.dumps(candidate), encoding="utf-8")
                report_path.write_text(
                    json.dumps(
                        {
                            "format": "rpg-world-forge.phase_report",
                            "format_version": 1,
                            "phase": "p10_canon_lock",
                            "status": "ready",
                            "summary": "Canon candidate validated",
                            "deliverables": ["build/p10.worldpack.json"],
                            "decisions": [],
                            "blockers": [],
                            "validations": [
                                {
                                    "name": "worldpack",
                                    "passed": True,
                                    "evidence": "Compiler output",
                                }
                            ],
                            "reviewed_by": "lead",
                            "worldpack_path": "build/p10.worldpack.json",
                            "worldpack_hash": candidate["content_hash"],
                        }
                    ),
                    encoding="utf-8",
                )

            malformed = json.loads(json.dumps(canonical))
            malformed["collections"].pop("locales")
            write_candidate(malformed)
            with self.assertRaisesRegex(WorkflowError, "Worldpack validation failed"):
                complete_phase(target, report_path)

            wrong_world = json.loads(json.dumps(canonical))
            wrong_world["world"]["id"] = "another_world"
            write_candidate(wrong_world)
            with self.assertRaisesRegex(WorkflowError, "world_id does not match"):
                complete_phase(target, report_path)

            wrong_version = json.loads(json.dumps(canonical))
            wrong_version["world"]["version"] = "9.9.9"
            write_candidate(wrong_version)
            with self.assertRaisesRegex(WorkflowError, "version does not match"):
                complete_phase(target, report_path)

            write_candidate(json.loads(json.dumps(canonical)))
            completed = complete_phase(target, report_path)
            self.assertEqual("p11_art_audio", completed["current_phase"])

    def test_p10_rejects_relative_evidence_through_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "p10_symlink"
            manifest = create_world_project(
                target,
                world_id="p10_symlink",
                title="P10 Symlink",
                language="en",
                actor_id="hero",
                actor_name="Hero",
            )
            outside = Path(directory) / "outside-worldpacks"
            outside.mkdir()
            pack_path = outside / "canon.worldpack.json"
            pack = compile_project(manifest, pack_path)
            try:
                os.symlink(outside, target / "artifacts", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are not available")
            status_path = target / ".worldforge/status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            status.update(
                {
                    "completed_phases": [phase.id for phase in workflow.PHASES[:10]],
                    "current_phase": "p10_canon_lock",
                    "revision": 10,
                }
            )
            status_path.write_text(json.dumps(status), encoding="utf-8")
            report = target / "p10-report.json"
            report.write_text(
                json.dumps(
                    {
                        "format": "rpg-world-forge.phase_report",
                        "format_version": 1,
                        "phase": "p10_canon_lock",
                        "status": "ready",
                        "summary": "Reject linked evidence",
                        "deliverables": ["artifacts/canon.worldpack.json"],
                        "decisions": [],
                        "blockers": [],
                        "validations": [{"name": "pack", "passed": True, "evidence": "Compiler"}],
                        "reviewed_by": "lead",
                        "worldpack_path": "artifacts/canon.worldpack.json",
                        "worldpack_hash": pack["content_hash"],
                    }
                ),
                encoding="utf-8",
            )
            before = status_path.read_bytes()
            with self.assertRaisesRegex(WorkflowError, "inside the project"):
                complete_phase(target, report)
            self.assertEqual(before, status_path.read_bytes())

    def test_multi_agent_claims_cannot_overlap_canonical_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "claims_world"
            create_world_project(
                target,
                world_id="claims_world",
                title="Claims World",
                language="es",
            )
            claims_root = target / ".worldforge/claims"
            base = {
                "format": "rpg-world-forge.task_claim",
                "format_version": 1,
                "owner": "agent",
                "role": "canon_research",
                "status": "claimed",
                "objective": "Research a bounded topic",
                "non_goals": [],
                "read_inputs": [],
                "dependencies": [],
                "validation": ["lead_review"],
                "handoff_path": ".worldforge/handoffs/report.md",
            }
            first = dict(base, task_id="canon_task", owned_paths=["source/canon"])
            second = dict(
                base,
                task_id="facts_task",
                owned_paths=["source/canon/facts.json"],
            )
            (claims_root / "canon_task.json").write_text(json.dumps(first), encoding="utf-8")
            (claims_root / "facts_task.json").write_text(json.dumps(second), encoding="utf-8")
            messages = [issue.message for issue in validate_claims(target)]
            self.assertTrue(any("ownership overlaps" in message for message in messages))

    def test_asset_plan_is_bound_to_compiled_world_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "assets/manifest.json"
            manifest = init_asset_manifest(COMPILED, manifest_path)
            compiled = json.loads(COMPILED.read_text(encoding="utf-8"))
            self.assertEqual(compiled["content_hash"], manifest["world_content_hash"])
            self.assertEqual(
                [],
                validate_asset_manifest(
                    manifest_path,
                    profile="draft",
                    worldpack_path=COMPILED,
                ),
            )

    def test_asset_release_cannot_be_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "assets/manifest.json"
            init_asset_manifest(COMPILED, manifest_path)
            messages = [
                issue.message for issue in validate_asset_manifest(manifest_path, profile="release")
            ]
            self.assertIn("a release must contain assets", messages)

    def test_asset_release_requires_processed_and_human_approved_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "assets/manifest.json"
            init_asset_manifest(COMPILED, manifest_path)
            spec = manifest_path.parent / "specs/hero.json"
            spec.write_text("{}", encoding="utf-8")
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw["assets"] = [
                {
                    "id": "hero_sprite",
                    "kind": "sprite",
                    "status": "planned",
                    "specification_file": "specs/hero.json",
                }
            ]
            manifest_path.write_text(json.dumps(raw), encoding="utf-8")
            messages = [
                issue.message for issue in validate_asset_manifest(manifest_path, profile="release")
            ]
            self.assertIn("release requires processed status", messages)
            self.assertIn("provenance is required", messages)
            self.assertIn("license record is required", messages)
            self.assertIn("authorized approval is required", messages)

    def test_asset_plan_detects_changed_canon(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "assets/manifest.json"
            init_asset_manifest(COMPILED, manifest_path)
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw["world_content_hash"] = "0" * 64
            manifest_path.write_text(json.dumps(raw), encoding="utf-8")
            messages = [
                issue.message
                for issue in validate_asset_manifest(
                    manifest_path,
                    worldpack_path=COMPILED,
                )
            ]
            self.assertIn("canon changed; restart or migrate the asset plan", messages)


if __name__ == "__main__":
    unittest.main()
