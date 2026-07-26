from __future__ import annotations

import ctypes
import hashlib
import os
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from apps.studio.scripts import shell_package_snapshot as snapshot
from isoworld.content import resource_snapshot
from scripts.studio_runtime_assembly import RuntimeAssemblyError


class _PinnedTreeFakeApi:
    def __init__(self, root: Path) -> None:
        self.closed: list[int] = []
        self.handles: dict[int, Path] = {1: root}
        self.next_handle = 2
        self.opens: list[dict[str, object]] = []
        self.path_identities: dict[Path, tuple[int, int]] = {root: (1, 1)}
        self.writable_directories: set[Path] = set()

    def _identity(self, path: Path) -> tuple[int, int]:
        identity = self.path_identities.get(path)
        if identity is None:
            identity = (1, len(self.path_identities) + 1)
            self.path_identities[path] = identity
        return identity

    def _retain(self, path: Path) -> int:
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = path
        return handle

    def relative(
        self,
        parent: int,
        name: str,
        *,
        directory: bool,
        create: bool,
        writable: bool = False,
        share_write: bool = False,
        field: str,
    ) -> int:
        if not directory or create or field != "package":
            raise AssertionError("unexpected directory retention contract")
        path = self.handles[parent] / name
        already_retained_writable = path in self.writable_directories
        self.opens.append(
            {
                "path": path,
                "share_write": share_write,
                "writable": writable,
            }
        )
        if already_retained_writable and not writable and not share_write:
            raise RuntimeAssemblyError(
                "private_sharing_violation",
                "C:\\private\\resources",
            )
        if writable:
            self.writable_directories.add(path)
        return self._retain(path)

    def state(self, handle: int, _field: str) -> SimpleNamespace:
        path = self.handles[handle]
        return SimpleNamespace(
            identity=self._identity(path),
            is_directory=path.is_dir(),
            is_reparse=False,
            nlink=1,
            size=path.stat().st_size if path.is_file() else 0,
        )

    def close(self, handle: int) -> None:
        self.closed.append(handle)


class _PinnedTreeFakeChain:
    instances: list[_PinnedTreeFakeChain] = []

    def __init__(self, root: Path, field: str, **options: object) -> None:
        if field != "package":
            raise AssertionError("unexpected chain field")
        self.api = _PinnedTreeFakeApi(root)
        self.leaf = 1
        self.options = options
        self.bindings_checked = False
        self.instances.append(self)

    def require_bindings(self) -> None:
        self.bindings_checked = True

    def close(self) -> None:
        pass


class _PinnedTreeFakeReader:
    def __init__(self, api: _PinnedTreeFakeApi) -> None:
        self.api = api
        self.payloads: dict[int, bytes] = {}

    def create(self, parent: int, name: str) -> int:
        directory = self.api.handles[parent]
        if directory not in self.api.writable_directories:
            raise RuntimeAssemblyError(
                "private_create_failure",
                "C:\\private\\shell-package-manifest.json",
            )
        path = directory / name
        path.touch(exist_ok=False)
        handle = self.api._retain(path)
        self.payloads[handle] = b""
        return handle

    def write(self, handle: int, payload: bytes) -> None:
        self.payloads[handle] = payload
        self.api.handles[handle].write_bytes(payload)

    def chunks(self, handle: int, _size: int):
        yield self.api.handles[handle].read_bytes()

    def open(
        self,
        parent: int,
        name: str,
        *,
        share_write: bool = False,
    ) -> int:
        if not share_write:
            raise RuntimeAssemblyError(
                "private_sharing_violation",
                "C:\\private\\shell-package-manifest.json",
            )
        return self.api._retain(self.api.handles[parent] / name)


class _RecordingWindowsFunction:
    def __init__(self, callback: object) -> None:
        self.callback = callback
        self.argtypes: list[object] | None = None
        self.restype: object | None = None

    def __call__(self, *args: object) -> object:
        return self.callback(*args)  # type: ignore[operator]


class _SnapshotRootCleanupFakeApi:
    def __init__(
        self,
        *,
        identity: tuple[int, int] = (17, 29),
        is_directory: bool = True,
        is_reparse: bool = False,
        entries: tuple[object, ...] = (),
        open_handle: int | None = 71,
        disposition_succeeds: bool = True,
        close_fails: bool = False,
        events: list[str] | None = None,
    ) -> None:
        self.ctypes = ctypes
        self.wintypes = wintypes
        self.identity = identity
        self.is_directory = is_directory
        self.is_reparse = is_reparse
        self.entries = entries
        self.open_handle = open_handle
        self.disposition_succeeds = disposition_succeeds
        self.close_fails = close_fails
        self.events = events if events is not None else []
        self.opens: list[tuple[object, ...]] = []
        self.closed: list[int] = []
        self.dispositions: list[bool] = []
        self.CreateFileW = _RecordingWindowsFunction(self._create_file)
        self.kernel32 = SimpleNamespace(
            SetFileInformationByHandle=_RecordingWindowsFunction(self._set_disposition),
        )

    def _create_file(self, *args: object) -> wintypes.HANDLE:
        self.events.append("root-open")
        self.opens.append(args)
        if self.open_handle is None:
            return wintypes.HANDLE(-1)
        return wintypes.HANDLE(self.open_handle)

    def _set_disposition(
        self,
        _handle: object,
        _info_class: object,
        disposition: object,
        _size: object,
    ) -> bool:
        self.events.append("root-disposition")
        value = ctypes.cast(
            disposition,
            ctypes.POINTER(snapshot._WindowsSnapshotRootCleanup.FileDispositionInfo),
        ).contents
        self.dispositions.append(bool(value.DeleteFile))
        return self.disposition_succeeds

    def state(self, _handle: int, _field: str) -> SimpleNamespace:
        return SimpleNamespace(
            identity=self.identity,
            is_directory=self.is_directory,
            is_reparse=self.is_reparse,
        )

    def directory_entries(
        self,
        _handle: int,
        _field: str,
        *,
        unsafe_code: str,
    ) -> tuple[object, ...]:
        if unsafe_code != "windows_snapshot_cleanup_failed":
            raise AssertionError("snapshot-root enumeration was not fail closed")
        return self.entries

    def close(self, handle: int) -> None:
        self.events.append("root-close")
        self.closed.append(handle)
        if self.close_fails:
            raise RuntimeAssemblyError("private_close_failure", "private-root")


def _windows_reader_with_status(
    status_code: int,
) -> tuple[snapshot._WindowsReader, SimpleNamespace]:
    class FakeVoid:
        def __init__(self, value: object = None) -> None:
            self.value = value

    class FakeHandle:
        def __init__(self, value: object = None) -> None:
            self.value = value

    api = SimpleNamespace()
    api.ctypes = SimpleNamespace(
        byref=lambda value: value,
        cast=lambda value, _kind: SimpleNamespace(
            value=value.value if isinstance(value, FakeHandle) else value
        ),
        c_void_p=FakeVoid,
        create_unicode_buffer=lambda value: value,
        pointer=lambda value: value,
        sizeof=lambda _value: 1,
    )
    api.wintypes = SimpleNamespace(HANDLE=FakeHandle, LPWSTR=object)
    api.UnicodeString = lambda *_args: object()
    api.ObjectAttributes = lambda *_args: object()
    api.IoStatusBlock = lambda: object()
    api.NtCreateFile = lambda *_args: status_code - (1 << 32)
    api.close = lambda _handle: None
    api.state = lambda _handle, _field: SimpleNamespace(
        is_directory=False,
        is_reparse=False,
        nlink=1,
    )
    reader = object.__new__(snapshot._WindowsReader)
    reader.api = api
    reader.ctypes = api.ctypes
    reader.wintypes = api.wintypes
    return reader, api


class StudioShellPackageSnapshotTests(unittest.TestCase):
    @staticmethod
    def _raise(error: BaseException) -> None:
        raise error

    def test_windows_snapshot_phase_preserves_specific_snapshot_errors(self) -> None:
        original = snapshot.SnapshotError("package_entry_changed")

        with self.assertRaises(snapshot.SnapshotError) as captured:
            snapshot._run_windows_snapshot_phase(
                lambda: self._raise(original),
                "windows_snapshot_package_failed",
                sharing_conflict_code="windows_snapshot_package_sharing_conflict",
            )

        self.assertIs(original, captured.exception)

    def test_windows_snapshot_setup_maps_resource_lock_failure_to_phase_code(
        self,
    ) -> None:
        private_failure = resource_snapshot.ResourceSnapshotError("private Windows snapshot path")
        with patch.object(
            resource_snapshot,
            "_windows_lock_directory",
            side_effect=private_failure,
        ):
            with self.assertRaises(snapshot.SnapshotError) as captured:
                snapshot._run_windows_snapshot_phase(
                    lambda: resource_snapshot._windows_lock_directory(Path("C:/private/package")),
                    "windows_snapshot_setup_failed",
                    sharing_conflict_code="windows_snapshot_setup_sharing_conflict",
                )

        self.assertEqual("windows_snapshot_setup_failed", captured.exception.code)
        self.assertEqual("windows_snapshot_setup_failed", str(captured.exception))
        self.assertNotEqual(
            "windows_snapshot_setup_sharing_conflict",
            captured.exception.code,
        )
        self.assertNotEqual("backend_failure", captured.exception.code)

    def test_windows_snapshot_cleanup_maps_resource_close_failure_to_phase_code(
        self,
    ) -> None:
        private_failure = resource_snapshot.ResourceSnapshotError("private Windows snapshot handle")
        with patch.object(
            resource_snapshot,
            "_windows_close_handle",
            side_effect=private_failure,
        ):
            with self.assertRaises(snapshot.SnapshotError) as captured:
                snapshot._run_windows_snapshot_phase(
                    lambda: resource_snapshot._windows_close_handle(7),
                    "windows_snapshot_cleanup_failed",
                    sharing_conflict_code=None,
                )

        self.assertEqual("windows_snapshot_cleanup_failed", captured.exception.code)
        self.assertEqual("windows_snapshot_cleanup_failed", str(captured.exception))
        self.assertNotIn("sharing_conflict", captured.exception.code)
        self.assertNotEqual("backend_failure", captured.exception.code)

    def test_windows_snapshot_root_cleanup_uses_exact_handle_contract_once(
        self,
    ) -> None:
        api = _SnapshotRootCleanupFakeApi()
        cleanup = snapshot._WindowsSnapshotRootCleanup(api)
        root = Path("C:/private/shell-snapshots")

        cleanup.delete_empty(root, (17, 29))

        self.assertEqual(1, len(api.opens))
        (
            opened_path,
            access,
            share,
            security,
            disposition,
            options,
            template,
        ) = api.opens[0]
        self.assertEqual(str(root), opened_path)
        self.assertEqual(
            snapshot._WindowsSnapshotRootCleanup.DELETE
            | snapshot._WindowsSnapshotRootCleanup.FILE_LIST_DIRECTORY
            | snapshot._WindowsSnapshotRootCleanup.FILE_READ_ATTRIBUTES
            | snapshot._WindowsSnapshotRootCleanup.SYNCHRONIZE,
            access,
        )
        self.assertEqual(
            snapshot._WindowsSnapshotRootCleanup.FILE_SHARE_READ
            | snapshot._WindowsSnapshotRootCleanup.FILE_SHARE_WRITE,
            share,
        )
        self.assertFalse(share & snapshot._WindowsSnapshotRootCleanup.FILE_SHARE_DELETE)
        self.assertIsNone(security)
        self.assertEqual(snapshot._WindowsSnapshotRootCleanup.OPEN_EXISTING, disposition)
        self.assertEqual(
            snapshot._WindowsSnapshotRootCleanup.FILE_FLAG_BACKUP_SEMANTICS
            | snapshot._WindowsSnapshotRootCleanup.FILE_FLAG_OPEN_REPARSE_POINT,
            options,
        )
        self.assertIsNone(template)
        self.assertEqual([True], api.dispositions)
        self.assertEqual([71], api.closed)
        set_disposition = api.kernel32.SetFileInformationByHandle
        self.assertEqual(1, len(api.dispositions))
        self.assertIsNotNone(set_disposition.argtypes)
        self.assertIs(wintypes.BOOL, set_disposition.restype)

    def test_windows_snapshot_root_cleanup_refuses_changed_unsafe_or_nonempty_root(
        self,
    ) -> None:
        cases = (
            ("identity", {"identity": (17, 30)}),
            ("not-directory", {"is_directory": False}),
            ("reparse", {"is_reparse": True}),
            ("nonempty", {"entries": (SimpleNamespace(name="survivor"),)}),
        )
        for name, options in cases:
            with self.subTest(case=name):
                api = _SnapshotRootCleanupFakeApi(**options)
                cleanup = snapshot._WindowsSnapshotRootCleanup(api)
                with self.assertRaises(snapshot.SnapshotError) as captured:
                    cleanup.delete_empty(Path("C:/private/shell-snapshots"), (17, 29))

                self.assertEqual(
                    "windows_snapshot_cleanup_failed",
                    captured.exception.code,
                )
                self.assertEqual([], api.dispositions)
                self.assertEqual([71], api.closed)

    def test_windows_snapshot_root_cleanup_maps_open_disposition_and_close_failures(
        self,
    ) -> None:
        cases = (
            ("open", {"open_handle": None}, 0, []),
            ("disposition", {"disposition_succeeds": False}, 1, [71]),
            ("close", {"close_fails": True}, 1, [71]),
        )
        for name, options, disposition_count, closed in cases:
            with self.subTest(case=name):
                api = _SnapshotRootCleanupFakeApi(**options)
                cleanup = snapshot._WindowsSnapshotRootCleanup(api)
                with self.assertRaises(snapshot.SnapshotError) as captured:
                    cleanup.delete_empty(Path("C:/private/shell-snapshots"), (17, 29))

                self.assertEqual(
                    "windows_snapshot_cleanup_failed",
                    captured.exception.code,
                )
                self.assertEqual(disposition_count, len(api.dispositions))
                self.assertEqual(closed, api.closed)

    def test_success_cleanup_closes_old_handles_before_root_delete_and_ack(
        self,
    ) -> None:
        events: list[str] = []

        snapshot._complete_windows_snapshot_cleanup(
            delete_snapshots=lambda: events.append("snapshot-files-delete"),
            close_handles=lambda: events.append("old-handles-close"),
            delete_root=lambda: events.append("snapshot-root-delete"),
            acknowledge=lambda: events.append("final-ack"),
        )

        self.assertEqual(
            [
                "snapshot-files-delete",
                "old-handles-close",
                "snapshot-root-delete",
                "final-ack",
            ],
            events,
        )

    def test_primary_cleanup_failure_never_deletes_root_or_acknowledges(
        self,
    ) -> None:
        events: list[str] = []

        def fail_primary() -> None:
            events.append("primary-failure")
            raise snapshot.SnapshotError("windows_snapshot_package_sharing_conflict")

        with self.assertRaises(snapshot.SnapshotError) as captured:
            snapshot._complete_windows_snapshot_cleanup(
                delete_snapshots=fail_primary,
                close_handles=lambda: events.append("old-handles-close"),
                delete_root=lambda: events.append("snapshot-root-delete"),
                acknowledge=lambda: events.append("final-ack"),
            )

        self.assertEqual(
            "windows_snapshot_package_sharing_conflict",
            captured.exception.code,
        )
        self.assertEqual(["primary-failure"], events)

    def test_cleanup_failure_withholds_root_delete_or_final_ack_by_phase(
        self,
    ) -> None:
        for failing_phase in ("snapshot-files-delete", "old-handles-close", "snapshot-root-delete"):
            with self.subTest(failing_phase=failing_phase):
                events: list[str] = []

                def action(
                    name: str,
                    *,
                    recorded: list[str] = events,
                    failure: str = failing_phase,
                ) -> None:
                    recorded.append(name)
                    if name == failure:
                        raise snapshot.SnapshotError("windows_snapshot_cleanup_failed")

                with self.assertRaises(snapshot.SnapshotError) as captured:
                    snapshot._complete_windows_snapshot_cleanup(
                        delete_snapshots=lambda: action("snapshot-files-delete"),
                        close_handles=lambda: action("old-handles-close"),
                        delete_root=lambda: action("snapshot-root-delete"),
                        acknowledge=lambda: action("final-ack"),
                    )

                self.assertEqual(
                    "windows_snapshot_cleanup_failed",
                    captured.exception.code,
                )
                self.assertNotIn("final-ack", events)
                if failing_phase != "snapshot-root-delete":
                    self.assertNotIn("snapshot-root-delete", events)

    def test_snapshot_file_cleanup_uses_retained_handles_without_path_cleanup(
        self,
    ) -> None:
        events: list[str] = []

        class FakeChain:
            @staticmethod
            def require_bindings() -> None:
                events.append("bindings")

        class FakeReader:
            @staticmethod
            def delete_owned_snapshot(handle: int) -> None:
                events.append(f"delete:{handle}")

        class FakeApi:
            @staticmethod
            def close(handle: int) -> None:
                events.append(f"close:{handle}")

        tree = object.__new__(snapshot._WindowsPinnedTree)
        tree.snapshot_chain = FakeChain()
        tree.files = {"payload": SimpleNamespace(snapshot_name="payload.snapshot")}
        tree.snapshot_handles = {"payload.snapshot": 83}
        tree.reader = FakeReader()
        tree.api = FakeApi()

        with patch.object(snapshot.os, "scandir", side_effect=AssertionError("path cleanup")):
            tree.cleanup_snapshots()

        self.assertEqual(["bindings", "delete:83", "close:83"], events)
        self.assertEqual({}, tree.snapshot_handles)

    def test_strict_old_handle_close_attempts_every_handle_and_maps_failure(
        self,
    ) -> None:
        events: list[str] = []

        class FakeApi:
            @staticmethod
            def close(handle: int) -> None:
                events.append(f"close:{handle}")
                if handle == 91:
                    raise RuntimeAssemblyError("private_close_failure", "private")

        class FakeChain:
            @staticmethod
            def close() -> None:
                events.append("chain-close")

        tree = object.__new__(snapshot._WindowsPinnedTree)
        tree.snapshot_handles = {"snapshot": 91}
        tree.extra_handles = [92]
        tree.api = FakeApi()
        tree.chain = FakeChain()

        with self.assertRaises(snapshot.SnapshotError) as captured:
            tree.close_strict()

        self.assertEqual("windows_snapshot_cleanup_failed", captured.exception.code)
        self.assertEqual(["close:91", "close:92", "chain-close"], events)
        self.assertEqual({}, tree.snapshot_handles)
        self.assertEqual([], tree.extra_handles)

    @unittest.skipUnless(os.name == "nt", "native Windows snapshot cleanup")
    def test_windows_snapshot_root_cleanup_deletes_native_empty_directory_by_handle(
        self,
    ) -> None:
        from scripts.studio_runtime_assembly import _WindowsDirectoryChain

        with tempfile.TemporaryDirectory() as raw_parent:
            root = Path(raw_parent) / "shell-snapshots"
            root.mkdir()
            chain = _WindowsDirectoryChain(
                root,
                "package",
                writable_leaf=True,
                share_write=True,
            )
            api = chain.api
            identity = api.state(chain.leaf, "package").identity
            chain.close()

            snapshot._WindowsSnapshotRootCleanup(api).delete_empty(root, identity)

            self.assertFalse(root.exists())

    def test_snapshot_setup_retains_independent_temp_root_for_snapshot_writes(
        self,
    ) -> None:
        class SetupReached(RuntimeError):
            pass

        guarded_paths: list[Path] = []
        closed_handles: list[int] = []
        chain_calls: list[tuple[Path, str, bool, bool]] = []

        with (
            tempfile.TemporaryDirectory() as raw_output_parent,
            tempfile.TemporaryDirectory() as raw_snapshot_parent,
        ):
            output_parent = Path(raw_output_parent)
            snapshot_parent = Path(raw_snapshot_parent)
            package_root = output_parent / "guarded-output"
            source_root = output_parent / "repository" / "apps" / "studio"
            snapshot_root = snapshot_parent / "shell-snapshots"
            package_root.mkdir()
            snapshot_root.mkdir()

            self.assertEqual(snapshot_parent, snapshot_root.parent)
            self.assertNotIn(package_root, snapshot_root.parents)

            def lock_directory(path: Path) -> int:
                guarded_paths.append(path)
                return len(guarded_paths)

            class FakeSnapshotApi:
                @staticmethod
                def state(_handle: int, _field: str) -> SimpleNamespace:
                    return SimpleNamespace(
                        identity=(3, 7),
                        is_directory=True,
                        is_reparse=False,
                    )

            class FakeSnapshotChain:
                def __init__(
                    self,
                    root: Path,
                    field: str,
                    *,
                    writable_leaf: bool = True,
                    share_write: bool = False,
                ) -> None:
                    chain_calls.append((root, field, writable_leaf, share_write))
                    if root not in guarded_paths:
                        raise AssertionError("snapshot root was not locked first")
                    if not writable_leaf or not share_write:
                        raise RuntimeAssemblyError(
                            "private_sharing_violation",
                            "private_snapshot_root",
                            native_status=0xC0000043,
                        )
                    self.leaf = 7
                    self.api = FakeSnapshotApi()

                def close(self) -> None:
                    pass

            fake_os = SimpleNamespace(name="nt", scandir=os.scandir)
            with (
                patch.object(snapshot, "os", fake_os),
                patch.object(
                    snapshot,
                    "_strict_arguments",
                    return_value=(
                        package_root,
                        "win32-x64",
                        source_root,
                        snapshot_root,
                    ),
                ),
                patch.object(snapshot, "_source_paths"),
                patch(
                    "isoworld.content.resource_snapshot._windows_lock_directory",
                    side_effect=lock_directory,
                ),
                patch(
                    "isoworld.content.resource_snapshot._windows_close_handle",
                    side_effect=closed_handles.append,
                ),
                patch(
                    "scripts.studio_runtime_assembly._WindowsDirectoryChain",
                    FakeSnapshotChain,
                ),
                patch.object(snapshot, "_WindowsSnapshotRootCleanup") as root_cleanup,
                patch.object(
                    snapshot,
                    "_WindowsPinnedTree",
                    side_effect=SetupReached("package scan reached"),
                ),
            ):
                with self.assertRaises(SetupReached) as captured:
                    snapshot._serve(["serve"])

            self.assertEqual("package scan reached", str(captured.exception))
            self.assertEqual([package_root, snapshot_root], guarded_paths)
            self.assertEqual(
                [(snapshot_root, "package", True, True)],
                chain_calls,
            )
            self.assertEqual([2, 1], closed_handles)
            root_cleanup.assert_not_called()

    def test_windows_reader_retains_redacted_status_for_failed_open_and_create(
        self,
    ) -> None:
        for status_code, create in (
            (0xC0000022, False),
            (0xC0000056, True),
        ):
            with self.subTest(status_code=hex(status_code), create=create):
                reader, _api = _windows_reader_with_status(status_code)
                with self.assertRaises(RuntimeAssemblyError) as captured:
                    if create:
                        reader.create(7, "shell-package-manifest.json")
                    else:
                        reader.open(7, "app.asar")

                failure = captured.exception
                self.assertEqual("package_entry_changed", failure.code)
                self.assertEqual("package", failure.field)
                self.assertEqual(status_code, failure.native_status)
                self.assertEqual("package_entry_changed", str(failure))
                self.assertEqual(
                    {
                        "code": "package_entry_changed",
                        "field": "package",
                    },
                    failure.as_dict(),
                )
                self.assertNotIn(hex(status_code), str(failure))
                self.assertNotIn("app.asar", str(failure))
                self.assertNotIn("shell-package-manifest.json", str(failure))

    def test_windows_reader_open_status_drives_exact_prepublication_phase(
        self,
    ) -> None:
        for failure_code, sharing_conflict_code in (
            (
                "windows_snapshot_package_failed",
                "windows_snapshot_package_sharing_conflict",
            ),
            (
                "windows_snapshot_source_failed",
                "windows_snapshot_source_sharing_conflict",
            ),
        ):
            for status_code, expected_code in (
                (0xC0000043, sharing_conflict_code),
                (0xC0000055, sharing_conflict_code),
                (0xC0000054, failure_code),
                (0xC0000056, failure_code),
                (0xC0000022, failure_code),
                (0xC0000001, failure_code),
            ):
                with self.subTest(
                    phase=failure_code,
                    status_code=hex(status_code),
                ):
                    reader, _api = _windows_reader_with_status(status_code)
                    with self.assertRaises(snapshot.SnapshotError) as captured:
                        snapshot._run_windows_snapshot_phase(
                            lambda reader=reader: reader.open(7, "app.asar"),
                            failure_code,
                            sharing_conflict_code=sharing_conflict_code,
                        )
                    self.assertEqual(expected_code, captured.exception.code)
                    self.assertEqual(expected_code, str(captured.exception))

    def test_windows_reader_manifest_create_preserves_exact_failure_boundaries(
        self,
    ) -> None:
        resources = snapshot._Directory(
            absolute=Path("C:/private/resources"),
            children=(),
            handle=7,
            identity=(1, 7),
            name="resources",
            parent=None,
            relative="resources",
        )
        for status_code, expected_code in (
            (0xC0000035, "shell_manifest_already_exists"),
            (0xC0000043, "shell_manifest_publish_failed"),
            (0xC0000022, "shell_manifest_publish_failed"),
        ):
            with self.subTest(status_code=hex(status_code)):
                reader, api = _windows_reader_with_status(status_code)
                tree = object.__new__(snapshot._WindowsPinnedTree)
                tree.api = api
                tree.directories = {"resources": resources}
                tree.extra_handles = []
                tree.files = {}
                tree.reader = reader
                with self.assertRaises(snapshot.SnapshotError) as captured:
                    tree.publish_manifest(b"{}\n")

                self.assertEqual(expected_code, captured.exception.code)
                self.assertEqual(expected_code, str(captured.exception))
                self.assertEqual([], tree.extra_handles)
                self.assertEqual({}, tree.files)

    def test_windows_snapshot_phase_classifies_only_exact_prepublication_busy_errors(
        self,
    ) -> None:
        phases = (
            (
                "windows_snapshot_setup_failed",
                "windows_snapshot_setup_sharing_conflict",
            ),
            (
                "windows_snapshot_package_failed",
                "windows_snapshot_package_sharing_conflict",
            ),
            (
                "windows_snapshot_source_failed",
                "windows_snapshot_source_sharing_conflict",
            ),
        )
        for failure_code, sharing_conflict_code in phases:
            for native_status in (0xC0000043, 0xC0000055):
                with self.subTest(
                    phase=failure_code,
                    native_status=hex(native_status),
                ):
                    error = RuntimeAssemblyError(
                        "private_native_failure",
                        "private_path",
                        native_status=native_status,
                    )
                    with self.assertRaises(snapshot.SnapshotError) as captured:
                        snapshot._run_windows_snapshot_phase(
                            lambda error=error: self._raise(error),
                            failure_code,
                            sharing_conflict_code=sharing_conflict_code,
                        )
                    self.assertEqual(
                        sharing_conflict_code,
                        captured.exception.code,
                    )

            for winerror in (32, 33):
                with self.subTest(
                    phase=failure_code,
                    winerror=winerror,
                ):
                    error = OSError("private Windows path")
                    error.winerror = winerror
                    with self.assertRaises(snapshot.SnapshotError) as captured:
                        snapshot._run_windows_snapshot_phase(
                            lambda error=error: self._raise(error),
                            failure_code,
                            sharing_conflict_code=sharing_conflict_code,
                        )
                    self.assertEqual(
                        sharing_conflict_code,
                        captured.exception.code,
                    )

    def test_windows_snapshot_phase_keeps_other_failures_phase_specific(self) -> None:
        cases = (
            (
                RuntimeAssemblyError(
                    "private_native_failure",
                    "private_path",
                    native_status=0xC0000054,
                ),
                "windows_snapshot_setup_failed",
            ),
            (
                RuntimeAssemblyError(
                    "private_native_failure",
                    "private_path",
                    native_status=0xC0000056,
                ),
                "windows_snapshot_package_failed",
            ),
            (
                RuntimeAssemblyError(
                    "private_native_failure",
                    "private_path",
                    native_status=0xC0000022,
                ),
                "windows_snapshot_source_failed",
            ),
            (
                RuntimeAssemblyError(
                    "private_native_failure",
                    "private_path",
                    native_status=0xC0000001,
                ),
                "windows_snapshot_source_failed",
            ),
        )
        for error, phase_code in cases:
            with self.subTest(native_status=hex(error.native_status), phase_code=phase_code):
                with self.assertRaises(snapshot.SnapshotError) as captured:
                    snapshot._run_windows_snapshot_phase(
                        lambda error=error: self._raise(error),
                        phase_code,
                        sharing_conflict_code=phase_code.replace(
                            "_failed",
                            "_sharing_conflict",
                        ),
                    )
                self.assertEqual(phase_code, captured.exception.code)

    def test_windows_snapshot_phase_never_classifies_postreport_failures_as_busy(
        self,
    ) -> None:
        native_busy = RuntimeAssemblyError(
            "private_native_failure",
            "private_path",
            native_status=0xC0000043,
        )
        winerror_busy = OSError("private Windows path")
        winerror_busy.winerror = 33
        for error, phase_code in (
            (native_busy, "windows_snapshot_finalize_failed"),
            (winerror_busy, "windows_snapshot_cleanup_failed"),
        ):
            with self.subTest(phase_code=phase_code):
                with self.assertRaises(snapshot.SnapshotError) as captured:
                    snapshot._run_windows_snapshot_phase(
                        lambda error=error: self._raise(error),
                        phase_code,
                        sharing_conflict_code=None,
                    )
                self.assertEqual(phase_code, captured.exception.code)

    def test_package_resources_are_the_only_writable_retained_directory(self) -> None:
        payload = b'{"status":"blocked"}\n'
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            (root / "locales").mkdir()
            (root / "resources" / "packaging").mkdir(parents=True)
            _PinnedTreeFakeChain.instances.clear()
            with (
                patch(
                    "scripts.studio_runtime_assembly._WindowsDirectoryChain",
                    _PinnedTreeFakeChain,
                ),
                patch.object(snapshot, "_WindowsReader", _PinnedTreeFakeReader),
            ):
                tree = snapshot._WindowsPinnedTree(
                    root,
                    allow_manifest_publication=True,
                    share_write=True,
                    writable_leaf=False,
                )
                try:
                    tree.publish_manifest(payload)
                    tree.finalize()
                finally:
                    tree.close()

            chain = _PinnedTreeFakeChain.instances[0]
            self.assertTrue(chain.bindings_checked)
            self.assertEqual(
                {"share_write": True, "writable_leaf": False},
                chain.options,
            )
            relative_opens = [
                {
                    **record,
                    "path": record["path"].relative_to(root).as_posix(),
                }
                for record in chain.api.opens
            ]
            initial: dict[str, dict[str, object]] = {}
            for record in relative_opens:
                initial.setdefault(str(record["path"]), record)
            self.assertEqual(
                {
                    "locales": False,
                    "resources": True,
                    "resources/packaging": False,
                },
                {relative: bool(record["writable"]) for relative, record in initial.items()},
            )
            self.assertEqual(
                [True],
                [
                    bool(record["share_write"])
                    for record in relative_opens
                    if record["path"] == "resources" and not record["writable"]
                ],
            )
            self.assertFalse(
                any(
                    record["writable"] for record in relative_opens if record["path"] != "resources"
                )
            )

    def test_nonpublication_tree_keeps_resources_read_only_and_redacts_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            (root / "resources" / "nested").mkdir(parents=True)
            _PinnedTreeFakeChain.instances.clear()
            with (
                patch(
                    "scripts.studio_runtime_assembly._WindowsDirectoryChain",
                    _PinnedTreeFakeChain,
                ),
                patch.object(snapshot, "_WindowsReader", _PinnedTreeFakeReader),
            ):
                tree = snapshot._WindowsPinnedTree(root)
                try:
                    with self.assertRaises(snapshot.SnapshotError) as captured:
                        tree.publish_manifest(b"{}\n")
                finally:
                    tree.close()

            self.assertEqual(
                "shell_manifest_publish_failed",
                captured.exception.code,
            )
            self.assertEqual(
                "shell_manifest_publish_failed",
                str(captured.exception),
            )
            self.assertEqual(
                {"share_write": False, "writable_leaf": False},
                _PinnedTreeFakeChain.instances[0].options,
            )
            self.assertFalse(
                any(record["writable"] for record in _PinnedTreeFakeChain.instances[0].api.opens)
            )

    def test_windows_verification_reader_coexists_with_retained_writer_only(self) -> None:
        class FakeVoid:
            def __init__(self, value: object = None) -> None:
                self.value = value

        class FakeHandle:
            def __init__(self, value: object = None) -> None:
                self.value = value

        api = SimpleNamespace()
        api.ctypes = SimpleNamespace(
            byref=lambda value: value,
            cast=lambda value, _kind: SimpleNamespace(
                value=value.value if isinstance(value, FakeHandle) else value
            ),
            c_void_p=FakeVoid,
            create_unicode_buffer=lambda value: value,
            pointer=lambda value: value,
            sizeof=lambda _value: 1,
        )
        api.wintypes = SimpleNamespace(HANDLE=FakeHandle, LPWSTR=object)
        api.UnicodeString = lambda *_args: object()
        api.ObjectAttributes = lambda *_args: object()
        api.IoStatusBlock = lambda: object()
        calls: list[tuple[int, int]] = []
        opened: list[tuple[int, int]] = []

        def nt_create(
            output: FakeHandle,
            access: int,
            _attributes: object,
            _io_status: object,
            _allocation: object,
            _file_attributes: int,
            share: int,
            _disposition: int,
            _options: int,
            _ea: object,
            _ea_length: int,
        ) -> int:
            calls.append((access, share))
            for existing_access, existing_share in opened:
                if (
                    access & snapshot._WindowsReader.GENERIC_READ
                    and not existing_share & snapshot._WindowsReader.FILE_SHARE_READ
                    or access & snapshot._WindowsReader.GENERIC_WRITE
                    and not existing_share & snapshot._WindowsReader.FILE_SHARE_WRITE
                    or existing_access & snapshot._WindowsReader.GENERIC_READ
                    and not share & snapshot._WindowsReader.FILE_SHARE_READ
                    or existing_access & snapshot._WindowsReader.GENERIC_WRITE
                    and not share & snapshot._WindowsReader.FILE_SHARE_WRITE
                ):
                    return 0xC0000043 - (1 << 32)
            opened.append((access, share))
            output.value = 70 + len(opened)
            return 0

        api.NtCreateFile = nt_create
        api.state = lambda _handle, _field: SimpleNamespace(
            is_directory=False,
            is_reparse=False,
            nlink=1,
        )
        api.close = lambda _handle: None
        reader = object.__new__(snapshot._WindowsReader)
        reader.api = api
        reader.ctypes = api.ctypes
        reader.wintypes = api.wintypes

        reader.create(7, "shell-package-manifest.json")
        with self.assertRaises(RuntimeAssemblyError) as incompatible:
            reader.open(7, "shell-package-manifest.json")
        self.assertEqual("package_entry_changed", incompatible.exception.code)
        self.assertEqual(0xC0000043, incompatible.exception.native_status)

        reader.open(
            7,
            "shell-package-manifest.json",
            share_write=True,
        )
        with self.assertRaises(RuntimeAssemblyError) as writer:
            reader.create(7, "shell-package-manifest.json")
        self.assertEqual("package_entry_changed", writer.exception.code)
        self.assertEqual(0xC0000043, writer.exception.native_status)

        writer_access, writer_share = calls[0]
        reader_access, reader_share = calls[2]
        self.assertTrue(writer_access & snapshot._WindowsReader.GENERIC_WRITE)
        self.assertEqual(snapshot._WindowsReader.FILE_SHARE_READ, writer_share)
        self.assertTrue(reader_access & snapshot._WindowsReader.GENERIC_READ)
        self.assertEqual(
            snapshot._WindowsReader.FILE_SHARE_READ | snapshot._WindowsReader.FILE_SHARE_WRITE,
            reader_share,
        )
        self.assertFalse(writer_share & snapshot._WindowsReader.FILE_SHARE_WRITE)

    def test_finalize_uses_writer_compatible_reopen_for_owned_manifest(self) -> None:
        payload = b'{"status":"blocked"}\n'

        class FakeChain:
            def require_bindings(self) -> None:
                pass

        class FakeApi:
            def __init__(self) -> None:
                self.closed: list[int] = []

            def state(self, handle: int, _field: str) -> SimpleNamespace:
                if handle == 1:
                    return SimpleNamespace(
                        identity=(1, 1),
                        is_directory=True,
                        is_reparse=False,
                    )
                return SimpleNamespace(
                    identity=(2, 2),
                    is_directory=False,
                    is_reparse=False,
                    nlink=1,
                    size=len(payload),
                )

            def close(self, handle: int) -> None:
                self.closed.append(handle)

        class FakeReader:
            def __init__(self) -> None:
                self.share_write: list[bool] = []

            def chunks(self, _handle: int, _size: int):
                yield payload

            def open(
                self,
                _parent: int,
                _name: str,
                *,
                share_write: bool = False,
            ) -> int:
                self.share_write.append(share_write)
                if not share_write:
                    raise snapshot.SnapshotError("package_entry_changed")
                return 3

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            name = "shell-package-manifest.json"
            (root / name).write_bytes(payload)
            directory = snapshot._Directory(
                absolute=root,
                children=(name,),
                handle=1,
                identity=(1, 1),
                name="",
                parent=None,
                relative="",
            )
            record = snapshot._File(
                handle=2,
                identity=(2, 2),
                name=name,
                nlink=1,
                parent=directory,
                payload=payload,
                relative=snapshot.SHELL_MANIFEST_PATH,
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload),
                retained_writer=True,
            )
            tree = object.__new__(snapshot._WindowsPinnedTree)
            tree.chain = FakeChain()
            tree.api = FakeApi()
            tree.reader = FakeReader()
            tree.directories = {"": directory}
            tree.files = {snapshot.SHELL_MANIFEST_PATH: record}
            tree.snapshot_chain = None

            tree.finalize()

            self.assertEqual([True], tree.reader.share_write)
            self.assertEqual([3], tree.api.closed)

    def test_output_guard_reopen_shares_the_retained_creator(self) -> None:
        expected = SimpleNamespace(
            identity=(4, 2),
            is_directory=True,
            is_reparse=False,
        )

        class FakeApi:
            def __init__(self) -> None:
                self.closed: list[int] = []
                self.reopens: list[tuple[int, str, bool]] = []

            def state(self, _handle: int, _field: str) -> SimpleNamespace:
                return expected

            def relative(
                self,
                parent: int,
                name: str,
                *,
                directory: bool,
                create: bool,
                share_write: bool = False,
                field: str,
            ) -> int:
                self.assert_contract(directory, create, field)
                self.reopens.append((parent, name, share_write))
                if not share_write:
                    raise snapshot.SnapshotError("package_output_changed")
                return 12

            def close(self, handle: int) -> None:
                self.closed.append(handle)

            @staticmethod
            def assert_contract(directory: bool, create: bool, field: str) -> None:
                if not directory or create or field != "package":
                    raise AssertionError("unexpected guard reopen contract")

        class FakeChain:
            def __init__(self) -> None:
                self.api = FakeApi()
                self.bindings_checked = False
                self.leaf = 7

            def require_bindings(self) -> None:
                self.bindings_checked = True

        chain = FakeChain()
        snapshot._require_guard_output_binding(
            chain,
            output_handle=11,
            output_name="external-shell-output",
            expected=expected,
        )

        self.assertTrue(chain.bindings_checked)
        self.assertEqual([(7, "external-shell-output", True)], chain.api.reopens)
        self.assertEqual([12], chain.api.closed)


if __name__ == "__main__":
    unittest.main()
