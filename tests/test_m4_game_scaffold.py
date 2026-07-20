from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
import wave
import zipfile
from pathlib import Path
from unittest.mock import patch

from isoworld import __version__ as ISOWORLD_VERSION
from isoworld.content.loader import load_worldpack
from isoworld.content.models import RUNTIME_API_VERSION, SUPPORTED_RUNTIME_FEATURES
from worldforge.bundle import (
    BundleError,
    export_runtime_bundle,
    import_runtime_bundle,
    verify_game_catalog_compatibility,
)
from worldforge.game_boundary import audit_game_repository
from worldforge.game_scaffold import (
    GameScaffoldError,
    create_game_project,
    update_game_runtime_snapshot,
)
from worldforge.integrity import canonical_payload_hash
from worldforge.scaffold import ScaffoldError, create_world_project

ROOT = Path(__file__).resolve().parents[1]
COMPILED = ROOT / "content/compiled/foundation.worldpack.json"


def _environment(game: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(game / "src")
    return environment


def _run_game_script(game: Path, *arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=cwd,
        env=_environment(game),
        capture_output=True,
        text=True,
        check=False,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture(
    root: Path,
    world_id: str = "modly_foundation",
) -> tuple[Path, Path, Path]:
    source = root / "source"
    source.mkdir(parents=True)
    worldpack_raw = json.loads(COMPILED.read_text(encoding="utf-8"))
    worldpack_raw["world"]["id"] = world_id
    worldpack_raw["content_hash"] = canonical_payload_hash(worldpack_raw)
    worldpack = source / "worldpack.json"
    worldpack.write_text(
        json.dumps(worldpack_raw, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pack = load_worldpack(worldpack)
    audio = source / "mutable/audio.wav"
    audio.parent.mkdir()
    with wave.open(str(audio), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(22050)
        target.writeframes(b"\x00\x00" * 64)
    renderpack_raw = {
        "format": "isoworld.renderpack",
        "format_version": 1,
        "world_id": pack.world_id,
        "world_content_hash": pack.content_hash,
        "assets": [
            {
                "id": "neutral_sfx",
                "kind": "sfx",
                "files": [
                    {
                        "role": "audio",
                        "path": "mutable/audio.wav",
                        "sha256": _sha256(audio),
                        "media_type": "audio/wav",
                    }
                ],
            }
        ],
        "bindings": [],
    }
    renderpack_raw["content_hash"] = canonical_payload_hash(renderpack_raw)
    renderpack = source / "renderpack.json"
    renderpack.write_text(json.dumps(renderpack_raw), encoding="utf-8")
    licenses = root / "licenses"
    licenses.mkdir()
    (licenses / "CONTENT-LICENSE.txt").write_text("Fixture: CC0-1.0\n", encoding="utf-8")
    return worldpack, renderpack, licenses


class GameScaffoldTests(unittest.TestCase):
    def test_materialized_game_is_clean_locked_and_cwd_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "standalone"
            create_game_project(
                game,
                game_id="standalone_game",
                title="Standalone Game",
                source_revision="m4-test",
            )
            self.assertEqual([], audit_game_repository(game))
            for forbidden in ("AGENTS.md", ".agents", ".worldforge", "source"):
                self.assertFalse((game / forbidden).exists())
            with (game / "pyproject.toml").open("rb") as source:
                project = tomllib.load(source)
            self.assertEqual(["raylib==6.0.1.0"], project["project"]["dependencies"])
            self.assertEqual(">=3.11,<3.13", project["project"]["requires-python"])
            platform = json.loads((game / "platform.lock.json").read_text(encoding="utf-8"))
            self.assertEqual("isoworld.pyray_platform", platform["format"])
            self.assertEqual("standard", platform["backend"])
            self.assertEqual(project["project"]["requires-python"], platform["python"])
            locked_requirements = (game / "requirements.lock").read_text(encoding="utf-8")
            for requirement in platform["locked_requirements"]:
                self.assertIn(f"{requirement}\n", locked_requirements)
            notices = (game / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
            for requirement in platform["locked_requirements"]:
                self.assertIn(requirement, notices)
            self.assertIn(
                "--requirement requirements.lock",
                (game / ".github/workflows/ci.yml").read_text(encoding="utf-8"),
            )
            runtime = json.loads((game / "runtime.lock.json").read_text(encoding="utf-8"))
            self.assertEqual(ISOWORLD_VERSION, runtime["runtime_version"])
            self.assertEqual(RUNTIME_API_VERSION, runtime["runtime_api_version"])
            self.assertEqual(
                sorted(SUPPORTED_RUNTIME_FEATURES),
                runtime["supported_runtime_features"],
            )

            ci = (game / ".github/workflows/ci.yml").read_text(encoding="utf-8")
            self.assertIn("ruff check src tests scripts run_game.py", ci)
            self.assertIn("ruff format --check src tests scripts run_game.py", ci)
            native_matrix = ci.split("  native-smoke:\n", 1)[1]
            self.assertIn("        profile:\n", native_matrix)
            self.assertIn("matrix.profile.width", native_matrix)

            outside = root / "outside"
            outside.mkdir()
            verified = _run_game_script(
                game,
                str(game / "scripts/verify_game.py"),
                cwd=outside,
            )
            self.assertEqual(0, verified.returncode, verified.stdout + verified.stderr)
            tests = _run_game_script(
                game,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(game / "tests"),
                cwd=outside,
            )
            self.assertEqual(0, tests.returncode, tests.stdout + tests.stderr)
            empty_start = _run_game_script(
                game,
                "-m",
                "game",
                "--headless-ticks",
                "0",
                cwd=outside,
            )
            self.assertEqual(
                0,
                empty_start.returncode,
                empty_start.stdout + empty_start.stderr,
            )
            self.assertIn("status=empty_catalog shell=ready", empty_start.stdout)

    def test_shared_assets_are_notice_bound_verified_and_packaged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            outside = root / "outside"
            outside.mkdir()
            create_game_project(game, game_id="shared_game", title="Shared Game")
            shared = game / "game_data/shared/shaders/glsl330"
            shared.mkdir(parents=True)
            shader = shared / "palette.fs"
            shader.write_text(
                "#version 330\nout vec4 finalColor;\nvoid main() { finalColor = vec4(1.0); }\n",
                encoding="utf-8",
            )
            notices = game / "THIRD_PARTY_NOTICES.md"
            notices.write_text(
                notices.read_text(encoding="utf-8")
                + "\n- `palette.fs`: original test fixture, MIT, Shared Game authors.\n",
                encoding="utf-8",
            )
            fixture = game / "tests/fixtures/replay.json"
            fixture.parent.mkdir(parents=True)
            fixture.write_text(
                json.dumps({"actions": [], "format": "game.replay_fixture"}) + "\n",
                encoding="utf-8",
            )
            extra_test = game / "tests/helpers/test_extra.py"
            extra_test.parent.mkdir(parents=True)
            extra_test.write_text(
                "import unittest\n\n\nclass ExtraTest(unittest.TestCase):\n"
                "    def test_fixture_exists(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )

            locked = _run_game_script(
                game,
                str(game / "scripts/lock_shared_assets.py"),
                cwd=outside,
            )
            self.assertEqual(0, locked.returncode, locked.stdout + locked.stderr)
            lock_path = game / "game_data/shared.lock.json"
            lock_bytes = lock_path.read_bytes()
            lock = json.loads(lock_bytes)
            self.assertEqual("isoworld.shared_assets", lock["format"])
            self.assertEqual(
                _sha256(game / "THIRD_PARTY_NOTICES.md"),
                lock["notices_sha256"],
            )
            self.assertEqual(
                [
                    {
                        "media_type": "text/x-glsl",
                        "path": "game_data/shared/shaders/glsl330/palette.fs",
                        "sha256": _sha256(shader),
                        "size": shader.stat().st_size,
                    }
                ],
                lock["files"],
            )
            verified = _run_game_script(
                game,
                str(game / "scripts/verify_game.py"),
                cwd=outside,
            )
            self.assertEqual(0, verified.returncode, verified.stdout + verified.stderr)
            verify_game_catalog_compatibility(
                game,
                RUNTIME_API_VERSION,
                SUPPORTED_RUNTIME_FEATURES,
            )

            package = root / "shared-game.zip"
            packaged = _run_game_script(
                game,
                str(game / "scripts/package_game.py"),
                "--output",
                str(package),
                cwd=outside,
            )
            self.assertEqual(0, packaged.returncode, packaged.stdout + packaged.stderr)
            with zipfile.ZipFile(package) as archive:
                names = set(archive.namelist())
                self.assertIn("game_data/shared.lock.json", names)
                self.assertIn("game_data/shared/shaders/glsl330/palette.fs", names)
                self.assertIn("tests/fixtures/replay.json", names)
                self.assertIn("tests/helpers/test_extra.py", names)
                manifest = json.loads(archive.read("PACKAGE-MANIFEST.json"))
            self.assertEqual(_sha256(lock_path), manifest["shared_assets_lock_hash"])

            provider_fixture = game / "tests/fixtures/provider.json"
            provider_fixture.write_text(
                json.dumps({"provider": "must-not-ship"}) + "\n",
                encoding="utf-8",
            )
            rejected_package = root / "provider-fixture.zip"
            rejected = _run_game_script(
                game,
                str(game / "scripts/package_game.py"),
                "--output",
                str(rejected_package),
                cwd=outside,
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("authoring/provider metadata", rejected.stderr)
            self.assertFalse(rejected_package.exists())
            provider_fixture.unlink()

            mismatch = json.loads(lock_bytes)
            mismatch["files"][0]["media_type"] = "audio/wav"
            mismatch["content_hash"] = canonical_payload_hash(mismatch)
            lock_path.write_text(
                json.dumps(mismatch, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            mismatched = _run_game_script(
                game,
                str(game / "scripts/verify_game.py"),
                cwd=outside,
            )
            self.assertNotEqual(0, mismatched.returncode)
            self.assertIn("extension and media_type disagree", mismatched.stderr)
            with self.assertRaisesRegex(BundleError, "extension and media_type disagree"):
                verify_game_catalog_compatibility(
                    game,
                    RUNTIME_API_VERSION,
                    SUPPORTED_RUNTIME_FEATURES,
                )
            lock_path.write_bytes(lock_bytes)

            notices.write_text(
                notices.read_text(encoding="utf-8") + "\nUnreviewed change.\n",
                encoding="utf-8",
            )
            notice_mismatch = _run_game_script(
                game,
                str(game / "scripts/verify_game.py"),
                cwd=outside,
            )
            self.assertNotEqual(0, notice_mismatch.returncode)
            self.assertIn("THIRD_PARTY_NOTICES.md", notice_mismatch.stderr)

    def test_shared_asset_lock_rejects_symlinked_game_data_without_writing(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            outside_data = root / "outside-data"
            create_game_project(game, game_id="linked_game", title="Linked Game")
            outside_data.mkdir()
            original_lock = (game / "game_data/shared.lock.json").read_bytes()
            (game / "game_data/shared.lock.json").replace(outside_data / "shared.lock.json")
            (game / "game_data/worlds.lock.json").replace(outside_data / "worlds.lock.json")
            (game / "game_data").rmdir()
            try:
                os.symlink(outside_data, game / "game_data", target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symbolic links are unavailable: {exc}")
            result = _run_game_script(
                game,
                str(game / "scripts/lock_shared_assets.py"),
                cwd=root,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("game data root must be a real directory", result.stderr)
            self.assertEqual(original_lock, (outside_data / "shared.lock.json").read_bytes())
            self.assertEqual([], list(outside_data.glob(".shared.lock.json.*")))

    def test_scaffold_rejects_mixed_or_ambiguous_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(GameScaffoldError):
                create_game_project(root / "bad", game_id="Bad-ID", title="Bad")
            with self.assertRaisesRegex(GameScaffoldError, "portable"):
                create_game_project(root / "reserved", game_id="con", title="Reserved")
            with self.assertRaises(ScaffoldError):
                create_world_project(
                    root / "reserved-world",
                    world_id="nul",
                    title="Reserved World",
                    language="en",
                )
            existing = root / "existing"
            existing.mkdir()
            with self.assertRaises(GameScaffoldError):
                create_game_project(existing, game_id="valid_game", title="Existing")
            world = root / "world"
            create_world_project(
                world,
                world_id="outer_world",
                title="Outer World",
                language="en",
            )
            with self.assertRaisesRegex(GameScaffoldError, "world repository"):
                create_game_project(
                    world / "game",
                    game_id="nested_game",
                    title="Nested Game",
                )
            outer_game = root / "outer-game"
            create_game_project(
                outer_game,
                game_id="outer_game",
                title="Outer Game",
            )
            with self.assertRaisesRegex(GameScaffoldError, "game repository"):
                create_game_project(
                    outer_game / "nested-game",
                    game_id="nested_game",
                    title="Nested Game",
                )
            with self.assertRaisesRegex(ScaffoldError, "game repository"):
                create_world_project(
                    outer_game / "nested-world",
                    world_id="nested_world",
                    title="Nested World",
                    language="en",
                )
        forbidden = ROOT / "m4_forbidden_game_target"
        self.assertFalse(forbidden.exists())
        with self.assertRaisesRegex(GameScaffoldError, "outside the Forge"):
            create_game_project(forbidden, game_id="forbidden_game", title="Forbidden")
        self.assertFalse(forbidden.exists())

    def test_game_title_is_context_escaped_and_control_characters_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "quoted"
            title = 'The "Quoted" \\ Game'
            create_game_project(game, game_id="quoted_game", title=title)
            with (game / "pyproject.toml").open("rb") as source:
                tomllib.load(source)
            result = _run_game_script(
                game,
                "-c",
                "from game import GAME_TITLE; print(GAME_TITLE)",
                cwd=root,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertEqual(title, result.stdout.strip())
            invalid = root / "invalid-title"
            with self.assertRaisesRegex(GameScaffoldError, "single-line"):
                create_game_project(
                    invalid,
                    game_id="invalid_title",
                    title="line one\nline two",
                )
            self.assertFalse(invalid.exists())

    def test_generated_verifier_and_packager_fail_closed_on_boundary_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            create_game_project(game, game_id="hardened_game", title="Hardened Game")
            verifier = str(game / "scripts/verify_game.py")

            project_path = game / "pyproject.toml"
            project_bytes = project_path.read_bytes()
            project_path.write_text(
                project_bytes.decode("utf-8").replace(
                    'requires-python = ">=3.11,<3.13"',
                    'requires-python = ">=3.11"',
                ),
                encoding="utf-8",
            )
            mismatch = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, mismatch.returncode)
            self.assertIn("Python range", mismatch.stderr)
            project_path.write_bytes(project_bytes)

            requirements_path = game / "requirements.lock"
            requirements_bytes = requirements_path.read_bytes()
            requirements_path.write_text(
                requirements_bytes.decode("utf-8").replace(
                    "cffi==1.17.1",
                    "cffi==1.17.0",
                ),
                encoding="utf-8",
            )
            dependency_mismatch = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, dependency_mismatch.returncode)
            self.assertIn("platform lock", dependency_mismatch.stderr)
            requirements_path.write_bytes(requirements_bytes)

            platform_path = game / "platform.lock.json"
            platform_bytes = platform_path.read_bytes()
            platform = json.loads(platform_bytes)
            platform["locked_requirements"].append("openai==1.0.0")
            platform["locked_requirements"].sort(key=str.casefold)
            platform_path.write_text(
                json.dumps(platform, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            requirements_path.write_text(
                "\n".join(platform["locked_requirements"]) + "\n",
                encoding="utf-8",
            )
            extra_dependency = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, extra_dependency.returncode)
            self.assertIn("exact dependency closure", extra_dependency.stderr)
            dependency_package = root / "dependency-bypass.zip"
            dependency_packaged = _run_game_script(
                game,
                str(game / "scripts/package_game.py"),
                "--output",
                str(dependency_package),
                cwd=root,
            )
            self.assertNotEqual(0, dependency_packaged.returncode)
            self.assertFalse(dependency_package.exists())
            platform_path.write_bytes(platform_bytes)
            requirements_path.write_bytes(requirements_bytes)

            project_path.write_text(
                project_bytes.decode("utf-8")
                + '\n[tool.uv]\ndev-dependencies = ["openai==1.0.0"]\n',
                encoding="utf-8",
            )
            hidden_tool_dependency = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, hidden_tool_dependency.returncode)
            self.assertIn("unsupported dependency declaration", hidden_tool_dependency.stderr)
            project_path.write_bytes(project_bytes)

            project_path.write_text(
                project_bytes.decode("utf-8").replace(
                    'where = ["src"]',
                    'where = ["missing"]',
                ),
                encoding="utf-8",
            )
            missing_package_root = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, missing_package_root.returncode)
            self.assertIn("setuptools declaration", missing_package_root.stderr)
            project_path.write_bytes(project_bytes)

            project_path.write_text(
                project_bytes.decode("utf-8").replace(
                    'description = "Standalone deterministic 2D/2.5D isometric RPG"',
                    "description = 123",
                ),
                encoding="utf-8",
            )
            invalid_project_metadata = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, invalid_project_metadata.returncode)
            self.assertIn("packaging metadata", invalid_project_metadata.stderr)
            project_path.write_bytes(project_bytes)

            project_path.write_text(
                project_bytes.decode("utf-8").replace(
                    'build-backend = "setuptools.build_meta"',
                    'build-backend = "setuptools.build_meta"\nbackend-path = ["src"]',
                ),
                encoding="utf-8",
            )
            redirected_backend = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, redirected_backend.returncode)
            self.assertIn("build backend", redirected_backend.stderr)
            project_path.write_bytes(project_bytes)

            project_path.write_text(
                project_bytes.decode("utf-8").replace(
                    'version = "0.1.0"',
                    'version = "0.1.1"',
                    1,
                ),
                encoding="utf-8",
            )
            version_mismatch = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, version_mismatch.returncode)
            self.assertIn("versions do not match", version_mismatch.stderr)
            project_path.write_bytes(project_bytes)

            omitted_provider = game / "src/game/provider_leak.py"
            omitted_provider.write_text("import cohere\n", encoding="utf-8")
            provider_result = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, provider_result.returncode)
            self.assertIn("forbidden runtime import", provider_result.stderr)
            omitted_provider.unlink()

            benchmark = game / "scripts/benchmark_scene.py"
            benchmark_bytes = benchmark.read_bytes()
            benchmark.write_text(
                "import openai\n" + benchmark_bytes.decode("utf-8"),
                encoding="utf-8",
            )
            script_provider = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, script_provider.returncode)
            self.assertIn("forbidden runtime import", script_provider.stderr)
            script_package = root / "script-provider.zip"
            script_packaged = _run_game_script(
                game,
                str(game / "scripts/package_game.py"),
                "--output",
                str(script_package),
                cwd=root,
            )
            self.assertNotEqual(0, script_packaged.returncode)
            self.assertFalse(script_package.exists())
            benchmark.write_bytes(benchmark_bytes)

            first_case = game / "src/game/Foo.py"
            second_case = game / "src/game/foo.py"
            first_case.write_text("VALUE = 1\n", encoding="utf-8")
            second_case.write_text("VALUE = 2\n", encoding="utf-8")
            case_package = root / "case-collision.zip"
            case_result = _run_game_script(
                game,
                str(game / "scripts/package_game.py"),
                "--output",
                str(case_package),
                cwd=root,
            )
            self.assertNotEqual(0, case_result.returncode)
            self.assertIn("case-insensitive", case_result.stderr)
            self.assertFalse(case_package.exists())
            first_case.unlink()
            second_case.unlink()

            reserved_source = game / "src/game/CON.py"
            reserved_source.write_text("VALUE = 1\n", encoding="utf-8")
            reserved_package = root / "reserved-path.zip"
            reserved_path_result = _run_game_script(
                game,
                str(game / "scripts/package_game.py"),
                "--output",
                str(reserved_package),
                cwd=root,
            )
            self.assertNotEqual(0, reserved_path_result.returncode)
            self.assertIn("not portable", reserved_path_result.stderr)
            self.assertFalse(reserved_package.exists())
            reserved_source.unlink()

            catalog_source = game / "src/game/catalog.py"
            catalog_bytes = catalog_source.read_bytes()
            catalog_lock = game / "game_data/worlds.lock.json"
            catalog_lock_bytes = catalog_lock.read_bytes()
            catalog_source.write_text(
                catalog_bytes.decode("utf-8")
                + "\ndef verify_catalog(project_root=None):\n    return ()\n",
                encoding="utf-8",
            )
            catalog_lock.write_text("not json\n", encoding="utf-8")
            bypass = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, bypass.returncode)
            self.assertIn("JSON", bypass.stderr)
            bypass_package = root / "catalog-bypass.zip"
            packaged_bypass = _run_game_script(
                game,
                str(game / "scripts/package_game.py"),
                "--output",
                str(bypass_package),
                cwd=root,
            )
            self.assertNotEqual(0, packaged_bypass.returncode)
            self.assertFalse(bypass_package.exists())
            catalog_source.write_bytes(catalog_bytes)
            catalog_lock.write_bytes(catalog_lock_bytes)

            identity_source = game / "src/game/__init__.py"
            identity_bytes = identity_source.read_bytes()
            executed = root / "mutable-game-code-executed"
            identity_source.write_text(
                identity_bytes.decode("utf-8")
                + "\nfrom pathlib import Path\n"
                + f"Path({str(executed)!r}).write_text('executed')\n",
                encoding="utf-8",
            )
            identity_result = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, identity_result.returncode)
            self.assertIn("constants only", identity_result.stderr)
            identity_package = root / "identity-bypass.zip"
            identity_packaged = _run_game_script(
                game,
                str(game / "scripts/package_game.py"),
                "--output",
                str(identity_package),
                cwd=root,
            )
            self.assertNotEqual(0, identity_packaged.returncode)
            self.assertFalse(identity_package.exists())
            self.assertFalse(executed.exists())
            identity_source.write_bytes(identity_bytes)

            dynamic = game / "src/game/dynamic_leak.py"
            dynamic.write_text(
                "from importlib import import_module\nimport_module('openai')\n",
                encoding="utf-8",
            )
            leaked = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, leaked.returncode)
            self.assertIn("forbidden runtime import", leaked.stderr)
            dynamic.unlink()

            reserved = game / "Agents.md"
            reserved.write_text("must not ship\n", encoding="utf-8")
            reserved_result = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, reserved_result.returncode)
            self.assertIn("forbidden control", reserved_result.stderr)
            reserved.unlink()

            private = game / "game_data/private"
            private.mkdir()
            (private / "credentials.env").write_text("secret\n", encoding="utf-8")
            private_result = _run_game_script(game, verifier, cwd=root)
            self.assertNotEqual(0, private_result.returncode)
            self.assertIn("allowlist", private_result.stderr)
            package = root / "must-not-exist.zip"
            packaged = _run_game_script(
                game,
                str(game / "scripts/package_game.py"),
                "--output",
                str(package),
                cwd=root,
            )
            self.assertNotEqual(0, packaged.returncode)
            self.assertFalse(package.exists())

    def test_runtime_update_uses_optimistic_hash_and_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "game"
            create_game_project(
                game,
                game_id="runtime_game",
                title="Runtime Game",
                source_revision="before",
            )
            before = json.loads((game / "runtime.lock.json").read_text(encoding="utf-8"))
            with self.assertRaisesRegex(GameScaffoldError, "expected hash"):
                update_game_runtime_snapshot(game, expected_content_hash="0" * 64)
            updated = update_game_runtime_snapshot(
                game,
                expected_content_hash=before["content_hash"],
                source_revision="after",
            )
            self.assertNotEqual(before["content_hash"], updated["content_hash"])
            lock = game / ".isoworld-mutation.lock"
            lock.write_text("owned elsewhere\n", encoding="utf-8")
            with self.assertRaisesRegex(GameScaffoldError, "already in progress"):
                update_game_runtime_snapshot(
                    game,
                    expected_content_hash=updated["content_hash"],
                )
            self.assertEqual("owned elsewhere\n", lock.read_text(encoding="utf-8"))
            lock.unlink()
            runtime_file = game / "src/isoworld/__init__.py"
            runtime_file.write_text(
                runtime_file.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(GameScaffoldError, "failed verification"):
                update_game_runtime_snapshot(
                    game,
                    expected_content_hash=updated["content_hash"],
                )

    def test_imported_bundle_verifies_and_runs_without_forge_on_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            create_game_project(game, game_id="bundle_game", title="Bundle Game")
            worldpack, renderpack, licenses = _write_fixture(root / "fixture")
            bundle = export_runtime_bundle(
                worldpack,
                renderpack,
                root / "bundle",
                release_id="1.0.0",
                licenses_directory=licenses,
            )
            imported = import_runtime_bundle(
                bundle.root,
                game,
                expected_bundle_hash=bundle.bundle_hash,
            )
            self.assertEqual(game / "game_data/worlds/modly_foundation/1.0.0", imported)
            outside = root / "outside"
            outside.mkdir()
            verified = _run_game_script(
                game,
                str(game / "scripts/verify_game.py"),
                cwd=outside,
            )
            self.assertEqual(0, verified.returncode, verified.stdout + verified.stderr)
            run = _run_game_script(
                game,
                "-m",
                "game",
                "--world",
                "modly_foundation",
                "--release",
                "1.0.0",
                "--headless-ticks",
                "2",
                cwd=outside,
            )
            self.assertEqual(0, run.returncode, run.stdout + run.stderr)
            self.assertIn("world=modly_foundation release=1.0.0 tick=2", run.stdout)
            self.assertEqual([], audit_game_repository(game))
            generated_tests = _run_game_script(
                game,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(game / "tests"),
                cwd=outside,
            )
            self.assertEqual(
                0,
                generated_tests.returncode,
                generated_tests.stdout + generated_tests.stderr,
            )

            extra = imported / "assets/empty"
            extra.mkdir()
            tampered = _run_game_script(
                game,
                str(game / "scripts/verify_game.py"),
                cwd=outside,
            )
            self.assertNotEqual(0, tampered.returncode)
            self.assertIn("directory tree", tampered.stderr)

    def test_runtime_update_prechecks_every_installed_world_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            create_game_project(game, game_id="update_game", title="Update Game")
            worldpack, renderpack, licenses = _write_fixture(root / "fixture")
            bundle = export_runtime_bundle(
                worldpack,
                renderpack,
                root / "bundle",
                release_id="1.0.0",
                licenses_directory=licenses,
            )
            import_runtime_bundle(
                bundle.root,
                game,
                expected_bundle_hash=bundle.bundle_hash,
            )
            lock_path = game / "runtime.lock.json"
            runtime_path = game / "src/isoworld/content/models.py"
            before_lock = lock_path.read_bytes()
            before_runtime = runtime_path.read_bytes()
            before_hash = json.loads(before_lock)["content_hash"]
            with patch(
                "worldforge.game_scaffold.SUPPORTED_RUNTIME_FEATURES",
                frozenset(),
            ):
                with self.assertRaisesRegex(GameScaffoldError, "installed catalog"):
                    update_game_runtime_snapshot(
                        game,
                        expected_content_hash=before_hash,
                        source_revision="incompatible-candidate",
                    )
            self.assertEqual(before_lock, lock_path.read_bytes())
            self.assertEqual(before_runtime, runtime_path.read_bytes())
            self.assertFalse((game / ".isoworld-mutation.lock").exists())

    def test_two_worlds_keep_same_named_save_slots_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "game"
            create_game_project(game, game_id="multi_world_game", title="Multi World Game")
            bundles = []
            for world_id in ("first_world", "second_world"):
                worldpack, renderpack, licenses = _write_fixture(
                    root / f"fixture-{world_id}",
                    world_id=world_id,
                )
                bundle = export_runtime_bundle(
                    worldpack,
                    renderpack,
                    root / f"bundle-{world_id}",
                    release_id="1.0.0",
                    licenses_directory=licenses,
                )
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )
                bundles.append(bundle)

            user_data = root / "user-data"
            outside = root / "outside"
            outside.mkdir()
            for bundle in bundles:
                result = _run_game_script(
                    game,
                    "-m",
                    "game",
                    "--world",
                    bundle.world_id,
                    "--release",
                    bundle.release_id,
                    "--headless-ticks",
                    "0",
                    "--save-on-exit-slot",
                    "shared",
                    "--user-data",
                    str(user_data),
                    cwd=outside,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)

            for world_id in ("first_world", "second_world"):
                self.assertTrue((user_data / "saves" / world_id / "1.0.0/shared.json").is_file())
            generated_tests = _run_game_script(
                game,
                "-m",
                "unittest",
                "discover",
                "-s",
                str(game / "tests"),
                cwd=outside,
            )
            self.assertEqual(
                0,
                generated_tests.returncode,
                generated_tests.stdout + generated_tests.stderr,
            )


class GameSkillLayoutTests(unittest.TestCase):
    def test_every_skill_maps_to_one_bounded_forge_phase(self) -> None:
        skills_root = ROOT / ".agents/skills"
        skill_names = {path.parent.name for path in skills_root.glob("*/SKILL.md")}
        self.assertEqual(24, len(skill_names))
        phase_document = (ROOT / "docs/GAME_IMPLEMENTATION_PHASES.md").read_text(encoding="utf-8")
        table_names = {
            line.split("`$")[1].split("`")[0]
            for line in phase_document.splitlines()
            if line.startswith("| ") and "`$" in line
        }
        self.assertEqual(skill_names, table_names)
        self.assertNotIn("build-pyray-isometric-runtime", skill_names)
        self.assertNotIn("forge-pyray-game", skill_names)
        self.assertNotIn("manage-world-repositories", skill_names)

        world_skills = {
            "create-world-project",
            "clone-world-project",
            "version-world-project",
            "forge-world-release",
        }
        for name in sorted(skill_names):
            skill = (skills_root / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(skill.startswith("---\n"), name)
            frontmatter = skill.split("---\n", 2)[1]
            fields = {line.split(":", 1)[0] for line in frontmatter.splitlines() if ":" in line}
            self.assertEqual({"name", "description"}, fields, name)
            self.assertIn(f"name: {name}\n", skill, name)
            self.assertNotIn("TODO", skill, name)
            metadata = (skills_root / name / "agents/openai.yaml").read_text(encoding="utf-8")
            self.assertIn(f"${name}", metadata, name)
            if name not in world_skills:
                self.assertIn("GAME_ROOT", skill, name)


if __name__ == "__main__":
    unittest.main()
