from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
import os
import stat
import tempfile
import threading
import types
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from gamepack_runtime import (
    MAX_GAME_REPLAY_BYTES,
    MAX_GAME_SAVE_BYTES,
    PERSISTENCE_GENERATION_FORMAT,
    PERSISTENCE_GENERATION_VERSION,
    GameLogicError,
    GamePersistenceContext,
    GameReplayRecorder,
    GameSession,
    RecordingGameSession,
    build_game_persistence_context,
    build_game_replay,
    build_game_save,
    build_persistence_generation,
    canonical_gamepack_hash,
    canonical_persistence_hash,
    load_game_replay_bytes,
    load_game_save_bytes,
    load_persistence_generation_bytes,
    migrate_legacy_game_save_slot,
    play_game_replay,
    read_game_replay_slot,
    read_game_save_slot,
    resolve_game_save_slot_conflict,
    restore_game_save,
    rollback_game_replay_slot,
    rollback_game_save_slot,
    serialize_game_replay,
    serialize_game_save,
    validate_game_replay_document,
    validate_game_save_document,
    validate_persistence_generation_document,
    validate_slot_name,
    write_game_replay_slot,
    write_game_save_slot,
)
from scripts.generate_game_persistence_fixtures import build_fixtures
from scripts.generate_game_persistence_schemas import build_schemas
from scripts.generate_game_runtime_bundle_schema import build_schema as build_bundle_schema
from tests.test_multigenre_game_runtime_bundle import _build_bundle
from worldforge.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
PUZZLE = ROOT / (
    "examples/multigenre-contracts/abstract-puzzle/artifacts/abstract-puzzle.gamepack.json"
)


def _windows_stat(
    identity: tuple[int, int],
    *,
    size: int,
    directory: bool = False,
) -> types.SimpleNamespace:
    mode = (stat.S_IFDIR if directory else stat.S_IFREG) | 0o600
    return types.SimpleNamespace(
        st_mode=mode,
        st_dev=identity[0],
        st_ino=identity[1],
        st_nlink=1,
        st_size=size,
        st_mtime_ns=1,
        st_ctime_ns=1,
        st_file_attributes=0,
    )


class _ShareEnforcingWindowsPersistenceApi:
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004

    def __init__(self, backing: Path, expected_payload: bytes) -> None:
        self.backing = backing
        self.expected_payload = expected_payload
        self.identity = (41, 42)
        self.visible_identity = self.identity
        self.retained_handle = 70
        self.retained_descriptor = os.open(backing, os.O_RDONLY)
        self._descriptor_identity = {self.retained_descriptor: self.identity}
        self._descriptor_size = {self.retained_descriptor: len(expected_payload)}
        self._open_handles: dict[int, tuple[str, bool]] = {self.retained_handle: ("retained", True)}
        self._next_handle = 100
        self.flushes: list[int] = []
        self.open_existing_file_writable_flags: list[bool] = []
        self.read_open_count = 0
        self.fail_flush = False
        self.retained_share_mode = self.FILE_SHARE_READ

    def close_real_descriptors(self) -> None:
        for descriptor in list(self._descriptor_identity):
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._descriptor_identity.pop(descriptor, None)
            self._descriptor_size.pop(descriptor, None)

    def descriptor_file_stat(self, descriptor: int) -> types.SimpleNamespace:
        identity = self._descriptor_identity.get(descriptor)
        if identity is None:
            return os.fstat(descriptor)
        return _windows_stat(
            identity,
            size=self._descriptor_size[descriptor],
        )

    def _state(self, handle: int, *, directory: bool, context: str) -> types.SimpleNamespace:
        del context
        if directory:
            return _windows_stat((3, 3), size=0, directory=True)
        handle_kind = self._open_handles[handle][0]
        identity = self.identity if handle_kind == "retained" else self.visible_identity
        return _windows_stat(identity, size=self.backing.stat().st_size)

    def open_existing_entry(self, _parent: int, name: str) -> int:
        if name != "generation.json" or self.visible_identity is None:
            raise FileNotFoundError(name)
        return self._open_path_handle(writable=False)

    def entry_info(self, handle: int, *, context: str) -> types.SimpleNamespace:
        del context
        return self._state(handle, directory=False, context="entry")

    def open_existing_file(
        self,
        _parent: int,
        name: str,
        *,
        writable: bool = False,
        share_write: bool = True,
        share_delete: bool = True,
    ) -> int:
        if name != "generation.json" or self.visible_identity is None:
            raise FileNotFoundError(name)
        self.open_existing_file_writable_flags.append(writable)
        if writable:
            raise PermissionError(32, "sharing violation", name)
        if not share_write or not share_delete:
            raise PermissionError(32, "sharing violation", name)
        self.read_open_count += 1
        return self._open_path_handle(writable=writable)

    def _open_path_handle(self, *, writable: bool) -> int:
        handle = self._next_handle
        self._next_handle += 1
        self._open_handles[handle] = ("path", writable)
        return handle

    def duplicate_to_descriptor(self, handle: int, *, writable: bool) -> int:
        del writable
        descriptor = os.open(self.backing, os.O_RDONLY)
        handle_kind = self._open_handles[handle][0]
        identity = self.identity if handle_kind == "retained" else self.visible_identity
        self._descriptor_identity[descriptor] = identity
        self._descriptor_size[descriptor] = self.backing.stat().st_size
        return descriptor

    def flush_file_buffers(self, handle: object) -> int:
        value = int(getattr(handle, "value", handle))
        self.flushes.append(value)
        return 0 if self.fail_flush else 1

    def flush_relative_directory(
        self,
        parent: int,
        name: str,
        expected_identity: tuple[int, int],
        *,
        context: str,
    ) -> None:
        del parent, name, expected_identity, context

    def close(self, handle: int) -> None:
        self._open_handles.pop(handle, None)


def _windows_publication_fixture(
    persistence_io: object,
    api: _ShareEnforcingWindowsPersistenceApi,
) -> tuple[object, object, object]:
    staging = persistence_io._PinnedOutputParent(  # type: ignore[attr-defined]
        Path("C:/retained/staging"),
        ((1, 1), (2, 2), (4, 4)),
        windows_api=api,
        windows_handles=(10, 20, 40),
    )
    destination = persistence_io._PinnedOutputParent(  # type: ignore[attr-defined]
        Path("C:/retained/generations"),
        ((1, 1), (2, 2), (3, 3)),
        windows_api=api,
        windows_handles=(10, 20, 30),
    )
    temporary_entry = persistence_io._TemporaryEntry(  # type: ignore[attr-defined]
        descriptor=api.retained_descriptor,
        identity=api.identity,
        stage_prefix=".generation.json.stage.",
        name=".generation.json.stage.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        windows_handle=api.retained_handle,
        published=True,
    )
    return staging, destination, temporary_entry


class _WindowsPublicationState:
    FILE_SHARE_READ = 0x00000001

    def __init__(self, backing: Path) -> None:
        self.backing = backing
        self.identity = (41, 42)
        self.visible_identity: tuple[int, int] | None = None
        self.retained_handle = 700
        self.retained_share_mode = self.FILE_SHARE_READ
        self.temporary_name: str | None = None
        self.destination_name: str | None = None
        self.renamed = False
        self.fail_flush = False
        self.visible_nlink = 1
        self.visible_reparse = False
        self.retained_nlink = 1
        self.retained_reparse = False
        self.retained_descriptor_identity = self.identity
        self.retained_handle_identity = self.identity
        self.visible_identity_sequence: list[tuple[int, int] | None] = []
        self._next_handle = 800
        self._handles: dict[int, str] = {}
        self._descriptor_kinds: dict[int, str] = {}
        self.flushes: list[int] = []
        self.open_existing_file_writable_flags: list[bool] = []
        self.rename_calls: list[tuple[int, int, str, bool]] = []
        self.closed_handles: list[int] = []
        self.deleted_handles: list[int] = []

    def close_real_descriptors(self) -> None:
        for descriptor in list(self._descriptor_kinds):
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._descriptor_kinds.pop(descriptor, None)

    def descriptor_file_stat(self, descriptor: int) -> types.SimpleNamespace:
        kind = self._descriptor_kinds.get(descriptor)
        if kind is None:
            return os.fstat(descriptor)
        if kind == "retained":
            return self._stat(
                self.retained_descriptor_identity,
                nlink=self.retained_nlink,
                reparse=self.retained_reparse,
            )
        return self._stat(self._visible_identity())

    def _visible_identity(self) -> tuple[int, int] | None:
        if self.visible_identity_sequence:
            return self.visible_identity_sequence.pop(0)
        return self.visible_identity

    def _stat(
        self,
        identity: tuple[int, int] | None,
        *,
        nlink: int | None = None,
        reparse: bool | None = None,
    ) -> types.SimpleNamespace:
        if identity is None:
            raise FileNotFoundError("generation.json")
        attributes = 0x00000400 if (self.visible_reparse if reparse is None else reparse) else 0
        return types.SimpleNamespace(
            st_mode=stat.S_IFREG | 0o600,
            st_dev=identity[0],
            st_ino=identity[1],
            st_nlink=self.visible_nlink if nlink is None else nlink,
            st_size=self.backing.stat().st_size,
            st_mtime_ns=1,
            st_ctime_ns=1,
            st_file_attributes=attributes,
        )


class _WindowsPublicationApiWrapper:
    def __init__(self, state: _WindowsPublicationState, role: str) -> None:
        self.state = state
        self.role = role

    def _state(self, handle: int, *, directory: bool, context: str) -> types.SimpleNamespace:
        del context
        if directory:
            return _windows_stat((3, 3), size=0, directory=True)
        kind = self.state._handles[handle]
        if kind == "retained":
            return self.state._stat(
                self.state.retained_handle_identity,
                nlink=self.state.retained_nlink,
                reparse=self.state.retained_reparse,
            )
        return self.state._stat(self.state._visible_identity())

    def create_temporary(self, _parent: int, name: str) -> int:
        self.state.temporary_name = name
        self.state.backing.write_bytes(b"")
        self.state._handles[self.state.retained_handle] = "retained"
        return self.state.retained_handle

    def open_ancestry(
        self,
        path: Path,
        *,
        create: bool,
    ) -> tuple[list[int], tuple[tuple[int, int], ...]]:
        del create
        handles = list(range(10, 10 + len(path.parts)))
        if path.name == "staging":
            identities = tuple((index, index) for index in range(1, len(path.parts))) + ((4, 4),)
            return handles, identities
        identities = tuple((index, index) for index in range(1, len(path.parts))) + ((3, 3),)
        return handles, identities

    def close_many(self, handles: list[int] | tuple[int, ...]) -> None:
        del handles

    def open_existing_entry(self, _parent: int, name: str) -> int:
        if name == self.state.temporary_name and not self.state.renamed:
            return self._open_handle("retained")
        if name == self.state.destination_name and self.state.visible_identity is not None:
            return self._open_handle("path")
        raise FileNotFoundError(name)

    def entry_info(self, handle: int, *, context: str) -> types.SimpleNamespace:
        del context
        return self._state(handle, directory=False, context="entry")

    def open_existing_file(
        self,
        _parent: int,
        name: str,
        *,
        writable: bool = False,
        share_write: bool = True,
        share_delete: bool = True,
    ) -> int:
        if name != self.state.destination_name or self.state.visible_identity is None:
            raise FileNotFoundError(name)
        self.state.open_existing_file_writable_flags.append(writable)
        if writable or not share_write or not share_delete:
            raise PermissionError(32, "sharing violation", name)
        return self._open_handle("path")

    def duplicate_to_descriptor(self, handle: int, *, writable: bool) -> int:
        kind = self.state._handles[handle]
        flags = os.O_RDWR if writable else os.O_RDONLY
        descriptor = os.open(self.state.backing, flags)
        self.state._descriptor_kinds[descriptor] = kind
        return descriptor

    def rename(
        self,
        handle: int,
        parent_handle: int,
        destination_name: str,
        *,
        replace: bool,
    ) -> None:
        self.state.rename_calls.append((handle, parent_handle, destination_name, replace))
        if not replace and self.state.visible_identity is not None:
            raise FileExistsError(183, "entry already exists", destination_name)
        self.state.destination_name = destination_name
        self.state.visible_identity = self.state.identity
        self.state.renamed = True

    def flush_file_buffers(self, handle: object) -> int:
        value = int(getattr(handle, "value", handle))
        self.state.flushes.append(value)
        return 0 if self.state.fail_flush else 1

    def flush_relative_directory(
        self,
        parent: int,
        name: str,
        expected_identity: tuple[int, int],
        *,
        context: str,
    ) -> None:
        del parent, name, expected_identity, context

    def mark_delete_on_close(self, handle: int) -> None:
        self.state.deleted_handles.append(handle)

    def close(self, handle: int) -> None:
        self.state.closed_handles.append(handle)
        self.state._handles.pop(handle, None)

    def _open_handle(self, kind: str) -> int:
        handle = self.state._next_handle
        self.state._next_handle += 1
        self.state._handles[handle] = kind
        return handle


@contextmanager
def _fake_windows_publication_parents(
    persistence_io: object,
    state: _WindowsPublicationState,
    path: Path,
    *,
    create: bool = True,
) -> Iterator[object]:
    del create
    if path.name == "staging":
        handles = tuple(range(10, 10 + len(path.parts)))
        identities = tuple((index, index) for index in range(1, len(path.parts))) + ((4, 4),)
        yield persistence_io._PinnedOutputParent(  # type: ignore[attr-defined]
            path,
            identities,
            windows_api=_WindowsPublicationApiWrapper(state, "staging"),
            windows_handles=handles,
        )
        return
    if path.name == "generations":
        handles = tuple(range(10, 10 + len(path.parts)))
        identities = tuple((index, index) for index in range(1, len(path.parts))) + ((3, 3),)
        yield persistence_io._PinnedOutputParent(  # type: ignore[attr-defined]
            path,
            identities,
            windows_api=_WindowsPublicationApiWrapper(state, "destination"),
            windows_handles=handles,
        )
        return
    raise AssertionError(f"unexpected retained parent path: {path}")


NARRATIVE = ROOT / (
    "examples/multigenre-contracts/branching-narrative/artifacts/branching-narrative.gamepack.json"
)


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _document_from_bundle(verified: object, relative: str) -> dict[str, object]:
    payload = verified.read_bytes(relative)
    value = json.loads(payload)
    assert isinstance(value, dict)
    return value


def _transient_context(source: GamePersistenceContext) -> GamePersistenceContext:
    gamepack = source.gamepack
    for state in gamepack["logic"]["state_schema"]:
        if state["id"] == "move_count":
            state["persistence"] = "transient"
            break
    gamepack["content_hash"] = canonical_gamepack_hash(gamepack)
    gamepack_identity = {
        "format": gamepack["format"],
        "format_version": gamepack["format_version"],
        "id": gamepack["game"]["id"],
        "content_hash": gamepack["content_hash"],
    }
    adapter = source.adapter
    adapter_identity = {
        "format": adapter["format"],
        "format_version": adapter["format_version"],
        "id": adapter["adapter_id"],
        "content_hash": adapter["content_hash"],
    }
    composition = {
        "format": "world-forge.game_runtime_composition",
        "format_version": 1,
        "composition_id": "runtime_composition_transient_test",
        "gamepack": gamepack_identity,
        "adapter": adapter_identity,
        "content_hash": "",
    }
    composition["content_hash"] = canonical_persistence_hash(composition)
    composition_identity = {
        "format": composition["format"],
        "format_version": composition["format_version"],
        "id": composition["composition_id"],
        "content_hash": composition["content_hash"],
    }
    bundle = {
        "format": "world-forge.game_runtime_bundle",
        "format_version": 1,
        "bundle_id": "",
        "contracts": {
            "gamepack": gamepack_identity,
            "runtime_composition": composition_identity,
            "runtime_adapter": adapter_identity,
        },
        "runtime_snapshot_tree": {
            "runtime_api": {
                "id": "gamepack_runtime",
                "version": "1.0.0",
            }
        },
        "content_hash": "",
    }
    bundle_seed = {
        key: value for key, value in bundle.items() if key not in {"bundle_id", "content_hash"}
    }
    bundle["bundle_id"] = "game_runtime_bundle_" + canonical_persistence_hash(bundle_seed)[:48]
    bundle["content_hash"] = canonical_persistence_hash(bundle)
    return build_game_persistence_context(
        gamepack,
        composition,
        bundle,
        adapter,
    )


def _forged_context_identity(
    source: GamePersistenceContext,
    binding: str,
    identifier: str,
) -> GamePersistenceContext:
    forged = copy.deepcopy(source)
    bindings = object.__getattribute__(forged, "_bindings")
    assert type(bindings) is dict
    identity = bindings[binding]
    assert type(identity) is dict
    identity["id"] = identifier
    return forged


def _reseal_persistence(document: dict[str, object]) -> None:
    if document["format"] == "world-forge.game_save":
        identifier = "save_id"
        prefix = "game_save_"
    else:
        identifier = "replay_id"
        prefix = "game_replay_"
    seed = {
        key: value for key, value in document.items() if key not in {identifier, "content_hash"}
    }
    document[identifier] = prefix + canonical_persistence_hash(seed)[:48]
    document["content_hash"] = canonical_persistence_hash(document)


class PersistenceLockCleanupTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "requires a native POSIX lock descriptor")
    def test_release_failure_still_closes_the_native_lock_descriptor(self) -> None:
        from gamepack_runtime import persistence_io

        with tempfile.TemporaryDirectory(prefix="wf-persistence-lock-release-") as temporary:
            lock = Path(temporary) / "slot.lock"
            opened: list[int] = []
            actual_open = persistence_io._open_lock_entry

            def capture_open(*args, **kwargs):
                result = actual_open(*args, **kwargs)
                opened.append(result[0])
                return result

            try:
                with (
                    mock.patch.object(
                        persistence_io,
                        "_open_lock_entry",
                        side_effect=capture_open,
                    ),
                    mock.patch.object(
                        persistence_io,
                        "_release_os_lock",
                        side_effect=OSError("injected unlock failure"),
                    ),
                    self.assertRaisesRegex(
                        persistence_io.PersistenceIOError,
                        "Could not release persistence slot lock",
                    ),
                ):
                    with persistence_io.held_persistence_lock(lock):
                        pass
                self.assertEqual(len(opened), 1)
                with self.assertRaises(OSError):
                    os.fstat(opened[0])
            finally:
                for descriptor in opened:
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

    def test_mocked_windows_release_failure_retires_descriptor_and_handle(self) -> None:
        from gamepack_runtime import persistence_io

        class FakeWindowsApi:
            def __init__(self) -> None:
                self.closed: list[int] = []

            def close(self, handle: int) -> None:
                self.closed.append(handle)

        class FakeParent:
            def __init__(self, path: Path, windows_api: FakeWindowsApi) -> None:
                self.path = path
                self.parent_fd = None
                self.windows_api = windows_api
                self.windows_parent_handle = 10

            def assert_current(self) -> None:
                return None

        with tempfile.TemporaryDirectory(prefix="wf-persistence-lock-win-cleanup-") as temporary:
            root = Path(temporary)
            backing = root / "descriptor.bin"
            descriptor = os.open(backing, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            actual_close = os.close
            windows_api = FakeWindowsApi()
            parent = FakeParent(root, windows_api)
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)

            @contextlib.contextmanager
            def fake_parent(*_args, **_kwargs):
                yield parent

            try:
                with (
                    mock.patch.object(
                        persistence_io,
                        "_open_verified_output_parent",
                        fake_parent,
                    ),
                    mock.patch.object(
                        persistence_io,
                        "_open_lock_entry",
                        return_value=(descriptor, 99, identity),
                    ),
                    mock.patch.object(persistence_io, "_acquire_os_lock"),
                    mock.patch.object(
                        persistence_io,
                        "_release_os_lock",
                        side_effect=OSError("injected Windows unlock failure"),
                    ),
                    mock.patch.object(
                        persistence_io,
                        "_validated_target_identity",
                        return_value=identity,
                    ),
                    mock.patch.object(
                        persistence_io,
                        "_entry_info",
                        return_value=info,
                    ),
                    mock.patch.object(persistence_io, "_fsync_retained_ancestry"),
                    self.assertRaisesRegex(
                        persistence_io.PersistenceIOError,
                        "Could not release persistence slot lock",
                    ),
                ):
                    with persistence_io.held_persistence_lock(root / "slot.lock"):
                        pass
                self.assertEqual(windows_api.closed, [99])
                with self.assertRaises(OSError):
                    os.fstat(descriptor)
            finally:
                try:
                    actual_close(descriptor)
                except OSError:
                    pass

    def test_lock_cleanup_keeps_body_error_primary_and_aggregates_failures(self) -> None:
        from gamepack_runtime import persistence_io

        class FailingWindowsApi:
            def __init__(self) -> None:
                self.closed: list[int] = []

            def close(self, handle: int) -> None:
                self.closed.append(handle)
                raise persistence_io.PersistenceIOError("injected handle close failure")

        class FakeParent:
            def __init__(self, path: Path, windows_api: FailingWindowsApi) -> None:
                self.path = path
                self.parent_fd = None
                self.windows_api = windows_api
                self.windows_parent_handle = 10

            def assert_current(self) -> None:
                return None

        with tempfile.TemporaryDirectory(prefix="wf-persistence-lock-primary-") as temporary:
            root = Path(temporary)
            backing = root / "descriptor.bin"
            descriptor = os.open(backing, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            actual_close = os.close
            windows_api = FailingWindowsApi()
            parent = FakeParent(root, windows_api)
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)

            @contextlib.contextmanager
            def fake_parent(*_args, **_kwargs):
                yield parent

            try:
                with (
                    mock.patch.object(
                        persistence_io,
                        "_open_verified_output_parent",
                        fake_parent,
                    ),
                    mock.patch.object(
                        persistence_io,
                        "_open_lock_entry",
                        return_value=(descriptor, 101, identity),
                    ),
                    mock.patch.object(persistence_io, "_acquire_os_lock"),
                    mock.patch.object(
                        persistence_io,
                        "_release_os_lock",
                        side_effect=OSError("injected unlock failure"),
                    ),
                    mock.patch.object(
                        persistence_io.os,
                        "close",
                        side_effect=OSError("injected descriptor close failure"),
                    ),
                    mock.patch.object(
                        persistence_io,
                        "_validated_target_identity",
                        return_value=identity,
                    ),
                    mock.patch.object(
                        persistence_io,
                        "_entry_info",
                        return_value=info,
                    ),
                    mock.patch.object(persistence_io, "_fsync_retained_ancestry"),
                    self.assertRaisesRegex(
                        RuntimeError,
                        "primary slot failure",
                    ) as raised,
                ):
                    with persistence_io.held_persistence_lock(root / "slot.lock"):
                        raise RuntimeError("primary slot failure")
                self.assertEqual(windows_api.closed, [101])
                notes = getattr(raised.exception, "__notes__", ())
                self.assertTrue(any("Persistence lock release failed" in note for note in notes))
                self.assertTrue(
                    any("Persistence lock descriptor cleanup failed" in note for note in notes)
                )
                self.assertTrue(
                    any("Persistence lock handle cleanup failed" in note for note in notes)
                )
            finally:
                try:
                    actual_close(descriptor)
                except OSError:
                    pass


class WindowsRetainedJsonPublicationTests(unittest.TestCase):
    def test_publish_json_windows_public_flow_uses_distinct_retained_wrappers(self) -> None:
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=["publish_json_noreplace"],
        )
        with tempfile.TemporaryDirectory(prefix="wf-windows-public-flow-") as temporary:
            root = Path(temporary)
            backing = root / "backing.json"
            state = _WindowsPublicationState(backing)
            try:
                with (
                    mock.patch(
                        "gamepack_runtime.persistence_io._open_verified_output_parent",
                        side_effect=lambda path, create=True: _fake_windows_publication_parents(
                            persistence_io,
                            state,
                            path,
                            create=create,
                        ),
                    ),
                    mock.patch(
                        "gamepack_runtime.persistence_io.descriptor_file_stat",
                        side_effect=state.descriptor_file_stat,
                    ),
                ):
                    published = persistence_io.publish_json_noreplace(
                        root / "staging",
                        root / "generations",
                        "generation.json",
                        {"value": 1},
                    )
            finally:
                state.close_real_descriptors()
            self.assertEqual(published, root / "generations" / "generation.json")
            self.assertEqual(backing.read_bytes(), b'{\n  "value": 1\n}\n')
            self.assertEqual(
                [(handle, name, replace) for handle, _parent, name, replace in state.rename_calls],
                [(state.retained_handle, "generation.json", False)],
            )
            self.assertEqual(state.flushes, [state.retained_handle])
            self.assertNotIn(True, state.open_existing_file_writable_flags)
            self.assertEqual(state.retained_share_mode, state.FILE_SHARE_READ)
            self.assertIn(state.retained_handle, state.closed_handles)

    def test_windows_fresh_publication_flushes_retained_renamed_handle_without_writable_reopen(
        self,
    ) -> None:
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=[
                "_PinnedOutputParent",
                "_TemporaryEntry",
                "_complete_windows_retained_publication_durability",
            ],
        )
        payload = b'{"value":1}\n'
        with tempfile.TemporaryDirectory(prefix="wf-windows-retained-flush-") as temporary:
            backing = Path(temporary) / "generation.json"
            backing.write_bytes(payload)
            api = _ShareEnforcingWindowsPersistenceApi(backing, payload)
            staging = persistence_io._PinnedOutputParent(
                Path("C:/retained/staging"),
                ((1, 1), (2, 2), (4, 4)),
                windows_api=api,
                windows_handles=(10, 20, 40),
            )
            destination = persistence_io._PinnedOutputParent(
                Path("C:/retained/generations"),
                ((1, 1), (2, 2), (3, 3)),
                windows_api=api,
                windows_handles=(10, 20, 30),
            )
            temporary_entry = persistence_io._TemporaryEntry(
                descriptor=api.retained_descriptor,
                identity=api.identity,
                stage_prefix=".generation.json.stage.",
                name=".generation.json.stage.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                windows_handle=api.retained_handle,
                published=True,
            )
            try:
                with (
                    mock.patch.object(
                        persistence_io._PinnedOutputParent,
                        "assert_current",
                        return_value=None,
                    ),
                    mock.patch(
                        "gamepack_runtime.persistence_io.descriptor_file_stat",
                        side_effect=api.descriptor_file_stat,
                    ),
                ):
                    persistence_io._complete_windows_retained_publication_durability(
                        staging,
                        destination,
                        "generation.json",
                        payload,
                        temporary_entry,
                    )
            finally:
                api.close_real_descriptors()
            self.assertEqual(api.flushes, [api.retained_handle])
            self.assertNotIn(True, api.open_existing_file_writable_flags)
            self.assertEqual(api.retained_share_mode, api.FILE_SHARE_READ)
            self.assertGreaterEqual(api.read_open_count, 2)

    def test_windows_fresh_publication_fails_closed_when_visible_identity_changes(self) -> None:
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=[
                "_PinnedOutputParent",
                "_TemporaryEntry",
                "_complete_windows_retained_publication_durability",
            ],
        )
        payload = b'{"value":1}\n'
        for visible_identity in ((99, 100), None):
            with self.subTest(visible_identity=visible_identity):
                with tempfile.TemporaryDirectory(
                    prefix="wf-windows-retained-identity-"
                ) as temporary:
                    backing = Path(temporary) / "generation.json"
                    backing.write_bytes(payload)
                    api = _ShareEnforcingWindowsPersistenceApi(backing, payload)
                    api.visible_identity = visible_identity
                    staging, destination, temporary_entry = _windows_publication_fixture(
                        persistence_io,
                        api,
                    )
                    try:
                        with (
                            mock.patch.object(
                                persistence_io._PinnedOutputParent,
                                "assert_current",
                                return_value=None,
                            ),
                            mock.patch(
                                "gamepack_runtime.persistence_io.descriptor_file_stat",
                                side_effect=api.descriptor_file_stat,
                            ),
                        ):
                            with self.assertRaises(persistence_io.PersistenceIOError) as raised:
                                persistence_io._complete_windows_retained_publication_durability(
                                    staging,
                                    destination,
                                    "generation.json",
                                    payload,
                                    temporary_entry,
                                )
                    finally:
                        api.close_real_descriptors()
                    self.assertEqual(
                        raised.exception.reason_code,
                        "persistence_windows_retained_identity_indeterminate",
                    )

    def test_windows_fresh_publication_reports_flush_read_and_parent_failures(self) -> None:
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=[
                "PersistenceIOError",
                "_PinnedOutputParent",
                "_TemporaryEntry",
                "_complete_windows_retained_publication_durability",
            ],
        )
        payload = b'{"value":1}\n'
        cases = (
            ("flush", "persistence_windows_retained_flush_indeterminate"),
            ("read", "persistence_windows_retained_read_indeterminate"),
            ("parent", "persistence_durability_unavailable"),
        )
        for failure, reason_code in cases:
            with self.subTest(failure=failure):
                with tempfile.TemporaryDirectory(
                    prefix="wf-windows-retained-failure-"
                ) as temporary:
                    backing = Path(temporary) / "generation.json"
                    backing.write_bytes(payload if failure != "read" else b'{"mutated":true}\n')
                    api = _ShareEnforcingWindowsPersistenceApi(backing, payload)
                    api.fail_flush = failure == "flush"
                    staging, destination, temporary_entry = _windows_publication_fixture(
                        persistence_io,
                        api,
                    )
                    try:
                        with (
                            mock.patch.object(
                                persistence_io._PinnedOutputParent,
                                "assert_current",
                                return_value=None,
                            ),
                            mock.patch(
                                "gamepack_runtime.persistence_io.descriptor_file_stat",
                                side_effect=api.descriptor_file_stat,
                            ),
                            mock.patch(
                                "gamepack_runtime.persistence_io._fsync_retained_ancestry",
                                side_effect=(
                                    persistence_io.PersistenceIOError(
                                        "injected parent flush failure",
                                        reason_code="persistence_durability_unavailable",
                                    )
                                    if failure == "parent"
                                    else None
                                ),
                            ),
                        ):
                            with self.assertRaises(persistence_io.PersistenceIOError) as raised:
                                persistence_io._complete_windows_retained_publication_durability(
                                    staging,
                                    destination,
                                    "generation.json",
                                    payload,
                                    temporary_entry,
                                )
                    finally:
                        api.close_real_descriptors()
                    self.assertEqual(raised.exception.reason_code, reason_code)

    def test_windows_retained_publication_identity_edges_are_indeterminate(self) -> None:
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=[
                "_TemporaryEntry",
                "_complete_windows_retained_publication_durability",
            ],
        )
        payload = b'{"value":1}\n'
        cases = (
            ("hardlink", lambda state: setattr(state, "visible_nlink", 2)),
            ("reparse", lambda state: setattr(state, "visible_reparse", True)),
            (
                "descriptor_mismatch",
                lambda state: setattr(state, "retained_descriptor_identity", (90, 91)),
            ),
            (
                "handle_mismatch",
                lambda state: setattr(state, "retained_handle_identity", (92, 93)),
            ),
            (
                "visible_aba",
                lambda state: setattr(
                    state,
                    "visible_identity_sequence",
                    [state.identity, (94, 95)],
                ),
            ),
        )
        for name, configure in cases:
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory(prefix="wf-windows-identity-edge-") as temporary:
                    root = Path(temporary)
                    backing = root / "generation.json"
                    backing.write_bytes(payload)
                    state = _WindowsPublicationState(backing)
                    state.destination_name = "generation.json"
                    state.visible_identity = state.identity
                    state.renamed = True
                    state._handles[state.retained_handle] = "retained"
                    descriptor = os.open(backing, os.O_RDONLY)
                    state._descriptor_kinds[descriptor] = "retained"
                    configure(state)
                    try:
                        with (
                            _fake_windows_publication_parents(
                                persistence_io,
                                state,
                                root / "staging",
                            ) as staging,
                            _fake_windows_publication_parents(
                                persistence_io,
                                state,
                                root / "generations",
                            ) as destination,
                            mock.patch(
                                "gamepack_runtime.persistence_io.descriptor_file_stat",
                                side_effect=state.descriptor_file_stat,
                            ),
                        ):
                            temporary_entry = persistence_io._TemporaryEntry(
                                descriptor=descriptor,
                                identity=state.identity,
                                stage_prefix=".generation.json.stage.",
                                name=".generation.json.stage.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                                windows_handle=state.retained_handle,
                                published=True,
                            )
                            with self.assertRaises(persistence_io.PersistenceIOError) as raised:
                                persistence_io._complete_windows_retained_publication_durability(
                                    staging,
                                    destination,
                                    "generation.json",
                                    payload,
                                    temporary_entry,
                                )
                    finally:
                        state.close_real_descriptors()
                    self.assertEqual(
                        raised.exception.reason_code,
                        "persistence_windows_retained_identity_indeterminate",
                    )

    def test_publish_json_windows_public_failure_closes_retained_handle(self) -> None:
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=["publish_json_noreplace"],
        )
        with tempfile.TemporaryDirectory(prefix="wf-windows-public-cleanup-") as temporary:
            root = Path(temporary)
            backing = root / "backing.json"
            state = _WindowsPublicationState(backing)
            state.visible_nlink = 2
            try:
                with (
                    mock.patch(
                        "gamepack_runtime.persistence_io._open_verified_output_parent",
                        side_effect=lambda path, create=True: _fake_windows_publication_parents(
                            persistence_io,
                            state,
                            path,
                            create=create,
                        ),
                    ),
                    mock.patch(
                        "gamepack_runtime.persistence_io.descriptor_file_stat",
                        side_effect=state.descriptor_file_stat,
                    ),
                ):
                    with self.assertRaises(persistence_io.PersistenceIOError) as raised:
                        persistence_io.publish_json_noreplace(
                            root / "staging",
                            root / "generations",
                            "generation.json",
                            {"value": 1},
                        )
            finally:
                state.close_real_descriptors()
            self.assertEqual(
                raised.exception.reason_code,
                "persistence_windows_retained_identity_indeterminate",
            )
            self.assertIn(state.retained_handle, state.closed_handles)

    def test_windows_published_temporary_close_failure_is_indeterminate(self) -> None:
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=["_PinnedOutputParent", "_TemporaryEntry", "_close_temporary_entry"],
        )

        class FailingCloseApi:
            def close(self, _handle: int) -> None:
                raise persistence_io.PersistenceIOError("injected close failure")

        with tempfile.TemporaryDirectory(prefix="wf-windows-close-indeterminate-") as temporary:
            backing = Path(temporary) / "generation.json"
            backing.write_bytes(b"{}\n")
            descriptor = os.open(backing, os.O_RDONLY)
            try:
                parent = persistence_io._PinnedOutputParent(
                    Path("C:/retained/staging"),
                    ((1, 1),),
                    windows_api=FailingCloseApi(),
                    windows_handles=(20,),
                )
                temporary_entry = persistence_io._TemporaryEntry(
                    descriptor=descriptor,
                    identity=(7, 8),
                    stage_prefix=".generation.json.stage.",
                    name=".generation.json.stage.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    windows_handle=70,
                    published=True,
                )
                descriptor = -1
                with self.assertRaises(persistence_io.PersistenceIOError) as raised:
                    persistence_io._close_temporary_entry(parent, temporary_entry)
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            self.assertEqual(
                raised.exception.reason_code,
                "persistence_windows_retained_close_indeterminate",
            )


class GamePersistenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="wf-game-persistence-")
        root = Path(cls._temporary.name)
        cls._bundles: dict[str, object] = {}
        cls._contexts: dict[str, GamePersistenceContext] = {}
        for fixture in ("abstract-puzzle", "branching-narrative"):
            verified = _build_bundle(fixture, root)
            cls._bundles[fixture] = verified
            gamepack = _document_from_bundle(verified, "contracts/gamepack.json")
            composition = _document_from_bundle(
                verified,
                "contracts/runtime-composition.json",
            )
            adapter_path = verified.manifest["contracts"]["runtime_adapter"]["path"]
            adapter = _document_from_bundle(verified, adapter_path)
            cls._contexts[fixture] = build_game_persistence_context(
                gamepack,
                composition,
                verified.manifest,
                adapter,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        for verified in cls._bundles.values():
            verified.close()
        cls._temporary.cleanup()

    def test_generated_schemas_are_closed_and_additive(self) -> None:
        schemas = build_schemas()
        self.assertEqual(
            set(schemas),
            {
                "schemas/game-replay.schema.json",
                "schemas/game-save.schema.json",
                "schemas/persistence-generation.schema.json",
            },
        )
        for relative, schema in schemas.items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    json.loads((ROOT / relative).read_text(encoding="utf-8")),
                    schema,
                )
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(schema["properties"]["format_version"]["const"], 1)
                self.assertTrue(schema["x-world-forge-canonical-content-hash"])
        self.assertEqual(
            build_bundle_schema()["properties"]["state"]["const"],
            "pre_execution",
        )

    def test_canonical_fixtures_regenerate_byte_identically_and_verify(self) -> None:
        first = build_fixtures(ROOT)
        second = build_fixtures(ROOT)
        expected = {
            "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/replays/solve.json",
            "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/"
            "replays/zero-step.json",
            "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/saves/initial.json",
            "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/saves/solved.json",
            "examples/multigenre-contracts/branching-narrative/runtime/persistence/"
            "replays/left.json",
            "examples/multigenre-contracts/branching-narrative/runtime/persistence/"
            "replays/right.json",
            "examples/multigenre-contracts/branching-narrative/runtime/persistence/saves/left.json",
            "examples/multigenre-contracts/branching-narrative/runtime/persistence/"
            "saves/right.json",
        }
        expected |= {
            relative.replace("/persistence/", "/persistence/generations/") for relative in expected
        }
        self.assertEqual(set(first), expected)
        self.assertEqual(first, second)
        for relative, payload in first.items():
            with self.subTest(relative=relative):
                self.assertEqual((ROOT / relative).read_bytes(), payload)
                fixture = (
                    "abstract-puzzle" if "abstract-puzzle" in relative else "branching-narrative"
                )
                context = self._contexts[fixture]
                if "/generations/" in relative:
                    load_persistence_generation_bytes(
                        payload,
                        context=context,
                        source=relative,
                    )
                elif "/saves/" in relative:
                    load_game_save_bytes(payload, context, source=relative)
                else:
                    load_game_replay_bytes(payload, context, source=relative)

    def test_puzzle_save_restore_and_zero_or_one_step_replay_are_deterministic(
        self,
    ) -> None:
        context = self._contexts["abstract-puzzle"]
        first_session = GameSession(context.gamepack)
        second_session = GameSession(copy.deepcopy(context.gamepack))

        initial_save = build_game_save(context, first_session.state)
        self.assertEqual(
            serialize_game_save(initial_save),
            serialize_game_save(build_game_save(context, second_session.state)),
        )
        self.assertEqual(
            restore_game_save(context, initial_save),
            first_session.state,
        )

        zero_replay = build_game_replay(context, [])
        self.assertEqual(
            play_game_replay(context, zero_replay).state_hash,
            first_session.state_hash,
        )

        result = first_session.apply(
            "swap_tiles",
            {"first_index": 0, "second_index": 1},
        )
        self.assertTrue(result.accepted)
        solved_save = build_game_save(context, first_session.state)
        solved_replay = build_game_replay(context, [result])
        played = play_game_replay(context, solved_replay)
        self.assertEqual(played.state_hash, first_session.state_hash)
        self.assertEqual(played.classification.ending_ids, ("puzzle_complete",))
        self.assertEqual(
            serialize_game_replay(solved_replay),
            serialize_game_replay(build_game_replay(context, [result])),
        )
        self.assertNotEqual(initial_save["save_id"], solved_save["save_id"])

    def test_narrative_left_and_right_endings_restore_and_replay_exactly(self) -> None:
        context = self._contexts["branching-narrative"]
        endings: dict[str, tuple[str, str]] = {}
        for action_id in ("choose_left", "choose_right"):
            with self.subTest(action_id=action_id):
                session = GameSession(context.gamepack)
                result = session.apply(action_id, {})
                self.assertTrue(result.accepted)
                save = validate_game_save_document(
                    build_game_save(context, session.state),
                    context,
                )
                replay = validate_game_replay_document(
                    build_game_replay(context, [result]),
                    context,
                )
                restored = restore_game_save(context, save)
                played = play_game_replay(context, replay)
                self.assertEqual(restored, session.state)
                self.assertEqual(played.state_hash, session.state_hash)
                endings[action_id] = (
                    replay["classification"]["ending_ids"][0],
                    replay["final_state_hash"],
                )
        self.assertEqual(
            endings,
            {
                "choose_left": (
                    "ending_left",
                    "1083d4e41a6bfad92c38beee91b01a267d67ca428c3a2625dc30bac79d2d7f51",
                ),
                "choose_right": (
                    "ending_right",
                    "a91e46da8e98f6b24bc1add282a76463426477e08d5ab7a0cca5d2df27e23a89",
                ),
            },
        )

    def test_save_resets_transient_state_and_rejects_constant_or_identity_tamper(
        self,
    ) -> None:
        context = self._contexts["abstract-puzzle"]
        transient_context = _transient_context(context)
        changed = GameSession(transient_context.gamepack)
        result = changed.apply("swap_tiles", {"first_index": 0, "second_index": 1})
        self.assertTrue(result.accepted)
        save = build_game_save(transient_context, changed.state)
        restored = restore_game_save(transient_context, save)
        self.assertEqual(restored["move_count"], 0)
        self.assertEqual(restored["board"], ["A", "B", "C"])

        tampered_constant = copy.deepcopy(save)
        tampered_constant["state"]["saved"]["target"] = ["B", "A", "C"]
        tampered_constant["state"]["saved_hash"] = canonical_persistence_hash(
            tampered_constant["state"]["saved"]
        )
        _reseal_persistence(tampered_constant)
        with self.assertRaisesRegex(GameLogicError, "save_constant_mismatch"):
            restore_game_save(transient_context, tampered_constant)

        for field in (
            "gamepack",
            "runtime_composition",
            "runtime_bundle",
            "runtime_api",
            "execution_semantics",
        ):
            with self.subTest(field=field):
                crossed = copy.deepcopy(save)
                target = crossed["bindings"][field]
                if "content_hash" in target:
                    target["content_hash"] = "f" * 64
                else:
                    target["version"] = 999
                _reseal_persistence(crossed)
                with self.assertRaises(GameLogicError):
                    validate_game_save_document(crossed, transient_context)

    def test_recording_keeps_only_accepted_actions_and_rejects_restore(self) -> None:
        context = self._contexts["abstract-puzzle"]
        recording = RecordingGameSession(context)
        rejected = recording.apply(
            "swap_tiles",
            {"first_index": 0, "second_index": 2},
        )
        accepted = recording.apply(
            "swap_tiles",
            {"first_index": 0, "second_index": 1},
        )
        self.assertFalse(rejected.accepted)
        self.assertTrue(accepted.accepted)
        replay = recording.finish()
        self.assertEqual(len(replay["steps"]), 1)
        self.assertEqual(replay["steps"][0]["index"], 0)
        self.assertEqual(replay["steps"][0]["action_id"], "swap_tiles")
        with self.assertRaisesRegex(GameLogicError, "recording_restore_forbidden"):
            recording.restore(build_game_save(context, recording.state))

        recorder = GameReplayRecorder(context)
        recorder.record(rejected)
        recorder.record(accepted)
        self.assertEqual(recorder.finish(), replay)

        class HostileState(dict[str, object]):
            def __deepcopy__(self, _memo: object) -> object:
                raise AssertionError("hostile deepcopy reached")

        hostile = accepted.__class__(
            accepted.accepted,
            accepted.action,
            accepted.pre_state,
            HostileState(accepted.post_state),
            accepted.pre_state_hash,
            accepted.post_state_hash,
            accepted.events,
            accepted.rejection_reason,
        )
        with self.assertRaises(GameLogicError):
            GameReplayRecorder(context).record(hostile)

    def test_replay_rejects_first_step_final_classification_and_hash_mismatch(
        self,
    ) -> None:
        context = self._contexts["abstract-puzzle"]
        session = GameSession(context.gamepack)
        result = session.apply("swap_tiles", {"first_index": 0, "second_index": 1})
        baseline = build_game_replay(context, [result])
        mutations = (
            ("step", lambda value: value["steps"][0].__setitem__("post_state_hash", "f" * 64)),
            ("events", lambda value: value["steps"][0].__setitem__("events", ["wrong_event"])),
            ("final", lambda value: value.__setitem__("final_state_hash", "e" * 64)),
            (
                "classification",
                lambda value: value["classification"].__setitem__("ending_ids", []),
            ),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                document = copy.deepcopy(baseline)
                mutate(document)
                document["trace_hash"] = canonical_persistence_hash({"steps": document["steps"]})
                _reseal_persistence(document)
                with self.assertRaisesRegex(GameLogicError, "replay_.*mismatch"):
                    play_game_replay(context, document)

    def test_session_restore_and_context_construction_fail_closed(self) -> None:
        context = self._contexts["abstract-puzzle"]
        session = GameSession(context.gamepack)
        changed = session.state
        changed["target"] = ["B", "A", "C"]
        with self.assertRaisesRegex(GameLogicError, "state_constant_mismatch"):
            session.restore(changed)
        self.assertEqual(
            session.state_hash,
            "0e45dbe418fea6b992d47cc9099d83a733c57ea64ae2c994d2d1e225f9a14bad",
        )
        with self.assertRaisesRegex(GameLogicError, "persistence_context_invalid"):
            GamePersistenceContext(
                gamepack=context.gamepack,
                adapter=context.adapter,
                bindings=context.bindings,
                max_actions=128,
                max_state_bytes=65536,
            )

    def test_hostile_graph_and_numeric_limits_fail_before_contract_comparison(self) -> None:
        context = self._contexts["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)

        aliased = copy.deepcopy(save)
        shared = ["A", "B", "C"]
        aliased["state"]["saved"]["board"] = shared
        aliased["state"]["saved"]["target"] = shared
        with self.assertRaisesRegex(GameLogicError, "json_alias"):
            validate_game_save_document(aliased, context)

        cyclic = copy.deepcopy(save)
        board = cyclic["state"]["saved"]["board"]
        board.append(board)
        with self.assertRaisesRegex(GameLogicError, "json_cycle"):
            validate_game_save_document(cyclic, context)

        unsafe = copy.deepcopy(save)
        unsafe["state"]["saved"]["move_count"] = 9_007_199_254_740_992
        with self.assertRaisesRegex(GameLogicError, "json_integer_unsupported"):
            validate_game_save_document(unsafe, context)

        deep = copy.deepcopy(save)
        nested: dict[str, object] = {}
        cursor = nested
        for _ in range(65):
            child: dict[str, object] = {}
            cursor["x"] = child
            cursor = child
        deep["bindings"]["runtime_api"] = nested
        with self.assertRaisesRegex(GameLogicError, "json_depth_exceeded"):
            validate_game_save_document(deep, context)

        with self.assertRaisesRegex(GameLogicError, "persistence_bytes_exceeded"):
            load_game_save_bytes(
                b"{" + b" " * MAX_GAME_SAVE_BYTES + b"}",
                context,
            )
        with self.assertRaisesRegex(GameLogicError, "persistence_bytes_exceeded"):
            load_game_replay_bytes(
                b"{" + b" " * MAX_GAME_REPLAY_BYTES + b"}",
                context,
            )

    def test_replay_action_budget_and_trace_chain_are_exact(self) -> None:
        context = self._contexts["abstract-puzzle"]
        session = GameSession(context.gamepack)
        accepted = session.apply("swap_tiles", {"first_index": 0, "second_index": 1})
        recorder = GameReplayRecorder(context)
        for _ in range(context.max_actions):
            recorder.record(accepted)
        with self.assertRaisesRegex(GameLogicError, "replay_action_limit"):
            recorder.record(accepted)
        with self.assertRaisesRegex(GameLogicError, "replay_step_mismatch"):
            build_game_replay(context, [accepted, accepted])

    def test_strict_bytes_bounds_slots_and_user_data_layout(self) -> None:
        context = self._contexts["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)
        replay = build_game_replay(context, [])
        for payload, loader in (
            (serialize_game_save(save), lambda value: load_game_save_bytes(value, context)),
            (serialize_game_replay(replay), lambda value: load_game_replay_bytes(value, context)),
        ):
            self.assertIsInstance(loader(payload), dict)
            for invalid in (
                b'{"format":"x","format":"x"}',
                b'{"value":1.0}',
                b'{"value":1e2}',
                b'{"value":NaN}',
                b"[]",
                b"\xff",
            ):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(GameLogicError):
                        loader(invalid)

        self.assertEqual(validate_slot_name("slot_01"), "slot_01")
        for invalid in ("A", "aux", "com1", "bad.name", "../escape", "a" * 33, "é"):
            with self.subTest(slot=invalid):
                with self.assertRaisesRegex(GameLogicError, "slot_invalid"):
                    validate_slot_name(invalid)

        with tempfile.TemporaryDirectory(prefix="wf-persistence-slots-") as temporary:
            root = Path(temporary)
            save_path = write_game_save_slot(root, "slot_01", save, context)
            replay_path = write_game_replay_slot(root, "slot_01", replay, context)
            self.assertEqual(
                save_path.relative_to(root).as_posix(),
                (
                    f"saves/{context.gamepack_identity['id']}/"
                    f"{context.runtime_bundle_identity['id']}/slot_01.slot/v1/"
                    f"generations/{save_path.name}"
                ),
            )
            self.assertEqual(
                replay_path.relative_to(root).as_posix(),
                (
                    f"replays/{context.gamepack_identity['id']}/"
                    f"{context.runtime_bundle_identity['id']}/slot_01.slot/v1/"
                    f"generations/{replay_path.name}"
                ),
            )
            self.assertEqual(read_game_save_slot(root, "slot_01", context), save)
            self.assertEqual(read_game_replay_slot(root, "slot_01", context), replay)

    def test_generation_contract_hash_filename_and_schema_are_exact(self) -> None:
        context = self._contexts["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)
        generation = build_persistence_generation(
            save,
            kind="save",
            slot="slot_01",
            sequence=0,
            parent_hashes=[],
            operation="write",
            context=context,
        )
        self.assertEqual(
            generation,
            {
                "format": PERSISTENCE_GENERATION_FORMAT,
                "format_version": PERSISTENCE_GENERATION_VERSION,
                "kind": "save",
                "slot": "slot_01",
                "sequence": 0,
                "parent_hashes": [],
                "operation": "write",
                "payload": save,
                "payload_hash": save["content_hash"],
                "content_hash": generation["content_hash"],
            },
        )
        self.assertEqual(
            generation["content_hash"],
            canonical_persistence_hash(generation),
        )
        self.assertEqual(
            validate_persistence_generation_document(
                generation,
                context=context,
                expected_kind="save",
                expected_slot="slot_01",
            ),
            generation,
        )
        with self.assertRaisesRegex(GameLogicError, "persistence_generation_invalid"):
            build_persistence_generation(
                save,
                kind="save",
                slot="slot_01",
                sequence=1,
                parent_hashes=[],
                operation="write",
                context=context,
            )
        with self.assertRaisesRegex(GameLogicError, "persistence_generation_invalid"):
            build_persistence_generation(
                save,
                kind="save",
                slot="slot_01",
                sequence=1,
                parent_hashes=["f" * 64, "0" * 64],
                operation="write",
                context=context,
            )

        schemas = build_schemas()
        self.assertIn("schemas/persistence-generation.schema.json", schemas)
        schema = schemas["schemas/persistence-generation.schema.json"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["properties"]["format"]["const"],
            PERSISTENCE_GENERATION_FORMAT,
        )
        self.assertEqual(
            schema["properties"]["payload"]["oneOf"],
            [
                {"$ref": "game-replay.schema.json"},
                {"$ref": "game-save.schema.json"},
            ],
        )

    def test_slots_append_immutable_generations_and_preserve_payload_hashes(self) -> None:
        context = self._contexts["abstract-puzzle"]
        initial_session = GameSession(context.gamepack)
        initial = build_game_save(context, initial_session.state)
        solved_session = GameSession(context.gamepack)
        solved_session.apply("swap_tiles", {"first_index": 0, "second_index": 1})
        solved = build_game_save(context, solved_session.state)
        replay = build_game_replay(context, [])

        with tempfile.TemporaryDirectory(prefix="wf-persistence-generations-") as temporary:
            root = Path(temporary)
            first = write_game_save_slot(root, "slot", initial, context)
            first_bytes = first.read_bytes()
            second = write_game_save_slot(root, "slot", solved, context)
            replay_generation = write_game_replay_slot(root, "slot", replay, context)

            self.assertRegex(
                first.relative_to(root).as_posix(),
                (
                    r"^saves/[^/]+/[^/]+/slot\.slot/v1/generations/"
                    r"00000000000000000000-[0-9a-f]{64}\.json$"
                ),
            )
            self.assertRegex(
                second.name,
                r"^00000000000000000001-[0-9a-f]{64}\.json$",
            )
            self.assertRegex(
                replay_generation.relative_to(root).as_posix(),
                (
                    r"^replays/[^/]+/[^/]+/slot\.slot/v1/generations/"
                    r"00000000000000000000-[0-9a-f]{64}\.json$"
                ),
            )
            self.assertEqual(first.read_bytes(), first_bytes)
            first_document = json.loads(first_bytes)
            second_document = json.loads(second.read_bytes())
            self.assertEqual(first_document["payload_hash"], initial["content_hash"])
            self.assertEqual(second_document["payload_hash"], solved["content_hash"])
            self.assertEqual(
                second_document["parent_hashes"],
                [first_document["content_hash"]],
            )
            self.assertEqual(read_game_save_slot(root, "slot", context), solved)
            self.assertEqual(read_game_replay_slot(root, "slot", context), replay)

    def test_concurrent_writers_serialize_and_external_fork_requires_resolution(
        self,
    ) -> None:
        context = self._contexts["abstract-puzzle"]
        initial_session = GameSession(context.gamepack)
        initial = build_game_save(context, initial_session.state)
        left_session = GameSession(context.gamepack)
        left_session.apply("swap_tiles", {"first_index": 0, "second_index": 1})
        left = build_game_save(context, left_session.state)
        right_session = GameSession(context.gamepack)
        right_session.apply("swap_tiles", {"first_index": 1, "second_index": 2})
        right = build_game_save(context, right_session.state)

        with tempfile.TemporaryDirectory(prefix="wf-persistence-fork-") as temporary:
            root = Path(temporary)
            write_game_save_slot(root, "slot", initial, context)
            barrier = threading.Barrier(2)
            failures: list[BaseException] = []

            def writer(value: dict[str, object]) -> None:
                try:
                    barrier.wait(timeout=5)
                    write_game_save_slot(root, "slot", value, context)
                except BaseException as exc:
                    failures.append(exc)

            threads = [
                threading.Thread(target=writer, args=(left,)),
                threading.Thread(target=writer, args=(right,)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(failures, [])
            self.assertIn(
                read_game_save_slot(root, "slot", context),
                (left, right),
            )

            generations = sorted(
                (
                    root
                    / "saves"
                    / context.gamepack_identity["id"]
                    / context.runtime_bundle_identity["id"]
                    / "slot.slot"
                    / "v1"
                    / "generations"
                ).glob("*.json")
            )
            self.assertEqual(len(generations), 3)
            documents = [json.loads(path.read_text(encoding="utf-8")) for path in generations]
            tip = max(documents, key=lambda item: item["sequence"])
            parent_hash = tip["parent_hashes"][0]
            sibling_payload = (
                initial if tip["payload"]["content_hash"] != initial["content_hash"] else left
            )
            sibling = build_persistence_generation(
                sibling_payload,
                kind="save",
                slot="slot",
                sequence=tip["sequence"],
                parent_hashes=[parent_hash],
                operation="write",
                context=context,
            )
            sibling_path = generations[0].parent / (
                f"{sibling['sequence']:020d}-{sibling['content_hash']}.json"
            )
            sibling_path.write_bytes(
                __import__(
                    "gamepack_runtime",
                    fromlist=["serialize_persistence_generation"],
                ).serialize_persistence_generation(
                    sibling,
                    context=context,
                )
            )

            with self.assertRaisesRegex(GameLogicError, "persistence_generation_fork"):
                read_game_save_slot(root, "slot", context)
            with self.assertRaisesRegex(GameLogicError, "persistence_generation_fork"):
                write_game_save_slot(root, "slot", initial, context)

            resolution = resolve_game_save_slot_conflict(
                root,
                "slot",
                right,
                context,
            )
            document = json.loads(resolution.read_text(encoding="utf-8"))
            self.assertEqual(document["operation"], "conflict_resolution")
            self.assertEqual(len(document["parent_hashes"]), 2)
            self.assertEqual(
                document["parent_hashes"],
                sorted(document["parent_hashes"]),
            )
            self.assertEqual(read_game_save_slot(root, "slot", context), right)

    def test_capacity_reserves_the_final_generation_for_conflict_resolution(
        self,
    ) -> None:
        context = self._contexts["abstract-puzzle"]
        initial_session = GameSession(context.gamepack)
        initial = build_game_save(context, initial_session.state)
        solved_session = GameSession(context.gamepack)
        solved_session.apply("swap_tiles", {"first_index": 0, "second_index": 1})
        solved = build_game_save(context, solved_session.state)
        generation_module = __import__(
            "gamepack_runtime.persistence_generation",
            fromlist=["MAX_PERSISTENCE_GENERATIONS"],
        )

        with (
            tempfile.TemporaryDirectory(prefix="wf-persistence-capacity-") as temporary,
            mock.patch.object(
                generation_module,
                "MAX_PERSISTENCE_GENERATIONS",
                4,
            ),
        ):
            root = Path(temporary)
            for value in (initial, solved, initial):
                write_game_save_slot(root, "ordinary", value, context)
            ordinary_generations = (
                root
                / "saves"
                / context.gamepack_identity["id"]
                / context.runtime_bundle_identity["id"]
                / "ordinary.slot"
                / "v1"
                / "generations"
            )
            before = sorted(path.name for path in ordinary_generations.glob("*.json"))
            self.assertEqual(len(before), 3)
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_generation_limit",
            ):
                write_game_save_slot(root, "ordinary", solved, context)
            self.assertEqual(
                sorted(path.name for path in ordinary_generations.glob("*.json")),
                before,
            )
            self.assertEqual(read_game_save_slot(root, "ordinary", context), initial)

            first = write_game_save_slot(root, "fork", initial, context)
            second = write_game_save_slot(root, "fork", solved, context)
            first_document = json.loads(first.read_text(encoding="utf-8"))
            second_document = json.loads(second.read_text(encoding="utf-8"))
            sibling = build_persistence_generation(
                initial,
                kind="save",
                slot="fork",
                sequence=second_document["sequence"],
                parent_hashes=[first_document["content_hash"]],
                operation="write",
                context=context,
            )
            generations = second.parent
            (
                generations / f"{sibling['sequence']:020d}-{sibling['content_hash']}.json"
            ).write_bytes(
                __import__(
                    "gamepack_runtime",
                    fromlist=["serialize_persistence_generation"],
                ).serialize_persistence_generation(
                    sibling,
                    context=context,
                )
            )
            resolution = resolve_game_save_slot_conflict(
                root,
                "fork",
                solved,
                context,
            )
            self.assertEqual(
                json.loads(resolution.read_text(encoding="utf-8"))["operation"],
                "conflict_resolution",
            )
            self.assertEqual(len(list(generations.glob("*.json"))), 4)
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_generation_limit",
            ):
                write_game_save_slot(root, "fork", initial, context)
            self.assertEqual(len(list(generations.glob("*.json"))), 4)
            self.assertEqual(read_game_save_slot(root, "fork", context), solved)

    def test_total_byte_capacity_rejects_before_immutable_publication(self) -> None:
        context = self._contexts["abstract-puzzle"]
        initial_session = GameSession(context.gamepack)
        initial = build_game_save(context, initial_session.state)
        solved_session = GameSession(context.gamepack)
        solved_session.apply("swap_tiles", {"first_index": 0, "second_index": 1})
        solved = build_game_save(context, solved_session.state)
        generation_module = __import__(
            "gamepack_runtime.persistence_generation",
            fromlist=["MAX_PERSISTENCE_GENERATION_TOTAL_BYTES"],
        )

        with tempfile.TemporaryDirectory(prefix="wf-persistence-total-capacity-") as temporary:
            root = Path(temporary)
            first = write_game_save_slot(root, "slot", initial, context)
            generations = first.parent
            before = {path.name: path.read_bytes() for path in sorted(generations.glob("*.json"))}
            with (
                mock.patch.object(
                    generation_module,
                    "MAX_PERSISTENCE_GENERATION_TOTAL_BYTES",
                    sum(len(payload) for payload in before.values()) + 1,
                ),
                self.assertRaisesRegex(
                    GameLogicError,
                    "persistence_generation_limit",
                ),
            ):
                write_game_save_slot(root, "slot", solved, context)
            self.assertEqual(
                {path.name: path.read_bytes() for path in sorted(generations.glob("*.json"))},
                before,
            )
            self.assertEqual(read_game_save_slot(root, "slot", context), initial)

    def test_legacy_slots_require_explicit_migration_and_anchor_exact_bytes(self) -> None:
        context = self._contexts["abstract-puzzle"]
        initial_session = GameSession(context.gamepack)
        initial = build_game_save(context, initial_session.state)
        solved_session = GameSession(context.gamepack)
        solved_session.apply("swap_tiles", {"first_index": 0, "second_index": 1})
        solved = build_game_save(context, solved_session.state)

        with tempfile.TemporaryDirectory(prefix="wf-persistence-legacy-") as temporary:
            root = Path(temporary)
            legacy = (
                root
                / "saves"
                / context.gamepack_identity["id"]
                / context.runtime_bundle_identity["id"]
                / "slot.json"
            )
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(serialize_game_save(initial))
            before = sorted(path.relative_to(root) for path in root.rglob("*"))
            self.assertEqual(read_game_save_slot(root, "slot", context), initial)
            self.assertEqual(
                sorted(path.relative_to(root) for path in root.rglob("*")),
                before,
            )
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_legacy_migration_required",
            ):
                write_game_save_slot(root, "slot", solved, context)

            migrated = migrate_legacy_game_save_slot(root, "slot", context)
            generation = json.loads(migrated.read_text(encoding="utf-8"))
            self.assertEqual(generation["sequence"], 0)
            self.assertEqual(generation["operation"], "legacy_migration")
            self.assertEqual(generation["payload_hash"], initial["content_hash"])
            self.assertEqual(read_game_save_slot(root, "slot", context), initial)

            legacy.write_bytes(serialize_game_save(solved))
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_legacy_anchor_mismatch",
            ):
                read_game_save_slot(root, "slot", context)

    def test_rollbacks_append_verified_payloads_without_rewriting_history(self) -> None:
        context = self._contexts["abstract-puzzle"]
        initial_session = GameSession(context.gamepack)
        initial = build_game_save(context, initial_session.state)
        solved_session = GameSession(context.gamepack)
        solved_session.apply("swap_tiles", {"first_index": 0, "second_index": 1})
        solved = build_game_save(context, solved_session.state)
        replay = build_game_replay(context, [])

        with tempfile.TemporaryDirectory(prefix="wf-persistence-rollback-") as temporary:
            root = Path(temporary)
            first = write_game_save_slot(root, "slot", initial, context)
            write_game_save_slot(root, "slot", solved, context)
            first_document = json.loads(first.read_text(encoding="utf-8"))
            rollback = rollback_game_save_slot(
                root,
                "slot",
                first_document["content_hash"],
                context,
            )
            rollback_document = json.loads(rollback.read_text(encoding="utf-8"))
            self.assertEqual(rollback_document["sequence"], 2)
            self.assertEqual(rollback_document["operation"], "rollback")
            self.assertEqual(rollback_document["payload"], initial)
            self.assertEqual(read_game_save_slot(root, "slot", context), initial)
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_generation_unknown",
            ):
                rollback_game_save_slot(root, "slot", "f" * 64, context)

            replay_generation = write_game_replay_slot(root, "replay", replay, context)
            replay_hash = json.loads(replay_generation.read_text())["content_hash"]
            replay_rollback = rollback_game_replay_slot(
                root,
                "replay",
                replay_hash,
                context,
            )
            self.assertEqual(
                json.loads(replay_rollback.read_text())["operation"],
                "rollback",
            )
            self.assertEqual(read_game_replay_slot(root, "replay", context), replay)

    def test_generation_inventory_rejects_tamper_links_and_ambiguous_entries(self) -> None:
        context = self._contexts["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)
        with tempfile.TemporaryDirectory(prefix="wf-persistence-inventory-") as temporary:
            root = Path(temporary)
            generation = write_game_save_slot(root, "slot", save, context)
            original = generation.read_bytes()

            generation.rename(generation.with_name("00000000000000000001-" + generation.name[21:]))
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_generation_filename_mismatch",
            ):
                read_game_save_slot(root, "slot", context)

            generation = generation.with_name("00000000000000000001-" + generation.name[21:])
            generation.unlink()
            generation = generation.with_name("00000000000000000000-" + generation.name[21:])
            generation.write_bytes(original)
            ambiguous = generation.parent / "unexpected.txt"
            ambiguous.write_text("foreign", encoding="utf-8")
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_generation_inventory_unsafe",
            ):
                read_game_save_slot(root, "slot", context)
            ambiguous.unlink()

            hardlink = generation.parent / ("00000000000000000001-" + "f" * 64 + ".json")
            try:
                os.link(generation, hardlink)
            except OSError:
                self.skipTest("hardlink creation is unavailable")
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_generation_inventory_unsafe",
            ):
                read_game_save_slot(root, "slot", context)

    def test_generation_graph_bounds_missing_parents_and_cycles_fail_closed(self) -> None:
        context = self._contexts["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)
        generation_module = __import__(
            "gamepack_runtime.persistence_generation",
            fromlist=["_verify_dag"],
        )
        root_hash = "0" * 64
        child_hash = "1" * 64

        with self.assertRaisesRegex(
            GameLogicError,
            "persistence_generation_missing_parent",
        ):
            generation_module._verify_dag(
                {
                    child_hash: {
                        "sequence": 1,
                        "parent_hashes": [root_hash],
                    }
                }
            )

        with self.assertRaisesRegex(
            GameLogicError,
            "persistence_generation_cycle",
        ):
            generation_module._verify_dag(
                {
                    root_hash: {
                        "sequence": 2,
                        "parent_hashes": [child_hash],
                    },
                    child_hash: {
                        "sequence": 1,
                        "parent_hashes": [root_hash],
                    },
                }
            )

        chain = {
            root_hash: {
                "sequence": 0,
                "parent_hashes": [],
            },
            child_hash: {
                "sequence": 1,
                "parent_hashes": [root_hash],
            },
        }
        with (
            mock.patch.object(
                generation_module,
                "MAX_PERSISTENCE_GENERATION_DEPTH",
                1,
            ),
            self.assertRaisesRegex(
                GameLogicError,
                "persistence_generation_limit",
            ),
        ):
            generation_module._verify_dag(chain)
        with (
            mock.patch.object(
                generation_module,
                "MAX_PERSISTENCE_GENERATIONS",
                1,
            ),
            self.assertRaisesRegex(
                GameLogicError,
                "persistence_generation_limit",
            ),
        ):
            generation_module._verify_dag(chain)

        too_many_parents = [f"{index:064x}" for index in range(129)]
        with self.assertRaisesRegex(
            GameLogicError,
            "persistence_generation_invalid",
        ):
            build_persistence_generation(
                save,
                kind="save",
                slot="bounded",
                sequence=1,
                parent_hashes=too_many_parents,
                operation="conflict_resolution",
                context=context,
            )

        with tempfile.TemporaryDirectory(prefix="wf-persistence-byte-bound-") as temporary:
            root = Path(temporary)
            write_game_save_slot(root, "bounded", save, context)
            with (
                mock.patch.object(
                    generation_module,
                    "MAX_PERSISTENCE_GENERATION_TOTAL_BYTES",
                    1,
                ),
                self.assertRaisesRegex(
                    GameLogicError,
                    "persistence_generation_limit",
                ),
            ):
                read_game_save_slot(root, "bounded", context)

    def test_slot_paths_reject_forged_nonportable_identities_before_io(self) -> None:
        context = self._contexts["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)
        replay = build_game_replay(context, [])
        hostile_identities = (
            ("gamepack", "../../outside-root"),
            ("gamepack", "folder/name"),
            ("gamepack", r"folder\name"),
            ("gamepack", "."),
            ("gamepack", ".."),
            ("gamepack", "game.name"),
            ("gamepack", "aux"),
            ("gamepack", "Game"),
            ("gamepack", "e\u0301"),
            ("gamepack", "é"),
            ("runtime_bundle", "../../../outside-root"),
            ("runtime_bundle", "folder/name"),
            ("runtime_bundle", r"folder\name"),
            ("runtime_bundle", "."),
            ("runtime_bundle", ".."),
            ("runtime_bundle", "bundle.name"),
            ("runtime_bundle", "COM1"),
            ("runtime_bundle", f"GAME_RUNTIME_BUNDLE_{'a' * 48}"),
            ("runtime_bundle", "e\u0301"),
            ("runtime_bundle", "é"),
        )
        with tempfile.TemporaryDirectory(prefix="wf-persistence-containment-") as temporary:
            sandbox = Path(temporary)
            root = sandbox / "user-data"
            root.mkdir()
            outside = sandbox / "outside-root"
            outside.mkdir()
            sentinel = outside / "sentinel"
            sentinel.write_bytes(b"untouched")
            before = sorted(path.relative_to(sandbox) for path in sandbox.rglob("*"))

            for binding, identifier in hostile_identities:
                forged = _forged_context_identity(context, binding, identifier)
                operations = (
                    lambda forged=forged: write_game_save_slot(root, "slot", save, forged),
                    lambda forged=forged: write_game_replay_slot(
                        root,
                        "slot",
                        replay,
                        forged,
                    ),
                    lambda forged=forged: read_game_save_slot(root, "slot", forged),
                    lambda forged=forged: read_game_replay_slot(root, "slot", forged),
                )
                for operation in operations:
                    with self.subTest(binding=binding, identifier=identifier):
                        with self.assertRaisesRegex(
                            GameLogicError,
                            "persistence_path_identity_invalid",
                        ):
                            operation()

            self.assertEqual(sentinel.read_bytes(), b"untouched")
            self.assertEqual(
                sorted(path.relative_to(sandbox) for path in sandbox.rglob("*")),
                before,
            )

        verified = self._bundles["abstract-puzzle"]
        composition = _document_from_bundle(
            verified,
            "contracts/runtime-composition.json",
        )
        adapter_path = verified.manifest["contracts"]["runtime_adapter"]["path"]
        adapter = _document_from_bundle(verified, adapter_path)
        hostile_bundle = copy.deepcopy(verified.manifest)
        hostile_bundle["bundle_id"] = "../../../outside-root"
        hostile_bundle["content_hash"] = canonical_persistence_hash(hostile_bundle)
        with self.assertRaisesRegex(
            GameLogicError,
            "persistence_path_identity_invalid",
        ):
            build_game_persistence_context(
                context.gamepack,
                composition,
                hostile_bundle,
                adapter,
            )

    def test_atomic_writes_serialize_competitors_and_reject_links(self) -> None:
        context = self._contexts["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)
        with tempfile.TemporaryDirectory(prefix="wf-persistence-atomic-") as temporary:
            root = Path(temporary)
            failures: list[BaseException] = []

            def writer() -> None:
                try:
                    write_game_save_slot(root, "parallel", save, context)
                except GameLogicError as exc:
                    failures.append(exc)

            threads = [threading.Thread(target=writer) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(read_game_save_slot(root, "parallel", context), save)
            self.assertTrue(all(error.reason_code == "persistence_locked" for error in failures))

            target = write_game_save_slot(root, "linked", save, context)
            target.unlink()
            try:
                target.symlink_to(root / "foreign.json")
            except OSError:
                self.skipTest("symlink creation is unavailable")
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_(?:target_unsafe|generation_inventory_unsafe)",
            ):
                write_game_save_slot(root, "linked", save, context)
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_(?:target_unsafe|generation_inventory_unsafe)",
            ):
                read_game_save_slot(root, "linked", context)

            target.unlink()
            foreign = root / "foreign.json"
            foreign.write_bytes(serialize_game_save(save))
            try:
                os.link(foreign, target)
            except OSError:
                self.skipTest("hardlink creation is unavailable")
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_(?:target_unsafe|generation_inventory_unsafe)",
            ):
                write_game_save_slot(root, "linked", save, context)
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_(?:target_unsafe|generation_inventory_unsafe)",
            ):
                read_game_save_slot(root, "linked", context)

    def test_slot_reads_and_writes_reject_parent_escape_outside_root(self) -> None:
        context = self._contexts["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)
        with tempfile.TemporaryDirectory(prefix="wf-persistence-parent-escape-") as temporary:
            sandbox = Path(temporary)
            root = sandbox / "user-data"
            root.mkdir()
            outside = sandbox / "outside"
            bundle_id = context.runtime_bundle_identity["id"]
            outside_slot = outside / bundle_id / "slot.json"
            outside_slot.parent.mkdir(parents=True)
            outside_slot.write_bytes(serialize_game_save(save))
            before = outside_slot.read_bytes()

            saves = root / "saves"
            saves.mkdir()
            escaped_game_parent = saves / context.gamepack_identity["id"]
            try:
                escaped_game_parent.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlink creation is unavailable")

            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_path_outside_root|persistence_parent_unsafe",
            ):
                write_game_save_slot(root, "slot", save, context)
            with self.assertRaisesRegex(
                GameLogicError,
                "persistence_path_outside_root|persistence_parent_unsafe",
            ):
                read_game_save_slot(root, "slot", context)
            self.assertEqual(outside_slot.read_bytes(), before)

    def test_slot_namespace_swap_fails_before_outside_read_or_write(self) -> None:
        context = self._contexts["abstract-puzzle"]
        initial_session = GameSession(context.gamepack)
        initial = build_game_save(context, initial_session.state)
        solved_session = GameSession(context.gamepack)
        solved_session.apply(
            "swap_tiles",
            {"first_index": 0, "second_index": 1},
        )
        solved = build_game_save(context, solved_session.state)

        for operation in ("read", "write"):
            with (
                self.subTest(operation=operation),
                tempfile.TemporaryDirectory(
                    prefix=f"wf-persistence-namespace-{operation}-"
                ) as temporary,
            ):
                sandbox = Path(temporary)
                root = sandbox / "user-data"
                root.mkdir()
                target = write_game_save_slot(root, "slot", initial, context)
                target_bytes = target.read_bytes()
                saves = root / "saves"
                outside = sandbox / "outside"
                outside.mkdir()
                moved = outside / "moved-saves"
                original_tree = {
                    path.relative_to(saves).as_posix(): (
                        path.read_bytes() if path.is_file() else None
                    )
                    for path in sorted(saves.rglob("*"))
                }
                original_open_parent = __import__(
                    "gamepack_runtime.persistence_io",
                    fromlist=["_open_verified_output_parent"],
                )._open_verified_output_parent

                @contextlib.contextmanager
                def swap_after_pin(
                    path: Path,
                    *,
                    create: bool = True,
                    open_parent=original_open_parent,
                    source=saves,
                    destination=moved,
                ):
                    with open_parent(path, create=create) as retained:
                        source.rename(destination)
                        source.symlink_to(destination, target_is_directory=True)
                        yield retained

                with mock.patch(
                    "gamepack_runtime.persistence_io._open_verified_output_parent",
                    swap_after_pin,
                ):
                    with self.assertRaisesRegex(
                        GameLogicError,
                        "persistence_parent_unsafe|persistence_path_outside_root",
                    ):
                        if operation == "read":
                            read_game_save_slot(root, "slot", context)
                        else:
                            write_game_save_slot(root, "slot", solved, context)

                self.assertTrue(saves.is_symlink())
                self.assertEqual(
                    {
                        path.relative_to(moved).as_posix(): (
                            path.read_bytes() if path.is_file() else None
                        )
                        for path in sorted(moved.rglob("*"))
                    },
                    original_tree,
                )
                self.assertEqual(
                    (moved / target.relative_to(root / "saves")).read_bytes(),
                    target_bytes,
                )

    def test_immutable_publication_is_idempotent_but_never_replaces(self) -> None:
        context = self._contexts["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=["publish_json_noreplace"],
        )
        with tempfile.TemporaryDirectory(prefix="wf-persistence-no-replace-") as temporary:
            root = Path(temporary)
            target = write_game_save_slot(root, "slot", save, context)
            before = target.read_bytes()
            generation = json.loads(before)
            staging = target.parents[1] / "staging"
            self.assertEqual(
                persistence_io.publish_json_noreplace(
                    staging,
                    target.parent,
                    target.name,
                    generation,
                ),
                target,
            )
            self.assertEqual(target.read_bytes(), before)
            with self.assertRaises(GameLogicError) as raised:
                persistence_io.publish_json_noreplace(
                    staging,
                    target.parent,
                    target.name,
                    {"different": True},
                )
            self.assertEqual(
                raised.exception.reason_code,
                "persistence_generation_collision",
            )
            self.assertEqual(target.read_bytes(), before)

    def test_immutable_publication_flushes_fresh_and_idempotent_ancestry(self) -> None:
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=["publish_json_noreplace"],
        )
        with tempfile.TemporaryDirectory(prefix="wf-persistence-durable-") as temporary:
            root = Path(temporary)
            staging = root / "slot" / "v1" / "staging"
            generations = root / "slot" / "v1" / "generations"
            observed: list[Path] = []
            original_fsync = os.fsync

            def capture_fsync(descriptor: int) -> None:
                try:
                    observed.append(Path(os.readlink(f"/proc/self/fd/{descriptor}")))
                except OSError:
                    pass
                original_fsync(descriptor)

            with mock.patch(
                "gamepack_runtime.persistence_io.os.fsync",
                side_effect=capture_fsync,
            ):
                target = persistence_io.publish_json_noreplace(
                    staging,
                    generations,
                    "generation.json",
                    {"value": 1},
                )
            self.assertIn(root, observed)
            self.assertIn(staging, observed)
            self.assertIn(generations, observed)

            observed.clear()
            with mock.patch(
                "gamepack_runtime.persistence_io.os.fsync",
                side_effect=capture_fsync,
            ):
                self.assertEqual(
                    persistence_io.publish_json_noreplace(
                        staging,
                        generations,
                        "generation.json",
                        {"value": 1},
                    ),
                    target,
                )
            self.assertIn(target, observed)
            self.assertIn(staging, observed)
            self.assertIn(generations, observed)

    @unittest.skipUnless(os.name == "posix", "requires POSIX retained descriptors")
    def test_identical_publish_race_removes_only_its_owned_stage(self) -> None:
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=["_linux_link_descriptor_no_replace", "publish_json_noreplace"],
        )
        with tempfile.TemporaryDirectory(prefix="wf-persistence-race-cleanup-") as temporary:
            root = Path(temporary)
            staging = root / "staging"
            generations = root / "generations"
            original_link = persistence_io._linux_link_descriptor_no_replace
            injected = False

            def publish_winner_then_collide(
                source_descriptor: int,
                destination_descriptor: int,
                destination_name: str,
            ) -> None:
                nonlocal injected
                if injected:
                    original_link(
                        source_descriptor,
                        destination_descriptor,
                        destination_name,
                    )
                    return
                injected = True
                payload = os.pread(
                    source_descriptor,
                    os.fstat(source_descriptor).st_size,
                    0,
                )
                destination = os.open(
                    destination_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=destination_descriptor,
                )
                try:
                    self.assertEqual(os.write(destination, payload), len(payload))
                    os.fsync(destination)
                finally:
                    os.close(destination)
                raise FileExistsError

            with mock.patch.object(
                persistence_io,
                "_linux_link_descriptor_no_replace",
                side_effect=publish_winner_then_collide,
            ):
                result = persistence_io.publish_json_noreplace(
                    staging,
                    generations,
                    "generation.json",
                    {"value": 1},
                )
            self.assertEqual(result, generations / "generation.json")
            self.assertEqual(list(staging.iterdir()), [])

    def test_deep_json_is_normalized_before_the_native_decoder_recurses(self) -> None:
        context = self._contexts["abstract-puzzle"]
        payload = b'{"value":' + (b"[" * 10_000) + b"null" + (b"]" * 10_000) + b"}"
        with self.assertRaises(GameLogicError) as raised:
            load_persistence_generation_bytes(
                payload,
                context=context,
            )
        self.assertIn(
            raised.exception.reason_code,
            {"json_depth_exceeded", "json_syntax_invalid"},
        )

    def test_unreturned_temporary_and_windows_handles_are_closed(self) -> None:
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=[
                "PersistenceIOError",
                "_PinnedOutputParent",
                "_create_temporary_entry",
            ],
        )
        with tempfile.TemporaryDirectory(prefix="wf-persistence-fd-cleanup-") as temporary:
            root = Path(temporary)
            parent_descriptor = os.open(
                root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            created: list[int] = []
            original_open = os.open

            def capture_open(
                path: object,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
                if path == "." and flags & getattr(os, "O_TMPFILE", 0):
                    created.append(descriptor)
                return descriptor

            parent = persistence_io._PinnedOutputParent(
                root,
                ((1, 1),),
                posix_descriptors=(parent_descriptor,),
            )
            try:
                with (
                    mock.patch(
                        "gamepack_runtime.persistence_io.os.open",
                        capture_open,
                    ),
                    mock.patch.object(
                        persistence_io._PinnedOutputParent,
                        "assert_current",
                        side_effect=[
                            None,
                            persistence_io.PersistenceIOError(
                                "injected post-create ancestry change",
                                reason_code="persistence_parent_unsafe",
                            ),
                        ],
                    ),
                ):
                    with self.assertRaises(persistence_io.PersistenceIOError):
                        persistence_io._create_temporary_entry(parent, ".leak.")
                self.assertEqual(len(created), 1)
                leaked = True
                try:
                    os.fstat(created[0])
                except OSError:
                    leaked = False
                finally:
                    if leaked:
                        os.close(created[0])
                self.assertFalse(leaked, "temporary descriptor leaked before ownership return")
            finally:
                os.close(parent_descriptor)

        class FailingDuplicateApi:
            def __init__(self) -> None:
                self.closed: list[int] = []
                self.deleted: list[int] = []

            def create_temporary(self, _parent: int, _name: str) -> int:
                return 71

            def open_existing_entry(self, _parent: int, _name: str) -> int:
                raise FileNotFoundError

            def duplicate_to_descriptor(self, _handle: int, *, writable: bool) -> int:
                self.assert_writable = writable
                raise persistence_io.PersistenceIOError("injected duplicate failure")

            def _state(
                self,
                _handle: int,
                *,
                directory: bool,
                context: str,
            ) -> object:
                self.assert_state = (directory, context)
                return types.SimpleNamespace(
                    st_mode=stat.S_IFREG | 0o600,
                    st_nlink=1,
                    st_dev=1,
                    st_ino=2,
                    st_size=0,
                    st_file_attributes=0,
                )

            def close(self, handle: int) -> None:
                self.closed.append(handle)

            def mark_delete_on_close(self, handle: int) -> None:
                self.deleted.append(handle)

        api = FailingDuplicateApi()
        parent = persistence_io._PinnedOutputParent(
            Path("C:/retained"),
            ((1, 1),),
            windows_api=api,
            windows_handles=(99,),
        )
        with mock.patch.object(
            persistence_io._PinnedOutputParent,
            "assert_current",
            return_value=None,
        ):
            with self.assertRaises(persistence_io.PersistenceIOError):
                persistence_io._create_temporary_entry(parent, ".temporary.")
        self.assertEqual(api.closed, [71])
        self.assertEqual(api.deleted, [71])

    def test_windows_generation_directory_flush_uses_retained_parent(self) -> None:
        persistence_io = __import__(
            "gamepack_runtime.persistence_io",
            fromlist=["_PinnedOutputParent", "_fsync_parent"],
        )

        class RecordingWindowsApi:
            def __init__(self) -> None:
                self.calls: list[tuple[int, str, tuple[int, int], str]] = []

            def flush_relative_directory(
                self,
                parent: int,
                name: str,
                expected_identity: tuple[int, int],
                *,
                context: str,
            ) -> None:
                self.calls.append((parent, name, expected_identity, context))

        api = RecordingWindowsApi()
        parent = persistence_io._PinnedOutputParent(
            Path("C:/retained/generations"),
            ((1, 1), (2, 2)),
            windows_api=api,
            windows_handles=(97, 98),
        )
        with mock.patch.object(
            persistence_io._PinnedOutputParent,
            "assert_current",
            return_value=None,
        ) as assert_current:
            persistence_io._fsync_parent(parent)
        self.assertEqual(
            api.calls,
            [
                (
                    97,
                    "generations",
                    (2, 2),
                    "persistence directory C:/retained/generations",
                )
            ],
        )
        self.assertEqual(assert_current.call_count, 2)

    def test_parent_creation_errors_are_normalized(self) -> None:
        context = self._contexts["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)
        with tempfile.TemporaryDirectory(prefix="wf-persistence-parent-error-") as temporary:
            root = Path(temporary) / "missing-user-data"
            with mock.patch(
                "gamepack_runtime.persistence_io.os.mkdir",
                side_effect=PermissionError("injected parent denial"),
            ):
                with self.assertRaises(GameLogicError) as raised:
                    write_game_save_slot(root, "slot", save, context)
            self.assertEqual(raised.exception.reason_code, "persistence_io_error")

    def test_atomic_partial_write_parent_link_and_foreign_temp_fail_closed(self) -> None:
        context = self._contexts["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)
        with tempfile.TemporaryDirectory(prefix="wf-persistence-failure-") as temporary:
            root = Path(temporary)
            with mock.patch(
                "gamepack_runtime.persistence_io._write_all",
                side_effect=OSError("injected partial write"),
            ):
                with self.assertRaisesRegex(GameLogicError, "persistence_io_error"):
                    write_game_save_slot(root, "partial", save, context)
            slot_parent = (
                root
                / "saves"
                / context.gamepack_identity["id"]
                / context.runtime_bundle_identity["id"]
            )
            partial_root = slot_parent / "partial.slot" / "v1"
            self.assertEqual(
                list((partial_root / "generations").glob("*.json")),
                [],
            )
            retained = list((partial_root / "staging").glob(".*.stage.*"))
            self.assertEqual(len(retained), 1)
            self.assertEqual(retained[0].stat().st_nlink, 1)

            generation = build_persistence_generation(
                save,
                kind="save",
                slot="collision",
                sequence=0,
                parent_hashes=[],
                operation="write",
                context=context,
            )
            generation_name = f"{0:020d}-{generation['content_hash']}.json"
            collision_staging = slot_parent / "collision.slot" / "v1" / "staging"
            collision_staging.mkdir(parents=True)
            collision = collision_staging / (f".{generation_name}.stage.{'a' * 32}")
            collision.write_bytes(b"foreign")
            with (
                mock.patch(
                    "gamepack_runtime.persistence_io.secrets.token_hex",
                    return_value="a" * 32,
                ),
                mock.patch(
                    "gamepack_runtime.persistence_io._write_all",
                    side_effect=OSError("injected colliding partial write"),
                ),
            ):
                with self.assertRaisesRegex(GameLogicError, "persistence_io_error"):
                    write_game_save_slot(root, "collision", save, context)
            self.assertEqual(collision.read_bytes(), b"foreign")

            linked_root = root / "linked-root"
            outside = root / "outside"
            outside.mkdir()
            try:
                linked_root.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("directory symlink creation is unavailable")
            with self.assertRaisesRegex(GameLogicError, "persistence.*unsafe"):
                write_game_save_slot(linked_root, "slot", save, context)

    def test_cli_verify_is_machine_readable_and_contract_failures_use_stderr(self) -> None:
        context = self._contexts["abstract-puzzle"]
        verified = self._bundles["abstract-puzzle"]
        save = build_game_save(context, GameSession(context.gamepack).state)
        replay = build_game_replay(context, [])
        generation = build_persistence_generation(
            save,
            kind="save",
            slot="cli",
            sequence=0,
            parent_hashes=[],
            operation="write",
            context=context,
        )
        with tempfile.TemporaryDirectory(prefix="wf-persistence-cli-") as temporary:
            root = Path(temporary)
            save_path = root / "save.json"
            replay_path = root / "replay.json"
            generation_path = root / "generation.json"
            save_path.write_bytes(serialize_game_save(save))
            replay_path.write_bytes(serialize_game_replay(replay))
            generation_path.write_bytes(
                json.dumps(
                    generation,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8")
                + b"\n"
            )
            for command, source in (
                ("verify-game-save", save_path),
                ("verify-game-replay", replay_path),
                ("verify-persistence-generation", generation_path),
            ):
                with self.subTest(command=command):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with (
                        mock.patch(
                            "sys.argv",
                            ["worldforge", command, str(source), "--bundle", str(verified.root)],
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        self.assertEqual(main(), 0)
                    payload = json.loads(stdout.getvalue())
                    self.assertEqual(payload["status"], "verified")
                    self.assertEqual(
                        payload["content_hash"],
                        json.loads(source.read_text())["content_hash"],
                    )
                    self.assertEqual(stderr.getvalue(), "")

            replay_path.write_bytes(b'{"format":"world-forge.game_replay","format_version":1}')
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "verify-game-replay",
                        str(replay_path),
                        "--bundle",
                        str(verified.root),
                    ],
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(main(), 1)
            self.assertEqual(stdout.getvalue(), "")
            error = json.loads(stderr.getvalue())
            self.assertEqual(error["status"], "error")
            self.assertIsInstance(error["reason_code"], str)

    def test_forge_verifies_generation_against_the_exact_runtime_bundle(self) -> None:
        from worldforge.persistence_generation import verify_persistence_generation

        source = ROOT / (
            "examples/multigenre-contracts/abstract-puzzle/runtime/persistence/"
            "generations/saves/initial.json"
        )
        result = verify_persistence_generation(
            source,
            bundle_root=self._bundles["abstract-puzzle"].root,
        )
        generation = json.loads(source.read_text(encoding="utf-8"))
        self.assertEqual(
            result,
            {
                "content_hash": generation["content_hash"],
                "format": PERSISTENCE_GENERATION_FORMAT,
                "format_version": 1,
                "kind": "save",
                "operation": "write",
                "payload_hash": generation["payload_hash"],
                "sequence": 0,
                "slot": "initial",
                "status": "verified",
            },
        )

    def test_neutral_persistence_has_no_forge_legacy_network_or_process_imports(self) -> None:
        forbidden_roots = {
            "httpx",
            "isoworld",
            "requests",
            "socket",
            "subprocess",
            "worldforge",
        }
        for path in sorted((ROOT / "src/gamepack_runtime").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
            imported_roots: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    imported_roots.add(node.module.partition(".")[0])
            with self.subTest(path=path.name):
                self.assertEqual(imported_roots & forbidden_roots, set())
        self.assertEqual(
            _load(PUZZLE)["content_hash"],
            "0510d69d0f78d3e80810aa26dd4b76752416809f7733e731274ac8d7f35dac09",
        )
        self.assertEqual(
            _load(NARRATIVE)["content_hash"],
            "56b8a5393615603ca3a6bbc1a55cf557cadee2e05cf03a8b4714b4536e6cb7b7",
        )
