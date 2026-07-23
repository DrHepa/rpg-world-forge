from __future__ import annotations

import json
import re
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import worldforge.game_boundary as game_boundary_module
import worldforge.game_boundary_policy as boundary_policy_module
from worldforge.game_boundary import audit_game_repository
from worldforge.runtime_audit import audit_runtime

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src/isoworld"


class ArchitectureTests(unittest.TestCase):
    def test_runtime_has_no_ai_sdk_imports(self) -> None:
        self.assertEqual([], audit_runtime(RUNTIME))

    def test_runtime_audit_catches_current_and_dynamic_provider_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "adapter.py").write_text(
                "import importlib\n"
                "from google import genai\n"
                'importlib.import_module("openai")\n'
                '__import__("modly")\n',
                encoding="utf-8",
            )

            findings = audit_runtime(root)

            self.assertEqual(
                ["google.genai", "modly", "openai"],
                sorted(finding.module for finding in findings),
            )

    def test_runtime_does_not_import_authoring_tools(self) -> None:
        offenders: list[str] = []
        for path in RUNTIME.rglob("*.py"):
            if "worldforge" in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual([], offenders)

    def test_runtime_does_not_access_project_source_directories(self) -> None:
        runtime_text = "\n".join(path.read_text(encoding="utf-8") for path in RUNTIME.rglob("*.py"))
        self.assertNotIn("projects/", runtime_text)
        self.assertNotIn("examples/", runtime_text)

    def test_public_json_schemas_are_valid_json_objects(self) -> None:
        for path in sorted((ROOT / "schemas").glob("*.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(value, dict, path.name)
            self.assertIn("$schema", value, path.name)

    def test_public_world_schemas_use_the_portable_id_contract(self) -> None:
        schemas = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in (ROOT / "schemas").glob("*.json")
        }
        patterns = {
            "world project": schemas["world-project.schema.json"]["$defs"]["id"]["pattern"],
            "runtime bundle": schemas["runtime-bundle.schema.json"]["$defs"]["world_id"]["pattern"],
            "world catalog": schemas["world-catalog.schema.json"]["$defs"]["world_id"]["pattern"],
            "worldpack": schemas["worldpack.schema.json"]["properties"]["world"]["properties"][
                "id"
            ]["pattern"],
        }
        reserved_names = (
            "aux",
            "con",
            "nul",
            "prn",
            "com1",
            "com9",
            "lpt1",
            "lpt9",
        )
        for context, pattern in patterns.items():
            identifier = re.compile(pattern)
            with self.subTest(context=context):
                self.assertIsNotNone(identifier.fullmatch("portable_world"))
            for reserved in reserved_names:
                with self.subTest(context=context, reserved=reserved):
                    self.assertIsNone(identifier.fullmatch(reserved))
        catalog_path = re.compile(
            schemas["world-catalog.schema.json"]["$defs"]["release"]["properties"]["path"][
                "pattern"
            ]
        )
        self.assertIsNotNone(catalog_path.fullmatch("game_data/worlds/portable_world/1.0.0"))
        self.assertIsNone(catalog_path.fullmatch("game_data/worlds/con/1.0.0"))

    def test_public_localization_schemas_share_the_bcp47_contract(self) -> None:
        project = json.loads(
            (ROOT / "schemas/world-project.schema.json").read_text(encoding="utf-8")
        )
        worldpack = json.loads((ROOT / "schemas/worldpack.schema.json").read_text(encoding="utf-8"))
        narrative = json.loads(
            (ROOT / "schemas/narrative-content.schema.json").read_text(encoding="utf-8")
        )
        patterns = (
            project["properties"]["language"]["pattern"],
            worldpack["$defs"]["language_tag"]["pattern"],
            narrative["$defs"]["locale"]["properties"]["language_tag"]["pattern"],
        )
        for pattern in patterns:
            language_tag = re.compile(pattern)
            self.assertIsNotNone(language_tag.fullmatch("en-US"))
            self.assertIsNotNone(language_tag.fullmatch("zh-Hant-TW"))
            self.assertIsNone(language_tag.fullmatch("not_a_tag"))

    def test_clean_game_repository_has_no_authoring_control_plane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src/isoworld/core").mkdir(parents=True)
            (root / "src/isoworld/core/app.py").write_text(
                "from __future__ import annotations\n\nimport pyray as pr\n",
                encoding="utf-8",
            )
            (root / "game_data/worlds").mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "scripts/verify_game.py").write_text(
                "from __future__ import annotations\n",
                encoding="utf-8",
            )
            (root / "pyproject.toml").write_text(
                '[project]\nname = "example-game"\ndependencies = ["raylib==6.0.1.0"]\n',
                encoding="utf-8",
            )

            self.assertEqual([], audit_game_repository(root))

    def test_game_repository_root_symlink_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "game"
            root.mkdir()
            alias = parent / "game-alias"
            try:
                alias.symlink_to(root, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")

            with patch.object(
                game_boundary_module,
                "_structure_findings",
                side_effect=AssertionError("link target was traversed"),
            ) as traversal:
                findings = audit_game_repository(alias)

            traversal.assert_not_called()
            self.assertTrue(
                any(finding.detail == "FS_SYMLINK:." for finding in findings),
                findings,
            )

    def test_game_repository_linked_ancestor_is_rejected_before_target_traversal(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            actual_parent = parent / "actual"
            root = actual_parent / "game"
            root.mkdir(parents=True)
            alias = parent / "actual-alias"
            try:
                alias.symlink_to(actual_parent, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")

            with patch.object(
                game_boundary_module,
                "_structure_findings",
                side_effect=AssertionError("link target was traversed"),
            ) as traversal:
                findings = audit_game_repository(alias / "game")

            traversal.assert_not_called()
            self.assertTrue(
                any(finding.detail == "FS_SYMLINK:." for finding in findings),
                findings,
            )

    def test_game_repository_reparse_root_is_rejected_before_target_traversal(
        self,
    ) -> None:
        for directory in (True, False):
            with self.subTest(directory=directory), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "game"
                if directory:
                    root.mkdir()
                else:
                    root.write_text("not a directory\n", encoding="utf-8")
                real_stat = boundary_policy_module._non_following_stat

                def reparse_root(
                    candidate: Path,
                    expected_root: Path = root,
                    stat_file=real_stat,
                ) -> object:
                    info = stat_file(candidate)
                    if candidate == expected_root:
                        return SimpleNamespace(
                            st_mode=info.st_mode,
                            st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
                        )
                    return info

                with (
                    patch.object(
                        boundary_policy_module,
                        "_non_following_stat",
                        side_effect=reparse_root,
                    ),
                    patch.object(
                        game_boundary_module,
                        "_structure_findings",
                        side_effect=AssertionError("reparse target was traversed"),
                    ) as traversal,
                ):
                    findings = audit_game_repository(root)

                traversal.assert_not_called()
                self.assertTrue(
                    any(finding.detail == "FS_SYMLINK:." for finding in findings),
                    findings,
                )

    def test_game_repository_rejects_forge_and_world_authoring_paths(self) -> None:
        forbidden_paths = (
            "AGENTS.md",
            ".agents/skills/render/SKILL.md",
            "skills/render/SKILL.md",
            ".claude/settings.json",
            ".codex/config.toml",
            ".cursor/rules/project.mdc",
            ".worldforge/status.json",
            "authoring/prompts/quest.md",
            "source/canon/world.md",
        )
        for relative in forbidden_paths:
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("not runtime data\n", encoding="utf-8")

                findings = audit_game_repository(root)

                self.assertEqual(1, len(findings))
                self.assertEqual("forbidden_game_path", findings[0].rule)

    def test_game_repository_rejects_authoring_and_ai_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src/app.py").write_text(
                "import importlib\n"
                "import blender_mcp\n"
                "import bpy\n"
                "import openai\n"
                "import worldforge.compiler\n"
                "from google import genai\n"
                "from modly import workflow\n"
                "from modly_cli_mcp import server\n"
                'importlib.import_module("transformers")\n',
                encoding="utf-8",
            )

            findings = audit_game_repository(root)

            self.assertEqual(
                [
                    "blender_mcp",
                    "bpy",
                    "google.genai",
                    "modly",
                    "modly_cli_mcp",
                    "openai",
                    "transformers",
                    "worldforge.compiler",
                ],
                sorted(finding.detail for finding in findings),
            )
            self.assertTrue(all(finding.rule == "forbidden_game_import" for finding in findings))

    def test_game_repository_rejects_authoring_and_ai_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text(
                "[project]\n"
                'name = "example-game"\n'
                'dependencies = ["raylib==6.0.1.0", "rpg-world-forge", "openai>=1"]\n',
                encoding="utf-8",
            )

            findings = audit_game_repository(root)

            self.assertEqual(2, len(findings))
            self.assertTrue(
                all(finding.rule == "forbidden_game_dependency" for finding in findings)
            )

    def test_game_repository_rejects_mcp_npm_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "dependencies": {"modly-cli-mcp": "0.1.1"},
                        "devDependencies": {"blender-mcp": "1.6.4"},
                    }
                ),
                encoding="utf-8",
            )

            findings = audit_game_repository(root)

            self.assertEqual(
                [
                    ("package.json", "blender-mcp"),
                    ("package.json", "modly-cli-mcp"),
                ],
                sorted((str(finding.path), finding.detail) for finding in findings),
            )

    def test_game_repository_rejects_npm_aliases_to_mcp_distributions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "render-bridge": "npm:modly-cli-mcp@0.1.1",
                            "safe-runtime": "1.0.0",
                        },
                        "devDependencies": {
                            "scene-bridge": "npm:blender-mcp@^1.6.4",
                        },
                    }
                ),
                encoding="utf-8",
            )

            findings = audit_game_repository(root)

            self.assertEqual(
                [
                    ("package.json", "npm:blender-mcp@^1.6.4"),
                    ("package.json", "npm:modly-cli-mcp@0.1.1"),
                ],
                sorted((str(finding.path), finding.detail) for finding in findings),
            )

    def test_game_repository_rejects_indirect_packages_scripts_and_included_ai_requirements(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "dependencies": {
                            "render-bridge": "github:DrHepa/modly_CLI_MCP",
                        },
                        "scripts": {"author": "npx modly-cli-mcp"},
                    }
                ),
                encoding="utf-8",
            )
            (root / "requirements.txt").write_text("-r config/runtime.in\n", encoding="utf-8")
            (root / "config").mkdir()
            (root / "config/runtime.in").write_text("openai==2.0.0\n", encoding="utf-8")

            findings = audit_game_repository(root)

            self.assertEqual(
                [
                    "author: npx modly-cli-mcp",
                    "github:DrHepa/modly_CLI_MCP",
                    "openai==2.0.0",
                ],
                sorted(finding.detail for finding in findings),
            )
            self.assertEqual(
                {"forbidden_game_dependency", "forbidden_game_script"},
                {finding.rule for finding in findings},
            )

    def test_game_repository_checks_dependency_groups_and_requirements_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text(
                "[project]\n"
                'name = "example-game"\n'
                "[dependency-groups]\n"
                'dev = ["google-genai>=1"]\n',
                encoding="utf-8",
            )
            (root / "requirements-release.txt").write_text(
                "raylib==6.0.1.0\ntransformers>=4\n",
                encoding="utf-8",
            )

            findings = audit_game_repository(root)

            self.assertEqual(
                ["google-genai>=1", "transformers>=4"],
                sorted(finding.detail for finding in findings),
            )

    def test_game_repository_rejects_editable_authoring_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "requirements.txt").write_text(
                "-e git+https://example.invalid/DrHepa/modly.git#egg=runtime-bridge\n",
                encoding="utf-8",
            )

            findings = audit_game_repository(root)

            self.assertEqual(1, len(findings))
            self.assertEqual("forbidden_game_dependency", findings[0].rule)
            self.assertIn("modly.git", findings[0].detail)

    def test_game_repository_checks_requirement_and_platform_lock_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "requirements.lock").write_text(
                "raylib==6.0.1.0\nopenai==1.0.0\n",
                encoding="utf-8",
            )
            (root / "platform.lock.json").write_text(
                json.dumps({"locked_requirements": ["raylib==6.0.1.0", "torch==2.0.0"]}),
                encoding="utf-8",
            )

            findings = audit_game_repository(root)

            self.assertEqual(
                [
                    ("platform.lock.json", "torch==2.0.0"),
                    ("requirements.lock", "openai==1.0.0"),
                ],
                [(str(finding.path), finding.detail) for finding in findings],
            )

    def test_game_repository_rejects_authoring_formats_even_at_other_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "assets/manifest.json").write_text(
                '{"format": "rpg-world-forge.asset_manifest", "format_version": 2}\n',
                encoding="utf-8",
            )

            findings = audit_game_repository(root)

            self.assertEqual(1, len(findings))
            self.assertEqual("forbidden_authoring_format", findings[0].rule)

    def test_game_repository_rejects_blender_authoring_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets/models").mkdir(parents=True)
            (root / "assets/models/hero.BLEND").write_bytes(b"BLENDER")

            findings = audit_game_repository(root)

            self.assertEqual(1, len(findings))
            self.assertEqual("assets/models/hero.BLEND", findings[0].path.as_posix())
            self.assertEqual("forbidden_game_path", findings[0].rule)

    def test_game_repository_rejects_authoring_metadata_in_runtime_json(self) -> None:
        documents = {
            "provider.json": {"render": {"provider": "openai"}},
            "bridge.json": {"transport": "mcp://blender"},
            "weights.json": {"asset": "models/weights/hero.bin"},
            "workflow.json": {"input": "workflows/hero.json"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "game_data/config"
            data.mkdir(parents=True)
            for name, document in documents.items():
                (data / name).write_text(json.dumps(document), encoding="utf-8")

            findings = audit_game_repository(root)

            self.assertEqual(sorted(documents), sorted(finding.path.name for finding in findings))
            self.assertTrue(
                all(finding.rule == "forbidden_authoring_metadata" for finding in findings)
            )

    def test_game_repository_rejects_provider_and_secret_json_values(self) -> None:
        documents = {
            "engine.json": {"engine": "openai"},
            "tool.json": {"tool": "modly_cli_mcp"},
            "token.json": {"result": "Bearer abcdefghijklmnop"},
            "aws.json": {"result": "AKIAABCDEFGHIJKLMNOP"},
            "private.json": {
                "result": "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
            },
        }
        safe_documents = {
            "catalog.json": {
                "path": "game_data/worlds/modly_foundation/1.0.0",
                "world_id": "modly_foundation",
            },
            "narrative.json": {"secrets": ["the sealed archive"]},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.mkdir(exist_ok=True)
            for name, document in {**documents, **safe_documents}.items():
                (root / name).write_text(json.dumps(document), encoding="utf-8")

            findings = audit_game_repository(root)

            self.assertEqual(sorted(documents), sorted(finding.path.name for finding in findings))


if __name__ == "__main__":
    unittest.main()
