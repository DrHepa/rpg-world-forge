#!/usr/bin/env python3
"""Run the cross-platform M5 release-readiness gate in disposable directories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path, PurePosixPath

from scripts.generate_m5_neutral import generate as generate_neutral_fixture
from worldforge.asset_manifest_v3 import finalize_asset_release
from worldforge.assetpack import build_assetpack, verify_assetpack
from worldforge.assets import validate_asset_manifest
from worldforge.bundle import export_runtime_bundle, import_runtime_bundle, verify_runtime_bundle
from worldforge.game_boundary import audit_game_repository
from worldforge.game_scaffold import create_game_project
from worldforge.renderpack import build_renderpack

ROOT = Path(__file__).resolve().parents[1]
WORLDPACK = ROOT / "content/compiled/foundation.worldpack.json"
COMMITTED_NEUTRAL_FIXTURE = ROOT / "examples/m5-neutral"
FIXTURE_LOCK = "fixture.lock.json"
PINNED_BUILD_REQUIREMENTS = ("setuptools==83.0.0", "wheel==0.47.0")


class ReadinessError(RuntimeError):
    """Raised when one release-readiness assertion fails."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_records(root: Path) -> dict[str, dict[str, int | str]]:
    return {
        path.relative_to(root).as_posix(): {
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["PIP_CONFIG_FILE"] = os.devnull
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    print("+ " + subprocess.list2cmdline(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=_clean_environment(),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        raise ReadinessError(f"command failed: {subprocess.list2cmdline(command)}\n{detail}")
    return completed


def _manifest_hash(path: Path) -> str:
    document = json.loads(path.read_text(encoding="utf-8"))
    content_hash = document.get("content_hash")
    if not isinstance(content_hash, str):
        raise ReadinessError(f"manifest has no content hash: {path}")
    return content_hash


def _validate_committed_neutral_fixture(
    fixture: Path = COMMITTED_NEUTRAL_FIXTURE,
    worldpack: Path = WORLDPACK,
) -> set[str]:
    lock_path = fixture / FIXTURE_LOCK
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"committed neutral fixture lock is invalid: {exc}") from exc
    if (
        not isinstance(lock, dict)
        or lock.get("format") != "rpg-world-forge.m5_neutral_fixture_lock"
    ):
        raise ReadinessError("committed neutral fixture lock has an invalid format")
    if lock.get("format_version") != 1:
        raise ReadinessError("committed neutral fixture lock has an invalid format version")

    anchor = lock.get("worldpack_anchor")
    expected_anchor = "content/compiled/foundation.worldpack.json"
    if not isinstance(anchor, dict) or anchor.get("path") != expected_anchor:
        raise ReadinessError("committed neutral fixture lock has an invalid worldpack anchor")
    if anchor.get("sha256") != _sha256(worldpack):
        raise ReadinessError("committed neutral fixture worldpack anchor hash does not match")

    locked_files = lock.get("files")
    if not isinstance(locked_files, list):
        raise ReadinessError("committed neutral fixture lock files must be an array")
    expected: dict[str, dict[str, int | str]] = {}
    for index, record in enumerate(locked_files):
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
            raise ReadinessError(f"committed neutral fixture lock file {index} is invalid")
        relative = record.get("path")
        digest = record.get("sha256")
        size = record.get("size")
        if not isinstance(relative, str):
            raise ReadinessError(f"committed neutral fixture lock file {index} has no path")
        path = PurePosixPath(relative)
        if (
            path.is_absolute()
            or path.as_posix() != relative
            or "\\" in relative
            or any(part in {"", ".", ".."} for part in path.parts)
            or relative == FIXTURE_LOCK
            or relative in expected
        ):
            raise ReadinessError(f"committed neutral fixture lock path is invalid: {relative!r}")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise ReadinessError(f"committed neutral fixture lock metadata is invalid: {relative}")
        expected[relative] = {"sha256": digest, "size": size}

    actual = _tree_records(fixture)
    expected_paths = {*expected, FIXTURE_LOCK}
    if set(actual) != expected_paths:
        missing = sorted(expected_paths - set(actual))
        extra = sorted(set(actual) - expected_paths)
        raise ReadinessError(
            f"committed neutral fixture path set does not match its lock; "
            f"missing={missing}, extra={extra}"
        )
    for relative, record in expected.items():
        if actual[relative] != record:
            raise ReadinessError(f"committed neutral fixture bytes do not match lock: {relative}")
    return expected_paths


def _regenerate_neutral_fixture(work_root: Path) -> Path:
    expected_paths = _validate_committed_neutral_fixture()
    generated_roots: list[Path] = []
    generated_records: list[dict[str, dict[str, int | str]]] = []
    for label in ("a", "b"):
        output = work_root / f"neutral-regenerated-{label}"
        generate_neutral_fixture(output, allow_repo=False)
        generated = output / "m5-neutral"
        records = _tree_records(generated)
        if set(records) != expected_paths:
            missing = sorted(expected_paths - set(records))
            extra = sorted(set(records) - expected_paths)
            raise ReadinessError(
                f"regenerated M5 neutral fixture path set differs from committed lock; "
                f"missing={missing}, extra={extra}"
            )
        generated_roots.append(generated)
        generated_records.append(records)
    if generated_records[0] != generated_records[1]:
        raise ReadinessError(
            "same-toolchain M5 neutral fixture regeneration is not byte-reproducible"
        )
    return generated_roots[0]


def _require_clean_source_tree(repo: Path = ROOT) -> None:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
        env=_clean_environment(),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        raise ReadinessError(f"could not verify release source identity with git status\n{detail}")
    changes = [line for line in completed.stdout.splitlines() if line]
    if changes:
        preview = "; ".join(changes[:5])
        suffix = "" if len(changes) <= 5 else f"; ... ({len(changes)} entries)"
        raise ReadinessError(
            "full release readiness requires a clean tracked and untracked source tree "
            f"matching HEAD; git status reported: {preview}{suffix}"
        )


def _build_neutral_assets(fixture: Path) -> Path:
    render_root = fixture / "renderpack"
    render_manifest = render_root / "manifest.json"
    render_output = render_root / "build/renderpack.json"
    if findings := validate_asset_manifest(
        render_manifest,
        profile="build",
        worldpack_path=WORLDPACK,
    ):
        raise ReadinessError(f"neutral render manifest failed build validation: {findings}")
    build_renderpack(render_manifest, WORLDPACK, render_output)
    finalize_asset_release(
        render_manifest,
        render_output,
        WORLDPACK,
        expected_manifest_hash=_manifest_hash(render_manifest),
    )
    if findings := validate_asset_manifest(
        render_manifest,
        profile="release",
        worldpack_path=WORLDPACK,
    ):
        raise ReadinessError(f"neutral render manifest failed release validation: {findings}")

    asset_root = fixture / "assetpack"
    asset_manifest = asset_root / "manifest.json"
    asset_output = asset_root / "build/assetpack.json"
    if findings := validate_asset_manifest(
        asset_manifest,
        profile="build",
        worldpack_path=WORLDPACK,
    ):
        raise ReadinessError(f"neutral 3D manifest failed build validation: {findings}")
    build_assetpack(asset_manifest, WORLDPACK, asset_output)
    verify_assetpack(asset_output, WORLDPACK)
    finalize_asset_release(
        asset_manifest,
        asset_output,
        WORLDPACK,
        expected_manifest_hash=_manifest_hash(asset_manifest),
    )
    if findings := validate_asset_manifest(
        asset_manifest,
        profile="release",
        worldpack_path=WORLDPACK,
    ):
        raise ReadinessError(f"neutral 3D manifest failed release validation: {findings}")
    return render_output


def _copy_runtime_notices(fixture: Path, destination: Path) -> None:
    destination.mkdir()
    notices = sorted((fixture / "renderpack/evidence").glob("*_NOTICE.txt"))
    if not notices:
        raise ReadinessError("neutral fixture has no runtime license notices")
    for notice in notices:
        shutil.copyfile(notice, destination / notice.name)


def _assert_safe_zip(archive_path: Path) -> list[str]:
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
    if names != sorted(names) or len(names) != len(set(names)):
        raise ReadinessError("game package entries are not unique and sorted")
    folded: set[str] = set()
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or path.as_posix() != name or "\\" in name:
            raise ReadinessError(f"game package has an unsafe path: {name}")
        if any(part in {"", ".", ".."} for part in path.parts):
            raise ReadinessError(f"game package has an unsafe path: {name}")
        portable = name.casefold()
        if portable in folded:
            raise ReadinessError(f"game package has a portable path collision: {name}")
        folded.add(portable)
    return names


def _run_standalone_e2e(work_root: Path, fixture: Path, renderpack: Path) -> None:
    licenses = work_root / "runtime-licenses"
    _copy_runtime_notices(fixture, licenses)
    bundle = export_runtime_bundle(
        WORLDPACK,
        renderpack,
        work_root / "bundle",
        release_id="1.0.0",
        licenses_directory=licenses,
    )
    verify_runtime_bundle(bundle.root, expected_bundle_hash=bundle.bundle_hash)

    game = work_root / "standalone-game"
    create_game_project(
        game,
        game_id="m5_neutral_game",
        title="M5 Neutral Game",
        source_revision="m5-release-readiness",
    )
    import_runtime_bundle(
        bundle.root,
        game,
        expected_bundle_hash=bundle.bundle_hash,
    )
    if findings := audit_game_repository(game):
        raise ReadinessError(f"standalone game boundary audit failed: {findings}")

    outside = work_root / "empty-cwd"
    outside.mkdir()
    _run([sys.executable, "-S", str(game / "scripts/verify_game.py")], cwd=outside)
    base = [
        sys.executable,
        "-S",
        str(game / "run_game.py"),
        "--world",
        bundle.world_id,
        "--release",
        bundle.release_id,
        "--user-data",
        str(work_root / "game-user-data"),
    ]
    recorded = _run(
        [*base, "--headless-ticks", "3", "--record-replay-slot", "readiness"],
        cwd=outside,
    )
    replayed = _run([*base, "--replay-slot", "readiness"], cwd=outside)
    if "tick=3" not in recorded.stdout or "replay_actions=3 tick=3" not in replayed.stdout:
        raise ReadinessError("standalone replay did not reproduce the three-tick run")

    package_a = work_root / "m5-neutral-a.zip"
    package_b = work_root / "m5-neutral-b.zip"
    for output in (package_a, package_b):
        _run(
            [
                sys.executable,
                "-S",
                str(game / "scripts/package_game.py"),
                "--output",
                str(output),
            ],
            cwd=outside,
        )
    if _sha256(package_a) != _sha256(package_b):
        raise ReadinessError("standalone package is not byte-reproducible")
    names = _assert_safe_zip(package_a)
    if "PACKAGE-MANIFEST.json" not in names:
        raise ReadinessError("standalone package has no package manifest")
    extracted = work_root / "extracted-game"
    with zipfile.ZipFile(package_a) as archive:
        archive.extractall(extracted)
    extracted_base = [
        sys.executable,
        "-S",
        str(extracted / "run_game.py"),
        "--world",
        bundle.world_id,
        "--release",
        bundle.release_id,
        "--user-data",
        str(work_root / "game-user-data"),
    ]
    extracted_run = _run([*extracted_base, "--replay-slot", "readiness"], cwd=outside)
    if "replay_actions=3 tick=3" not in extracted_run.stdout:
        raise ReadinessError("extracted standalone package failed deterministic replay")


def _venv_python(environment: Path) -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return environment / relative


def _verify_clean_install(artifact: Path, environment: Path, empty_cwd: Path) -> None:
    venv.EnvBuilder(with_pip=True).create(environment)
    python = _venv_python(environment)
    if artifact.name.endswith(".tar.gz"):
        _run(
            [python.as_posix(), "-m", "pip", "install", *PINNED_BUILD_REQUIREMENTS],
            cwd=empty_cwd,
        )
        install_options = ["--no-build-isolation", "--no-deps"]
    else:
        install_options = ["--no-deps"]
    _run(
        [python.as_posix(), "-m", "pip", "install", *install_options, str(artifact)],
        cwd=empty_cwd,
    )
    _run([python.as_posix(), "-m", "pip", "check"], cwd=empty_cwd)
    located = _run(
        [
            python.as_posix(),
            "-I",
            "-c",
            (
                "from pathlib import Path; import sys, worldforge; "
                "module=Path(worldforge.__file__).resolve(); "
                "prefix=Path(sys.prefix).resolve(); "
                "assert module.is_relative_to(prefix), (module, prefix); print(module)"
            ),
        ],
        cwd=empty_cwd,
    )
    if not located.stdout.strip():
        raise ReadinessError(f"could not locate clean installation for {artifact.name}")
    _run([python.as_posix(), "-I", "-m", "worldforge", "audit-contracts"], cwd=empty_cwd)


def _build_and_install_release(work_root: Path) -> list[Path]:
    release = work_root / "release"
    _run(
        [sys.executable, str(ROOT / "scripts/build_release.py"), "--output-dir", str(release)],
        cwd=ROOT,
    )
    artifacts = sorted(path for path in release.iterdir() if path.is_file())
    sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
    wheels = [path for path in artifacts if path.suffix == ".whl"]
    if len(sdists) != 1 or len(wheels) != 1:
        raise ReadinessError("release builder did not publish exactly one wheel and one sdist")
    empty_cwd = work_root / "installed-empty-cwd"
    empty_cwd.mkdir()
    _verify_clean_install(wheels[0], work_root / "wheel-venv", empty_cwd)
    _verify_clean_install(sdists[0], work_root / "sdist-venv", empty_cwd)
    return artifacts


def verify_release_readiness(work_root: Path, *, neutral_only: bool = False) -> list[Path]:
    work_root = work_root.expanduser().resolve()
    try:
        work_root.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ReadinessError("readiness work root must be outside the repository")
    if not neutral_only:
        _require_clean_source_tree()
    if work_root.exists():
        raise ReadinessError(f"refusing to reuse readiness work root: {work_root}")
    work_root.mkdir(parents=True)
    fixture = _regenerate_neutral_fixture(work_root)
    renderpack = _build_neutral_assets(fixture)
    _run_standalone_e2e(work_root, fixture, renderpack)
    print("neutral-e2e=pass")
    if neutral_only:
        return []
    artifacts = _build_and_install_release(work_root)
    for artifact in artifacts:
        print(f"release-artifact={artifact.name} sha256={_sha256(artifact)}")
    print("m5-release-readiness=pass")
    return artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-root",
        type=Path,
        help="new external directory for disposable outputs; defaults to a temporary directory",
    )
    parser.add_argument(
        "--neutral-only",
        action="store_true",
        help="run regeneration and standalone E2E without building Python release artifacts",
    )
    args = parser.parse_args(argv)
    try:
        if args.work_root is not None:
            verify_release_readiness(args.work_root, neutral_only=args.neutral_only)
        else:
            with tempfile.TemporaryDirectory(prefix="rwf-m5-readiness-") as temporary:
                root = Path(temporary) / "work"
                verify_release_readiness(root, neutral_only=args.neutral_only)
    except ReadinessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
