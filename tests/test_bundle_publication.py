from __future__ import annotations

import errno
import json
import os
import sys
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
    def _run_posix_close_failure(
        self,
        failing_roles: set[str],
        *,
        primary: BaseException | None = None,
    ) -> BaseException:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            claim = root / "claim"
            claim.mkdir()
            identity = directory_publish_module.directory_identity(
                claim,
                context="claimed directory",
            )
            real_close = os.close
            real_descriptor_file_stat = directory_publish_module.descriptor_file_stat
            descriptors: dict[str, int] = {}

            def track_descriptor(descriptor: int):
                role = "parent" if "parent" not in descriptors else "claim"
                descriptors.setdefault(role, descriptor)
                return real_descriptor_file_stat(descriptor)

            def close_then_fail(descriptor: int) -> None:
                role = next(name for name, value in descriptors.items() if value == descriptor)
                real_close(descriptor)
                if role in failing_roles:
                    raise OSError(f"{role} close failure")

            unlink_kwargs = (
                {"side_effect": primary} if primary is not None else {"return_value": None}
            )
            caught: BaseException | None = None
            with (
                patch.object(
                    directory_publish_module,
                    "descriptor_file_stat",
                    side_effect=track_descriptor,
                ),
                patch.object(directory_publish_module.os, "close", side_effect=close_then_fail),
                patch.object(
                    directory_publish_module,
                    "_posix_unlink_descriptor",
                    **unlink_kwargs,
                ),
                patch.object(
                    directory_publish_module,
                    "_posix_preflight_directory_deletion",
                    return_value=False,
                ),
            ):
                try:
                    directory_publish_module._posix_remove_claimed_directory(  # noqa: SLF001
                        root,
                        claim.name,
                        identity,
                        recursive=False,
                    )
                except BaseException as exc:
                    caught = exc
            self.assertIsNotNone(caught)
            leaked: list[str] = []
            for role, descriptor in descriptors.items():
                try:
                    os.fstat(descriptor)
                except OSError:
                    continue
                leaked.append(role)
                real_close(descriptor)
            self.assertEqual([], leaked)
            assert caught is not None
            return caught

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "POSIX descriptor cleanup",
    )
    def test_posix_claim_close_failure_still_closes_parent_descriptor(self) -> None:
        raised = self._run_posix_close_failure({"claim"})
        self.assertIsInstance(raised, DirectoryPublishError)
        self.assertRegex(str(raised), "claim.*close failure")

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "POSIX descriptor cleanup",
    )
    def test_posix_parent_close_failure_has_deterministic_precedence(self) -> None:
        raised = self._run_posix_close_failure({"parent"})
        self.assertIsInstance(raised, DirectoryPublishError)
        self.assertRegex(str(raised), "parent.*close failure")

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "POSIX descriptor cleanup",
    )
    def test_posix_close_failures_are_notes_on_the_primary_error(self) -> None:
        primary = RuntimeError("primary deletion failure")
        raised = self._run_posix_close_failure(
            {"claim", "parent"},
            primary=primary,
        )
        self.assertIs(primary, raised)
        notes = getattr(primary, "__notes__", ())
        self.assertTrue(any("claim close failure" in note for note in notes))
        self.assertTrue(any("parent close failure" in note for note in notes))

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "real Linux descriptor cleanup capability",
    )
    def test_real_linux_unavailable_cleanup_preserves_original_journal_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for recursive in (False, True):
                with self.subTest(recursive=recursive):
                    original = root / ("recursive" if recursive else "empty")
                    original.mkdir()
                    if recursive:
                        (original / "owned.txt").write_text("owned\n", encoding="utf-8")
                    identity = directory_publish_module.directory_identity(
                        original,
                        context="journal-owned directory",
                    )

                    with self.assertRaisesRegex(DirectoryPublishError, "unavailable"):
                        if recursive:
                            directory_publish_module.quarantine_and_remove_owned_directory(
                                original,
                                identity,
                                verify=lambda _candidate: None,
                            )
                        else:
                            directory_publish_module.remove_owned_empty_directory(
                                original,
                                identity,
                            )

                    self.assertEqual(
                        identity,
                        directory_publish_module.directory_identity(
                            original,
                            context="preserved journal-owned directory",
                        ),
                    )
                    if recursive:
                        self.assertEqual(
                            "owned\n",
                            (original / "owned.txt").read_text(encoding="utf-8"),
                        )
                    self.assertEqual(
                        [original],
                        [path for path in root.iterdir() if path.name == original.name],
                    )
                    self.assertEqual(
                        [],
                        [
                            path
                            for path in root.iterdir()
                            if path.name.startswith(f".{original.name}.")
                        ],
                    )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "POSIX identity-bound cleanup seam",
    )
    def test_posix_mocked_supported_empty_cleanup_uses_original_descriptor(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = root / "created"
            created.mkdir()
            expected_identity = directory_publish_module.directory_identity(
                created,
                context="created directory",
            )

            with (
                patch.object(
                    directory_publish_module,
                    "_posix_unlink_descriptor_raw",
                    return_value=None,
                ) as unlink_descriptor,
                patch.object(
                    directory_publish_module,
                    "_verify_posix_descriptor_deleted",
                ) as verify_deleted,
            ):
                directory_publish_module.remove_owned_empty_directory(
                    created,
                    expected_identity,
                )

            unlink_descriptor.assert_called_once()
            self.assertTrue(unlink_descriptor.call_args.kwargs["directory"])
            verify_deleted.assert_called_once_with(unlink_descriptor.call_args.args[0])
            self.assertTrue(created.is_dir())
            self.assertEqual([], list(root.glob(".created.*")))

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "POSIX identity-bound cleanup seam",
    )
    def test_posix_mocked_supported_recursive_cleanup_preflights_before_children(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = root / "created"
            created.mkdir()
            (created / "owned.txt").write_text("owned\n", encoding="utf-8")
            expected_identity = directory_publish_module.directory_identity(
                created,
                context="created directory",
            )

            with (
                patch.object(
                    directory_publish_module,
                    "_posix_unlink_descriptor_raw",
                    side_effect=[
                        errno.ENOTEMPTY,
                        errno.EISDIR,
                        None,
                        None,
                    ],
                ) as unlink_descriptor,
                patch.object(
                    directory_publish_module,
                    "_verify_posix_descriptor_deleted",
                ) as verify_deleted,
            ):
                directory_publish_module.quarantine_and_remove_owned_directory(
                    created,
                    expected_identity,
                    verify=lambda _candidate: None,
                )

            self.assertEqual(
                [True, False, False, True],
                [call.kwargs["directory"] for call in unlink_descriptor.call_args_list],
            )
            self.assertEqual(2, verify_deleted.call_count)
            self.assertEqual("owned\n", (created / "owned.txt").read_text(encoding="utf-8"))
            self.assertEqual([], list(root.glob(".created.*")))

    def test_empty_cleanup_rejects_last_window_claim_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = root / "created"
            created.mkdir()
            expected_identity = directory_publish_module.directory_identity(
                created,
                context="created directory",
            )
            remove_claimed = directory_publish_module._remove_claimed_directory  # noqa: SLF001
            displaced = root / "owned-empty-claim"
            foreign_identity: tuple[int, int] | None = None

            def swap_before_guard(
                parent: Path,
                claim_name: str,
                identity: tuple[int, int],
                *,
                recursive: bool,
            ) -> None:
                nonlocal foreign_identity
                claimed = parent / claim_name
                claimed.rename(displaced)
                claimed.mkdir()
                foreign_identity = directory_publish_module.directory_identity(
                    claimed,
                    context="foreign empty replacement",
                )
                remove_claimed(
                    parent,
                    claim_name,
                    identity,
                    recursive=recursive,
                )

            with (
                patch.object(
                    directory_publish_module,
                    "_remove_claimed_directory",
                    side_effect=swap_before_guard,
                ),
                self.assertRaisesRegex(DirectoryPublishError, "identity"),
            ):
                directory_publish_module.remove_owned_empty_directory(
                    created,
                    expected_identity,
                )

            self.assertTrue(displaced.is_dir())
            self.assertIsNotNone(foreign_identity)
            remaining_identities = {
                directory_publish_module.directory_identity(
                    child,
                    context="remaining empty cleanup directory",
                )
                for child in root.iterdir()
                if child.is_dir()
            }
            self.assertIn(foreign_identity, remaining_identities)

    def test_recursive_cleanup_rejects_last_window_claim_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = root / "created"
            created.mkdir()
            (created / "owned.txt").write_text("owned\n", encoding="utf-8")
            expected_identity = directory_publish_module.directory_identity(
                created,
                context="created directory",
            )
            remove_claimed = directory_publish_module._remove_claimed_directory  # noqa: SLF001
            displaced = root / "owned-recursive-claim"
            foreign_claim: Path | None = None

            def swap_before_guard(
                parent: Path,
                claim_name: str,
                identity: tuple[int, int],
                *,
                recursive: bool,
            ) -> None:
                nonlocal foreign_claim
                claimed = parent / claim_name
                claimed.rename(displaced)
                claimed.mkdir()
                foreign_claim = claimed
                (claimed / "foreign.txt").write_text("foreign\n", encoding="utf-8")
                remove_claimed(
                    parent,
                    claim_name,
                    identity,
                    recursive=recursive,
                )

            with (
                patch.object(
                    directory_publish_module,
                    "_remove_claimed_directory",
                    side_effect=swap_before_guard,
                ),
                self.assertRaisesRegex(DirectoryPublishError, "identity"),
            ):
                directory_publish_module.quarantine_and_remove_owned_directory(
                    created,
                    expected_identity,
                    verify=lambda _candidate: None,
                )

            self.assertEqual("owned\n", (displaced / "owned.txt").read_text(encoding="utf-8"))
            assert foreign_claim is not None
            self.assertEqual(
                "foreign\n",
                (foreign_claim / "foreign.txt").read_text(encoding="utf-8"),
            )

    def test_recursive_claimed_cleanup_preserves_primary_error_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = root / "created"
            created.mkdir()
            (created / "owned.txt").write_text("owned\n", encoding="utf-8")
            expected_identity = directory_publish_module.directory_identity(
                created,
                context="created directory",
            )
            primary = RuntimeError("claimed cleanup primary")

            with (
                patch.object(
                    directory_publish_module,
                    "_remove_claimed_directory",
                    side_effect=primary,
                ),
                self.assertRaises(RuntimeError) as raised,
            ):
                directory_publish_module.quarantine_and_remove_owned_directory(
                    created,
                    expected_identity,
                    verify=lambda _candidate: None,
                )

            self.assertIs(primary, raised.exception)
            self.assertEqual("owned\n", (created / "owned.txt").read_text(encoding="utf-8"))
            self.assertEqual([], list(root.glob(".created.rollback-*")))

    def test_empty_directory_cleanup_claim_preserves_a_foreign_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = root / "created"
            created.mkdir()
            expected_identity = directory_publish_module.directory_identity(
                created,
                context="created directory",
            )
            displaced = root / "owned-before-cleanup-race"
            original_stat = directory_publish_module.path_file_stat
            foreign_identity: tuple[int, int] | None = None
            swapped = False

            def replace_after_identity(path: str | Path):
                nonlocal foreign_identity, swapped
                info = original_stat(path)
                if Path(path) == created and not swapped:
                    created.rename(displaced)
                    created.mkdir()
                    foreign = original_stat(created)
                    foreign_identity = (foreign.st_dev, foreign.st_ino)
                    swapped = True
                return info

            with (
                patch.object(
                    directory_publish_module,
                    "path_file_stat",
                    side_effect=replace_after_identity,
                ),
                self.assertRaisesRegex(
                    DirectoryPublishError,
                    "identity.*journal|identity.*changed",
                ),
            ):
                directory_publish_module.remove_owned_empty_directory(
                    created,
                    expected_identity,
                )

            self.assertTrue(swapped)
            self.assertTrue(displaced.is_dir())
            self.assertIsNotNone(foreign_identity)
            remaining_identities = {
                directory_publish_module.directory_identity(
                    child,
                    context="remaining cleanup directory",
                )
                for child in root.iterdir()
                if child.is_dir()
            }
            self.assertIn(foreign_identity, remaining_identities)

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

            linux_fail_closed = sys.platform.startswith("linux") and os.name == "posix"
            expected_error = (
                "staged cleanup could not complete"
                if linux_fail_closed
                else "destination already exists"
            )
            with (
                patch.object(
                    bundle_module,
                    "publish_directory_noreplace",
                    side_effect=race,
                ),
                self.assertRaisesRegex(BundleError, expected_error),
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
            stages = list(root.glob(".bundle.export-*"))
            self.assertEqual(1 if linux_fail_closed else 0, len(stages))

    def test_catalog_failure_rolls_back_only_the_owned_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            catalog = game / "game_data/worlds.lock.json"
            before = catalog.read_bytes()
            linux_fail_closed = sys.platform.startswith("linux") and os.name == "posix"
            expected_error = (
                "recovery could not complete" if linux_fail_closed else "injected catalog failure"
            )
            with (
                patch.object(
                    bundle_module,
                    "_write_catalog_atomic",
                    side_effect=OSError("injected catalog failure"),
                ),
                self.assertRaisesRegex(
                    BundleError if linux_fail_closed else OSError,
                    expected_error,
                ),
            ):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )

            self.assertEqual(before, catalog.read_bytes())
            destination = game / "game_data/worlds/modly_foundation/1.0.0"
            self.assertEqual(linux_fail_closed, destination.exists())
            self.assertEqual(linux_fail_closed, (game / IMPORT_JOURNAL).exists())
            self.assertEqual([], list((game / "game_data").rglob("*.import-*")))
            self.assertEqual([], list(destination.parent.glob(".1.0.0.rollback-*")))

    def test_interrupted_publication_is_recovered_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            destination = self._leave_published_crash(bundle, game)

            linux_fail_closed = sys.platform.startswith("linux") and os.name == "posix"
            if linux_fail_closed:
                with self.assertRaisesRegex(BundleError, "deletion is unavailable"):
                    import_runtime_bundle(
                        bundle.root,
                        game,
                        expected_bundle_hash=bundle.bundle_hash,
                    )
                self.assertTrue(destination.is_dir())
                self.assertTrue((game / IMPORT_JOURNAL).exists())
            else:
                recovered = import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )
                self.assertEqual(destination.resolve(), recovered.resolve())
                self.assertFalse((game / IMPORT_JOURNAL).exists())
            catalog = json.loads((game / "game_data/worlds.lock.json").read_text(encoding="utf-8"))
            self.assertEqual(0 if linux_fail_closed else 1, len(catalog["releases"]))
            self.assertEqual([], list(destination.parent.glob(".1.0.0.rollback-*")))

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
            linux_fail_closed = sys.platform.startswith("linux") and os.name == "posix"
            if linux_fail_closed:
                with self.assertRaisesRegex(BundleError, "deletion is unavailable"):
                    import_runtime_bundle(
                        bundle.root,
                        game,
                        expected_bundle_hash=bundle.bundle_hash,
                    )
                stage = game / journal["temporary"]
                self.assertTrue(stage.is_dir())
                self.assertEqual("partial\n", (stage / "partial.txt").read_text(encoding="utf-8"))
                self.assertTrue((game / IMPORT_JOURNAL).exists())
                self.assertEqual([], list(stage.parent.glob(f".{stage.name}.rollback-*")))
            else:
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
            linux_fail_closed = sys.platform.startswith("linux") and os.name == "posix"
            if linux_fail_closed:
                with self.assertRaisesRegex(BundleError, "deletion is unavailable"):
                    import_runtime_bundle(
                        bundle.root,
                        game,
                        expected_bundle_hash=bundle.bundle_hash,
                    )
                self.assertTrue((game / IMPORT_JOURNAL).exists())
                stages = list((game / "game_data/worlds/modly_foundation").glob(".*.import-*"))
                self.assertEqual(1, len(stages))
                self.assertEqual([], list(stages[0].parent.glob(f".{stages[0].name}.rollback-*")))
            else:
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
