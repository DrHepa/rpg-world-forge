from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import worldforge.world_lifecycle as lifecycle
from worldforge.compiler import compile_project
from worldforge.scaffold import ScaffoldError, create_world_project
from worldforge.workflow import PHASES, WorkflowError
from worldforge.world_lifecycle import (
    StableSemVer,
    bump_world_version,
    clone_world_project,
    inspect_world_project,
    parse_stable_semver,
    upgrade_legacy_world_project,
)


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _inject_duplicate_key(path: Path, key: str) -> None:
    payload = _read(path)
    encoded_key = json.dumps(key, ensure_ascii=False)
    encoded_value = json.dumps(payload[key], ensure_ascii=False)
    original = path.read_text(encoding="utf-8")
    path.write_bytes(
        original.replace("{\n", f"{{\n  {encoded_key}: {encoded_value},\n", 1).encode("utf-8")
    )


def _make_v2_world(root: Path, *, world_id: str = "source_world", version: str = "1.2.3") -> None:
    create_world_project(
        root,
        world_id=world_id,
        title="Source World",
        language="en",
    )
    world_path = root / "source/world.json"
    project_path = root / ".worldforge/project.json"
    status_path = root / ".worldforge/status.json"
    world = _read(world_path)
    project = _read(project_path)
    status = _read(status_path)
    world["version"] = version
    project.update({"format_version": 2, "project_kind": "world", "world_version": version})
    status["world_version"] = version
    _write(world_path, world)
    _write(project_path, project)
    _write(status_path, status)


class StableSemVerTests(unittest.TestCase):
    def test_accepts_only_stable_major_minor_patch(self) -> None:
        self.assertEqual(StableSemVer(0, 1, 0), parse_stable_semver("0.1.0"))
        self.assertEqual("12.0.9", str(parse_stable_semver("12.0.9")))
        for value in (
            "1",
            "1.2",
            "01.2.3",
            "1.02.3",
            "1.2.03",
            "1.2.3-alpha",
            "1.2.3+build",
            " 1.2.3",
            "1.2.3 ",
            True,
            123,
            None,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_stable_semver(value)

    def test_bumps_reset_lower_components(self) -> None:
        version = parse_stable_semver("2.8.6")
        self.assertEqual("3.0.0", str(version.bump("major")))
        self.assertEqual("2.9.0", str(version.bump("minor")))
        self.assertEqual("2.8.7", str(version.bump("patch")))
        with self.assertRaises(ValueError):
            version.bump("pre")


class ProjectInspectionTests(unittest.TestCase):
    def test_inspects_only_a_v2_world_authoring_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            result = inspect_world_project(root)
            self.assertEqual("source_world", result.world_id)
            self.assertEqual("1.2.3", result.world_version)
            self.assertFalse(result.legacy)
            self.assertEqual("p00_brief", result.current_phase)

    def test_inspection_rejects_a_platform_reserved_world_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            for relative in (
                ".worldforge/project.json",
                ".worldforge/status.json",
                "source/world.json",
            ):
                path = root / relative
                document = _read(path)
                if relative.endswith("world.json"):
                    document["id"] = "con"
                else:
                    document["world_id"] = "con"
                _write(path, document)
            with self.assertRaisesRegex(WorkflowError, "invalid world_id"):
                inspect_world_project(root)

    def test_rejects_game_unknown_and_implicit_legacy_projects(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            project_path = root / ".worldforge/project.json"
            for field, value in (
                ("project_kind", "game"),
                ("format", "some.other.project"),
                ("format_version", 9),
                ("format_version", True),
                ("format_version", 2.0),
            ):
                project = _read(project_path)
                original = project[field]
                project[field] = value
                _write(project_path, project)
                with self.subTest(field=field), self.assertRaises(WorkflowError):
                    inspect_world_project(root)
                project[field] = original
                _write(project_path, project)
            project = _read(project_path)
            project["format_version"] = 1
            project.pop("project_kind", None)
            project.pop("world_version", None)
            _write(project_path, project)
            world = _read(root / "source/world.json")
            world.pop("version", None)
            _write(root / "source/world.json", world)
            with self.assertRaisesRegex(WorkflowError, "explicit upgrade"):
                inspect_world_project(root)

    def test_rejects_dirty_or_inconsistent_release_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            status_path = root / ".worldforge/status.json"
            status = _read(status_path)
            status["worldpack_hash"] = "a" * 64
            _write(status_path, status)
            with self.assertRaisesRegex(WorkflowError, "complete or empty"):
                inspect_world_project(root)

    def test_rejects_duplicate_keys_in_project_world_and_status_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            for relative, key in (
                (".worldforge/project.json", "world_id"),
                ("source/manifest.json", "world"),
                ("source/world.json", "id"),
                (".worldforge/status.json", "world_id"),
            ):
                path = root / relative
                original = path.read_bytes()
                _inject_duplicate_key(path, key)
                with (
                    self.subTest(relative=relative),
                    self.assertRaisesRegex(
                        WorkflowError,
                        "duplicate JSON object key",
                    ),
                ):
                    inspect_world_project(root)
                path.write_bytes(original)

    def test_rejects_non_normalized_or_unsafe_source_manifest_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            manifest_path = root / "source/manifest.json"
            baseline = _read(manifest_path)
            absolute_world = str((root / "source/world.json").resolve())
            invalid_world_paths = (
                absolute_world,
                "./world.json",
                "nested/../world.json",
                "C:/world.json",
                "nested/world.json",
            )
            for value in invalid_world_paths:
                manifest = deepcopy(baseline)
                manifest["world"] = value
                _write(manifest_path, manifest)
                with (
                    self.subTest(world=value),
                    self.assertRaisesRegex(
                        WorkflowError,
                        "Source manifest world",
                    ),
                ):
                    inspect_world_project(root)

            invalid_collection_paths = (
                str((root / "source/maps/starting_area.json").resolve()),
                "./maps/starting_area.json",
                "maps//starting_area.json",
                "maps/../world.json",
                "maps\\starting_area.json",
            )
            for value in invalid_collection_paths:
                manifest = deepcopy(baseline)
                manifest["collections"]["maps"] = [value]
                _write(manifest_path, manifest)
                with (
                    self.subTest(collection=value),
                    self.assertRaisesRegex(
                        WorkflowError,
                        "relative POSIX path",
                    ),
                ):
                    inspect_world_project(root)

    def test_project_and_world_languages_are_consistent_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            project_path = root / ".worldforge/project.json"
            world_path = root / "source/world.json"
            project = _read(project_path)
            world = _read(world_path)
            project["language"] = "en-US"
            world["language"] = "en-us"
            _write(project_path, project)
            _write(world_path, world)
            self.assertEqual("source_world", inspect_world_project(root).world_id)

            world["language"] = "es"
            _write(world_path, world)
            with self.assertRaisesRegex(WorkflowError, "languages do not match"):
                inspect_world_project(root)

    def test_completed_release_gates_require_their_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            status_path = root / ".worldforge/status.json"
            baseline = _read(status_path)

            missing_canon = dict(baseline)
            missing_canon["completed_phases"] = [phase.id for phase in PHASES[:11]]
            missing_canon["current_phase"] = "p11_art_audio"
            _write(status_path, missing_canon)
            with self.assertRaisesRegex(WorkflowError, "P10 completion"):
                inspect_world_project(root)

            premature_canon = dict(baseline)
            premature_canon.update(
                {
                    "canon_locked": True,
                    "worldpack_hash": "a" * 64,
                    "worldpack_path": "build/world.worldpack.json",
                }
            )
            _write(status_path, premature_canon)
            with self.assertRaisesRegex(WorkflowError, "P10 completion"):
                inspect_world_project(root)

            missing_assets = dict(baseline)
            missing_assets.update(
                {
                    "completed_phases": [phase.id for phase in PHASES[:14]],
                    "current_phase": "p14_handoff",
                    "canon_locked": True,
                    "worldpack_hash": "a" * 64,
                    "worldpack_path": "build/world.worldpack.json",
                }
            )
            _write(status_path, missing_assets)
            with self.assertRaisesRegex(WorkflowError, "P13 completion"):
                inspect_world_project(root)

    def test_rejects_non_prefix_or_incoherent_workflow_progress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            status_path = root / ".worldforge/status.json"
            baseline = _read(status_path)
            invalid_progress = (
                ([{}], "p00_brief", "invalid completed phases"),
                ([], {}, "invalid current phase"),
                (["p00_brief", "p00_brief"], "p02_world_laws", "unique ordered prefix"),
                (["p01_genre_style"], "p02_world_laws", "unique ordered prefix"),
                (
                    ["p01_genre_style", "p00_brief"],
                    "p02_world_laws",
                    "unique ordered prefix",
                ),
                ([], "p01_genre_style", "must follow"),
                ([], None, "must follow"),
                ([phase.id for phase in PHASES], "p14_handoff", "must follow"),
            )
            for completed, current, message in invalid_progress:
                status = dict(baseline)
                status["completed_phases"] = completed
                status["current_phase"] = current
                _write(status_path, status)
                with (
                    self.subTest(completed=completed, current=current),
                    self.assertRaisesRegex(
                        WorkflowError,
                        message,
                    ),
                ):
                    inspect_world_project(root)

    def test_v2_status_world_version_must_match_project_and_world(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            status_path = root / ".worldforge/status.json"
            baseline = _read(status_path)
            for value in (None, "1.2.2", 123, True):
                status = dict(baseline)
                if value is None:
                    status.pop("world_version")
                else:
                    status["world_version"] = value
                _write(status_path, status)
                with (
                    self.subTest(value=value),
                    self.assertRaisesRegex(
                        WorkflowError,
                        "versions do not match",
                    ),
                ):
                    inspect_world_project(root)

    def test_v2_project_control_enforces_closed_offline_authoring_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            project_path = root / ".worldforge/project.json"
            baseline = _read(project_path)
            invalid: list[tuple[str, dict[str, object]]] = []

            payload = deepcopy(baseline)
            payload["unexpected"] = True
            invalid.append(("unknown top-level field", payload))
            payload = deepcopy(baseline)
            payload.pop("lead_agent")
            invalid.append(("missing required field", payload))
            for label, field, value in (
                ("runtime AI enabled", "runtime_ai", True),
                ("wrong tool repository", "tool_repository", "another-forge"),
                ("empty lead", "lead_agent", "  "),
                ("empty approval mode", "approval_mode", ""),
                ("invalid language", "language", "not_a_language"),
                ("null language", "language", None),
            ):
                payload = deepcopy(baseline)
                payload[field] = value
                invalid.append((label, payload))

            for label, generation in (
                (
                    "asset extra field",
                    {
                        **baseline["asset_generation"],
                        "provider_token": "forbidden",
                    },
                ),
                (
                    "asset missing field",
                    {"enabled_routes": ["openai"], "local_model_route": "modly"},
                ),
                (
                    "empty routes",
                    {
                        "enabled_routes": [],
                        "local_model_route": "modly",
                        "runtime_inference": False,
                    },
                ),
                (
                    "duplicate routes",
                    {
                        "enabled_routes": ["openai", "openai"],
                        "local_model_route": "modly",
                        "runtime_inference": False,
                    },
                ),
                (
                    "unknown route",
                    {
                        "enabled_routes": ["direct-local"],
                        "local_model_route": "modly",
                        "runtime_inference": False,
                    },
                ),
                (
                    "wrong local route",
                    {
                        "enabled_routes": ["openai"],
                        "local_model_route": "direct-local",
                        "runtime_inference": False,
                    },
                ),
                (
                    "runtime inference enabled",
                    {
                        "enabled_routes": ["openai"],
                        "local_model_route": "modly",
                        "runtime_inference": True,
                    },
                ),
            ):
                payload = deepcopy(baseline)
                payload["asset_generation"] = generation
                invalid.append((label, payload))

            for label, lineage in (
                ("null lineage", None),
                (
                    "lineage extra field",
                    {
                        "world_id": "ancestor_world",
                        "world_version": "1.0.0",
                        "world_content_hash": None,
                        "path": "../ancestor",
                    },
                ),
                (
                    "lineage missing field",
                    {"world_id": "ancestor_world", "world_version": "1.0.0"},
                ),
                (
                    "lineage bad ID",
                    {
                        "world_id": "Ancestor World",
                        "world_version": "1.0.0",
                        "world_content_hash": None,
                    },
                ),
                (
                    "lineage bad version",
                    {
                        "world_id": "ancestor_world",
                        "world_version": "1.0",
                        "world_content_hash": None,
                    },
                ),
                (
                    "lineage bad hash",
                    {
                        "world_id": "ancestor_world",
                        "world_version": "1.0.0",
                        "world_content_hash": "not-a-hash",
                    },
                ),
            ):
                payload = deepcopy(baseline)
                payload["derived_from"] = lineage
                invalid.append((label, payload))

            for label, payload in invalid:
                _write(project_path, payload)
                with self.subTest(label=label), self.assertRaises(WorkflowError):
                    inspect_world_project(root)

            without_optional_language = deepcopy(baseline)
            without_optional_language.pop("language")
            _write(project_path, without_optional_language)
            self.assertEqual("source_world", inspect_world_project(root).world_id)


class LegacyUpgradeTests(unittest.TestCase):
    def test_explicit_upgrade_assigns_version_kind_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "legacy"
            create_world_project(
                root,
                world_id="legacy_world",
                title="Legacy World",
                language="en",
            )
            project = _read(root / ".worldforge/project.json")
            project["format_version"] = 1
            project.pop("project_kind", None)
            project.pop("world_version", None)
            _write(root / ".worldforge/project.json", project)
            world = _read(root / "source/world.json")
            world.pop("version", None)
            _write(root / "source/world.json", world)
            status = _read(root / ".worldforge/status.json")
            status.pop("world_version", None)
            _write(root / ".worldforge/status.json", status)
            lock_path = root / ".worldforge/lifecycle.lock"
            lock_path.write_text("owned by another lifecycle operation\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "already in progress"):
                upgrade_legacy_world_project(
                    root,
                    version="0.1.0",
                    reason="Must not race",
                    approved_by="gpt-lead",
                )
            self.assertEqual(
                "owned by another lifecycle operation\n",
                lock_path.read_text(encoding="utf-8"),
            )
            lock_path.unlink()
            result = upgrade_legacy_world_project(
                root,
                version="0.1.0",
                reason="Adopt the M4 world repository contract",
                approved_by="gpt-lead",
            )
            self.assertEqual("0.1.0", result.world_version)
            upgraded = _read(root / ".worldforge/project.json")
            self.assertEqual(2, upgraded["format_version"])
            self.assertEqual("world", upgraded["project_kind"])
            entry = _read(root / ".worldforge/version_log.json")["entries"][0]
            self.assertIsNone(entry["from"])
            self.assertEqual("legacy_upgrade", entry["part"])
            self.assertFalse(lock_path.exists())

    def test_explicit_upgrade_never_accepts_a_game_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "game"
            create_world_project(
                root,
                world_id="fake_game",
                title="Fake Game",
                language="en",
            )
            project = _read(root / ".worldforge/project.json")
            project["project_kind"] = "game"
            _write(root / ".worldforge/project.json", project)
            with self.assertRaisesRegex(WorkflowError, "game repository"):
                upgrade_legacy_world_project(
                    root,
                    version="0.1.0",
                    reason="Must fail",
                    approved_by="lead",
                )


class CloneWorldProjectTests(unittest.TestCase):
    def test_clone_preserves_canon_and_allowlisted_asset_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source_world"
            target = root / "derived_world"
            _make_v2_world(source)
            status_path = source / ".worldforge/status.json"
            status = _read(status_path)
            status.update(
                {
                    "current_phase": "p14_handoff",
                    "completed_phases": [phase.id for phase in PHASES[:14]],
                    "revision": 14,
                    "canon_locked": True,
                    "worldpack_hash": "a" * 64,
                    "worldpack_path": "build/source.worldpack.json",
                    "asset_manifest": "assets/manifest.json",
                    "renderpack": "build/runtime/renderpack.json",
                }
            )
            _write(status_path, status)
            for relative in (
                "source/secrets/diegetic_truth.json",
                "source/build/city.json",
                "source/generated_prophecy/ending.md",
                "assets/specs/hero.json",
                "assets/references/style.png",
                "assets/recipes/sprite.json",
                "assets/qa/hero.md",
                "assets/licenses/hero.json",
            ):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            _write(
                source / "assets/manifest.json",
                {
                    "format": "rpg-world-forge.asset_source_manifest",
                    "format_version": 1,
                },
            )
            for relative in (
                "build/old.json",
                "dist/release.rwfworld",
                "assets/generated/candidate.png",
                "assets/processed/hero.png",
                "assets/candidates/hero.png",
                "secrets/token.txt",
                ".git/config",
                ".worldforge/claims/active.json",
                ".worldforge/phase_reports/p00.json",
            ):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("excluded", encoding="utf-8")
            manifest = clone_world_project(
                source,
                target,
                world_id="derived_world",
                title="Derived World",
                version="0.4.0",
            )
            self.assertEqual((target / "source/manifest.json").resolve(strict=True), manifest)
            for relative in (
                "source/secrets/diegetic_truth.json",
                "source/build/city.json",
                "source/generated_prophecy/ending.md",
                "assets/references/style.png",
                "assets/recipes/sprite.json",
                "assets/licenses/hero.json",
                "assets/manifest.json",
            ):
                self.assertTrue((target / relative).is_file(), relative)
            for relative in (
                ".git",
                "build",
                "dist",
                "assets/generated",
                "assets/processed",
                "assets/candidates",
                "assets/specs",
                "assets/qa",
                "secrets",
                ".worldforge/claims",
                ".worldforge/phase_reports",
            ):
                self.assertFalse((target / relative).exists(), relative)
            project = _read(target / ".worldforge/project.json")
            cloned_status = _read(target / ".worldforge/status.json")
            self.assertEqual("0.4.0", project["world_version"])
            self.assertEqual("a" * 64, project["derived_from"]["world_content_hash"])
            self.assertEqual("p00_brief", cloned_status["current_phase"])
            self.assertEqual([], cloned_status["completed_phases"])
            readme = (target / "README.md").read_text(encoding="utf-8")
            agents = (target / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("world-authoring repository", readme)
            self.assertIn("not a game repository", readme)
            self.assertNotIn("Game project created", readme)
            self.assertIn("not a game repository", agents)

    def test_bound_asset_manifest_is_not_inherited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            target = Path(directory) / "target"
            _make_v2_world(source)
            _write(
                source / "assets/manifest.json",
                {
                    "format": "rpg-world-forge.asset_manifest",
                    "world_id": "source_world",
                    "world_content_hash": "a" * 64,
                },
            )
            clone_world_project(
                source,
                target,
                world_id="target_world",
                title="Target World",
            )
            self.assertFalse((target / "assets/manifest.json").exists())

    def test_clone_rejects_game_nested_existing_and_same_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            _make_v2_world(source)
            for target, world_id, title in (
                (source / "nested", "nested_world", "Nested"),
                (source, "new_world", "Existing"),
                (root / "same", "source_world", "Same"),
            ):
                with self.subTest(target=target), self.assertRaises(ScaffoldError):
                    clone_world_project(source, target, world_id=world_id, title=title)
            reserved = root / "reserved"
            with self.assertRaisesRegex(ScaffoldError, "portable"):
                clone_world_project(
                    source,
                    reserved,
                    world_id="con",
                    title="Reserved",
                )
            self.assertFalse(reserved.exists())
            project_path = source / ".worldforge/project.json"
            project = _read(project_path)
            project["project_kind"] = "game"
            _write(project_path, project)
            with self.assertRaisesRegex(ScaffoldError, "game repository"):
                clone_world_project(
                    source,
                    root / "from_game",
                    world_id="other_world",
                    title="Other",
                )

    def test_clone_rejects_forge_and_unrelated_world_ancestor_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            enclosing_world = root / "enclosing_world"
            _make_v2_world(source)
            _make_v2_world(enclosing_world, world_id="enclosing_world")

            nested_target = enclosing_world / "derived"
            with self.assertRaisesRegex(ScaffoldError, "nested inside a world repository"):
                clone_world_project(
                    source,
                    nested_target,
                    world_id="nested_derived",
                    title="Nested Derived",
                )
            self.assertFalse(nested_target.exists())

            forge_root = Path(__file__).resolve().parents[1]
            forge_target = forge_root / ".m4_clone_must_not_be_created"
            self.assertFalse(forge_target.exists())
            with self.assertRaisesRegex(ScaffoldError, "outside the Forge repository"):
                clone_world_project(
                    source,
                    forge_target,
                    world_id="forge_derived",
                    title="Forge Derived",
                )
            self.assertFalse(forge_target.exists())

    def test_clone_rejects_a_target_inside_a_standalone_game(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            game = root / "game"
            _make_v2_world(source)
            for relative in (
                "runtime.lock.json",
                "platform.lock.json",
                "game_data/worlds.lock.json",
            ):
                _write(game / relative, {})
            (game / "src/game").mkdir(parents=True)
            (game / "src/isoworld").mkdir(parents=True)

            target = game / "authored_world"
            with self.assertRaisesRegex(ScaffoldError, "nested inside a game repository"):
                clone_world_project(
                    source,
                    target,
                    world_id="mixed_world",
                    title="Mixed World",
                )
            self.assertFalse(target.exists())

    def test_source_and_clone_both_compile_from_relative_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            source_manifest = create_world_project(
                source,
                world_id="source_world",
                title="Source World",
                language="en",
                actor_id="first_actor",
                actor_name="First Actor",
            )
            source_pack = compile_project(source_manifest, root / "source.worldpack.json")
            cloned_manifest = clone_world_project(
                source,
                target,
                world_id="derived_world",
                title="Derived World",
                version="0.4.0",
            )
            cloned_pack = compile_project(cloned_manifest, root / "derived.worldpack.json")

            self.assertEqual("source_world", source_pack["world"]["id"])
            self.assertEqual("derived_world", cloned_pack["world"]["id"])
            self.assertEqual("0.4.0", cloned_pack["world"]["version"])
            self.assertEqual(source_pack["collections"], cloned_pack["collections"])

    def test_clone_holds_the_source_lifecycle_lock_for_its_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            _make_v2_world(source)
            entered_copy = threading.Event()
            release_copy = threading.Event()
            failures: list[BaseException] = []
            real_copy = lifecycle._copy_tree_strict

            def blocking_copy(copy_source: Path, destination: Path) -> None:
                entered_copy.set()
                if not release_copy.wait(timeout=5):
                    raise TimeoutError("test did not release clone copy")
                real_copy(copy_source, destination)

            def clone_snapshot() -> None:
                try:
                    clone_world_project(
                        source,
                        target,
                        world_id="target_world",
                        title="Target World",
                    )
                except BaseException as exc:  # pragma: no cover - asserted below
                    failures.append(exc)

            with patch(
                "worldforge.world_lifecycle._copy_tree_strict",
                side_effect=blocking_copy,
            ):
                worker = threading.Thread(target=clone_snapshot)
                worker.start()
                self.assertTrue(entered_copy.wait(timeout=5))
                try:
                    with self.assertRaisesRegex(WorkflowError, "already in progress"):
                        bump_world_version(
                            source,
                            expected_version="1.2.3",
                            part="patch",
                            reason="Must not change clone snapshot",
                            approved_by="lead",
                        )
                    with self.assertRaisesRegex(WorkflowError, "already in progress"):
                        inspect_world_project(source)
                finally:
                    release_copy.set()
                worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertEqual([], failures)
            self.assertTrue((target / "source/manifest.json").is_file())
            self.assertEqual("1.2.3", inspect_world_project(source).world_version)

    def test_clone_rejects_symlinks_and_credentials_without_partial_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            _make_v2_world(source)
            external = root / "outside.txt"
            external.write_text("outside", encoding="utf-8")
            link = source / "source/canon/outside.txt"
            try:
                os.symlink(external, link)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are not available")
            target = root / "linked"
            with self.assertRaises(ScaffoldError):
                clone_world_project(
                    source,
                    target,
                    world_id="linked_world",
                    title="Linked",
                )
            self.assertFalse(target.exists())
            link.unlink()
            credential = source / "source/canon/.env"
            credential.write_text("TOKEN=secret", encoding="utf-8")
            with self.assertRaisesRegex(ScaffoldError, "credential-like"):
                clone_world_project(
                    source,
                    root / "credentialed",
                    world_id="credentialed_world",
                    title="Credentialed",
                )
            self.assertFalse((root / "credentialed").exists())

    def test_clone_rejects_vcs_controls_at_any_source_depth(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = (
                ("git_directory", ".git", True),
                ("git_file", ".git", False),
                ("hg_directory", ".hg", True),
                ("svn_directory", ".svn", True),
            )
            for case, control_name, is_directory in cases:
                source = root / f"source_{case}"
                _make_v2_world(source, world_id=f"source_{case}")
                nested = source / "source/canon/vendor"
                nested.mkdir(parents=True)
                control = nested / control_name
                if is_directory:
                    control.mkdir()
                    (control / "config").write_text("metadata\n", encoding="utf-8")
                else:
                    control.write_text("gitdir: elsewhere\n", encoding="utf-8")
                target = root / f"target_{case}"
                with (
                    self.subTest(case=case),
                    self.assertRaisesRegex(
                        ScaffoldError,
                        "VCS control entry",
                    ),
                ):
                    clone_world_project(
                        source,
                        target,
                        world_id=f"derived_{case}",
                        title=f"Derived {case}",
                    )
                self.assertFalse(target.exists())


class BumpWorldVersionTests(unittest.TestCase):
    def test_bump_checks_expected_version_logs_and_invalidates_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            status_path = root / ".worldforge/status.json"
            status = _read(status_path)
            status.update(
                {
                    "current_phase": "p14_handoff",
                    "completed_phases": [phase.id for phase in PHASES[:14]],
                    "revision": 14,
                    "canon_locked": True,
                    "worldpack_hash": "b" * 64,
                    "worldpack_path": "build/world.worldpack.json",
                    "asset_manifest": "assets/manifest.json",
                    "renderpack": "build/runtime/renderpack.json",
                    "compatibility_report": "build/compatibility.json",
                    "release_hash": "c" * 64,
                    "release_package": "dist/world.rwfworld",
                }
            )
            _write(status_path, status)
            result = bump_world_version(
                root,
                expected_version="1.2.3",
                part="minor",
                reason="Add localized campaigns",
                approved_by="gpt-lead",
            )
            self.assertEqual("1.3.0", result)
            self.assertFalse((root / ".worldforge/lifecycle.lock").exists())
            updated_status = _read(status_path)
            self.assertEqual("p10_canon_lock", updated_status["current_phase"])
            self.assertEqual(
                [phase.id for phase in PHASES[:10]], updated_status["completed_phases"]
            )
            self.assertEqual(15, updated_status["revision"])
            for field in (
                "worldpack_hash",
                "worldpack_path",
                "asset_manifest",
                "renderpack",
                "compatibility_report",
                "release_hash",
                "release_package",
            ):
                self.assertIsNone(updated_status[field], field)
            entry = _read(root / ".worldforge/version_log.json")["entries"][0]
            self.assertEqual("1.2.3", entry["from"])
            self.assertEqual("1.3.0", entry["to"])

    def test_bump_rejects_stale_expected_version_game_and_dirty_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            with self.assertRaisesRegex(WorkflowError, "expected world version"):
                bump_world_version(
                    root,
                    expected_version="1.2.2",
                    part="patch",
                    reason="Stale writer",
                    approved_by="lead",
                )
            status = _read(root / ".worldforge/status.json")
            status["release_package"] = "dist/world.rwfworld"
            _write(root / ".worldforge/status.json", status)
            with self.assertRaisesRegex(WorkflowError, "complete or empty"):
                bump_world_version(
                    root,
                    expected_version="1.2.3",
                    part="patch",
                    reason="Dirty metadata",
                    approved_by="lead",
                )
            status["release_package"] = None
            _write(root / ".worldforge/status.json", status)
            project = _read(root / ".worldforge/project.json")
            project["project_kind"] = "game"
            _write(root / ".worldforge/project.json", project)
            with self.assertRaisesRegex(WorkflowError, "game repository"):
                bump_world_version(
                    root,
                    expected_version="1.2.3",
                    part="patch",
                    reason="Must fail",
                    approved_by="lead",
                )

    def test_transaction_restores_all_files_after_replace_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            paths = (
                root / ".worldforge/project.json",
                root / "source/world.json",
                root / ".worldforge/status.json",
            )
            before = {path: path.read_bytes() for path in paths}
            real_replace = os.replace
            calls = 0

            def fail_second(source: Path, target: Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected transaction failure")
                real_replace(source, target)

            with patch("worldforge.world_lifecycle._replace_file", side_effect=fail_second):
                with self.assertRaises(OSError):
                    bump_world_version(
                        root,
                        expected_version="1.2.3",
                        part="patch",
                        reason="Atomicity test",
                        approved_by="lead",
                    )
            for path in paths:
                self.assertEqual(before[path], path.read_bytes(), path)
            self.assertFalse((root / ".worldforge/version_log.json").exists())
            self.assertFalse((root / ".worldforge/lifecycle.lock").exists())

    def test_bump_rejects_duplicate_keys_in_version_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            log_path = root / ".worldforge/version_log.json"
            _write(
                log_path,
                {
                    "format": "rpg-world-forge.version_log",
                    "format_version": 1,
                    "entries": [],
                },
            )
            _inject_duplicate_key(log_path, "entries")
            with self.assertRaisesRegex(WorkflowError, "duplicate JSON object key"):
                bump_world_version(
                    root,
                    expected_version="1.2.3",
                    part="patch",
                    reason="Must reject ambiguous log",
                    approved_by="lead",
                )

    def test_bump_refuses_an_existing_lock_without_removing_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            lock_path = root / ".worldforge/lifecycle.lock"
            lock_path.write_text("stale or concurrent owner\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "already in progress"):
                bump_world_version(
                    root,
                    expected_version="1.2.3",
                    part="patch",
                    reason="Must not race",
                    approved_by="lead",
                )
            self.assertEqual("stale or concurrent owner\n", lock_path.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(WorkflowError, "already in progress"):
                inspect_world_project(root)
            self.assertEqual("1.2.3", _read(root / ".worldforge/project.json")["world_version"])
            self.assertEqual("1.2.3", _read(root / "source/world.json")["version"])
            self.assertEqual(
                "1.2.3",
                _read(root / ".worldforge/status.json")["world_version"],
            )

    @unittest.skipUnless(os.name == "posix", "POSIX open-file rename semantics required")
    def test_lock_cleanup_never_unlinks_a_replacement_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            lock_path = root / ".worldforge/lifecycle.lock"
            displaced_path = root / ".worldforge/displaced-owner.lock"

            def replace_owner(_updates: dict[Path, object]) -> None:
                lock_path.rename(displaced_path)
                lock_path.write_text("replacement owner\n", encoding="utf-8")
                raise OSError("injected failure after lock replacement")

            with patch(
                "worldforge.world_lifecycle._commit_json_transaction",
                side_effect=replace_owner,
            ):
                with self.assertRaisesRegex(OSError, "lock replacement"):
                    bump_world_version(
                        root,
                        expected_version="1.2.3",
                        part="patch",
                        reason="Identity test",
                        approved_by="lead",
                    )
            self.assertEqual("replacement owner\n", lock_path.read_text(encoding="utf-8"))
            self.assertTrue(displaced_path.is_file())

    @unittest.skipUnless(os.name == "nt", "Windows open-file rename semantics required")
    def test_windows_lock_rename_denial_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            lock_path = root / ".worldforge/lifecycle.lock"
            displaced_path = root / ".worldforge/displaced-owner.lock"
            control_paths = (
                root / ".worldforge/project.json",
                root / "source/world.json",
                root / ".worldforge/status.json",
            )
            before = {path: path.read_bytes() for path in control_paths}

            def rename_open_lock(_updates: dict[Path, object]) -> None:
                lock_path.rename(displaced_path)

            with patch(
                "worldforge.world_lifecycle._commit_json_transaction",
                side_effect=rename_open_lock,
            ):
                with self.assertRaises(PermissionError) as raised:
                    bump_world_version(
                        root,
                        expected_version="1.2.3",
                        part="patch",
                        reason="Windows fail-closed test",
                        approved_by="lead",
                    )

            self.assertIn(raised.exception.winerror, {5, 32})
            self.assertFalse(lock_path.exists())
            self.assertFalse(displaced_path.exists())
            for path, payload in before.items():
                self.assertEqual(payload, path.read_bytes(), path)

    def test_concurrent_bump_cannot_pass_the_same_expected_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "world"
            _make_v2_world(root)
            entered_transaction = threading.Event()
            release_transaction = threading.Event()
            results: list[str] = []
            failures: list[BaseException] = []
            real_transaction = lifecycle._commit_json_transaction

            def blocking_transaction(updates: dict[Path, object]) -> None:
                entered_transaction.set()
                if not release_transaction.wait(timeout=5):
                    raise TimeoutError("test did not release lifecycle transaction")
                real_transaction(updates)

            def first_bump() -> None:
                try:
                    results.append(
                        bump_world_version(
                            root,
                            expected_version="1.2.3",
                            part="patch",
                            reason="First writer",
                            approved_by="lead",
                        )
                    )
                except BaseException as exc:  # pragma: no cover - asserted below
                    failures.append(exc)

            with patch(
                "worldforge.world_lifecycle._commit_json_transaction",
                side_effect=blocking_transaction,
            ):
                worker = threading.Thread(target=first_bump)
                worker.start()
                self.assertTrue(entered_transaction.wait(timeout=5))
                try:
                    with self.assertRaisesRegex(WorkflowError, "already in progress"):
                        bump_world_version(
                            root,
                            expected_version="1.2.3",
                            part="minor",
                            reason="Second writer",
                            approved_by="lead",
                        )
                finally:
                    release_transaction.set()
                worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertEqual([], failures)
            self.assertEqual(["1.2.4"], results)
            self.assertEqual("1.2.4", inspect_world_project(root).world_version)


if __name__ == "__main__":
    unittest.main()
