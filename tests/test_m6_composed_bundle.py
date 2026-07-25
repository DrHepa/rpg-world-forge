from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import worldforge.composed_bundle as composed_module
import worldforge.directory_publish as directory_publish_module
from isoworld.content.loader import load_worldpack
from isoworld.runtime_adapter import RuntimeAdapterKey, StaticRuntimeAdapterRegistry
from worldforge.assetpack import build_assetpack
from worldforge.composed_bundle import (
    COMPOSED_BUNDLE_MANIFEST,
    ComposedBundleError,
    build_composed_runtime_bundle,
    validate_composed_runtime_bundle_manifest,
    verify_composed_runtime_bundle,
)
from worldforge.directory_publish import DirectoryPublishError, directory_identity
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.renderpack import build_renderpack
from worldforge.runtime_composition import RUNTIME_CAPABILITIES

ROOT = Path(__file__).resolve().parents[1]
WORLDPACK = ROOT / "content/compiled/foundation.worldpack.json"
M6_FIXTURES = ROOT / "examples/m6-contracts"


def _read(path: Path) -> dict[str, object]:
    if path.name.endswith(".composed-bundle.journal.json"):
        loaded = composed_module._read_journal_record(path)  # noqa: SLF001
        assert loaded is not None
        return loaded[0]
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class _FakeWindowsCall:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        return self.result


def _windows_dll_loader(
    kernel32: object,
    *,
    nt_set_information: object,
    nt_status_to_dos_error: object | None = None,
):
    ntdll = SimpleNamespace(
        NtSetInformationFile=nt_set_information,
        RtlNtStatusToDosError=nt_status_to_dos_error or _FakeWindowsCall(5),
    )

    def load(name: str, **_kwargs: object) -> object:
        if name == "kernel32":
            return kernel32
        if name == "ntdll":
            return ntdll
        raise OSError(f"unexpected Windows DLL: {name}")

    return load


class DirectoryPublicationPortabilityTests(unittest.TestCase):
    @staticmethod
    def _directory_state(identity: tuple[int, int]) -> SimpleNamespace:
        return SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700,
            st_dev=identity[0],
            st_ino=identity[1],
            st_nlink=1,
            st_size=0,
            st_mtime_ns=0,
            st_ctime_ns=0,
            st_file_attributes=0x10,
        )

    def _assert_windows_post_rename_failure_is_indeterminate(
        self,
        failure: str,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source = parent / "stage"
            destination = parent / "published"
            source.mkdir()
            source_state = directory_publish_module.path_file_stat(source)
            parent_state = directory_publish_module.path_file_stat(parent)
            source_identity = (source_state.st_dev, source_state.st_ino)
            parent_identity = (parent_state.st_dev, parent_state.st_ino)
            handles: dict[int, Path] = {}
            flush_counts: dict[int, int] = {}

            class CreateFile:
                argtypes: object = None
                restype: object = None

                def __call__(self, *args: object) -> int:
                    path = Path(str(args[0]))
                    handle = 880 + len(handles)
                    handles[handle] = path
                    return handle

            class SetInformation:
                argtypes: object = None
                restype: object = None

                def __call__(self, *_args: object) -> int:
                    source.rename(destination)
                    return 1

            class Flush:
                argtypes: object = None
                restype: object = None

                def __call__(self, handle: object) -> int:
                    value = int(handle.value)
                    flush_counts[value] = flush_counts.get(value, 0) + 1
                    if failure == "flush" and handles[value] == source and flush_counts[value] == 2:
                        return 0
                    return 1

            class Close:
                argtypes: object = None
                restype: object = None

                def __init__(self) -> None:
                    self.calls: list[int] = []

                def __call__(self, handle: object) -> int:
                    value = int(handle.value)
                    self.calls.append(value)
                    if failure in {"close", "validation_close"} and handles[value] == source:
                        return 0
                    return 1

            close = Close()
            kernel32 = SimpleNamespace(
                CreateFileW=CreateFile(),
                SetFileInformationByHandle=SetInformation(),
                FlushFileBuffers=Flush(),
                CloseHandle=close,
            )

            def audited_stat(path: str | Path):
                candidate = Path(path)
                if candidate == parent:
                    return parent_state
                if candidate == source:
                    if not source.exists():
                        raise FileNotFoundError(candidate)
                    return source_state
                if candidate == destination:
                    if failure == "validation_close":
                        raise OSError("injected destination validation failure")
                    return source_state
                raise FileNotFoundError(candidate)

            def handle_stat(handle: int):
                return source_state if handles[handle] == source else parent_state

            with (
                patch.object(
                    directory_publish_module,
                    "path_file_stat",
                    side_effect=audited_stat,
                ),
                patch.object(
                    directory_publish_module.ctypes,
                    "WinDLL",
                    create=True,
                    side_effect=_windows_dll_loader(
                        kernel32,
                        nt_set_information=kernel32.SetFileInformationByHandle,
                    ),
                ),
                patch.object(
                    directory_publish_module.ctypes,
                    "get_last_error",
                    create=True,
                    return_value=5,
                ),
                patch.object(
                    directory_publish_module.file_stat_module,
                    "_windows_handle_stat",
                    side_effect=handle_stat,
                ),
                self.assertRaises(
                    directory_publish_module.DirectoryPublishIndeterminateError
                ) as caught,
            ):
                with directory_publish_module._windows_rename_noreplace(  # noqa: SLF001
                    source,
                    destination,
                    source_identity=source_identity,
                    parent_identity=parent_identity,
                ):
                    pass

            self.assertIs(
                type(caught.exception),
                directory_publish_module.DirectoryPublishIndeterminateError,
            )
            self.assertIsInstance(caught.exception.__cause__, DirectoryPublishError)
            self.assertFalse(source.exists())
            self.assertTrue(destination.is_dir())
            self.assertEqual(
                3 if failure in {"flush", "validation_close"} else 4,
                len(close.calls),
            )
            if failure == "validation_close":
                self.assertTrue(
                    any(
                        "source handle cleanup" in note
                        for note in getattr(caught.exception, "__notes__", ())
                    )
                )

    def test_windows_publication_uses_handle_identity_across_rename_stat_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source = parent / "stage"
            destination = parent / "published"
            source.mkdir()
            parent_identity = (41, 101)
            published_identity = (41, (1 << 96) + 503)

            def audited_stat(path: str | Path):
                candidate = path
                if candidate == parent:
                    return self._directory_state(parent_identity)
                if candidate == source and not source.exists():
                    raise FileNotFoundError(candidate)
                if candidate in {source, destination}:
                    return self._directory_state(published_identity)
                raise FileNotFoundError(candidate)

            class _CreateFile:
                argtypes: object = None
                restype: object = None

                def __init__(self) -> None:
                    self.calls: list[tuple[object, ...]] = []

                def __call__(self, *args: object) -> int:
                    self.calls.append(args)
                    return 901 if args[0] == str(source) else 902

            class _SetInformation:
                argtypes: object = None
                restype: object = None

                def __init__(self) -> None:
                    self.calls: list[tuple[object, ...]] = []

                def __call__(self, *args: object) -> int:
                    self.calls.append(args)
                    source.rename(destination)
                    return 1

            create_file = _CreateFile()
            set_information = _SetInformation()
            flush = _FakeWindowsCall(1)
            close = _FakeWindowsCall(1)
            kernel32 = SimpleNamespace(
                CreateFileW=create_file,
                SetFileInformationByHandle=set_information,
                FlushFileBuffers=flush,
                CloseHandle=close,
            )

            def handle_stat(handle: int):
                return self._directory_state(
                    published_identity if handle == 901 else parent_identity
                )

            with (
                patch.object(
                    directory_publish_module,
                    "path_file_stat",
                    create=True,
                    side_effect=audited_stat,
                ),
                patch.object(directory_publish_module.sys, "platform", "win32"),
                patch.object(directory_publish_module.os, "name", "nt"),
                patch.object(
                    directory_publish_module.ctypes,
                    "WinDLL",
                    create=True,
                    side_effect=_windows_dll_loader(
                        kernel32,
                        nt_set_information=set_information,
                    ),
                ),
                patch.object(
                    directory_publish_module.file_stat_module,
                    "_windows_handle_stat",
                    side_effect=handle_stat,
                ),
            ):
                with directory_publish_module.publish_directory_noreplace(
                    source,
                    destination,
                    expected_source_identity=published_identity,
                ) as result:
                    pass

            self.assertEqual(published_identity, result)
            self.assertTrue(destination.is_dir())
            self.assertEqual(
                [str(source), str(parent), str(parent), str(parent)],
                [call[0] for call in create_file.calls],
            )
            self.assertEqual(
                [0xC0110000, 0x001000A1, 0x40100080, 0x40100080],
                [call[1] for call in create_file.calls],
            )
            self.assertEqual(
                [0x00000001, 0x00000003, 0x00000007, 0x00000007],
                [call[2] for call in create_file.calls],
            )
            self.assertEqual(1, len(set_information.calls))
            source_handle, io_status, payload, _size, information_class = set_information.calls[0]
            self.assertEqual(901, source_handle.value)
            self.assertEqual(10, information_class)
            io_status_type = directory_publish_module._IoStatusBlock  # noqa: SLF001
            self.assertEqual(
                2 * ctypes.sizeof(ctypes.c_void_p),
                ctypes.sizeof(io_status_type),
            )
            self.assertEqual(
                ctypes.sizeof(ctypes.c_void_p),
                io_status_type.information.offset,
            )
            self.assertIsNotNone(ctypes.cast(io_status, ctypes.POINTER(io_status_type)).contents)
            rename_type = directory_publish_module._NtFileRenameInformation  # noqa: SLF001
            self.assertEqual(0, rename_type.replace_if_exists.offset)
            self.assertEqual(ctypes.sizeof(ctypes.c_void_p), rename_type.root_directory.offset)
            self.assertEqual(
                rename_type.root_directory.offset + ctypes.sizeof(ctypes.c_void_p),
                rename_type.filename_length.offset,
            )
            self.assertEqual(
                rename_type.filename_length.offset + ctypes.sizeof(ctypes.c_uint32),
                rename_type.filename.offset,
            )
            rename = rename_type.from_buffer(payload)
            self.assertFalse(rename.replace_if_exists)
            self.assertEqual(902, rename.root_directory)
            filename_offset = rename_type.filename.offset
            self.assertEqual(
                destination.name,
                ctypes.string_at(
                    ctypes.addressof(payload) + filename_offset,
                    rename.filename_length,
                ).decode("utf-16-le"),
            )
            self.assertEqual(
                [901, 902, 901, 902],
                [call[0].value for call in flush.calls],
            )

    def test_windows_native_rename_maps_collisions_and_other_failures(self) -> None:
        collision_status = ctypes.c_int32(0xC0000035).value
        access_denied_status = ctypes.c_int32(0xC0000022).value
        for status, error, expected_error in (
            (collision_status, 183, FileExistsError),
            (access_denied_status, 5, DirectoryPublishError),
        ):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                source = parent / "stage"
                destination = parent / "published"
                source.mkdir()
                source_state = directory_publish_module.path_file_stat(source)
                parent_state = directory_publish_module.path_file_stat(parent)
                states = {
                    source: source_state,
                    parent: parent_state,
                }
                handles: dict[int, Path] = {}

                class CreateFile:
                    argtypes: object = None
                    restype: object = None

                    def __init__(self, opened: dict[int, Path]) -> None:
                        self.opened = opened

                    def __call__(self, *args: object) -> int:
                        path = Path(str(args[0]))
                        handle = 920 + len(self.opened)
                        self.opened[handle] = path
                        return handle

                nt_set_information = _FakeWindowsCall(status)
                nt_status_to_dos_error = _FakeWindowsCall(error)
                close = _FakeWindowsCall(1)
                kernel32 = SimpleNamespace(
                    CreateFileW=CreateFile(handles),
                    FlushFileBuffers=_FakeWindowsCall(1),
                    CloseHandle=close,
                )

                def audited_stat(
                    path: str | Path,
                    *,
                    expected_destination: Path = destination,
                    expected_states: dict[Path, object] = states,
                ):
                    candidate = Path(path)
                    if candidate == expected_destination:
                        raise FileNotFoundError(candidate)
                    return expected_states[candidate]

                with (
                    patch.object(
                        directory_publish_module,
                        "path_file_stat",
                        side_effect=audited_stat,
                    ),
                    patch.object(
                        directory_publish_module.ctypes,
                        "WinDLL",
                        create=True,
                        side_effect=_windows_dll_loader(
                            kernel32,
                            nt_set_information=nt_set_information,
                            nt_status_to_dos_error=nt_status_to_dos_error,
                        ),
                    ),
                    patch.object(
                        directory_publish_module.file_stat_module,
                        "_windows_handle_stat",
                        side_effect=lambda handle, states=states, handles=handles: states[
                            handles[handle]
                        ],
                    ),
                    self.assertRaises(expected_error) as caught,
                ):
                    with directory_publish_module._windows_rename_noreplace(  # noqa: SLF001
                        source,
                        destination,
                        source_identity=(
                            source_state.st_dev,
                            source_state.st_ino,
                        ),
                        parent_identity=(
                            parent_state.st_dev,
                            parent_state.st_ino,
                        ),
                    ):
                        pass

                self.assertEqual(error, caught.exception.errno)
                self.assertTrue(source.is_dir())
                self.assertFalse(destination.exists())
                self.assertEqual([(status,)], nt_status_to_dos_error.calls)
                self.assertEqual(1, len(nt_set_information.calls))
                self.assertEqual(10, nt_set_information.calls[0][4])
                self.assertEqual(len(handles), len(close.calls))

    def test_windows_rename_then_raise_is_indeterminate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source = parent / "stage"
            destination = parent / "published"
            source.mkdir()
            source_state = directory_publish_module.path_file_stat(source)
            parent_state = directory_publish_module.path_file_stat(parent)
            handles: dict[int, Path] = {}

            class CreateFile:
                argtypes: object = None
                restype: object = None

                def __call__(self, *args: object) -> int:
                    handle = 970 + len(handles)
                    handles[handle] = Path(str(args[0]))
                    return handle

            class RenameThenRaise:
                argtypes: object = None
                restype: object = None

                def __call__(self, *_args: object) -> int:
                    source.rename(destination)
                    raise KeyboardInterrupt("injected post-rename native interruption")

            close = _FakeWindowsCall(1)
            kernel32 = SimpleNamespace(
                CreateFileW=CreateFile(),
                FlushFileBuffers=_FakeWindowsCall(1),
                CloseHandle=close,
            )

            def handle_stat(handle: int):
                path = handles[handle]
                return source_state if path == source else parent_state

            with (
                patch.object(
                    directory_publish_module.ctypes,
                    "WinDLL",
                    create=True,
                    side_effect=_windows_dll_loader(
                        kernel32,
                        nt_set_information=RenameThenRaise(),
                    ),
                ),
                patch.object(
                    directory_publish_module.file_stat_module,
                    "_windows_handle_stat",
                    side_effect=handle_stat,
                ),
                self.assertRaisesRegex(
                    directory_publish_module.DirectoryPublishIndeterminateError,
                    "NtSetInformationFile raised",
                ) as caught,
            ):
                with directory_publish_module._windows_rename_noreplace(  # noqa: SLF001
                    source,
                    destination,
                    source_identity=(source_state.st_dev, source_state.st_ino),
                    parent_identity=(parent_state.st_dev, parent_state.st_ino),
                ):
                    pass

            self.assertIsInstance(caught.exception.__cause__, KeyboardInterrupt)
            self.assertFalse(source.exists())
            self.assertTrue(destination.is_dir())
            self.assertEqual(len(handles), len(close.calls))

    @unittest.skipUnless(
        sys.platform == "win32" and os.name == "nt",
        "requires native Windows directory publication",
    )
    def test_native_windows_directory_publication_and_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source = parent / "stage"
            destination = parent / "published"
            (source / "nested").mkdir(parents=True)
            (source / "nested/payload.bin").write_bytes(b"published payload")
            source_identity = directory_identity(
                source,
                context="native Windows publication source",
            )

            externally_blocked = parent / "externally-blocked"
            (externally_blocked / "nested").mkdir(parents=True)
            external_payload = externally_blocked / "nested/payload.bin"
            external_payload.write_bytes(b"externally retained")
            external_identity = directory_identity(
                externally_blocked,
                context="native Windows externally retained source",
            )
            external_destination = parent / "external-published"
            with external_payload.open("rb"):
                with self.assertRaises(DirectoryPublishError):
                    with directory_publish_module._windows_rename_noreplace(  # noqa: SLF001
                        externally_blocked,
                        external_destination,
                        source_identity=external_identity,
                        parent_identity=directory_identity(
                            parent,
                            context="native Windows external parent",
                        ),
                    ):
                        pass
            self.assertTrue(externally_blocked.is_dir())
            with directory_publish_module._windows_rename_noreplace(  # noqa: SLF001
                externally_blocked,
                external_destination,
                source_identity=external_identity,
                parent_identity=directory_identity(
                    parent,
                    context="native Windows released external parent",
                ),
            ):
                self.assertEqual(
                    b"externally retained",
                    (external_destination / "nested/payload.bin").read_bytes(),
                )

            with self.assertRaisesRegex(
                directory_publish_module.DirectoryPublishIndeterminateError,
                "caller verification",
            ):
                with directory_publish_module.publish_directory_noreplace(
                    source,
                    destination,
                    expected_source_identity=source_identity,
                ) as published_identity:
                    self.assertFalse(source.exists())
                    published_payload = destination / "nested/payload.bin"
                    self.assertEqual(
                        b"published payload",
                        published_payload.read_bytes(),
                    )
                    with self.assertRaises(OSError):
                        published_payload.write_bytes(b"mutated payload")
                    with self.assertRaises(OSError):
                        published_payload.unlink()
                    with self.assertRaises(OSError):
                        published_payload.rename(destination / "nested/renamed.bin")
                    (destination / "new-entry.bin").write_bytes(b"new")

            self.assertEqual(source_identity, published_identity)
            self.assertEqual(
                b"published payload",
                (destination / "nested/payload.bin").read_bytes(),
            )
            self.assertEqual(b"new", (destination / "new-entry.bin").read_bytes())

            contender = parent / "contender"
            winner = parent / "winner"
            contender.mkdir()
            (contender / "payload.bin").write_bytes(b"contender")
            winner.mkdir()
            (winner / "payload.bin").write_bytes(b"winner")
            contender_identity = directory_identity(
                contender,
                context="native Windows collision contender",
            )
            winner_identity = directory_identity(
                winner,
                context="native Windows collision winner",
            )

            with self.assertRaises(FileExistsError):
                with directory_publish_module._windows_rename_noreplace(  # noqa: SLF001
                    contender,
                    winner,
                    source_identity=contender_identity,
                    parent_identity=directory_identity(
                        parent,
                        context="native Windows collision parent",
                    ),
                ):
                    pass

            self.assertTrue(contender.is_dir())
            self.assertEqual(
                contender_identity,
                directory_identity(
                    contender,
                    context="native Windows retained contender",
                ),
            )
            self.assertEqual(
                winner_identity,
                directory_identity(
                    winner,
                    context="native Windows retained winner",
                ),
            )
            self.assertEqual(b"contender", (contender / "payload.bin").read_bytes())
            self.assertEqual(b"winner", (winner / "payload.bin").read_bytes())

    def test_windows_publication_flushes_files_and_deep_directories_before_rename(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source = parent / "stage"
            destination = parent / "published"
            (source / "nested/deeper").mkdir(parents=True)
            (source / "root.txt").write_text("root\n", encoding="utf-8")
            (source / "nested/child.txt").write_text("child\n", encoding="utf-8")
            (source / "nested/deeper/grand.txt").write_text("grand\n", encoding="utf-8")
            real_path_file_stat = directory_publish_module.path_file_stat
            paths = (
                parent,
                source,
                source / "nested",
                source / "nested/child.txt",
                source / "nested/deeper",
                source / "nested/deeper/grand.txt",
                source / "root.txt",
            )
            states = {path: real_path_file_stat(path) for path in paths}
            source_identity = (
                states[source].st_dev,
                states[source].st_ino,
            )
            parent_identity = (
                states[parent].st_dev,
                states[parent].st_ino,
            )
            handles: dict[int, Path] = {}
            handle_access: dict[int, int] = {}
            handle_shares: dict[int, int] = {}
            events: list[tuple[str, str]] = []
            descendant_release_verified = False
            parent_handle_lifecycle_verified = False

            class CreateFile:
                argtypes: object = None
                restype: object = None

                def __init__(self) -> None:
                    self.calls: list[tuple[object, ...]] = []

                def __call__(self, *args: object) -> int:
                    self.calls.append(args)
                    path = Path(str(args[0]))
                    handle = 900 + len(handles)
                    handles[handle] = path
                    handle_access[handle] = int(args[1])
                    handle_shares[handle] = int(args[2])
                    events.append(("open", path.relative_to(parent).as_posix() or "."))
                    return handle

            class SetInformation:
                argtypes: object = None
                restype: object = None

                def __call__(self, *args: object) -> int:
                    nonlocal descendant_release_verified
                    nonlocal parent_handle_lifecycle_verified
                    closed = {int(call[0].value) for call in close.calls}
                    payload_handles = {
                        handle for handle, path in handles.items() if path not in {source, parent}
                    }
                    if not payload_handles or not payload_handles <= closed:
                        raise AssertionError(
                            "Windows payload handles remained open during directory rename"
                        )
                    descendant_release_verified = True
                    parent_handles = [handle for handle, path in handles.items() if path == parent]
                    if (
                        len(parent_handles) != 2
                        or parent_handles[0] in closed
                        or parent_handles[1] not in closed
                    ):
                        raise AssertionError(
                            "Windows parent identity/flush handle lifetimes are invalid"
                        )
                    parent_handle_lifecycle_verified = True
                    events.append(("rename", destination.name))
                    source.rename(destination)
                    return 1

            class Flush:
                argtypes: object = None
                restype: object = None

                def __call__(self, handle: object) -> int:
                    value = int(handle.value)
                    path = handles[value]
                    events.append(("flush", path.relative_to(parent).as_posix() or "."))
                    return 1

            close = _FakeWindowsCall(1)
            create_file = CreateFile()
            set_information = SetInformation()
            flush = Flush()
            kernel32 = SimpleNamespace(
                CreateFileW=create_file,
                SetFileInformationByHandle=set_information,
                FlushFileBuffers=flush,
                CloseHandle=close,
            )

            def handle_stat(handle: int):
                candidate = handles[handle]
                if candidate == destination or destination in candidate.parents:
                    candidate = source / candidate.relative_to(destination)
                return states[candidate]

            def audited_stat(path: str | Path):
                candidate = Path(path)
                if candidate == source and not source.exists():
                    raise FileNotFoundError(candidate)
                if candidate == destination or destination in candidate.parents:
                    candidate = source / candidate.relative_to(destination)
                return states[candidate]

            with (
                patch.object(
                    directory_publish_module,
                    "path_file_stat",
                    side_effect=audited_stat,
                ),
                patch.object(
                    directory_publish_module.ctypes,
                    "WinDLL",
                    create=True,
                    side_effect=_windows_dll_loader(
                        kernel32,
                        nt_set_information=set_information,
                    ),
                ),
                patch.object(
                    directory_publish_module.file_stat_module,
                    "_windows_handle_stat",
                    side_effect=handle_stat,
                ),
            ):
                with directory_publish_module._windows_rename_noreplace(  # noqa: SLF001
                    source,
                    destination,
                    source_identity=source_identity,
                    parent_identity=parent_identity,
                ) as published:
                    post_handles = {
                        handle for handle, path in handles.items() if destination in path.parents
                    }
                    self.assertEqual(5, len(post_handles))
                    self.assertTrue(
                        all(handle_shares[handle] == 0x00000001 for handle in post_handles)
                    )
                    self.assertTrue(
                        all(handle_access[handle] == 0x00100081 for handle in post_handles)
                    )

            self.assertEqual(source_identity, published)
            self.assertTrue(descendant_release_verified)
            self.assertTrue(parent_handle_lifecycle_verified)
            self.assertEqual(
                [
                    ("flush", "stage/nested/child.txt"),
                    ("flush", "stage/nested/deeper/grand.txt"),
                    ("flush", "stage/root.txt"),
                    ("flush", "stage/nested/deeper"),
                    ("flush", "stage/nested"),
                    ("flush", "stage"),
                    ("flush", "."),
                    ("rename", "published"),
                    ("flush", "stage"),
                    ("flush", "."),
                ],
                [event for event in events if event[0] != "open"],
            )
            self.assertEqual(
                [
                    0x00000001,
                    0x00000003,
                    *([0x00000001] * 5),
                    0x00000007,
                    *([0x00000001] * 5),
                    0x00000007,
                ],
                [call[2] for call in create_file.calls],
            )
            self.assertEqual(
                [
                    0xC0110000,
                    0x001000A1,
                    *([0xC0100000] * 5),
                    0x40100080,
                    *([0x00100081] * 5),
                    0x40100080,
                ],
                [call[1] for call in create_file.calls],
            )
            parent_calls = [call for call in create_file.calls if Path(str(call[0])) == parent]
            self.assertEqual(
                [0x001000A1, 0x40100080, 0x40100080],
                [call[1] for call in parent_calls],
            )
            self.assertEqual(len(handles), len(close.calls))

    def test_windows_release_gap_same_metadata_substitution_is_indeterminate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source = parent / "stage"
            destination = parent / "published"
            source.mkdir()
            payload = source / "payload.bin"
            payload.write_bytes(b"clean\n")
            source_identity = directory_identity(
                source,
                context="Windows substitution source",
            )
            parent_identity = directory_identity(
                parent,
                context="Windows substitution parent",
            )
            real_path_file_stat = directory_publish_module.path_file_stat
            source_state = real_path_file_stat(source)
            payload_state = real_path_file_stat(payload)
            states = {
                parent: real_path_file_stat(parent),
                source: source_state,
                payload: payload_state,
                destination: source_state,
                destination / payload.name: payload_state,
            }
            handles: dict[int, Path] = {}
            closed: set[int] = set()
            source_handle: int | None = None

            class CreateFile:
                argtypes: object = None
                restype: object = None

                def __call__(self, *args: object) -> int:
                    nonlocal source_handle
                    handle = 1100 + len(handles)
                    path = Path(str(args[0]))
                    handles[handle] = path
                    if path == source and source_handle is None:
                        source_handle = handle
                    return handle

            class SetInformation:
                argtypes: object = None
                restype: object = None

                def __call__(self, *_args: object) -> int:
                    descendants = {
                        handle for handle, path in handles.items() if path not in {source, parent}
                    }
                    if not descendants or not descendants <= closed:
                        raise AssertionError("internal descendants must be released before rename")
                    before = payload.stat()
                    payload.write_bytes(b"evil!\n")
                    os.utime(
                        payload,
                        ns=(before.st_atime_ns, before.st_mtime_ns),
                    )
                    source.rename(destination)
                    return 1

            class Close:
                argtypes: object = None
                restype: object = None

                def __call__(self, handle: object) -> int:
                    closed.add(int(handle.value))
                    return 1

            def handle_stat(handle: int):
                candidate = handles[handle]
                if handle == source_handle and not source.exists():
                    candidate = destination
                return states[candidate]

            def audited_stat(path: str | Path):
                candidate = Path(path)
                if candidate == source and not source.exists():
                    raise FileNotFoundError(candidate)
                if candidate == destination or destination in candidate.parents:
                    candidate = source / candidate.relative_to(destination)
                return states[candidate]

            kernel32 = SimpleNamespace(
                CreateFileW=CreateFile(),
                FlushFileBuffers=_FakeWindowsCall(1),
                CloseHandle=Close(),
            )
            with (
                patch.object(
                    directory_publish_module,
                    "path_file_stat",
                    side_effect=audited_stat,
                ),
                patch.object(
                    directory_publish_module.ctypes,
                    "WinDLL",
                    create=True,
                    side_effect=_windows_dll_loader(
                        kernel32,
                        nt_set_information=SetInformation(),
                    ),
                ),
                patch.object(
                    directory_publish_module.file_stat_module,
                    "_windows_handle_stat",
                    side_effect=handle_stat,
                ),
                self.assertRaisesRegex(
                    directory_publish_module.DirectoryPublishIndeterminateError,
                    "fingerprint",
                ),
            ):
                with directory_publish_module._windows_rename_noreplace(  # noqa: SLF001
                    source,
                    destination,
                    source_identity=source_identity,
                    parent_identity=parent_identity,
                ):
                    pass

            self.assertFalse(source.exists())
            self.assertEqual(b"evil!\n", (destination / "payload.bin").read_bytes())

    def test_windows_post_rename_flush_failure_is_indeterminate(self) -> None:
        self._assert_windows_post_rename_failure_is_indeterminate("flush")

    def test_windows_post_rename_validation_and_close_failures_are_indeterminate(
        self,
    ) -> None:
        for failure in ("validation_close", "close"):
            with self.subTest(failure=failure):
                self._assert_windows_post_rename_failure_is_indeterminate(failure)

    def test_windows_payload_flush_failure_closes_all_handles_without_rename(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source = parent / "stage"
            destination = parent / "published"
            (source / "nested").mkdir(parents=True)
            (source / "nested/child.txt").write_text("child\n", encoding="utf-8")
            real_path_file_stat = directory_publish_module.path_file_stat
            paths = (
                parent,
                source,
                source / "nested",
                source / "nested/child.txt",
            )
            states = {path: real_path_file_stat(path) for path in paths}
            handles: dict[int, Path] = {}

            class CreateFile:
                argtypes: object = None
                restype: object = None

                def __init__(self) -> None:
                    self.calls: list[tuple[object, ...]] = []

                def __call__(self, *args: object) -> int:
                    self.calls.append(args)
                    path = Path(str(args[0]))
                    handle = 930 + len(handles)
                    handles[handle] = path
                    return handle

            class Flush:
                argtypes: object = None
                restype: object = None

                def __call__(self, handle: object) -> int:
                    path = handles[int(handle.value)]
                    return 0 if path == source / "nested" else 1

            create_file = CreateFile()
            set_information = _FakeWindowsCall(1)
            close = _FakeWindowsCall(1)
            kernel32 = SimpleNamespace(
                CreateFileW=create_file,
                SetFileInformationByHandle=set_information,
                FlushFileBuffers=Flush(),
                CloseHandle=close,
            )

            def audited_stat(path: str | Path):
                return states[Path(path)]

            with (
                patch.object(
                    directory_publish_module,
                    "path_file_stat",
                    side_effect=audited_stat,
                ),
                patch.object(
                    directory_publish_module.ctypes,
                    "WinDLL",
                    create=True,
                    side_effect=_windows_dll_loader(
                        kernel32,
                        nt_set_information=set_information,
                    ),
                ),
                patch.object(
                    directory_publish_module.ctypes,
                    "get_last_error",
                    create=True,
                    return_value=5,
                ),
                patch.object(
                    directory_publish_module.file_stat_module,
                    "_windows_handle_stat",
                    side_effect=lambda handle: states[handles[handle]],
                ),
                self.assertRaisesRegex(
                    DirectoryPublishError,
                    "durably flush.*nested",
                ),
            ):
                with directory_publish_module._windows_rename_noreplace(  # noqa: SLF001
                    source,
                    destination,
                    source_identity=(
                        states[source].st_dev,
                        states[source].st_ino,
                    ),
                    parent_identity=(
                        states[parent].st_dev,
                        states[parent].st_ino,
                    ),
                ):
                    pass

            self.assertTrue(source.is_dir())
            self.assertFalse(destination.exists())
            self.assertEqual([], set_information.calls)
            self.assertEqual(len(handles), len(close.calls))
            self.assertEqual(
                [0x00000001, 0x00000003, *([0x00000001] * (len(handles) - 2))],
                [call[2] for call in create_file.calls],
            )

    def test_windows_replaced_source_handle_is_rejected_before_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source = parent / "stage"
            destination = parent / "published"
            source.mkdir()
            expected_identity = (61, 701)
            foreign_identity = (61, 702)
            parent_identity = (61, 703)
            create_file = _FakeWindowsCall(901)
            close = _FakeWindowsCall(1)
            set_information = _FakeWindowsCall(1)
            kernel32 = SimpleNamespace(
                CreateFileW=create_file,
                CloseHandle=close,
                FlushFileBuffers=_FakeWindowsCall(1),
                SetFileInformationByHandle=set_information,
            )

            def audited_stat(path: str | Path):
                if path == parent:
                    return self._directory_state(parent_identity)
                raise FileNotFoundError(path)

            with (
                patch.object(
                    directory_publish_module,
                    "path_file_stat",
                    side_effect=audited_stat,
                ),
                patch.object(directory_publish_module.sys, "platform", "win32"),
                patch.object(directory_publish_module.os, "name", "nt"),
                patch.object(
                    directory_publish_module.ctypes,
                    "WinDLL",
                    create=True,
                    side_effect=_windows_dll_loader(
                        kernel32,
                        nt_set_information=set_information,
                    ),
                ),
                patch.object(
                    directory_publish_module.file_stat_module,
                    "_windows_handle_stat",
                    return_value=self._directory_state(foreign_identity),
                ),
                self.assertRaisesRegex(DirectoryPublishError, "identity changed"),
            ):
                with directory_publish_module.publish_directory_noreplace(
                    source,
                    destination,
                    expected_source_identity=expected_identity,
                ):
                    pass

            self.assertTrue(source.is_dir())
            self.assertFalse(destination.exists())
            self.assertEqual([], set_information.calls)
            self.assertEqual([901], [call[0].value for call in close.calls])

    def test_windows_disposition_uses_one_byte_boolean_abi(self) -> None:
        disposition_type = directory_publish_module._FileDispositionInfo  # noqa: SLF001
        self.assertEqual(1, ctypes.sizeof(disposition_type))
        self.assertEqual(0, disposition_type.delete_file.offset)

        set_information = _FakeWindowsCall(1)
        kernel32 = SimpleNamespace(SetFileInformationByHandle=set_information)
        with patch.object(
            directory_publish_module.ctypes,
            "WinDLL",
            create=True,
            return_value=kernel32,
        ):
            directory_publish_module._windows_mark_handle_for_deletion(  # noqa: SLF001
                901,
                Path("C:/synthetic/cleanup"),
            )

        self.assertEqual(1, len(set_information.calls))
        handle, information_class, payload, payload_size = set_information.calls[0]
        self.assertEqual(901, handle.value)
        self.assertEqual(4, information_class)
        self.assertEqual(1, payload_size)
        self.assertEqual(
            1,
            ctypes.cast(payload, ctypes.POINTER(ctypes.c_ubyte)).contents.value,
        )

    def test_windows_directory_flush_uses_one_pinned_kernel_handle(self) -> None:
        identity = (53, (1 << 104) + 907)
        state = self._directory_state(identity)
        create_file = _FakeWindowsCall(901)
        flush_file_buffers = _FakeWindowsCall(1)
        close_handle = _FakeWindowsCall(1)
        kernel32 = SimpleNamespace(
            CreateFileW=create_file,
            FlushFileBuffers=flush_file_buffers,
            CloseHandle=close_handle,
        )
        handle_stat = _FakeWindowsCall(state)
        file_stat_module = SimpleNamespace(_windows_handle_stat=handle_stat)
        path = Path("C:/synthetic/composed-stage")

        with (
            patch.object(
                directory_publish_module.ctypes,
                "WinDLL",
                create=True,
                return_value=kernel32,
            ),
            patch.object(
                directory_publish_module,
                "file_stat_module",
                file_stat_module,
                create=True,
            ),
        ):
            directory_publish_module._windows_fsync_directory(  # noqa: SLF001
                path,
                expected_identity=identity,
            )

        self.assertEqual(1, len(create_file.calls))
        create_args = create_file.calls[0]
        self.assertEqual(str(path), create_args[0])
        self.assertEqual(0xC0000000, create_args[1])
        self.assertEqual(0x00000003, create_args[2])
        self.assertEqual(3, create_args[4])
        self.assertEqual(0x02200000, create_args[5])
        self.assertEqual([901, 901], [call[0] for call in handle_stat.calls])
        self.assertEqual(901, flush_file_buffers.calls[0][0].value)
        self.assertEqual(901, close_handle.calls[0][0].value)

    def test_journal_identity_accepts_exact_unsigned_windows_file_id_width(
        self,
    ) -> None:
        destination = Path("portable-bundle")
        stage = Path(".portable-bundle.composed-0123456789abcdef0123456789abcdef")
        identity = (2**64 - 1, 2**128 - 1)
        journal = composed_module._journal_document(  # noqa: SLF001
            operation_id="0123456789abcdef0123456789abcdef",
            state="ready",
            stage=stage,
            destination=destination,
            stage_identity=identity,
            platform="windows_x86_64",
            runtime_api_version="0.5.0",
            bundle_hash="a" * 64,
        )

        validated = composed_module._validate_journal(journal, destination)  # noqa: SLF001
        self.assertEqual(
            identity,
            composed_module._identity_from_document(  # noqa: SLF001
                validated["stage_identity"],
                "journal/stage_identity",
            ),
        )

        for field, invalid in (
            ("device", 2**64),
            ("inode", 2**128),
            ("inode", -1),
            ("inode", True),
        ):
            with self.subTest(field=field, invalid=invalid):
                malformed = copy.deepcopy(journal)
                malformed["stage_identity"][field] = invalid
                with self.assertRaises(ComposedBundleError):
                    composed_module._validate_journal(  # noqa: SLF001
                        malformed,
                        destination,
                    )


class ComposedRuntimeBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.work = Path(cls.temporary.name)
        cls.neutral = cls.work / "neutral"
        shutil.copytree(ROOT / "examples/m5-neutral", cls.neutral)
        (cls.neutral / "renderpack/build").mkdir()
        (cls.neutral / "assetpack/build").mkdir()
        cls.renderpack = cls.neutral / "renderpack/build/renderpack.json"
        cls.assetpack = cls.neutral / "assetpack/build/assetpack.json"
        cls.renderpack_document = build_renderpack(
            cls.neutral / "renderpack/manifest.json",
            WORLDPACK,
            cls.renderpack,
        )
        cls.assetpack_document = build_assetpack(
            cls.neutral / "assetpack/manifest.json",
            WORLDPACK,
            cls.assetpack,
        )
        cls.worldpack = load_worldpack(WORLDPACK)
        cls.catalog = _read(M6_FIXTURES / "capability-catalog.json")
        cls.adapter = _read(M6_FIXTURES / "adapter.declared.json")
        cls.adapter["state"] = "verified"
        cls.adapter["capability_ids"] = sorted(RUNTIME_CAPABILITIES)
        cls.adapter["content_hash"] = canonical_payload_hash(cls.adapter)
        cls.adapter_key = RuntimeAdapterKey(
            str(cls.adapter["id"]),
            str(cls.adapter["version"]),
            str(cls.adapter["content_hash"]),
        )
        cls.adapter_value = object()
        cls.registry = StaticRuntimeAdapterRegistry([(cls.adapter_key, cls.adapter_value)])
        cls.notice = cls.work / "NOTICE.txt"
        cls.notice.write_bytes(b"Synthetic neutral test assets only.\n")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def _documents(
        self,
        name: str,
        *,
        profile_id: str,
        renderpack: bool,
        assetpack: bool,
    ) -> Path:
        root = self.work / f"documents-{name}-{uuid.uuid4().hex}"
        root.mkdir()
        profile = _read(M6_FIXTURES / f"profiles/{profile_id}.json")
        required = sorted(
            set(self.worldpack.runtime_requirements.required_features)
            | set(profile["required_capability_ids"])
        )
        packs: dict[str, object] = {
            "worldpack": {
                "format": "isoworld.worldpack",
                "format_version": self.worldpack.format_version,
                "path": "packs/worldpack/worldpack.json",
                "content_hash": self.worldpack.content_hash,
            }
        }
        owners: list[dict[str, str]] = []
        if assetpack:
            packs["assetpack"] = {
                "format": "rpg-world-forge.assetpack",
                "format_version": 1,
                "path": "packs/assetpack/assetpack.json",
                "content_hash": self.assetpack_document["content_hash"],
            }
            owners.append(
                {
                    "slot": "actor:neutral",
                    "plane": "world_base",
                    "pack": "assetpack",
                    "asset_id": "neutral_actor_3d",
                    "representation": "3d",
                }
            )
        if renderpack:
            packs["renderpack"] = {
                "format": "isoworld.renderpack",
                "format_version": 1,
                "path": "packs/renderpack/renderpack.json",
                "content_hash": self.renderpack_document["content_hash"],
            }
            owners.append(
                {
                    "slot": "ui:font" if assetpack else "actor:neutral",
                    "plane": "world_overlay" if assetpack else "world_base",
                    "pack": "renderpack",
                    "asset_id": "neutral_font" if assetpack else "neutral_sheet",
                    "representation": "2d",
                }
            )
        owners.sort(
            key=lambda item: (
                item["slot"],
                item["plane"],
                item["pack"],
                item["asset_id"],
                item["representation"],
            )
        )
        composition: dict[str, object] = {
            "format": "rpg-world-forge.runtime_composition",
            "format_version": 1,
            "world_id": self.worldpack.world_id,
            "world_content_hash": self.worldpack.content_hash,
            "release_id": "1.0.0",
            "profile": {
                "id": profile["id"],
                "content_hash": profile["content_hash"],
            },
            "capability_catalog_hash": self.catalog["content_hash"],
            "adapter": {
                "id": self.adapter["id"],
                "version": self.adapter["version"],
                "content_hash": self.adapter["content_hash"],
            },
            "packs": packs,
            "required_capability_ids": required,
            "slot_owners": owners,
        }
        composition["content_hash"] = canonical_payload_hash(composition)
        for filename, document in (
            ("catalog.json", self.catalog),
            ("profile.json", profile),
            ("adapter.json", self.adapter),
            ("composition.json", composition),
        ):
            (root / filename).write_bytes(canonical_json_bytes(document))
        return root

    def _build(
        self,
        name: str,
        *,
        profile_id: str = "profile_2d",
        renderpack: bool = True,
        assetpack: bool = False,
        platform: str = "linux_x86_64",
        destination: Path | None = None,
        license_sources: dict[str, Path] | None = None,
    ):
        documents = self._documents(
            name,
            profile_id=profile_id,
            renderpack=renderpack,
            assetpack=assetpack,
        )
        destination = destination or self.work / f"bundle-{name}-{uuid.uuid4().hex}"
        return build_composed_runtime_bundle(
            documents / "catalog.json",
            documents / "profile.json",
            documents / "adapter.json",
            documents / "composition.json",
            WORLDPACK,
            destination,
            bundle_id="neutral_bundle",
            bundle_version="1.0.0",
            platform=platform,
            registry=self.registry,
            license_sources=license_sources or {"NOTICE.txt": self.notice},
            renderpack_path=self.renderpack if renderpack else None,
            assetpack_path=self.assetpack if assetpack else None,
        )

    def test_builds_deterministic_exact_render_only_bundles_across_roots(self) -> None:
        first_root = self.work / f"deterministic-a-{uuid.uuid4().hex}"
        second_root = self.work / f"deterministic-b-{uuid.uuid4().hex}"
        first = self._build("deterministic-a", destination=first_root)
        second = self._build("deterministic-b", destination=second_root)
        try:
            self.assertEqual(first.bundle_hash, second.bundle_hash)
            self.assertEqual(_tree_bytes(first_root), _tree_bytes(second_root))
            self.assertEqual(
                [],
                [
                    path
                    for path in first_root.parent.glob(f".{first_root.name}.composed-*")
                    if len(path.name.rsplit("-", 1)[-1]) == 32
                ],
            )
            self.assertEqual(
                [],
                [
                    path
                    for path in second_root.parent.glob(f".{second_root.name}.composed-*")
                    if len(path.name.rsplit("-", 1)[-1]) == 32
                ],
            )
            self.assertIs(self.adapter_value, first.registered.adapter_value)
            self.assertTrue(first.verification.compatible)
            self.assertIsNotNone(first.renderpack)
            self.assertIsNone(first.assetpack)
            manifest = first.manifest
            self.assertIsNone(manifest["packs"]["assetpack"])
            self.assertNotIn(
                str(self.work), first_root.joinpath(COMPOSED_BUNDLE_MANIFEST).read_text()
            )
        finally:
            first.close()
            second.close()

    def test_builds_asset_only_and_combined_profiles(self) -> None:
        asset_only = self._build(
            "asset-only",
            profile_id="profile_3d",
            renderpack=False,
            assetpack=True,
        )
        combined = self._build(
            "combined",
            profile_id="profile_2d_over_3d",
            renderpack=True,
            assetpack=True,
        )
        try:
            self.assertIsNone(asset_only.renderpack)
            self.assertIsNotNone(asset_only.assetpack)
            self.assertIsNotNone(combined.renderpack)
            self.assertIsNotNone(combined.assetpack)
            self.assertEqual(
                "profile_2d_over_3d", combined.registered.documents.presentation_profile["id"]
            )
        finally:
            asset_only.close()
            combined.close()

    def test_loaded_bundle_survives_published_tree_mutation(self) -> None:
        destination = self.work / f"mutation-{uuid.uuid4().hex}"
        loaded = self._build("mutation", destination=destination)
        assert loaded.renderpack is not None
        item = loaded.renderpack.assets[0].files[0]
        before = loaded.renderpack.resolve_file(item).read_bytes()
        public = destination / "licenses/NOTICE.txt"
        public.write_bytes(b"tampered after load\n")

        self.assertEqual("neutral_bundle", loaded.bundle_id)
        self.assertEqual(before, loaded.renderpack.resolve_file(item).read_bytes())
        loaded.close()

    def test_tampered_persisted_evidence_is_recomputed_and_rejected(self) -> None:
        destination = self.work / f"evidence-{uuid.uuid4().hex}"
        built = self._build("evidence", destination=destination)
        built.close()
        report_path = destination / "evidence/runtime-compatibility-report.json"
        report = _read(report_path)
        report["platform"] = "windows_x86_64"
        report["content_hash"] = canonical_payload_hash(report)
        report_path.write_bytes(canonical_json_bytes(report))
        manifest_path = destination / COMPOSED_BUNDLE_MANIFEST
        manifest = _read(manifest_path)
        for record in manifest["files"]:
            if record["path"] == "evidence/runtime-compatibility-report.json":
                record["sha256"] = _sha256(report_path)
                record["size"] = report_path.stat().st_size
        manifest["compatibility_evidence"]["content_hash"] = report["content_hash"]
        manifest["bundle_hash"] = canonical_payload_hash(
            manifest,
            hash_field="bundle_hash",
        )
        manifest_path.write_bytes(canonical_json_bytes(manifest))

        with self.assertRaisesRegex(ComposedBundleError, "freshly recomputed"):
            verify_composed_runtime_bundle(
                destination,
                expected_bundle_hash=manifest["bundle_hash"],
                platform="linux_x86_64",
                runtime_api_version="0.5.0",
                registry=self.registry,
            )

    def test_wrong_registry_key_and_tree_attacks_fail_closed(self) -> None:
        original = self.work / f"attacks-{uuid.uuid4().hex}"
        built = self._build("attacks", destination=original)
        bundle_hash = built.bundle_hash
        built.close()
        wrong_registry: StaticRuntimeAdapterRegistry[object] = StaticRuntimeAdapterRegistry()
        with self.assertRaisesRegex(ComposedBundleError, "exact code-owned"):
            verify_composed_runtime_bundle(
                original,
                expected_bundle_hash=bundle_hash,
                platform="linux_x86_64",
                runtime_api_version="0.5.0",
                registry=wrong_registry,
            )

        for attack in ("extra", "symlink", "hardlink"):
            with self.subTest(attack=attack):
                target = self.work / f"attack-{attack}-{uuid.uuid4().hex}"
                shutil.copytree(original, target)
                notice = target / "licenses/NOTICE.txt"
                outside = self.work / f"outside-{attack}-{uuid.uuid4().hex}.txt"
                if attack == "extra":
                    (target / "licenses/EXTRA.txt").write_text("extra\n")
                elif attack == "symlink":
                    outside.write_text("replacement\n")
                    notice.unlink()
                    notice.symlink_to(outside)
                else:
                    outside.write_text("outside\n")
                    notice.unlink()
                    os.link(outside, notice)
                with self.assertRaises(ComposedBundleError):
                    verify_composed_runtime_bundle(
                        target,
                        expected_bundle_hash=bundle_hash,
                        platform="linux_x86_64",
                        runtime_api_version="0.5.0",
                        registry=self.registry,
                    )

    def test_runtime_boundary_rejects_provider_metadata_in_notice_json(self) -> None:
        notice = self.work / f"provider-{uuid.uuid4().hex}.json"
        notice.write_bytes(
            canonical_json_bytes(
                {
                    "format": "example.notice",
                    "provider": "must-not-enter-runtime",
                }
            )
        )
        with self.assertRaisesRegex(ComposedBundleError, "provider"):
            self._build(
                "provider",
                license_sources={"NOTICE.json": notice},
            )

    def test_existing_destination_is_preserved_and_unsupported_platform_is_early(
        self,
    ) -> None:
        destination = self.work / f"existing-{uuid.uuid4().hex}"
        destination.mkdir()
        marker = destination / "marker.txt"
        marker.write_text("preserve\n")
        with self.assertRaisesRegex(ComposedBundleError, "already exists"):
            self._build("existing", destination=destination)
        self.assertEqual("preserve\n", marker.read_text())

        documents = self._documents(
            "unsupported",
            profile_id="profile_2d",
            renderpack=True,
            assetpack=False,
        )
        unsupported = self.work / f"unsupported-{uuid.uuid4().hex}"
        with (
            patch.object(composed_module.sys, "platform", "darwin"),
            patch.object(composed_module.os, "name", "posix"),
            self.assertRaisesRegex(ComposedBundleError, "Linux and Windows"),
        ):
            build_composed_runtime_bundle(
                documents / "catalog.json",
                documents / "profile.json",
                documents / "adapter.json",
                documents / "composition.json",
                WORLDPACK,
                unsupported,
                bundle_id="neutral_bundle",
                bundle_version="1.0.0",
                platform="linux_x86_64",
                registry=self.registry,
                license_sources={"NOTICE.txt": self.notice},
                renderpack_path=self.renderpack,
            )
        self.assertFalse(unsupported.exists())

    def test_copying_journal_recovers_owned_stage_before_new_build(self) -> None:
        destination = self.work / f"recovery-{uuid.uuid4().hex}"
        operation_id = uuid.uuid4().hex
        stage = destination.parent / f".{destination.name}.composed-{operation_id}"
        (stage / "contracts").mkdir(parents=True)
        (stage / "contracts/runtime-composition.json").write_text("{}\n")
        identity = directory_identity(stage, context="test recovery stage")
        journal = composed_module._journal_document(
            operation_id=operation_id,
            state="copying",
            stage=stage,
            destination=destination,
            stage_identity=identity,
            platform="linux_x86_64",
            runtime_api_version="0.5.0",
            bundle_hash=None,
        )
        journal_path = composed_module._journal_path(destination)
        journal_path.write_bytes(canonical_json_bytes(journal))

        with self.assertRaisesRegex(ComposedBundleError, "incomplete"):
            self._build("recovery", destination=destination)
        self.assertTrue(stage.is_dir())
        self.assertTrue(journal_path.is_file())
        self.assertEqual([], list(stage.parent.glob(f".{stage.name}.rollback-*")))

    def test_ready_destination_recovery_reflushes_before_journal_cleanup(self) -> None:
        destination = self.work / f"durability-recovery-{uuid.uuid4().hex}"
        built = self._build("durability-recovery", destination=destination)
        bundle_hash = built.bundle_hash
        built.close()
        identity = directory_identity(destination, context="published test bundle")
        operation_id = uuid.uuid4().hex
        stage = destination.parent / f".{destination.name}.composed-{operation_id}"
        journal = composed_module._journal_document(  # noqa: SLF001
            operation_id=operation_id,
            state="ready",
            stage=stage,
            destination=destination,
            stage_identity=identity,
            platform="linux_x86_64",
            runtime_api_version="0.5.0",
            bundle_hash=bundle_hash,
        )
        journal_path = composed_module._journal_path(destination)  # noqa: SLF001
        journal_path.write_bytes(canonical_json_bytes(journal))

        with (
            patch.object(
                composed_module,
                "_fsync_tree_directories",
                side_effect=OSError("injected recovered tree flush failure"),
            ),
            self.assertRaisesRegex(OSError, "recovered tree flush failure"),
        ):
            composed_module._recover_journal(  # noqa: SLF001
                destination,
                platform="linux_x86_64",
                runtime_api_version="0.5.0",
                registry=self.registry,
            )
        self.assertTrue(journal_path.is_file())

        with patch.object(
            composed_module,
            "_fsync_tree_directories",
            wraps=composed_module._fsync_tree_directories,  # noqa: SLF001
        ) as reflush:
            composed_module._recover_journal(  # noqa: SLF001
                destination,
                platform="linux_x86_64",
                runtime_api_version="0.5.0",
                registry=self.registry,
            )
        reflush.assert_called_once_with(destination)
        committed = composed_module._read_journal_record(journal_path)  # noqa: SLF001
        self.assertIsNotNone(committed)
        assert committed is not None
        self.assertEqual("committed", committed[0]["state"])

    def test_injected_population_failure_preserves_journalled_private_stage(self) -> None:
        destination = self.work / f"failure-{uuid.uuid4().hex}"
        with (
            patch.object(
                composed_module,
                "_capture_source",
                side_effect=DirectoryPublishError("injected population failure"),
            ),
            self.assertRaisesRegex(ComposedBundleError, "injected population failure"),
        ):
            self._build("failure", destination=destination)
        self.assertFalse(destination.exists())
        self.assertTrue(composed_module._journal_path(destination).exists())
        self.assertEqual(
            "copying",
            _read(composed_module._journal_path(destination))["state"],
        )
        stages = [
            path
            for path in destination.parent.glob(f".{destination.name}.composed-*")
            if len(path.name.rsplit("-", 1)[-1]) == 32
        ]
        self.assertEqual(1, len(stages))

    def test_stage_parent_flush_failure_blocks_ready_and_preserves_stage(self) -> None:
        destination = self.work / f"stage-parent-fsync-{uuid.uuid4().hex}"
        flush_directory = composed_module._fsync_directory  # noqa: SLF001
        parent_identity = directory_identity(
            destination.parent,
            context="stage parent fixture",
        )
        injected = False

        def fail_completed_stage_parent(path: Path) -> None:
            nonlocal injected
            stages = tuple(destination.parent.glob(f".{destination.name}.composed-*"))
            if (
                not injected
                and directory_identity(path, context="stage parent flush fixture")
                == parent_identity
                and any((stage / COMPOSED_BUNDLE_MANIFEST).is_file() for stage in stages)
            ):
                injected = True
                raise ComposedBundleError("injected stage parent flush failure")
            flush_directory(path)

        with (
            patch.object(
                composed_module,
                "_fsync_directory",
                side_effect=fail_completed_stage_parent,
            ),
            self.assertRaisesRegex(
                ComposedBundleError,
                "stage parent flush failure",
            ),
        ):
            self._build("stage-parent-fsync", destination=destination)

        self.assertTrue(injected)
        self.assertFalse(destination.exists())
        journal_path = composed_module._journal_path(destination)  # noqa: SLF001
        self.assertTrue(journal_path.is_file())
        self.assertEqual("copying", _read(journal_path)["state"])
        stages = tuple(
            path
            for path in destination.parent.glob(f".{destination.name}.composed-*")
            if path.is_dir()
        )
        self.assertEqual(1, len(stages))
        self.assertTrue((stages[0] / COMPOSED_BUNDLE_MANIFEST).is_file())

    def test_move_then_raise_preserves_ready_journal_until_matching_recovery(
        self,
    ) -> None:
        write_journal = composed_module._write_journal
        for platform in ("linux_x86_64", "windows_x86_64"):
            with self.subTest(platform=platform):
                destination = self.work / f"moved-{platform}-{uuid.uuid4().hex}"

                def fail_ready(
                    path: Path,
                    document: dict[str, object],
                    **kwargs: object,
                ) -> tuple[int, int]:
                    if not kwargs["create"] and document["state"] == "ready":
                        raise KeyboardInterrupt("injected post-copy process loss")
                    return write_journal(path, document, **kwargs)

                with (
                    patch.object(
                        composed_module,
                        "_write_journal",
                        side_effect=fail_ready,
                    ),
                    self.assertRaisesRegex(
                        KeyboardInterrupt,
                        "post-copy process loss",
                    ),
                ):
                    self._build(
                        f"moved-{platform}",
                        destination=destination,
                        platform=platform,
                    )

                journal_path = composed_module._journal_path(destination)
                journal = _read(journal_path)
                expected_identity = (
                    journal["stage_identity"]["device"],
                    journal["stage_identity"]["inode"],
                )
                self.assertEqual("copying", journal["state"])
                stage = destination.parent / journal["stage_name"]
                self.assertEqual(
                    expected_identity,
                    directory_identity(stage, context="complete private stage"),
                )
                self.assertFalse(destination.exists())
                self.assertTrue(journal["stage_name"].startswith(f".{destination.name}.composed-"))

                with self.assertRaisesRegex(ComposedBundleError, "already exists"):
                    self._build(
                        f"recover-{platform}",
                        destination=destination,
                        platform=platform,
                    )
                committed = composed_module._read_journal_record(  # noqa: SLF001
                    journal_path
                )
                self.assertIsNotNone(committed)
                assert committed is not None
                self.assertEqual("committed", committed[0]["state"])
                with verify_composed_runtime_bundle(
                    destination,
                    expected_bundle_hash=_read(destination / COMPOSED_BUNDLE_MANIFEST)[
                        "bundle_hash"
                    ],
                    platform=platform,
                    runtime_api_version="0.5.0",
                    registry=self.registry,
                ):
                    pass

    def test_post_copy_recovery_preserves_mismatched_destinations(self) -> None:
        write_journal = composed_module._write_journal
        for mismatch in ("content", "identity"):
            with self.subTest(mismatch=mismatch):
                destination = self.work / f"mismatch-{mismatch}-{uuid.uuid4().hex}"

                def fail_ready(
                    path: Path,
                    document: dict[str, object],
                    **kwargs: object,
                ) -> tuple[int, int]:
                    if not kwargs["create"] and document["state"] == "ready":
                        raise KeyboardInterrupt("injected post-copy process loss")
                    return write_journal(path, document, **kwargs)

                with (
                    patch.object(
                        composed_module,
                        "_write_journal",
                        side_effect=fail_ready,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    self._build(f"mismatch-{mismatch}", destination=destination)
                journal_path = composed_module._journal_path(destination)
                journal_before = journal_path.read_bytes()
                journal = _read(journal_path)
                stage = destination.parent / journal["stage_name"]

                if mismatch == "content":
                    marker = stage / "licenses/NOTICE.txt"
                    marker.write_text("replacement content\n", encoding="utf-8")
                else:
                    shutil.rmtree(stage)
                    stage.mkdir()
                    marker = stage / "replacement.txt"
                    marker.write_text("replacement directory\n", encoding="utf-8")

                with self.assertRaises(ComposedBundleError):
                    self._build(f"retry-{mismatch}", destination=destination)
                self.assertEqual(journal_before, journal_path.read_bytes())
                self.assertFalse(destination.exists())
                self.assertTrue(stage.exists())
                self.assertTrue(marker.exists())

    def test_journal_transition_never_replaces_foreign_bytes(self) -> None:
        destination = self.work / f"journal-cas-{uuid.uuid4().hex}"
        operation_id = uuid.uuid4().hex
        stage = destination.parent / f".{destination.name}.composed-{operation_id}"
        stage.mkdir()
        identity = directory_identity(stage, context="CAS test stage")
        current = composed_module._journal_document(
            operation_id=operation_id,
            state="copying",
            stage=stage,
            destination=destination,
            stage_identity=identity,
            platform="linux_x86_64",
            runtime_api_version="0.5.0",
            bundle_hash=None,
        )
        updated = composed_module._journal_document(
            operation_id=operation_id,
            state="ready",
            stage=stage,
            destination=destination,
            stage_identity=identity,
            platform="linux_x86_64",
            runtime_api_version="0.5.0",
            bundle_hash="0" * 64,
        )
        path = composed_module._journal_path(destination)
        prior_identity = composed_module._write_journal(path, current, create=True)
        owned = path.with_name(f"{path.name}.owned")
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
                composed_module.os,
                "lseek",
                side_effect=swap_before_final_check,
            ),
            self.assertRaisesRegex(
                ComposedBundleError,
                "changed before transition|path binding changed",
            ),
        ):
            composed_module._write_journal(
                path,
                updated,
                create=False,
                expected_document=current,
                expected_identity=prior_identity,
            )
        self.assertTrue(swapped)
        if swap_blocked:
            self.assertFalse(owned.exists())
            self.assertEqual(canonical_json_bytes(current), path.read_bytes())
        else:
            self.assertEqual(foreign, path.read_bytes())
            self.assertEqual(canonical_json_bytes(current), owned.read_bytes())

    def test_manifest_rejects_casefold_prefix_collisions_and_false_selection(
        self,
    ) -> None:
        destination = self.work / f"manifest-{uuid.uuid4().hex}"
        built = self._build("manifest", destination=destination)
        built.close()
        manifest = copy.deepcopy(_read(destination / COMPOSED_BUNDLE_MANIFEST))
        record = copy.deepcopy(manifest["licenses"][0])
        record["path"] = "licenses/notice.TXT"
        manifest["files"].append(record)
        manifest["files"].sort(key=lambda item: item["path"])
        manifest["licenses"].append(record)
        manifest["licenses"].sort(key=lambda item: item["path"])
        manifest["bundle_hash"] = canonical_payload_hash(
            manifest,
            hash_field="bundle_hash",
        )
        with self.assertRaisesRegex(ComposedBundleError, "casefold"):
            validate_composed_runtime_bundle_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
