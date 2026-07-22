from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import isoworld.__main__ as isoworld_cli
import worldforge.__main__ as worldforge_cli
from isoworld.content.loader import load_worldpack
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash

ROOT = Path(__file__).resolve().parents[1]
COMPILED = ROOT / "content/compiled/foundation.worldpack.json"


class _TrackedBundle:
    def __init__(self) -> None:
        self.root = Path("tracked-bundle")
        self.world_id = "tracked_world"
        self.release_id = "1.0.0"
        self.bundle_hash = "a" * 64
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    def __enter__(self) -> _TrackedBundle:
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, traceback: object) -> None:
        self.close()


class _FailingBundle:
    def __init__(self, *, body_error: BaseException | None = None) -> None:
        self.root = Path("failing-bundle")
        self.release_id = "1.0.0"
        self.bundle_hash = "b" * 64
        self.body_error = body_error
        self.close_calls = 0

    @property
    def world_id(self) -> str:
        if self.body_error is not None:
            raise self.body_error
        return "failing_world"

    def close(self) -> None:
        self.close_calls += 1
        raise worldforge_cli.BundleError("bundle close failed")


class _FailingRuntimeRenderPack:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        raise isoworld_cli.RenderPackError("renderpack close failed")


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


def _run_runtime_cli(
    *arguments: str | Path,
    cwd: Path | None = None,
    pythonpath_prefix: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    paths = [str(ROOT / "src")]
    if pythonpath_prefix is not None:
        paths.insert(0, str(pythonpath_prefix))
    current = environment.get("PYTHONPATH")
    if current:
        paths.append(current)
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    return subprocess.run(
        [sys.executable, "-m", "isoworld", *(str(argument) for argument in arguments)],
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
    path.write_bytes(canonical_json_bytes(payload))


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


class M5RuntimeExecutionCliTests(unittest.TestCase):
    def test_explicit_zero_ticks_is_headless_without_importing_pyray(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory)
            (blocker / "pyray.py").write_text(
                'raise RuntimeError("pyray must not be imported in headless mode")\n',
                encoding="utf-8",
            )

            result = _run_runtime_cli(
                "--pack",
                COMPILED,
                "--headless-ticks",
                "0",
                pythonpath_prefix=blocker,
            )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("world=foundation_slice tick=0", result.stdout)
        self.assertEqual("", result.stderr)

    def test_negative_headless_ticks_is_an_argparse_usage_error(self) -> None:
        result = _run_runtime_cli("--headless-ticks", "-1")

        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("--headless-ticks: must be zero or greater", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_contract_failures_are_concise_stderr_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            malformed = root / "malformed.json"
            malformed.write_text("{}", encoding="utf-8")
            output_directory = root / "save-target"
            output_directory.mkdir()
            cases = (
                ("worldpack", ("--pack", root / "missing-worldpack.json", "--headless-ticks", "0")),
                (
                    "renderpack",
                    (
                        "--pack",
                        COMPILED,
                        "--renderpack",
                        root / "missing-renderpack.json",
                        "--headless-ticks",
                        "0",
                    ),
                ),
                (
                    "load-save",
                    ("--pack", COMPILED, "--load-save", malformed, "--headless-ticks", "0"),
                ),
                ("replay", ("--pack", COMPILED, "--replay", malformed)),
                (
                    "save-on-exit",
                    (
                        "--pack",
                        COMPILED,
                        "--save-on-exit",
                        output_directory,
                        "--headless-ticks",
                        "0",
                    ),
                ),
            )
            for name, arguments in cases:
                with self.subTest(name=name):
                    result = _run_runtime_cli(*arguments)
                    self.assertEqual(1, result.returncode, result.stdout + result.stderr)
                    self.assertEqual("", result.stdout)
                    self.assertTrue(result.stderr.startswith("ERROR: "), result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_runtime_cli_prints_no_success_when_renderpack_close_fails(self) -> None:
        renderpack = _FailingRuntimeRenderPack()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(isoworld_cli, "load_worldpack", return_value=object()),
            patch.object(isoworld_cli, "load_renderpack", return_value=renderpack),
            patch.object(
                isoworld_cli,
                "_run_loaded",
                return_value=(0, "world=would-have-succeeded"),
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = isoworld_cli.main(
                [
                    "--pack",
                    "worldpack.json",
                    "--renderpack",
                    "renderpack.json",
                    "--headless-ticks",
                    "0",
                ]
            )

        self.assertEqual(1, result)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("renderpack close failed", stderr.getvalue())
        self.assertNotIn("would-have-succeeded", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(1, renderpack.close_calls)

    def test_runtime_cli_preserves_primary_and_reports_close_failure(self) -> None:
        renderpack = _FailingRuntimeRenderPack()
        primary = isoworld_cli.PersistenceError("runtime body failed")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch.object(isoworld_cli, "load_worldpack", return_value=object()),
            patch.object(isoworld_cli, "load_renderpack", return_value=renderpack),
            patch.object(isoworld_cli, "_run_loaded", side_effect=primary),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = isoworld_cli.main(
                [
                    "--pack",
                    "worldpack.json",
                    "--renderpack",
                    "renderpack.json",
                    "--headless-ticks",
                    "0",
                ]
            )

        self.assertEqual(1, result)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("runtime body failed", stderr.getvalue())
        self.assertIn("renderpack cleanup failed: renderpack close failed", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertEqual(1, renderpack.close_calls)

    def test_replay_save_and_record_argument_conflicts_exit_two(self) -> None:
        cases = (
            ("--replay", "replay.json", "--headless-ticks", "0"),
            ("--replay", "replay.json", "--load-save", "save.json"),
            ("--replay", "replay.json", "--save", "quick.json"),
            ("--replay", "replay.json", "--save-on-exit", "save.json"),
            ("--replay", "replay.json", "--record-replay", "record.json"),
            ("--record-replay", "record.json", "--load-save", "save.json"),
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = _run_runtime_cli(*arguments)
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertEqual("", result.stdout)
                self.assertIn("cannot be combined", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


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
            self.assertIn(
                f"manifest={(source / 'source/manifest.json').resolve(strict=True)}",
                created.stdout,
            )

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
    def test_bundle_cli_success_paths_close_owned_verification_snapshots(self) -> None:
        exported = _TrackedBundle()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "worldforge",
                    "export-bundle",
                    "worldpack.json",
                    "renderpack.json",
                    "bundle",
                    "--release-id",
                    "1.0.0",
                    "--licenses",
                    "licenses",
                ],
            ),
            patch.object(
                worldforge_cli,
                "export_runtime_bundle",
                return_value=exported,
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(0, worldforge_cli.main())
        self.assertEqual(1, exported.close_calls)

        verified = _TrackedBundle()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "worldforge",
                    "verify-bundle",
                    "bundle",
                    "--expected-hash",
                    "a" * 64,
                ],
            ),
            patch.object(
                worldforge_cli,
                "verify_runtime_bundle",
                return_value=verified,
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(0, worldforge_cli.main())
        self.assertEqual(1, verified.close_calls)

        post_import = _TrackedBundle()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "worldforge",
                    "import-bundle",
                    "bundle",
                    "game",
                    "--expected-hash",
                    "a" * 64,
                ],
            ),
            patch.object(
                worldforge_cli,
                "import_runtime_bundle",
                return_value=Path("game/release"),
            ),
            patch.object(
                worldforge_cli,
                "verify_runtime_bundle",
                return_value=post_import,
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(0, worldforge_cli.main())
        self.assertEqual(1, post_import.close_calls)

    def test_bundle_cli_prints_no_ok_when_owned_close_fails(self) -> None:
        bundle = _FailingBundle()
        output = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "worldforge",
                    "export-bundle",
                    "worldpack.json",
                    "renderpack.json",
                    "bundle",
                    "--release-id",
                    "1.0.0",
                    "--licenses",
                    "licenses",
                ],
            ),
            patch.object(
                worldforge_cli,
                "export_runtime_bundle",
                return_value=bundle,
            ),
            redirect_stdout(output),
        ):
            result = worldforge_cli.main()

        self.assertEqual(1, result)
        self.assertIn("ERROR bundle close failed", output.getvalue())
        self.assertNotIn("OK bundle=", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())
        self.assertEqual(1, bundle.close_calls)

    def test_bundle_cli_preserves_primary_and_reports_close_failure(self) -> None:
        bundle = _FailingBundle(body_error=worldforge_cli.BundleError("bundle body failed"))
        output = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                [
                    "worldforge",
                    "verify-bundle",
                    "bundle",
                    "--expected-hash",
                    "b" * 64,
                ],
            ),
            patch.object(
                worldforge_cli,
                "verify_runtime_bundle",
                return_value=bundle,
            ),
            redirect_stdout(output),
        ):
            result = worldforge_cli.main()

        self.assertEqual(1, result)
        self.assertIn("bundle body failed", output.getvalue())
        self.assertIn("bundle cleanup failed: bundle close failed", output.getvalue())
        self.assertNotIn("OK bundle=", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())
        self.assertEqual(1, bundle.close_calls)

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
