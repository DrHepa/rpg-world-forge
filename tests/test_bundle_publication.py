from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import worldforge.bundle as bundle_module
import worldforge.directory_publish as directory_publish_module
from tests.test_m4_game_scaffold import _write_fixture
from worldforge.bundle import (
    IMPORT_JOURNAL,
    BundleError,
    export_runtime_bundle,
    import_runtime_bundle,
)
from worldforge.directory_publish import DirectoryPublishError, publish_directory_noreplace
from worldforge.game_scaffold import create_game_project


class BundlePublicationTests(unittest.TestCase):
    def test_windows_access_denied_is_a_collision_only_when_destination_exists(self) -> None:
        class _MoveFile:
            argtypes: object = None
            restype: object = None

            def __call__(self, source: str, destination: str, flags: int) -> int:
                del source, destination, flags
                return 0

        class _Kernel32:
            MoveFileExW = _MoveFile()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            destination = root / "destination"
            destination.mkdir()
            with (
                patch.object(
                    directory_publish_module.ctypes,
                    "WinDLL",
                    return_value=_Kernel32(),
                    create=True,
                ),
                patch.object(
                    directory_publish_module.ctypes,
                    "get_last_error",
                    return_value=5,
                    create=True,
                ),
                self.assertRaises(FileExistsError),
            ):
                directory_publish_module._windows_rename_noreplace(source, destination)

            destination.rmdir()
            with (
                patch.object(
                    directory_publish_module.ctypes,
                    "WinDLL",
                    return_value=_Kernel32(),
                    create=True,
                ),
                patch.object(
                    directory_publish_module.ctypes,
                    "get_last_error",
                    return_value=5,
                    create=True,
                ),
                patch.object(
                    directory_publish_module.ctypes,
                    "FormatError",
                    return_value="Access is denied",
                    create=True,
                ),
                self.assertRaisesRegex(DirectoryPublishError, "Access is denied"),
            ):
                directory_publish_module._windows_rename_noreplace(source, destination)

    def _bundle_and_game(self, root: Path) -> tuple[object, Path]:
        game = root / "game"
        create_game_project(game, game_id="publication_game", title="Publication Game")
        worldpack, renderpack, licenses = _write_fixture(root / "fixture")
        bundle = export_runtime_bundle(
            worldpack,
            renderpack,
            root / "bundle",
            release_id="1.0.0",
            licenses_directory=licenses,
        )
        return bundle, game

    def _leave_published_crash(self, bundle: object, game: Path) -> Path:
        with (
            patch.object(
                bundle_module,
                "_write_catalog_atomic",
                side_effect=KeyboardInterrupt("simulated process loss"),
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "simulated process loss"),
        ):
            import_runtime_bundle(
                bundle.root,
                game,
                expected_bundle_hash=bundle.bundle_hash,
            )
        destination = game / "game_data/worlds/modly_foundation/1.0.0"
        self.assertTrue(destination.is_dir())
        self.assertTrue((game / IMPORT_JOURNAL).is_file())
        return destination

    def test_export_does_not_replace_a_concurrent_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _write_fixture(root / "fixture")
            destination = root / "bundle"
            canonical_destination = destination.resolve(strict=False)
            original_publish = bundle_module.publish_directory_noreplace

            def race(source: Path, target: Path) -> tuple[int, int]:
                if target.resolve(strict=False) == canonical_destination:
                    target.mkdir()
                    (target / "concurrent.txt").write_text(
                        "preserve me\n",
                        encoding="utf-8",
                    )
                return original_publish(source, target)

            with (
                patch.object(
                    bundle_module,
                    "publish_directory_noreplace",
                    side_effect=race,
                ),
                self.assertRaisesRegex(BundleError, "destination already exists"),
            ):
                export_runtime_bundle(
                    worldpack,
                    renderpack,
                    destination,
                    release_id="1.0.0",
                    licenses_directory=licenses,
                )

            self.assertEqual(
                "preserve me\n",
                (destination / "concurrent.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual([], list(root.glob(".bundle.export-*")))

    def test_catalog_failure_rolls_back_only_the_owned_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            catalog = game / "game_data/worlds.lock.json"
            before = catalog.read_bytes()
            with (
                patch.object(
                    bundle_module,
                    "_write_catalog_atomic",
                    side_effect=OSError("injected catalog failure"),
                ),
                self.assertRaisesRegex(OSError, "injected catalog failure"),
            ):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )

            self.assertEqual(before, catalog.read_bytes())
            self.assertFalse((game / "game_data/worlds/modly_foundation/1.0.0").exists())
            self.assertFalse((game / IMPORT_JOURNAL).exists())
            self.assertEqual([], list((game / "game_data").rglob("*.import-*")))

    def test_interrupted_publication_is_recovered_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            destination = self._leave_published_crash(bundle, game)

            recovered = import_runtime_bundle(
                bundle.root,
                game,
                expected_bundle_hash=bundle.bundle_hash,
            )

            self.assertEqual(destination.resolve(), recovered.resolve())
            self.assertFalse((game / IMPORT_JOURNAL).exists())
            catalog = json.loads((game / "game_data/worlds.lock.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(catalog["releases"]))

    def test_recovery_preserves_a_hash_mismatched_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            destination = self._leave_published_crash(bundle, game)
            marker = destination / "concurrent.txt"
            marker.write_text("do not delete\n", encoding="utf-8")

            with self.assertRaisesRegex(BundleError, "recovery could not complete|tree mismatch"):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )

            self.assertEqual("do not delete\n", marker.read_text(encoding="utf-8"))
            self.assertTrue((game / IMPORT_JOURNAL).exists())

    def test_recovery_preserves_an_identity_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            destination = self._leave_published_crash(bundle, game)
            owned = destination.with_name("owned-before-race")
            destination.rename(owned)
            destination.mkdir()
            marker = destination / "concurrent.txt"
            marker.write_text("do not delete\n", encoding="utf-8")

            with self.assertRaisesRegex(BundleError, "identity.*matches|no longer matches"):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )

            self.assertEqual("do not delete\n", marker.read_text(encoding="utf-8"))
            self.assertTrue(owned.is_dir())
            self.assertTrue((game / IMPORT_JOURNAL).exists())

    def test_import_preserves_a_concurrent_destination_and_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            original_publish = bundle_module.publish_directory_noreplace

            def race(source: Path, target: Path) -> tuple[int, int]:
                if target.name == "1.0.0":
                    target.mkdir()
                    (target / "concurrent.txt").write_text(
                        "preserve me\n",
                        encoding="utf-8",
                    )
                return original_publish(source, target)

            with (
                patch.object(
                    bundle_module,
                    "publish_directory_noreplace",
                    side_effect=race,
                ),
                self.assertRaisesRegex(BundleError, "recovery could not complete"),
            ):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )

            destination = game / "game_data/worlds/modly_foundation/1.0.0"
            self.assertEqual(
                "preserve me\n",
                (destination / "concurrent.txt").read_text(encoding="utf-8"),
            )
            self.assertTrue((game / IMPORT_JOURNAL).is_file())
            self.assertEqual(1, len(list(destination.parent.glob(".1.0.0.import-*"))))

    def test_interrupted_copy_is_recovered_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)

            def interrupt_copy(
                source: Path,
                target: Path,
                **kwargs: object,
            ) -> None:
                del source, kwargs
                (target / "partial.txt").write_text("partial\n", encoding="utf-8")
                raise KeyboardInterrupt("simulated copy process loss")

            with (
                patch.object(
                    bundle_module.shutil,
                    "copytree",
                    side_effect=interrupt_copy,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "copy process loss"),
            ):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )

            journal = json.loads((game / IMPORT_JOURNAL).read_text(encoding="utf-8"))
            self.assertEqual("copying", journal["state"])
            recovered = import_runtime_bundle(
                bundle.root,
                game,
                expected_bundle_hash=bundle.bundle_hash,
            )
            self.assertTrue(recovered.is_dir())
            self.assertFalse((game / IMPORT_JOURNAL).exists())

    def test_interrupted_prepublish_stage_is_recovered_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            original_publish = bundle_module.publish_directory_noreplace

            def interrupt(source: Path, target: Path) -> tuple[int, int]:
                if target.name == "1.0.0":
                    raise KeyboardInterrupt("simulated prepublish process loss")
                return original_publish(source, target)

            with (
                patch.object(
                    bundle_module,
                    "publish_directory_noreplace",
                    side_effect=interrupt,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "prepublish process loss"),
            ):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )

            self.assertTrue((game / IMPORT_JOURNAL).is_file())
            self.assertEqual(
                1, len(list((game / "game_data/worlds/modly_foundation").glob(".*.import-*")))
            )
            recovered = import_runtime_bundle(
                bundle.root,
                game,
                expected_bundle_hash=bundle.bundle_hash,
            )
            self.assertTrue(recovered.is_dir())
            self.assertFalse((game / IMPORT_JOURNAL).exists())

    def test_unsupported_platform_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            destination = root / "destination"
            with (
                patch.object(directory_publish_module.sys, "platform", "darwin"),
                patch.object(directory_publish_module.os, "name", "posix"),
                self.assertRaisesRegex(
                    DirectoryPublishError,
                    "supported only on Linux and Windows",
                ),
            ):
                publish_directory_noreplace(source, destination)
            self.assertTrue(source.is_dir())
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
