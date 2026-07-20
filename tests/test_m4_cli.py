from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

from isoworld.content.loader import load_worldpack
from worldforge.integrity import canonical_payload_hash

ROOT = Path(__file__).resolve().parents[1]
COMPILED = ROOT / "content/compiled/foundation.worldpack.json"


def _run_cli(*arguments: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    source = str(ROOT / "src")
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = source if not current else source + os.pathsep + current
    return subprocess.run(
        [sys.executable, "-m", "worldforge", *(str(argument) for argument in arguments)],
        cwd=cwd or ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_ok(test: unittest.TestCase, result: subprocess.CompletedProcess[str]) -> None:
    test.assertEqual(0, result.returncode, result.stdout + result.stderr)
    test.assertTrue(result.stdout.startswith("OK "), result.stdout + result.stderr)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_bundle_inputs(root: Path) -> tuple[Path, Path, Path]:
    source = root / "bundle-inputs"
    source.mkdir(parents=True)
    worldpack = source / "worldpack.json"
    worldpack.write_bytes(COMPILED.read_bytes())
    pack = load_worldpack(worldpack)

    audio = source / "audio.wav"
    with wave.open(str(audio), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(22_050)
        target.writeframes(b"\x00\x00" * 64)
    renderpack_payload = {
        "format": "isoworld.renderpack",
        "format_version": 1,
        "world_id": pack.world_id,
        "world_content_hash": pack.content_hash,
        "assets": [
            {
                "id": "cli_sfx",
                "kind": "sfx",
                "files": [
                    {
                        "role": "audio",
                        "path": "audio.wav",
                        "sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
                        "media_type": "audio/wav",
                    }
                ],
            }
        ],
        "bindings": [],
    }
    renderpack_payload["content_hash"] = canonical_payload_hash(renderpack_payload)
    renderpack = source / "renderpack.json"
    _write_json(renderpack, renderpack_payload)

    licenses = root / "licenses"
    licenses.mkdir()
    (licenses / "CONTENT-LICENSE.txt").write_text(
        "CLI fixture: CC0-1.0\n",
        encoding="utf-8",
    )
    return worldpack, renderpack, licenses


class M4WorldLifecycleCliTests(unittest.TestCase):
    def test_create_inspect_clone_and_version_worlds_through_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source-world"
            created = _run_cli(
                "new-world",
                source,
                "--id",
                "source_world",
                "--title",
                "Source World",
                "--language",
                "en-GB",
                "--version",
                "1.2.3",
                cwd=root,
            )
            _assert_ok(self, created)
            self.assertIn(f"manifest={source / 'source/manifest.json'}", created.stdout)

            status = _run_cli("world-status", source, cwd=root)
            _assert_ok(self, status)
            self.assertIn("world=source_world version=1.2.3", status.stdout)
            self.assertIn("phase=p00_brief", status.stdout)

            clone = root / "derived-world"
            cloned = _run_cli(
                "clone-world",
                source,
                clone,
                "--id",
                "derived_world",
                "--title",
                "Derived World",
                "--version",
                "0.4.0",
                cwd=root,
            )
            _assert_ok(self, cloned)
            self.assertIn("world=derived_world version=0.4.0", cloned.stdout)

            bumped = _run_cli(
                "bump-world-version",
                clone,
                "--expected-version",
                "0.4.0",
                "--part",
                "minor",
                "--reason",
                "CLI release test",
                "--approved-by",
                "test-agent",
                cwd=root,
            )
            _assert_ok(self, bumped)
            self.assertIn("version=0.5.0", bumped.stdout)
            clone_status = _run_cli("world-status", clone, cwd=root)
            _assert_ok(self, clone_status)
            self.assertIn("world=derived_world version=0.5.0", clone_status.stdout)

    def test_explicit_legacy_upgrade_is_wired_through_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy-world"
            created = _run_cli(
                "new-world",
                legacy,
                "--id",
                "legacy_world",
                "--title",
                "Legacy World",
                cwd=root,
            )
            _assert_ok(self, created)

            project_path = legacy / ".worldforge/project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["format_version"] = 1
            project.pop("project_kind")
            project.pop("world_version")
            _write_json(project_path, project)
            world_path = legacy / "source/world.json"
            world = json.loads(world_path.read_text(encoding="utf-8"))
            world.pop("version")
            _write_json(world_path, world)

            upgraded = _run_cli(
                "upgrade-world",
                legacy,
                "--version",
                "0.8.0",
                "--reason",
                "Adopt the M4 repository contract",
                "--approved-by",
                "test-agent",
                cwd=root,
            )
            _assert_ok(self, upgraded)
            self.assertIn("world=legacy_world version=0.8.0 format_version=2", upgraded.stdout)
            status = _run_cli("world-status", legacy, cwd=root)
            _assert_ok(self, status)
            self.assertIn("world=legacy_world version=0.8.0", status.stdout)


class M4BundleAndGameCliTests(unittest.TestCase):
    def test_bundle_and_standalone_game_workflow_through_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _write_bundle_inputs(root)
            game = root / "game"
            scaffolded = _run_cli(
                "new-game",
                game,
                "--id",
                "cli_game",
                "--title",
                "CLI Game",
                "--source-revision",
                "m4-cli-test",
                cwd=root,
            )
            _assert_ok(self, scaffolded)
            self.assertEqual(f"OK game={game}\n", scaffolded.stdout)

            compatible = _run_cli("check-compatibility", worldpack, cwd=root)
            _assert_ok(self, compatible)
            self.assertIn("api_compatible=true", compatible.stdout)
            self.assertIn("missing_required=-", compatible.stdout)

            bundle = root / "bundle"
            exported = _run_cli(
                "export-bundle",
                worldpack,
                renderpack,
                bundle,
                "--release-id",
                "1.0.0",
                "--licenses",
                licenses,
                cwd=root,
            )
            _assert_ok(self, exported)
            manifest = json.loads((bundle / "bundle.manifest.json").read_text(encoding="utf-8"))
            bundle_hash = manifest["bundle_hash"]
            self.assertIn(f"hash={bundle_hash}", exported.stdout)

            verified = _run_cli(
                "verify-bundle",
                bundle,
                "--expected-hash",
                bundle_hash,
                cwd=root,
            )
            _assert_ok(self, verified)
            self.assertIn("world=foundation_slice release=1.0.0", verified.stdout)

            imported = _run_cli(
                "import-bundle",
                bundle,
                game,
                "--expected-hash",
                bundle_hash,
                cwd=root,
            )
            _assert_ok(self, imported)
            release = game / "game_data/worlds/foundation_slice/1.0.0"
            self.assertIn(f"imported={release}", imported.stdout)
            self.assertTrue((release / "worldpack.json").is_file())

            audit = _run_cli("audit-game", game, cwd=root)
            _assert_ok(self, audit)
            self.assertIn("authoring_leaks=0", audit.stdout)

            before = json.loads((game / "runtime.lock.json").read_text(encoding="utf-8"))
            updated = _run_cli(
                "update-game-runtime",
                game,
                "--expected-hash",
                before["content_hash"],
                "--source-revision",
                "m4-cli-test-updated",
                cwd=root,
            )
            _assert_ok(self, updated)
            after = json.loads((game / "runtime.lock.json").read_text(encoding="utf-8"))
            self.assertEqual("m4-cli-test-updated", after["source_revision"])
            self.assertNotEqual(before["content_hash"], after["content_hash"])
            self.assertIn(f"hash={after['content_hash']}", updated.stdout)


if __name__ == "__main__":
    unittest.main()
