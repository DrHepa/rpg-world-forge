from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from isoworld.content.loader import load_worldpack
from worldforge.assets import init_asset_manifest, validate_asset_manifest
from worldforge.claims import validate_claims
from worldforge.compiler import build_worldpack, compile_project
from worldforge.project import SourceProject, load_source_project
from worldforge.scaffold import create_world_project
from worldforge.validation import validate_project
from worldforge.workflow import complete_phase, describe_status, reopen_phase

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples/foundation/source/manifest.json"
COMPILED = ROOT / "content/compiled/foundation.worldpack.json"


class ContentPipelineTests(unittest.TestCase):
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
            self.assertEqual(2, build_worldpack(project)["format_version"])
            self.assertTrue((target / ".gitignore").is_file())
            self.assertTrue((target / "AGENTS.md").is_file())
            self.assertTrue((target / ".worldforge/status.json").is_file())
            self.assertTrue((target / "source/timeline/README.md").is_file())

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
            reopened = reopen_phase(
                target,
                "p00_brief",
                reason="Scope changed after review",
                approved_by="gpt-lead",
            )
            self.assertEqual("p00_brief", reopened["current_phase"])
            self.assertEqual([], reopened["completed_phases"])
            self.assertTrue((target / ".worldforge/reopen_log.json").is_file())

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
            self.assertIn("human approval is required", messages)

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
