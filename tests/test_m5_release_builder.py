from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import scripts.build_release as release_builder

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)


def _copy_committed_fixture(destination: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {".git", ".pytest_cache", ".ruff_cache", "__pycache__"}
            or name.endswith(".pyc")
        }

    shutil.copytree(ROOT, destination, ignore=ignore)
    subprocess.run(["git", "init"], cwd=destination, check=True, stdout=subprocess.PIPE)
    subprocess.run(
        ["git", "config", "user.email", "release-test@example.invalid"],
        cwd=destination,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Release Test"],
        cwd=destination,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=destination, check=True)
    subprocess.run(
        ["git", "commit", "-m", "test: fixture release source"],
        cwd=destination,
        check=True,
        stdout=subprocess.PIPE,
    )


def _run_builder(repo: Path, output: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "must-not-leak")
    return subprocess.run(
        [str(PYTHON), str(repo / "scripts/build_release.py"), "--output-dir", str(output)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _tar_payload(entries: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for info, payload in entries:
            if payload is None:
                archive.addfile(info)
            else:
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _artifact_pair(root: Path, name: str, payload: bytes) -> tuple[Path, Path]:
    left = root / "left" / name
    right = root / "right" / name
    left.parent.mkdir(exist_ok=True)
    right.parent.mkdir(exist_ok=True)
    left.write_bytes(payload)
    right.write_bytes(payload)
    return left, right


class M5ReleaseBuilderTests(unittest.TestCase):
    def test_release_builder_uses_git_archive_and_publishes_reproducible_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-release-test-") as temporary:
            workspace = Path(temporary)
            repo = workspace / "repo"
            output = workspace / "release"
            _copy_committed_fixture(repo)
            (repo / "UNTRACKED_PRIVATE_SENTINEL.txt").write_text("must not ship", encoding="utf-8")

            result = _run_builder(repo, output)

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            sdists = sorted(output.glob("*.tar.gz"))
            wheels = sorted(output.glob("*.whl"))
            self.assertEqual(1, len(sdists))
            self.assertEqual(1, len(wheels))
            self.assertEqual(
                {sdists[0].name, wheels[0].name}, {path.name for path in output.iterdir()}
            )
            self.assertIn("sha256=", result.stdout)

            second = _run_builder(repo, output)
            self.assertNotEqual(0, second.returncode)
            self.assertIn("refusing to replace existing artifact", second.stderr)

            with tarfile.open(sdists[0], "r:gz") as archive:
                names = set(archive.getnames())
                infos = archive.getmembers()
            self.assertTrue(any(name.endswith("README.md") for name in names))
            self.assertTrue(any("/docs/ARCHITECTURE.md" in name for name in names))
            self.assertTrue(any("/tests/test_m5_release_builder.py" in name for name in names))
            self.assertTrue(any("/tests/test_m5_release_readiness.py" in name for name in names))
            self.assertTrue(any("/scripts/verify_m5_release.py" in name for name in names))
            self.assertTrue(any("/.agents/skills/" in name for name in names))
            self.assertTrue(any("/authoring/prompts/00_BOUNDARY.md" in name for name in names))
            self.assertTrue(any("/examples/foundation/" in name for name in names))
            self.assertTrue(any("/schemas/source-manifest.schema.json" in name for name in names))
            self.assertTrue(any("/contracts/README.md" in name for name in names))
            self.assertFalse(any("UNTRACKED_PRIVATE_SENTINEL" in name for name in names))
            self.assertFalse(
                any("__pycache__" in name or ".pytest_cache" in name for name in names)
            )
            self.assertEqual(sorted(info.name for info in infos), [info.name for info in infos])
            self.assertTrue(all(info.uid == 0 and info.gid == 0 for info in infos))
            self.assertTrue(all(not info.pax_headers for info in infos))

            with zipfile.ZipFile(wheels[0]) as archive:
                wheel_names = set(archive.namelist())
                record_names = [name for name in wheel_names if name.endswith(".dist-info/RECORD")]
                record = archive.read(record_names[0]).decode("utf-8")
                infos = archive.infolist()
            self.assertEqual(1, len(record_names))
            self.assertIn(
                "rpg_world_forge-0.7.0.data/data/share/rpg-world-forge/schemas/",
                "\n".join(wheel_names),
            )
            self.assertIn("source-manifest.schema.json", "\n".join(wheel_names))
            self.assertIn(
                "rpg_world_forge-0.7.0.data/data/share/rpg-world-forge/contracts/README.md",
                wheel_names,
            )
            self.assertIn(record_names[0] + ",,", record)
            self.assertEqual(
                sorted(info.filename for info in infos), [info.filename for info in infos]
            )
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos))
            self.assertTrue(all(info.create_system == 3 for info in infos))
            self.assertTrue(
                all(stat.S_IFMT(info.external_attr >> 16) == stat.S_IFREG for info in infos)
            )
            self.assertTrue(
                all((info.external_attr >> 16) & 0o777 in {0o644, 0o755} for info in infos)
            )

            with zipfile.ZipFile(wheels[0]) as archive:
                rows = list(csv.reader(io.StringIO(archive.read(record_names[0]).decode("utf-8"))))
                records = {row[0]: row[1:] for row in rows}
                self.assertEqual(set(archive.namelist()), set(records))
                for name in archive.namelist():
                    digest, size = records[name]
                    if name == record_names[0]:
                        self.assertEqual(["", ""], [digest, size])
                        continue
                    payload = archive.read(name)
                    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest())
                    expected_digest = "sha256=" + encoded.decode("ascii").rstrip("=")
                    self.assertEqual(expected_digest, digest)
                    self.assertEqual(str(len(payload)), size)

    def test_git_archive_extraction_rejects_links_unsafe_paths_and_collisions(self) -> None:
        regular = tarfile.TarInfo("safe.txt")
        symbolic = tarfile.TarInfo("linked.txt")
        symbolic.type = tarfile.SYMTYPE
        symbolic.linkname = "safe.txt"
        hardlink = tarfile.TarInfo("hardlinked.txt")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "safe.txt"
        traversal = tarfile.TarInfo("../escape.txt")
        upper = tarfile.TarInfo("Content/File.txt")
        lower = tarfile.TarInfo("content/file.txt")
        aliased_directory = tarfile.TarInfo("Assets")
        aliased_directory.type = tarfile.DIRTYPE
        aliased_child = tarfile.TarInfo("assets/file.txt")
        cases = {
            "symbolic link": [(regular, b"safe"), (symbolic, None)],
            "hard link": [(regular, b"safe"), (hardlink, None)],
            "traversal": [(traversal, b"escape")],
            "portable collision": [(upper, b"upper"), (lower, b"lower")],
            "portable parent collision": [
                (aliased_directory, None),
                (aliased_child, b"child"),
            ],
        }
        with tempfile.TemporaryDirectory(prefix="rwf-archive-test-") as temporary:
            root = Path(temporary)
            for index, (label, entries) in enumerate(cases.items()):
                with self.subTest(label=label):
                    destination = root / f"case-{index}"
                    with self.assertRaises(release_builder.ReleaseBuildError):
                        release_builder._extract_archive(_tar_payload(entries), destination)
                    self.assertFalse(destination.exists())

    def test_build_environment_is_minimal_and_uses_isolated_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-env-test-") as temporary:
            environment_root = Path(temporary) / "environment"
            inherited = {
                "PATH": "untrusted-path",
                "PYTHONHOME": "untrusted-home",
                "PYTHONPATH": "untrusted-pythonpath",
                "SECRET_TOKEN": "must-not-leak",
                "SystemRoot": "C:\\Windows",
            }
            with patch.dict(release_builder.os.environ, inherited, clear=True):
                environment = release_builder._build_environment(1234, environment_root)

            self.assertNotIn("PATH", environment)
            self.assertNotIn("PYTHONHOME", environment)
            self.assertNotIn("PYTHONPATH", environment)
            self.assertNotIn("SECRET_TOKEN", environment)
            self.assertEqual("C:\\Windows", environment["SystemRoot"])
            self.assertEqual("1234", environment["SOURCE_DATE_EPOCH"])
            self.assertEqual(str(environment_root / "home"), environment["HOME"])
            self.assertEqual(str(environment_root / "home"), environment["USERPROFILE"])
            self.assertEqual(str(environment_root / "tmp"), environment["TMP"])
            self.assertTrue((environment_root / "home").is_dir())
            self.assertTrue((environment_root / "tmp").is_dir())

    def test_one_immutable_commit_oid_drives_epoch_and_archive(self) -> None:
        commit_oid = "a" * 40
        with tempfile.TemporaryDirectory(prefix="rwf-oid-test-") as temporary:
            root = Path(temporary)
            repo = root / "repo"
            output = root / "output"
            repo.mkdir()
            with (
                patch.object(release_builder, "_require_supported_platform"),
                patch.object(release_builder, "_verify_toolchain"),
                patch.object(release_builder, "_head_oid", return_value=commit_oid),
                patch.object(release_builder, "_source_date_epoch", return_value=123) as epoch,
                patch.object(release_builder, "_git_archive", return_value=b"archive") as archive,
                patch.object(release_builder, "_extract_archive"),
                patch.object(
                    release_builder,
                    "_build_from_source",
                    side_effect=[(root / "a.tar.gz", root / "a.whl")] * 2,
                ),
                patch.object(release_builder, "_publish_verified", return_value=[]),
            ):
                self.assertEqual([], release_builder.build_release(repo, output))
            epoch.assert_called_once_with(repo.resolve(), commit_oid)
            archive.assert_called_once_with(repo.resolve(), commit_oid)

    def test_release_builder_accepts_only_supported_desktop_platforms(self) -> None:
        release_builder._require_supported_platform("linux")
        release_builder._require_supported_platform("linux2")
        release_builder._require_supported_platform("win32")
        with self.assertRaisesRegex(
            release_builder.ReleaseBuildError, "supported only on Linux and Windows"
        ):
            release_builder._require_supported_platform("darwin")

    def test_publication_refuses_a_preexisting_artifact_without_partial_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-publish-collision-") as temporary:
            root = Path(temporary)
            first = _artifact_pair(root, "package.tar.gz", b"sdist")
            second = _artifact_pair(root, "package.whl", b"wheel")
            output = root / "output"
            output.mkdir()
            existing = output / second[0].name
            existing.write_bytes(b"foreign")

            with self.assertRaisesRegex(
                release_builder.ReleaseBuildError, "refusing to replace existing artifact"
            ):
                release_builder._publish_verified(
                    (first[0], second[0]), (first[1], second[1]), output
                )

            self.assertFalse((output / first[0].name).exists())
            self.assertEqual(b"foreign", existing.read_bytes())
            self.assertEqual([existing.name], [path.name for path in output.iterdir()])

    def test_publication_rolls_back_only_its_owned_links_after_partial_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-publish-rollback-") as temporary:
            root = Path(temporary)
            first = _artifact_pair(root, "package.tar.gz", b"sdist")
            second = _artifact_pair(root, "package.whl", b"wheel")
            output = root / "output"
            original_link = release_builder.os.link
            calls: list[tuple[Path, Path]] = []

            def fail_second_link(source: str | Path, destination: str | Path) -> None:
                source_path = Path(source)
                destination_path = Path(destination)
                calls.append((source_path, destination_path))
                self.assertEqual(output, source_path.parent)
                self.assertEqual(output, destination_path.parent)
                if len(calls) == 2:
                    raise OSError("simulated publication failure")
                original_link(source_path, destination_path)

            with patch.object(release_builder.os, "link", side_effect=fail_second_link):
                with self.assertRaisesRegex(
                    release_builder.ReleaseBuildError, "simulated publication failure"
                ):
                    release_builder._publish_verified(
                        (first[0], second[0]), (first[1], second[1]), output
                    )

            self.assertEqual([], list(output.iterdir()))

    def test_publication_preserves_replaced_foreign_file_during_rollback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rwf-publish-identity-") as temporary:
            root = Path(temporary)
            first = _artifact_pair(root, "package.tar.gz", b"sdist")
            second = _artifact_pair(root, "package.whl", b"wheel")
            output = root / "output"
            original_link = release_builder.os.link
            first_target = output / first[0].name
            calls = 0

            def replace_before_failure(source: str | Path, destination: str | Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    original_link(source, destination)
                    return
                first_target.unlink()
                first_target.write_bytes(b"foreign replacement")
                raise OSError("simulated publication failure")

            with patch.object(release_builder.os, "link", side_effect=replace_before_failure):
                with self.assertRaises(release_builder.ReleaseBuildError):
                    release_builder._publish_verified(
                        (first[0], second[0]), (first[1], second[1]), output
                    )

            self.assertEqual(b"foreign replacement", first_target.read_bytes())
            self.assertEqual([first_target.name], [path.name for path in output.iterdir()])


if __name__ == "__main__":
    unittest.main()
