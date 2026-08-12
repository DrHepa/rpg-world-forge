from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import isoworld.content.resource_snapshot as resource_snapshot_module
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


def _read_import_journal(path: Path) -> dict[str, object]:
    loaded = bundle_module._read_import_journal_record(path)  # noqa: SLF001
    assert loaded is not None
    return loaded[0]


class BundlePublicationTests(unittest.TestCase):
    def test_append_only_journal_recovers_after_a_partial_transition_tail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publication.journal"
            intent = b'{"state":"intent"}\n'
            copying = b'{"state":"copying"}\n'
            committed = b'{"state":"committed"}\n'
            identity = directory_publish_module.create_append_only_journal(
                path,
                intent,
                max_record_bytes=1024,
            )
            directory_publish_module.append_append_only_journal(
                path,
                expected_identity=identity,
                expected_payload=intent,
                updated_payload=copying,
                max_record_bytes=1024,
                max_file_bytes=16 * 1024,
            )
            partial = directory_publish_module._journal_frame(  # noqa: SLF001
                b'{"state":"ready"}\n'
            )
            with path.open("ab") as target:
                target.write(partial[: len(partial) // 2])
                target.flush()
                os.fsync(target.fileno())

            recovered = directory_publish_module.read_append_only_journal(
                path,
                max_record_bytes=1024,
                max_file_bytes=16 * 1024,
            )
            self.assertIsNotNone(recovered)
            assert recovered is not None
            self.assertEqual(copying, recovered[0])
            interrupted_bytes = path.read_bytes()

            with (
                patch.object(
                    directory_publish_module.os,
                    "replace",
                    side_effect=AssertionError("journal transition used pathname replace"),
                ),
                patch.object(
                    directory_publish_module.os,
                    "unlink",
                    side_effect=AssertionError("journal transition used pathname unlink"),
                ),
                self.assertRaisesRegex(
                    DirectoryPublishError,
                    "changed before transition",
                ),
            ):
                directory_publish_module.append_append_only_journal(
                    path,
                    expected_identity=identity,
                    expected_payload=copying,
                    updated_payload=committed,
                    max_record_bytes=1024,
                    max_file_bytes=16 * 1024,
                )

            terminal = directory_publish_module.read_append_only_journal(
                path,
                max_record_bytes=1024,
                max_file_bytes=16 * 1024,
            )
            self.assertIsNotNone(terminal)
            assert terminal is not None
            self.assertEqual(copying, terminal[0])
            self.assertEqual(identity, terminal[1])
            self.assertEqual(interrupted_bytes, path.read_bytes())

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
                    return_value=errno.ENOTEMPTY,
                ) as unlink_descriptor,
                patch.object(
                    directory_publish_module,
                    "_verify_posix_descriptor_deleted",
                ) as verify_deleted,
                self.assertRaisesRegex(
                    DirectoryPublishError,
                    "pre-recorded retained child snapshot",
                ),
            ):
                directory_publish_module.quarantine_and_remove_owned_directory(
                    created,
                    expected_identity,
                    verify=lambda _candidate: None,
                )

            unlink_descriptor.assert_called_once()
            self.assertTrue(unlink_descriptor.call_args.kwargs["directory"])
            verify_deleted.assert_not_called()
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

    def test_windows_cleanup_child_injection_preserves_original_journal_path(
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
            child = created / "injected.txt"
            verified: list[Path] = []

            def inject_after_root_handle(
                path: Path,
                identity: tuple[int, int],
                *,
                directory: bool,
            ) -> int:
                self.assertEqual(created, path)
                self.assertEqual(expected_identity, identity)
                self.assertTrue(directory)
                child.write_text("preserve injected evidence\n", encoding="utf-8")
                return 123

            with (
                patch.object(directory_publish_module.os, "name", "nt"),
                patch.object(
                    directory_publish_module,
                    "directory_identity",
                    return_value=expected_identity,
                ),
                patch.object(
                    directory_publish_module,
                    "_windows_open_delete_handle",
                    side_effect=inject_after_root_handle,
                ),
                patch.object(
                    directory_publish_module,
                    "_windows_close_cleanup_handle",
                ) as close_handle,
                patch.object(
                    directory_publish_module,
                    "_windows_mark_handle_for_deletion",
                ) as mark_for_deletion,
                patch.object(
                    directory_publish_module,
                    "publish_directory_noreplace",
                ) as rename_to_quarantine,
                self.assertRaisesRegex(
                    DirectoryPublishError,
                    "pre-recorded retained child snapshot",
                ),
            ):
                directory_publish_module.quarantine_and_remove_owned_directory(
                    created,
                    expected_identity,
                    verify=lambda candidate: verified.append(candidate),
                )

            self.assertEqual([created], verified)
            close_handle.assert_called_once_with(123)
            mark_for_deletion.assert_not_called()
            rename_to_quarantine.assert_not_called()
            self.assertTrue(created.is_dir())
            self.assertEqual(
                "preserve injected evidence\n",
                child.read_text(encoding="utf-8"),
            )
            self.assertEqual([], list(root.glob(".created.rollback-*")))

    def test_windows_retained_tree_closes_child_handle_when_initial_stat_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = root / "created"
            created.mkdir()
            child = created / "owned.txt"
            child.write_text("owned\n", encoding="utf-8")
            root_state = directory_publish_module.path_file_stat(created)

            def handle_stat(handle: int) -> object:
                if handle == 123:
                    return root_state
                raise OSError("simulated child handle stat failure")

            with (
                patch.object(
                    directory_publish_module.file_stat_module,
                    "_windows_handle_stat",
                    side_effect=handle_stat,
                ),
                patch.object(
                    directory_publish_module,
                    "_windows_open_delete_handle",
                    return_value=456,
                ),
                patch.object(
                    directory_publish_module,
                    "_windows_close_cleanup_handle",
                ) as close_handle,
                self.assertRaisesRegex(OSError, "child handle stat failure"),
            ):
                directory_publish_module._retain_windows_directory_tree(  # noqa: SLF001
                    created,
                    123,
                )

            close_handle.assert_called_once_with(456)

    def test_windows_retained_tree_closes_child_handle_when_registration_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = root / "created"
            created.mkdir()
            child = created / "owned.txt"
            child.write_text("owned\n", encoding="utf-8")
            states = {
                123: directory_publish_module.path_file_stat(created),
                456: directory_publish_module.path_file_stat(child),
            }

            with (
                patch.object(
                    directory_publish_module.file_stat_module,
                    "_windows_handle_stat",
                    side_effect=lambda handle: states[handle],
                ),
                patch.object(
                    directory_publish_module,
                    "_windows_open_delete_handle",
                    return_value=456,
                ),
                patch.object(
                    directory_publish_module,
                    "_register_retained_windows_entry",
                    create=True,
                    side_effect=MemoryError("simulated retained-list allocation failure"),
                ),
                patch.object(
                    directory_publish_module,
                    "_windows_close_cleanup_handle",
                ) as close_handle,
                self.assertRaisesRegex(MemoryError, "retained-list allocation failure"),
            ):
                directory_publish_module._retain_windows_directory_tree(  # noqa: SLF001
                    created,
                    123,
                )

            close_handle.assert_called_once_with(456)

    def test_windows_retained_tree_closes_registered_child_handle_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            created = root / "created"
            created.mkdir()
            child = created / "owned.txt"
            child.write_text("owned\n", encoding="utf-8")
            states = {
                123: directory_publish_module.path_file_stat(created),
                456: directory_publish_module.path_file_stat(child),
            }

            def append_then_interrupt(retained: list[object], entry: object) -> None:
                retained.append(entry)
                raise MemoryError("simulated interruption after retained-list registration")

            with (
                patch.object(
                    directory_publish_module.file_stat_module,
                    "_windows_handle_stat",
                    side_effect=lambda handle: states[handle],
                ),
                patch.object(
                    directory_publish_module,
                    "_windows_open_delete_handle",
                    return_value=456,
                ),
                patch.object(
                    directory_publish_module,
                    "_register_retained_windows_entry",
                    create=True,
                    side_effect=append_then_interrupt,
                ),
                patch.object(
                    directory_publish_module,
                    "_windows_close_cleanup_handle",
                ) as close_handle,
                self.assertRaisesRegex(MemoryError, "after retained-list registration"),
            ):
                directory_publish_module._retain_windows_directory_tree(  # noqa: SLF001
                    created,
                    123,
                )

            close_handle.assert_called_once_with(456)

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

    def test_windows_existing_destination_fails_before_native_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            destination = root / "destination"
            destination.mkdir()
            with (
                patch.object(directory_publish_module.sys, "platform", "win32"),
                patch.object(directory_publish_module.os, "name", "nt"),
                patch.object(
                    directory_publish_module,
                    "directory_identity",
                    return_value=(1, 2),
                ),
                self.assertRaises(FileExistsError),
            ):
                with directory_publish_module.publish_directory_noreplace(
                    source,
                    destination,
                    expected_source_identity=(1, 2),
                ):
                    pass
            self.assertTrue(source.is_dir())
            self.assertTrue(destination.is_dir())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux retained-FD publication",
    )
    def test_final_claim_source_replacement_and_destination_collision_touch_neither(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "owned.txt").write_text("owned\n", encoding="utf-8")
            displaced = root / "owned-source"
            foreign = root / "foreign"
            foreign.mkdir()
            (foreign / "foreign.txt").write_text("foreign\n", encoding="utf-8")
            destination = root / "destination"
            destination.mkdir()
            (destination / "public.txt").write_text("public\n", encoding="utf-8")
            expected = directory_publish_module.directory_identity(
                source,
                context="recorded publication source",
            )
            require_binding = directory_publish_module.RetainedDirectory.require_binding
            calls = 0

            def replace_before_rename(retained: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    source.rename(displaced)
                    foreign.rename(source)
                require_binding(retained)

            with (
                patch.object(
                    directory_publish_module.RetainedDirectory,
                    "require_binding",
                    new=replace_before_rename,
                ),
                self.assertRaisesRegex(DirectoryPublishError, "binding|identity"),
            ):
                with publish_directory_noreplace(
                    source,
                    destination,
                    expected_source_identity=expected,
                ):
                    pass

            self.assertEqual(
                "foreign\n",
                (source / "foreign.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "owned\n",
                (displaced / "owned.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                ["public.txt"],
                sorted(path.name for path in destination.iterdir()),
            )
            self.assertEqual(
                "public\n",
                (destination / "public.txt").read_text(encoding="utf-8"),
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux retained-FD publication",
    )
    def test_source_replacement_after_retention_fails_without_moving_either_tree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "owned.txt").write_text("owned\n", encoding="utf-8")
            displaced = root / "owned-source"
            foreign = root / "foreign"
            foreign.mkdir()
            (foreign / "foreign.txt").write_text("foreign\n", encoding="utf-8")
            destination = root / "destination"
            expected = directory_publish_module.directory_identity(
                source,
                context="recorded publication source",
            )
            require_binding = directory_publish_module.RetainedDirectory.require_binding
            calls = 0

            def replace_before_rename(retained: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    source.rename(displaced)
                    foreign.rename(source)
                require_binding(retained)

            with (
                patch.object(
                    directory_publish_module.RetainedDirectory,
                    "require_binding",
                    new=replace_before_rename,
                ),
                self.assertRaisesRegex(DirectoryPublishError, "binding|identity"),
            ):
                with publish_directory_noreplace(
                    source,
                    destination,
                    expected_source_identity=expected,
                ):
                    pass

            self.assertEqual(
                "foreign\n",
                (source / "foreign.txt").read_text(encoding="utf-8"),
            )
            self.assertFalse(destination.exists())
            self.assertEqual(
                "owned\n",
                (displaced / "owned.txt").read_text(encoding="utf-8"),
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux retained-FD publication",
    )
    def test_recorded_source_replacement_before_open_fails_without_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "owned.txt").write_text("owned\n", encoding="utf-8")
            expected = directory_publish_module.directory_identity(
                source,
                context="recorded publication source",
            )
            displaced = root / "owned-source"
            source.rename(displaced)
            source.mkdir()
            (source / "foreign.txt").write_text("foreign\n", encoding="utf-8")
            destination = root / "destination"

            with self.assertRaisesRegex(
                DirectoryPublishError,
                "identity changed",
            ):
                with publish_directory_noreplace(
                    source,
                    destination,
                    expected_source_identity=expected,
                ):
                    pass

            self.assertEqual(
                "foreign\n",
                (source / "foreign.txt").read_text(encoding="utf-8"),
            )
            self.assertFalse(destination.exists())
            self.assertEqual(
                "owned\n",
                (displaced / "owned.txt").read_text(encoding="utf-8"),
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux retained-directory descriptors",
    )
    def test_linux_rename_then_raise_is_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            (source / "payload.txt").write_text("payload\n", encoding="utf-8")
            source_identity = directory_publish_module.directory_identity(
                source,
                context="Linux rename interruption source",
            )

            class RenameThenRaise:
                argtypes: object = None
                restype: object = None

                def __call__(
                    self,
                    source_parent: int,
                    source_name: bytes,
                    destination_parent: int,
                    destination_name: bytes,
                    _flags: int,
                ) -> int:
                    os.rename(
                        source_name,
                        destination_name,
                        src_dir_fd=source_parent,
                        dst_dir_fd=destination_parent,
                    )
                    raise KeyboardInterrupt("injected post-rename Linux interruption")

            class FakeLibc:
                renameat2 = RenameThenRaise()

            with (
                patch.object(
                    directory_publish_module.ctypes,
                    "CDLL",
                    return_value=FakeLibc(),
                ),
                self.assertRaisesRegex(
                    directory_publish_module.DirectoryPublishIndeterminateError,
                    "renameat2 raised",
                ) as caught,
            ):
                with publish_directory_noreplace(
                    source,
                    destination,
                    expected_source_identity=source_identity,
                ):
                    pass

            self.assertIsInstance(caught.exception.__cause__, KeyboardInterrupt)
            self.assertFalse(source.exists())
            self.assertEqual(
                "payload\n",
                (destination / "payload.txt").read_text(encoding="utf-8"),
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux retained-directory descriptors",
    )
    def test_linux_returned_collision_remains_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            (source / "payload.txt").write_text("payload\n", encoding="utf-8")
            source_identity = directory_publish_module.directory_identity(
                source,
                context="Linux deterministic collision source",
            )

            class Collision:
                argtypes: object = None
                restype: object = None

                def __call__(
                    self,
                    _source_parent: int,
                    _source_name: bytes,
                    destination_parent: int,
                    destination_name: bytes,
                    _flags: int,
                ) -> int:
                    os.mkdir(destination_name, dir_fd=destination_parent)
                    (destination / "foreign.txt").write_text(
                        "foreign\n",
                        encoding="utf-8",
                    )
                    directory_publish_module.ctypes.set_errno(errno.EEXIST)
                    return -1

            class FakeLibc:
                renameat2 = Collision()

            with (
                patch.object(
                    directory_publish_module.ctypes,
                    "CDLL",
                    return_value=FakeLibc(),
                ),
                self.assertRaises(FileExistsError),
            ):
                with publish_directory_noreplace(
                    source,
                    destination,
                    expected_source_identity=source_identity,
                ):
                    pass

            self.assertTrue(source.is_dir())
            self.assertEqual(
                "payload\n",
                (source / "payload.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "foreign\n",
                (destination / "foreign.txt").read_text(encoding="utf-8"),
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux retained-directory descriptors",
    )
    def test_linux_post_rename_lease_close_failure_is_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "destination"
            source.mkdir()
            (source / "payload.txt").write_text("payload\n", encoding="utf-8")
            source_identity = directory_publish_module.directory_identity(
                source,
                context="Linux close failure source",
            )
            close_descriptors = directory_publish_module._close_descriptors  # noqa: SLF001

            def close_then_fail(descriptors: tuple[tuple[int, str], ...]) -> None:
                close_descriptors(descriptors)
                raise DirectoryPublishError("injected retained descriptor close failure")

            with (
                patch.object(
                    directory_publish_module,
                    "_close_descriptors",
                    side_effect=close_then_fail,
                ),
                self.assertRaisesRegex(
                    directory_publish_module.DirectoryPublishIndeterminateError,
                    "indeterminate after RENAME_NOREPLACE",
                ) as caught,
            ):
                with publish_directory_noreplace(
                    source,
                    destination,
                    expected_source_identity=source_identity,
                ):
                    pass

            self.assertIsInstance(caught.exception.__cause__, DirectoryPublishError)
            self.assertFalse(source.exists())
            self.assertEqual(
                "payload\n",
                (destination / "payload.txt").read_text(encoding="utf-8"),
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux retained-directory descriptors",
    )
    def test_retained_source_setup_failure_closes_every_opened_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            opened: list[int] = []
            close_descriptors = directory_publish_module._close_descriptors  # noqa: SLF001

            def tracked_close(descriptors: tuple[tuple[int, str], ...]) -> None:
                opened.extend(descriptor for descriptor, _context in descriptors)
                close_descriptors(descriptors)

            with (
                patch.object(
                    directory_publish_module,
                    "_close_descriptors",
                    side_effect=tracked_close,
                ),
                self.assertRaisesRegex(DirectoryPublishError, "identity changed"),
            ):
                with directory_publish_module.open_expected_directory(source, (-1, -1)):
                    self.fail("identity mismatch must fail before yield")

            self.assertGreaterEqual(len(opened), 2)
            for descriptor in opened:
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux retained-directory descriptors",
    )
    def test_destination_collision_setup_closes_parent_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "destination"
            destination.mkdir()
            opened: list[int] = []
            close_descriptors = directory_publish_module._close_descriptors  # noqa: SLF001

            def tracked_close(descriptors: tuple[tuple[int, str], ...]) -> None:
                opened.extend(descriptor for descriptor, _context in descriptors)
                close_descriptors(descriptors)

            with (
                patch.object(
                    directory_publish_module,
                    "_close_descriptors",
                    side_effect=tracked_close,
                ),
                self.assertRaises(FileExistsError),
            ):
                with directory_publish_module.claim_directory_noreplace(destination):
                    self.fail("destination collision must fail before yield")

            self.assertEqual(1, len(opened))
            with self.assertRaises(OSError):
                os.fstat(opened[0])

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

    def test_successful_linux_export_leaves_no_sibling_source_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, _game = self._bundle_and_game(root)
            try:
                self.assertEqual([], list(root.glob(".bundle.export-*")))
            finally:
                bundle.close()

    @unittest.skipUnless(
        sys.platform == "win32" and os.name == "nt",
        "native Windows publication seal sharing",
    )
    def test_native_windows_bundle_verifier_reads_while_seal_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _write_fixture(root / "fixture")
            bundle = export_runtime_bundle(
                worldpack,
                renderpack,
                root / "bundle",
                release_id="1.0.0",
                licenses_directory=licenses,
            )
            try:
                self.assertEqual(
                    bundle.bundle_hash,
                    bundle.manifest["bundle_hash"],
                )
                self.assertTrue((bundle.root / "bundle.manifest.json").is_file())
            finally:
                bundle.close()

    def test_windows_bundle_durability_uses_retained_handle_tree(self) -> None:
        root = Path("/synthetic/windows/stage")
        identity = (41, 503)
        expected = ("bundle.json", "assets/example.bin")
        with (
            patch.object(bundle_module.os, "name", "nt"),
            patch.object(
                bundle_module,
                "open_expected_directory",
                side_effect=AssertionError("Windows durability used POSIX descriptors"),
            ),
            patch.object(
                bundle_module,
                "flush_windows_directory_tree",
                return_value=expected,
            ) as flush,
        ):
            result = bundle_module._durably_flush_bundle_payload_tree(  # noqa: SLF001
                root,
                identity,
            )

        self.assertEqual(expected, result)
        flush.assert_called_once_with(
            root,
            expected_source_identity=identity,
        )

    def test_windows_seam_bundle_verifier_reads_with_seal_handles_active(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _write_fixture(root / "fixture")
            stage = root / "stage"
            destination = root / "bundle"
            stage.mkdir()
            manifest = bundle_module._populate_runtime_bundle(  # noqa: SLF001
                worldpack,
                renderpack,
                licenses,
                stage,
                "1.0.0",
            )
            stage_identity = directory_publish_module.directory_identity(
                stage,
                context="Windows verifier seam stage",
            )
            parent_identity = directory_publish_module.directory_identity(
                root,
                context="Windows verifier seam parent",
            )
            real_path_file_stat = directory_publish_module.path_file_stat
            real_windows_handle_stat = (
                directory_publish_module.file_stat_module._windows_handle_stat  # noqa: SLF001
            )
            real_file_stat_module = directory_publish_module.file_stat_module
            real_snapshot_ctypes = resource_snapshot_module.ctypes
            real_snapshot_path_file_stat = resource_snapshot_module.path_file_stat
            real_snapshot_descriptor_file_stat = resource_snapshot_module.descriptor_file_stat
            real_snapshot_open_source_descriptor = resource_snapshot_module._open_source_descriptor
            states = {
                root: real_path_file_stat(root),
                stage: real_path_file_stat(stage),
            }
            states.update({path: real_path_file_stat(path) for path in stage.rglob("*")})
            states.update(
                {
                    destination / path.relative_to(stage): state
                    for path, state in tuple(states.items())
                    if path == stage or stage in path.parents
                }
            )
            handles: dict[int, Path] = {}
            source_handle: int | None = None
            create_calls: list[tuple[object, ...]] = []
            fake_handle_stat_calls: list[int] = []
            forced_handle_stat_calls: list[int] = []
            native_fallback_calls: list[int] = []
            forced_native_handle = -0xC112
            forced_native_probe_pending = True

            class WindowsCall:
                argtypes: object = None
                restype: object = None

                def __init__(self, result: int) -> None:
                    self.result = result

                def __call__(self, *_args: object) -> int:
                    return self.result

            class CreateFile:
                argtypes: object = None
                restype: object = None

                def __call__(self, *args: object) -> int:
                    nonlocal source_handle
                    create_calls.append(args)
                    handle = -(0x1000 + len(handles))
                    path = Path(str(args[0]))
                    handles[handle] = path
                    if path == stage and source_handle is None:
                        source_handle = handle
                    return handle

            class Rename:
                argtypes: object = None
                restype: object = None

                def __call__(self, *_args: object) -> int:
                    stage.rename(destination)
                    return 1

            def load_dll(name: str, **_kwargs: object) -> object:
                if name == "kernel32":
                    return SimpleNamespace(
                        CreateFileW=CreateFile(),
                        FlushFileBuffers=WindowsCall(1),
                        CloseHandle=WindowsCall(1),
                    )
                if name == "ntdll":
                    return SimpleNamespace(
                        NtSetInformationFile=Rename(),
                        RtlNtStatusToDosError=WindowsCall(5),
                    )
                raise OSError(f"unexpected DLL: {name}")

            class CtypesProxy:
                def __init__(self, real_ctypes: object) -> None:
                    self._real_ctypes = real_ctypes

                def WinDLL(self, name: str, **kwargs: object) -> object:  # noqa: N802
                    return load_dll(name, **kwargs)

                def __getattr__(self, name: str) -> object:
                    return getattr(self._real_ctypes, name)

            class FileStatProxy:
                def __init__(self, real_module: object) -> None:
                    self._real_module = real_module

                def _windows_handle_stat(self, handle: int) -> object:
                    return handle_stat(handle)

                def __getattr__(self, name: str) -> object:
                    return getattr(self._real_module, name)

            def handle_stat(handle: int):
                if handle == forced_native_handle:
                    forced_handle_stat_calls.append(handle)
                    return states[root]
                path = handles.get(handle)
                if path is None:
                    native_fallback_calls.append(handle)
                    return real_windows_handle_stat(handle)
                fake_handle_stat_calls.append(handle)
                if handle == source_handle and not stage.exists():
                    path = destination
                return states[path]

            def audited_stat(path: str | Path):
                candidate = Path(path)
                if candidate == stage and not stage.exists():
                    raise FileNotFoundError(candidate)
                return states[candidate]

            media_signature_matches = bundle_module.media_signature_matches

            def media_signature_with_forced_native_handle(
                path: str | Path,
                media_type: str,
            ) -> bool:
                nonlocal forced_native_probe_pending
                candidate = Path(path)
                if forced_native_probe_pending and destination in candidate.parents:
                    forced_native_probe_pending = False
                    self.assertEqual(
                        states[root],
                        directory_publish_module.file_stat_module._windows_handle_stat(  # noqa: SLF001
                            forced_native_handle
                        ),
                    )
                return media_signature_matches(path, media_type)

            fake_ctypes = CtypesProxy(directory_publish_module.ctypes)
            fake_file_stat_module = FileStatProxy(real_file_stat_module)
            with (
                patch.object(
                    directory_publish_module,
                    "path_file_stat",
                    side_effect=audited_stat,
                ),
                patch.object(
                    directory_publish_module,
                    "ctypes",
                    fake_ctypes,
                ),
                patch.object(
                    directory_publish_module,
                    "file_stat_module",
                    fake_file_stat_module,
                ),
                patch.object(
                    bundle_module,
                    "media_signature_matches",
                    side_effect=media_signature_with_forced_native_handle,
                ),
            ):
                self.assertIs(
                    real_file_stat_module._windows_handle_stat,  # noqa: SLF001
                    real_windows_handle_stat,
                )
                self.assertIs(resource_snapshot_module.ctypes, real_snapshot_ctypes)
                self.assertIs(
                    resource_snapshot_module.path_file_stat,
                    real_snapshot_path_file_stat,
                )
                self.assertIs(
                    resource_snapshot_module.descriptor_file_stat,
                    real_snapshot_descriptor_file_stat,
                )
                self.assertIs(
                    resource_snapshot_module._open_source_descriptor,
                    real_snapshot_open_source_descriptor,
                )
                self.assertIs(directory_publish_module.ctypes, fake_ctypes)
                self.assertIs(
                    directory_publish_module.file_stat_module,
                    fake_file_stat_module,
                )
                self.assertIsNot(resource_snapshot_module.ctypes, fake_ctypes)
                self.assertIsNot(
                    getattr(resource_snapshot_module.ctypes, "WinDLL", None),
                    fake_ctypes.WinDLL,
                )
                with directory_publish_module._windows_rename_noreplace(  # noqa: SLF001
                    stage,
                    destination,
                    source_identity=stage_identity,
                    parent_identity=parent_identity,
                ):
                    with bundle_module.verify_runtime_bundle(
                        destination,
                        expected_bundle_hash=manifest["bundle_hash"],
                    ) as verified:
                        self.assertEqual(manifest["bundle_hash"], verified.bundle_hash)

            self.assertFalse(forced_native_probe_pending)
            self.assertEqual([forced_native_handle], forced_handle_stat_calls)
            self.assertEqual([], native_fallback_calls)
            self.assertGreater(len(fake_handle_stat_calls), 0)
            self.assertTrue(all(handle in handles for handle in fake_handle_stat_calls))
            self.assertTrue(all(handle < -1 for handle in fake_handle_stat_calls))
            post_seal_calls = [
                call for call in create_calls if destination in Path(str(call[0])).parents
            ]
            self.assertGreater(len(post_seal_calls), 0)
            self.assertTrue(all(call[1] == 0x00100081 for call in post_seal_calls))
            self.assertTrue(all(call[2] == 0x00000001 for call in post_seal_calls))

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux direct-claim payload durability",
    )
    def test_linux_export_fsyncs_each_payload_before_claim_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _write_fixture(root / "fixture")
            destination = root / "bundle"
            flushed: list[str] = []
            fsync_payload = bundle_module._fsync_regular_payload  # noqa: SLF001

            def record_payload(descriptor: int, relative: str) -> None:
                fsync_payload(descriptor, relative)
                flushed.append(relative)

            with patch.object(
                bundle_module,
                "_fsync_regular_payload",
                side_effect=record_payload,
            ):
                bundle = export_runtime_bundle(
                    worldpack,
                    renderpack,
                    destination,
                    release_id="1.0.0",
                    licenses_directory=licenses,
                )

            try:
                expected = tuple(
                    sorted(
                        {
                            bundle_module.BUNDLE_MANIFEST,
                            *(record["path"] for record in bundle.manifest["files"]),
                        }
                    )
                )
                self.assertEqual(expected, tuple(flushed))
                self.assertEqual(len(expected), len(set(flushed)))
            finally:
                bundle.close()

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux direct-claim payload inventory",
    )
    def test_linux_export_accepts_portable_file_directory_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _write_fixture(root / "fixture")
            (licenses / "foo.txt").write_text("top-level notice\n", encoding="utf-8")
            (licenses / "foo").mkdir()
            (licenses / "foo/bar.txt").write_text("nested notice\n", encoding="utf-8")

            bundle = export_runtime_bundle(
                worldpack,
                renderpack,
                root / "bundle",
                release_id="1.0.0",
                licenses_directory=licenses,
            )
            try:
                license_paths = {record["path"] for record in bundle.manifest["licenses"]}
                self.assertIn("licenses/foo.txt", license_paths)
                self.assertIn("licenses/foo/bar.txt", license_paths)
            finally:
                bundle.close()

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux direct-claim cleanup semantics",
    )
    def test_linux_directory_publish_error_preserves_private_stage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _write_fixture(root / "fixture")
            destination = root / "bundle"
            with (
                patch.object(
                    bundle_module,
                    "publish_directory_noreplace",
                    side_effect=DirectoryPublishError("injected publish failure"),
                ),
                self.assertRaisesRegex(BundleError, "injected publish failure") as raised,
            ):
                export_runtime_bundle(
                    worldpack,
                    renderpack,
                    destination,
                    release_id="1.0.0",
                    licenses_directory=licenses,
                )

            notes = "\n".join(getattr(raised.exception, "__notes__", ()))
            self.assertIn("private bundle export stage retained", notes)
            self.assertFalse(destination.exists())
            self.assertEqual(1, len(list(root.glob(".bundle.export-*"))))

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux direct-claim payload durability",
    )
    def test_linux_payload_fsync_failure_retains_exact_recovery_destination(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worldpack, renderpack, licenses = _write_fixture(root / "fixture")
            destination = root / "bundle"
            fsync_payload = bundle_module._fsync_regular_payload  # noqa: SLF001
            injected = False

            def fail_first_regular_file(descriptor: int, relative: str) -> None:
                nonlocal injected
                if not injected:
                    injected = True
                    with patch.object(
                        bundle_module.os,
                        "fsync",
                        side_effect=OSError(
                            errno.EIO,
                            "injected payload fsync failure",
                        ),
                    ):
                        fsync_payload(descriptor, relative)
                    return
                fsync_payload(descriptor, relative)

            with (
                patch.object(
                    bundle_module,
                    "_fsync_regular_payload",
                    side_effect=fail_first_regular_file,
                ),
                self.assertRaisesRegex(
                    BundleError,
                    "durably flush bundle payload",
                ) as raised,
            ):
                export_runtime_bundle(
                    worldpack,
                    renderpack,
                    destination,
                    release_id="1.0.0",
                    licenses_directory=licenses,
                )

            self.assertTrue(injected)
            self.assertFalse(destination.exists())
            stages = list(root.glob(".bundle.export-*"))
            self.assertEqual(1, len(stages))
            self.assertTrue((stages[0] / bundle_module.BUNDLE_MANIFEST).is_file())
            self.assertIn(
                "private bundle export stage retained",
                "\n".join(getattr(raised.exception, "__notes__", ())),
            )

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

            def race(
                source: Path,
                target: Path,
                **kwargs: object,
            ) -> tuple[int, int]:
                if target.resolve(strict=False) == canonical_destination:
                    target.mkdir()
                    (target / "concurrent.txt").write_text(
                        "preserve me\n",
                        encoding="utf-8",
                    )
                return original_publish(source, target, **kwargs)

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
            stages = list(root.glob(".bundle.export-*"))
            self.assertEqual(1, len(stages))

    def test_catalog_failure_rolls_back_only_the_owned_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            catalog = game / "game_data/worlds.lock.json"
            before = catalog.read_bytes()
            primary = OSError("injected catalog failure")
            with (
                patch.object(
                    bundle_module,
                    "_write_catalog_atomic",
                    side_effect=primary,
                ),
                self.assertRaisesRegex(OSError, "injected catalog failure") as raised,
            ):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )

            self.assertIs(primary, raised.exception)
            self.assertEqual(before, catalog.read_bytes())
            destination = game / "game_data/worlds/modly_foundation/1.0.0"
            self.assertTrue(destination.is_dir())
            self.assertTrue((game / IMPORT_JOURNAL).is_file())
            stages = list((game / "game_data").rglob("*.import-*"))
            self.assertEqual(0, len(stages))
            self.assertEqual([], list(destination.parent.glob(".1.0.0.rollback-*")))
            self.assertIn(
                "catalog roll-forward on retry",
                "\n".join(getattr(raised.exception, "__notes__", ())),
            )

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
            self.assertEqual(
                "committed",
                _read_import_journal(game / IMPORT_JOURNAL)["state"],
            )
            catalog = json.loads((game / "game_data/worlds.lock.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(catalog["releases"]))
            self.assertEqual([], list(destination.parent.glob(".1.0.0.rollback-*")))

    def test_copying_destination_is_promoted_after_post_copy_process_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            game = game.resolve(strict=True)
            replace_journal = bundle_module._replace_import_journal

            def interrupt_ready(
                game_root: Path,
                current: dict[str, object],
                updated: dict[str, object],
            ) -> None:
                if current["state"] == "copying" and updated["state"] == "ready":
                    raise KeyboardInterrupt("injected post-copy process loss")
                replace_journal(game_root, current, updated)

            with (
                patch.object(
                    bundle_module,
                    "_replace_import_journal",
                    side_effect=interrupt_ready,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "post-copy process loss"),
            ):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )

            journal = _read_import_journal(game / IMPORT_JOURNAL)
            self.assertEqual("copying", journal["state"])
            destination = game / journal["destination"]
            temporary = game / journal["temporary"]
            self.assertFalse(destination.exists())
            self.assertTrue(temporary.is_dir())
            recovered = import_runtime_bundle(
                bundle.root,
                game,
                expected_bundle_hash=bundle.bundle_hash,
            )
            self.assertEqual(destination, recovered)
            self.assertEqual(
                "committed",
                _read_import_journal(game / IMPORT_JOURNAL)["state"],
            )

    def test_import_journal_transition_never_replaces_foreign_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "game_data").mkdir()
            path = root / IMPORT_JOURNAL
            current = {"state": "copying"}
            updated = {"state": "ready"}
            path.write_bytes(bundle_module._pretty_json(current))
            owned = path.with_name("owned-journal.json")
            foreign = b'{"foreign":true}\n'
            real_lseek = os.lseek
            swapped = False
            swap_blocked = False

            def swap_before_final_check(
                descriptor: int,
                offset: int,
                whence: int,
            ) -> int:
                nonlocal swap_blocked, swapped
                if not swapped:
                    swapped = True
                    try:
                        path.rename(owned)
                    except OSError as exc:
                        if getattr(exc, "winerror", None) != 32:
                            raise
                        swap_blocked = True
                        raise OSError(
                            "path binding changed because Windows retained the journal"
                        ) from exc
                    path.write_bytes(foreign)
                return real_lseek(descriptor, offset, whence)

            with (
                patch.object(
                    bundle_module.os,
                    "lseek",
                    side_effect=swap_before_final_check,
                ),
                self.assertRaisesRegex(
                    BundleError,
                    "changed before state transition|path binding changed",
                ),
            ):
                bundle_module._replace_import_journal(root, current, updated)

            self.assertTrue(swapped)
            if swap_blocked:
                self.assertFalse(owned.exists())
                self.assertEqual(bundle_module._pretty_json(current), path.read_bytes())
            else:
                self.assertEqual(foreign, path.read_bytes())
                self.assertEqual(bundle_module._pretty_json(current), owned.read_bytes())

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
            journal = _read_import_journal(game / IMPORT_JOURNAL)
            temporary = game / journal["temporary"]
            self.assertFalse(temporary.exists())

            with self.assertRaisesRegex(
                BundleError,
                "identity.*matches|no longer matches|identity changed",
            ):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )

            self.assertEqual("do not delete\n", marker.read_text(encoding="utf-8"))
            self.assertTrue(owned.is_dir())
            self.assertFalse(temporary.exists())
            self.assertTrue((game / IMPORT_JOURNAL).exists())

    def test_import_preserves_a_concurrent_destination_and_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            original_publish = bundle_module.publish_directory_noreplace

            def race(
                source: Path,
                target: Path,
                **kwargs: object,
            ) -> tuple[int, int]:
                if target.name == "1.0.0":
                    target.mkdir()
                    (target / "concurrent.txt").write_text(
                        "preserve me\n",
                        encoding="utf-8",
                    )
                return original_publish(source, target, **kwargs)

            with (
                patch.object(
                    bundle_module,
                    "publish_directory_noreplace",
                    side_effect=race,
                ),
                self.assertRaisesRegex(BundleError, "Import destination already exists") as raised,
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
            self.assertEqual(
                1,
                len(list(destination.parent.glob(".1.0.0.import-*"))),
            )
            self.assertIn(
                "both staged and published directories",
                "\n".join(getattr(raised.exception, "__notes__", ())),
            )

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

            journal = _read_import_journal(game / IMPORT_JOURNAL)
            self.assertEqual("copying", journal["state"])
            destination = game / journal["destination"]
            temporary = game / journal["temporary"]
            with self.assertRaisesRegex(
                BundleError,
                "missing bundle.manifest.json|tree mismatch",
            ):
                import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )
            self.assertFalse(destination.exists())
            self.assertTrue(temporary.is_dir())
            self.assertEqual(
                "partial\n",
                (temporary / "partial.txt").read_text(encoding="utf-8"),
            )
            self.assertTrue((game / IMPORT_JOURNAL).exists())

    def test_interrupted_prepublish_stage_is_recovered_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            original_publish = bundle_module.publish_directory_noreplace

            def interrupt(
                source: Path,
                target: Path,
                **kwargs: object,
            ) -> tuple[int, int]:
                if target.name == "1.0.0":
                    raise KeyboardInterrupt("simulated prepublish process loss")
                return original_publish(source, target, **kwargs)

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
                1,
                len(list((game / "game_data/worlds/modly_foundation").glob(".*.import-*"))),
            )
            recovered = import_runtime_bundle(
                bundle.root,
                game,
                expected_bundle_hash=bundle.bundle_hash,
            )
            self.assertTrue(recovered.is_dir())
            self.assertEqual(
                "committed",
                _read_import_journal(game / IMPORT_JOURNAL)["state"],
            )

    def test_legacy_import_persists_empty_intent_before_creating_ancestors(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            write_journal = bundle_module._write_import_journal  # noqa: SLF001
            flush_directory = bundle_module.fsync_directory
            events: list[str] = []

            def record_flush(path: Path, *, context: str) -> None:
                events.append(context)
                flush_directory(path, context=context)

            def assert_intent_precedes_directories(
                path: Path,
                journal: dict[str, object],
            ) -> None:
                self.assertEqual("intent", journal["state"])
                self.assertEqual([], journal["created_directories"])
                self.assertFalse((game / "game_data/worlds").exists())
                write_journal(path, journal)
                self.assertTrue(path.is_file())

            try:
                with (
                    patch.object(
                        bundle_module,
                        "fsync_directory",
                        side_effect=record_flush,
                    ),
                    patch.object(
                        bundle_module,
                        "_write_import_journal",
                        side_effect=assert_intent_precedes_directories,
                    ),
                ):
                    imported = import_runtime_bundle(
                        bundle.root,
                        game,
                        expected_bundle_hash=bundle.bundle_hash,
                    )

                self.assertTrue(imported.is_dir())
                self.assertLess(
                    events.index("bundle import journal parent"),
                    events.index("created bundle import ancestor"),
                )
                committed = _read_import_journal(game / IMPORT_JOURNAL)
                self.assertEqual("committed", committed["state"])
                self.assertTrue(committed["created_directories"])
            finally:
                bundle.close()

    def test_legacy_intent_recovery_accepts_only_exact_empty_derived_ancestors(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            game = game.resolve(strict=True)
            flush_directory = bundle_module.fsync_directory
            injected = False

            def interrupt_first_ancestor_parent(path: Path, *, context: str) -> None:
                nonlocal injected
                if (
                    not injected
                    and context == "bundle import ancestor parent"
                    and path == game / "game_data"
                ):
                    injected = True
                    raise OSError("injected empty ancestor interruption")
                flush_directory(path, context=context)

            try:
                with (
                    patch.object(
                        bundle_module,
                        "fsync_directory",
                        side_effect=interrupt_first_ancestor_parent,
                    ),
                    self.assertRaisesRegex(
                        OSError,
                        "empty ancestor interruption",
                    ),
                ):
                    import_runtime_bundle(
                        bundle.root,
                        game,
                        expected_bundle_hash=bundle.bundle_hash,
                    )

                self.assertTrue(injected)
                worlds = game / "game_data/worlds"
                self.assertTrue(worlds.is_dir())
                self.assertEqual([], list(worlds.iterdir()))
                self.assertEqual(
                    "committed",
                    _read_import_journal(game / IMPORT_JOURNAL)["state"],
                )

                imported = import_runtime_bundle(
                    bundle.root,
                    game,
                    expected_bundle_hash=bundle.bundle_hash,
                )
                self.assertTrue(imported.is_dir())
                self.assertEqual(
                    [],
                    bundle_module._audit_game_repository_for_import(game),  # noqa: SLF001
                )
            finally:
                bundle.close()

    def test_legacy_intent_recovery_preserves_nonempty_derived_ancestor_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            game = game.resolve(strict=True)
            world_root = game / "game_data/worlds/modly_foundation"
            flush_directory = bundle_module.fsync_directory
            injected = False

            def inject_foreign_ancestor_content(path: Path, *, context: str) -> None:
                nonlocal injected
                if (
                    not injected
                    and context == "created bundle import ancestor"
                    and path == world_root
                ):
                    (world_root / "foreign.txt").write_text(
                        "preserve\n",
                        encoding="utf-8",
                    )
                    injected = True
                    raise OSError("injected nonempty ancestor interruption")
                flush_directory(path, context=context)

            try:
                with (
                    patch.object(
                        bundle_module,
                        "fsync_directory",
                        side_effect=inject_foreign_ancestor_content,
                    ),
                    self.assertRaisesRegex(
                        OSError,
                        "nonempty ancestor interruption",
                    ),
                ):
                    import_runtime_bundle(
                        bundle.root,
                        game,
                        expected_bundle_hash=bundle.bundle_hash,
                    )

                self.assertTrue(injected)
                self.assertEqual(
                    "preserve\n",
                    (world_root / "foreign.txt").read_text(encoding="utf-8"),
                )
                self.assertEqual(
                    "intent",
                    _read_import_journal(game / IMPORT_JOURNAL)["state"],
                )
            finally:
                bundle.close()

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux post-mutation publication evidence",
    )
    def test_linux_stage_swap_after_validation_is_indeterminate_and_never_commits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            foreign = root / "foreign-stage"
            foreign.mkdir()
            (foreign / "foreign.txt").write_text("foreign\n", encoding="utf-8")
            require_binding = directory_publish_module.RetainedDirectory.require_binding
            calls: dict[Path, int] = {}
            displaced: Path | None = None

            def swap_after_validation(retained: object) -> None:
                nonlocal displaced
                require_binding(retained)
                path = retained.path
                if not path.name.startswith(".1.0.0.import-"):
                    return
                calls[path] = calls.get(path, 0) + 1
                if calls[path] != 4:
                    return
                displaced = path.with_name(f"{path.name}.owned")
                path.rename(displaced)
                foreign.rename(path)

            try:
                with (
                    patch.object(
                        directory_publish_module.RetainedDirectory,
                        "require_binding",
                        new=swap_after_validation,
                    ),
                    self.assertRaisesRegex(
                        BundleError,
                        "indeterminate after RENAME_NOREPLACE",
                    ),
                ):
                    import_runtime_bundle(
                        bundle.root,
                        game,
                        expected_bundle_hash=bundle.bundle_hash,
                    )

                self.assertIsNotNone(displaced)
                assert displaced is not None
                destination = game / "game_data/worlds/modly_foundation/1.0.0"
                self.assertEqual(
                    "foreign\n",
                    (destination / "foreign.txt").read_text(encoding="utf-8"),
                )
                self.assertTrue((displaced / bundle_module.BUNDLE_MANIFEST).is_file())
                journal = _read_import_journal(game / IMPORT_JOURNAL)
                self.assertEqual("ready", journal["state"])
                catalog = json.loads(
                    (game / "game_data/worlds.lock.json").read_text(encoding="utf-8")
                )
                self.assertEqual([], catalog["releases"])
            finally:
                bundle.close()

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "Linux post-mutation publication evidence",
    )
    def test_linux_lexical_parent_swap_after_rename_is_indeterminate_and_never_commits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle, game = self._bundle_and_game(root)
            world_root = game / "game_data/worlds/modly_foundation"
            destination = world_root / "1.0.0"
            displaced_parent = world_root.with_name("modly_foundation-owned")
            real_fsync = directory_publish_module.os.fsync
            swapped = False

            def swap_parent_before_post_flush(descriptor: int) -> None:
                nonlocal swapped
                if not swapped and destination.exists():
                    world_root.rename(displaced_parent)
                    world_root.mkdir()
                    (world_root / "sentinel.txt").write_text(
                        "foreign-parent\n",
                        encoding="utf-8",
                    )
                    swapped = True
                real_fsync(descriptor)

            try:
                with (
                    patch.object(
                        directory_publish_module.os,
                        "fsync",
                        side_effect=swap_parent_before_post_flush,
                    ),
                    self.assertRaisesRegex(
                        BundleError,
                        "indeterminate after RENAME_NOREPLACE",
                    ),
                ):
                    import_runtime_bundle(
                        bundle.root,
                        game,
                        expected_bundle_hash=bundle.bundle_hash,
                    )

                self.assertTrue(swapped)
                self.assertEqual(
                    "foreign-parent\n",
                    (world_root / "sentinel.txt").read_text(encoding="utf-8"),
                )
                self.assertTrue(
                    (displaced_parent / "1.0.0" / bundle_module.BUNDLE_MANIFEST).is_file()
                )
                journal = _read_import_journal(game / IMPORT_JOURNAL)
                self.assertEqual("ready", journal["state"])
                catalog = json.loads(
                    (game / "game_data/worlds.lock.json").read_text(encoding="utf-8")
                )
                self.assertEqual([], catalog["releases"])
            finally:
                bundle.close()

    def test_unsupported_platform_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            destination = root / "destination"
            expected = directory_publish_module.directory_identity(
                source,
                context="unsupported publication source",
            )
            with (
                patch.object(directory_publish_module.sys, "platform", "darwin"),
                patch.object(directory_publish_module.os, "name", "posix"),
                self.assertRaisesRegex(
                    DirectoryPublishError,
                    "supported only on Linux and Windows",
                ),
            ):
                with publish_directory_noreplace(
                    source,
                    destination,
                    expected_source_identity=expected,
                ):
                    pass
            self.assertTrue(source.is_dir())
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
