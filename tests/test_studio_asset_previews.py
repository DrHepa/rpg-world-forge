from __future__ import annotations

import base64
import hashlib
import json
import shutil
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest import mock

import isoworld.content.resource_snapshot as snapshot_module
import worldforge.studio.asset_previews as preview_module
from isoworld.content.resource_snapshot import ResourceSnapshotChunk, ResourceSnapshotError
from worldforge.integrity import canonical_json_bytes
from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.errors import StudioError, conflict
from worldforge.studio.service import StudioService
from worldforge.studio.storage import StudioStore
from worldforge.workflow import PHASES


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _FakeCatalog:
    def __init__(self, authority: object) -> None:
        self.authority = authority
        self.assertions = 0
        self.fail_assertion: int | None = None

    def resolve_preview_authority(
        self,
        workspace_id: object,
        manifest_revision: object,
        entry_id: object,
    ) -> object:
        del manifest_revision, entry_id
        return SimpleNamespace(
            **{
                **vars(self.authority),
                "guard": SimpleNamespace(
                    **{
                        **vars(self.authority.guard),
                        "workspace_id": str(workspace_id),
                    }
                ),
            }
        )

    def assert_current(self, guard: object) -> None:
        del guard
        self.assertions += 1
        if self.assertions == self.fail_assertion:
            raise conflict("injected preview authority drift")


class _FakeReader:
    def __init__(
        self,
        payloads: list[bytes],
        *,
        close_failures: int = 0,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        digest = hashlib.sha256()
        total = 0
        self.chunks: deque[ResourceSnapshotChunk] = deque()
        for sequence, payload in enumerate(payloads):
            digest.update(payload)
            total += len(payload)
            self.chunks.append(
                ResourceSnapshotChunk(
                    sequence=sequence,
                    payload=payload,
                    cumulative_bytes=total,
                    cumulative_sha256=digest.hexdigest(),
                    eof=sequence == len(payloads) - 1,
                )
            )
        self.size = total
        self.sha256 = digest.hexdigest()
        self.closed = False
        self.close_failures = close_failures
        self.close_calls = 0
        self.read_calls = 0
        self.entered = entered
        self.release = release

    def read_next(self) -> ResourceSnapshotChunk:
        self.read_calls += 1
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            self.release.wait(timeout=5)
        return self.chunks.popleft()

    def close(self) -> None:
        if self.closed:
            return
        self.close_calls += 1
        self.closed = True
        if self.close_failures:
            self.close_failures -= 1
            raise ResourceSnapshotError("injected reader close failure")


class _FakeOwner:
    def __init__(
        self,
        reader: _FakeReader,
        *,
        close_failures: int = 0,
    ) -> None:
        self.reader = reader
        self.closed = False
        self.close_failures = close_failures
        self.close_calls = 0
        self.materialize_calls = 0

    def materialize(
        self,
        source_root: Path,
        relative: PurePosixPath,
        media_type: str,
        *,
        limit: int,
    ) -> object:
        del source_root, relative, media_type
        self.materialize_calls += 1
        if limit != self.reader.size:
            raise AssertionError((limit, self.reader.size))
        return SimpleNamespace(sha256=self.reader.sha256)

    def open_reader(self, relative: PurePosixPath) -> _FakeReader:
        del relative
        return self.reader

    def close(self) -> None:
        self.close_calls += 1
        if self.close_failures:
            self.close_failures -= 1
            raise ResourceSnapshotError("injected owner cleanup failure")
        self.closed = True


def _fake_authority(payloads: list[bytes]) -> object:
    payload = b"".join(payloads)
    return SimpleNamespace(
        guard=SimpleNamespace(
            workspace_id="workspace_01",
            manifest_revision="a" * 64,
        ),
        entry_id="asset_" + "b" * 64,
        world_root=Path("/not-public"),
        relative=PurePosixPath("assets/preview.png"),
        media_type="image/png",
        byte_length=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


class AssetPreviewManagerUnitTests(unittest.TestCase):
    def _manager(
        self,
        payloads: list[bytes],
        *,
        reader_close_failures: int = 0,
        owner_close_failures: int = 0,
        policy: object | None = None,
        clock: _Clock | None = None,
        tokens: list[str] | None = None,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> tuple[preview_module.AssetPreviewManager, _FakeCatalog, list[_FakeOwner]]:
        authority = _fake_authority(payloads)
        catalog = _FakeCatalog(authority)
        owners: list[_FakeOwner] = []

        def owner_factory() -> _FakeOwner:
            owner = _FakeOwner(
                _FakeReader(
                    payloads,
                    close_failures=reader_close_failures,
                    entered=entered,
                    release=release,
                ),
                close_failures=owner_close_failures,
            )
            owners.append(owner)
            return owner

        token_values = deque(tokens or ["A" * 43, "B" * 43, "C" * 43])
        manager = preview_module.AssetPreviewManager(
            catalog,
            _policy=policy,
            _clock=clock or _Clock(),
            _owner_factory=owner_factory,
            _token_factory=token_values.popleft,
            _start_reaper=False,
        )
        return manager, catalog, owners

    def test_sequences_replay_only_the_previous_response_and_authorize_eof(self) -> None:
        payloads = [b"a" * (64 * 1024), b"b" * (64 * 1024), b"c"]
        manager, catalog, owners = self._manager(payloads)
        opened = manager.open("workspace_01", "a" * 64, "asset_" + "b" * 64)
        handle = opened["handle"]
        self.assertEqual(
            {
                "handle",
                "entry_id",
                "manifest_revision",
                "media_type",
                "byte_length",
                "sha256",
            },
            set(opened),
        )
        self.assertNotIn("/not-public", repr(opened))

        first = manager.read(handle, 0)
        self.assertEqual(b"a" * (64 * 1024), first["payload"])
        self.assertFalse(first["eof"])
        self.assertEqual(first, manager.read(handle, 0))
        second = manager.read(handle, 1)
        self.assertEqual(b"b" * (64 * 1024), second["payload"])
        with self.assertRaisesRegex(StudioError, "sequence conflict"):
            manager.read(handle, 0)
        with self.assertRaisesRegex(StudioError, "sequence conflict"):
            manager.read(handle, 3)
        final = manager.read(handle, 2)
        self.assertTrue(final["eof"])
        self.assertEqual((2 * 64 * 1024) + 1, final["cumulative_bytes"])
        self.assertEqual(
            hashlib.sha256(b"".join(payloads)).hexdigest(),
            final["cumulative_sha256"],
        )
        self.assertEqual(final, manager.read(handle, 2))
        self.assertEqual(11, catalog.assertions)
        manager.close(handle)
        manager.close(handle)
        self.assertTrue(owners[0].reader.closed)
        self.assertTrue(owners[0].closed)

    def test_sequence_is_bounded_by_the_maximum_preview_size(self) -> None:
        manager, _, _ = self._manager([b"payload"])
        opened = manager.open("workspace_01", "a" * 64, "asset_" + "b" * 64)
        for sequence in (-1, 8192, True, 0.0):
            with self.subTest(sequence=sequence), self.assertRaises(StudioError) as raised:
                manager.read(opened["handle"], sequence)
            self.assertEqual("invalid_request", raised.exception.code)
            self.assertIn("sequence", raised.exception.message)
        manager.close(opened["handle"])
        manager.shutdown()

    def test_reaper_start_failure_stops_and_joins_a_partially_started_thread(self) -> None:
        events: list[object] = []
        owner: list[object] = []

        class PartialThread:
            def __init__(self, *, target: object, name: str, daemon: bool) -> None:
                events.append(("construct", name, daemon))
                owner.append(target.__self__)
                self.alive = True

            def start(self) -> None:
                events.append("start")
                raise RuntimeError("injected reaper start failure")

            def is_alive(self) -> bool:
                return self.alive

            def join(self, timeout: float | None = None) -> None:
                events.append(("join", timeout))
                self.alive = False

        with (
            mock.patch.object(preview_module.threading, "Thread", PartialThread),
            self.assertRaisesRegex(RuntimeError, "reaper start failure"),
        ):
            preview_module.AssetPreviewManager(_FakeCatalog(_fake_authority([b"payload"])))
        self.assertTrue(owner[0]._stop.is_set())
        self.assertEqual(("construct", "asset-preview-reaper", True), events[0])
        self.assertEqual("start", events[1])
        self.assertEqual("join", events[2][0])

    def test_one_inflight_read_blocks_parallel_reads_and_close_wins_before_return(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        manager, _, owners = self._manager(
            [b"payload"],
            entered=entered,
            release=release,
        )
        handle = manager.open("workspace_01", "a" * 64, "asset_" + "b" * 64)["handle"]
        failures: list[BaseException] = []
        returned: list[dict[str, object]] = []

        def read() -> None:
            try:
                returned.append(manager.read(handle, 0))
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=read)
        thread.start()
        self.assertTrue(entered.wait(timeout=5))
        with self.assertRaisesRegex(StudioError, "in progress"):
            manager.read(handle, 0)
        manager.close(handle)
        release.set()
        thread.join(timeout=5)
        self.assertEqual([], returned)
        self.assertEqual(1, len(failures))
        self.assertIsInstance(failures[0], StudioError)
        self.assertTrue(owners[0].closed)

    def test_guard_drift_before_or_after_read_returns_no_bytes_and_closes(self) -> None:
        for failing_assertion in (2, 3):
            with self.subTest(failing_assertion=failing_assertion):
                manager, catalog, owners = self._manager([b"payload"])
                handle = manager.open(
                    "workspace_01",
                    "a" * 64,
                    "asset_" + "b" * 64,
                )["handle"]
                catalog.fail_assertion = failing_assertion
                with self.assertRaisesRegex(StudioError, "authority drift"):
                    manager.read(handle, 0)
                self.assertTrue(owners[0].closed)
                if failing_assertion == 2:
                    self.assertEqual(0, owners[0].reader.read_calls)
                else:
                    self.assertEqual(1, owners[0].reader.read_calls)

    def test_open_revalidation_failure_releases_only_fully_closed_reservation(self) -> None:
        manager, catalog, owners = self._manager(
            [b"payload"],
            tokens=["A" * 43, "B" * 43],
        )
        catalog.fail_assertion = 1
        with self.assertRaisesRegex(StudioError, "authority drift"):
            manager.open("workspace_01", "a" * 64, "asset_" + "b" * 64)
        self.assertTrue(owners[0].reader.closed)
        self.assertTrue(owners[0].closed)

        catalog.fail_assertion = None
        opened = manager.open("workspace_01", "a" * 64, "asset_" + "b" * 64)
        self.assertEqual("B" * 43, opened["handle"])
        manager.close(opened["handle"])

    def test_exact_multiple_and_hard_lifetime_ignore_idle_refresh(self) -> None:
        clock = _Clock()
        policy = preview_module._AssetPreviewPolicy(
            max_artifact_bytes=2 * 64 * 1024,
            max_workspace_handles=4,
            max_workspace_bytes=2 * 64 * 1024,
            max_global_handles=16,
            max_global_bytes=2 * 64 * 1024,
            idle_seconds=10,
            lifetime_seconds=3,
            reaper_seconds=1,
        )
        manager, _, owners = self._manager(
            [b"a" * (64 * 1024), b"b" * (64 * 1024)],
            policy=policy,
            clock=clock,
        )
        opened = manager.open("workspace_01", "a" * 64, "asset_" + "b" * 64)
        first = manager.read(opened["handle"], 0)
        self.assertEqual(64 * 1024, len(first["payload"]))
        self.assertFalse(first["eof"])
        clock.advance(2)
        final = manager.read(opened["handle"], 1)
        self.assertEqual(2 * 64 * 1024, final["cumulative_bytes"])
        self.assertTrue(final["eof"])
        clock.advance(1)
        manager._reap_once()
        self.assertTrue(owners[0].closed)
        with self.assertRaisesRegex(StudioError, "unavailable"):
            manager.read(opened["handle"], 1)

    def test_new_reads_require_the_exact_fixed_chunk_length(self) -> None:
        manager, _, owners = self._manager([b"a" * 100])
        handle = manager.open(
            "workspace_01",
            "a" * 64,
            "asset_" + "b" * 64,
        )["handle"]
        short_payload = b"a" * 10
        owners[0].reader.chunks = deque(
            [
                ResourceSnapshotChunk(
                    sequence=0,
                    payload=short_payload,
                    cumulative_bytes=len(short_payload),
                    cumulative_sha256=hashlib.sha256(short_payload).hexdigest(),
                    eof=False,
                )
            ]
        )

        with self.assertRaisesRegex(StudioError, "read failed"):
            manager.read(handle, 0)
        self.assertTrue(owners[0].reader.closed)
        self.assertTrue(owners[0].closed)

    def test_new_reads_reject_boolean_chunk_evidence(self) -> None:
        for field_name, replacement in (("sequence", False), ("eof", 1)):
            with self.subTest(field=field_name):
                manager, _, owners = self._manager([b"payload"])
                handle = manager.open(
                    "workspace_01",
                    "a" * 64,
                    "asset_" + "b" * 64,
                )["handle"]
                chunk = owners[0].reader.chunks[0]
                values = {
                    "sequence": chunk.sequence,
                    "payload": chunk.payload,
                    "cumulative_bytes": chunk.cumulative_bytes,
                    "cumulative_sha256": chunk.cumulative_sha256,
                    "eof": chunk.eof,
                }
                values[field_name] = replacement
                owners[0].reader.chunks = deque([ResourceSnapshotChunk(**values)])

                with self.assertRaisesRegex(StudioError, "read failed"):
                    manager.read(handle, 0)
                self.assertTrue(owners[0].reader.closed)
                self.assertTrue(owners[0].closed)

    def test_reader_then_owner_cleanup_failures_quarantine_and_hold_quota(self) -> None:
        policy = preview_module._AssetPreviewPolicy(
            max_artifact_bytes=16,
            max_workspace_handles=1,
            max_workspace_bytes=16,
            max_global_handles=1,
            max_global_bytes=16,
            idle_seconds=60,
            lifetime_seconds=300,
            reaper_seconds=5,
        )
        manager, _, owners = self._manager(
            [b"payload"],
            reader_close_failures=1,
            owner_close_failures=1,
            policy=policy,
            tokens=["A" * 43, "B" * 43],
        )
        handle = manager.open("workspace_01", "a" * 64, "asset_" + "b" * 64)["handle"]
        manager.close(handle)
        self.assertEqual(0, owners[0].close_calls)
        with self.assertRaisesRegex(StudioError, "quota"):
            manager.open("workspace_01", "a" * 64, "asset_" + "b" * 64)

        manager._reap_once()
        self.assertEqual(1, owners[0].close_calls)
        with self.assertRaisesRegex(StudioError, "quota"):
            manager.open("workspace_01", "a" * 64, "asset_" + "b" * 64)
        manager._reap_once()
        self.assertTrue(owners[0].closed)
        replacement = manager.open(
            "workspace_01",
            "a" * 64,
            "asset_" + "b" * 64,
        )
        self.assertEqual("B" * 43, replacement["handle"])
        manager.close(replacement["handle"])
        manager._reap_once()
        manager.shutdown()

    def test_random_collision_quotas_expiry_and_shutdown_are_bounded(self) -> None:
        clock = _Clock()
        policy = preview_module._AssetPreviewPolicy(
            max_artifact_bytes=8,
            max_workspace_handles=2,
            max_workspace_bytes=8,
            max_global_handles=2,
            max_global_bytes=8,
            idle_seconds=2,
            lifetime_seconds=5,
            reaper_seconds=1,
        )
        manager, _, owners = self._manager(
            [b"four"],
            policy=policy,
            clock=clock,
            tokens=["A" * 43, "A" * 43, "B" * 43, "C" * 43],
        )
        first = manager.open("workspace_01", "a" * 64, "asset_" + "b" * 64)
        second = manager.open("workspace_02", "a" * 64, "asset_" + "b" * 64)
        self.assertEqual("A" * 43, first["handle"])
        self.assertEqual("B" * 43, second["handle"])
        with self.assertRaisesRegex(StudioError, "global preview quota"):
            manager.open("workspace_03", "a" * 64, "asset_" + "b" * 64)

        clock.advance(2)
        manager._reap_once()
        self.assertTrue(all(owner.closed for owner in owners[:2]))
        with self.assertRaisesRegex(StudioError, "unavailable"):
            manager.read(first["handle"], 0)
        third = manager.open("workspace_03", "a" * 64, "asset_" + "b" * 64)
        manager.shutdown()
        with self.assertRaisesRegex(StudioError, "shut down"):
            manager.open("workspace_04", "a" * 64, "asset_" + "b" * 64)
        with self.assertRaisesRegex(StudioError, "shut down"):
            manager.read(third["handle"], 0)

    def test_shutdown_failure_is_generic_and_retryable(self) -> None:
        manager, _, owners = self._manager(
            [b"payload"],
            owner_close_failures=1,
        )
        opened = manager.open("workspace_01", "a" * 64, "asset_" + "b" * 64)
        with self.assertRaises(StudioError) as raised:
            manager.shutdown()
        self.assertEqual("internal_error", raised.exception.code)
        self.assertEqual("Asset preview shutdown cleanup failed", raised.exception.message)
        self.assertNotIn(opened["handle"], str(raised.exception))
        self.assertFalse(owners[0].closed)

        manager.shutdown()
        self.assertTrue(owners[0].closed)

    def test_shutdown_waits_for_a_concurrent_close_cleanup(self) -> None:
        manager, _, owners = self._manager([b"payload"])
        opened = manager.open(
            "workspace_01",
            "a" * 64,
            "asset_" + "b" * 64,
        )
        owner = owners[0]
        original_close = owner.close
        close_entered = threading.Event()
        release_close = threading.Event()
        shutdown_started = threading.Event()
        shutdown_done = threading.Event()
        close_failures: list[BaseException] = []
        shutdown_failures: list[BaseException] = []

        def blocking_owner_close() -> None:
            close_entered.set()
            if not release_close.wait(timeout=5):
                raise AssertionError("concurrent owner close was not released")
            original_close()

        def close() -> None:
            try:
                manager.close(opened["handle"])
            except BaseException as exc:
                close_failures.append(exc)

        def shutdown() -> None:
            shutdown_started.set()
            try:
                manager.shutdown()
            except BaseException as exc:
                shutdown_failures.append(exc)
            finally:
                shutdown_done.set()

        owner.close = blocking_owner_close  # type: ignore[method-assign]
        close_thread = threading.Thread(target=close)
        shutdown_thread = threading.Thread(target=shutdown)
        close_thread.start()
        self.assertTrue(close_entered.wait(timeout=5))
        shutdown_thread.start()
        self.assertTrue(shutdown_started.wait(timeout=5))
        try:
            self.assertFalse(shutdown_done.wait(timeout=0.25))
        finally:
            release_close.set()
            close_thread.join(timeout=5)
            shutdown_thread.join(timeout=5)

        self.assertFalse(close_thread.is_alive())
        self.assertFalse(shutdown_thread.is_alive())
        self.assertEqual([], close_failures)
        self.assertEqual([], shutdown_failures)
        self.assertTrue(owner.closed)
        manager.shutdown()

    def test_shutdown_bounds_a_stuck_concurrent_cleanup_and_can_retry(self) -> None:
        policy = preview_module._AssetPreviewPolicy(shutdown_wait_seconds=0.01)
        manager, _, owners = self._manager([b"payload"], policy=policy)
        opened = manager.open(
            "workspace_01",
            "a" * 64,
            "asset_" + "b" * 64,
        )
        owner = owners[0]
        original_close = owner.close
        close_entered = threading.Event()
        release_close = threading.Event()
        close_failures: list[BaseException] = []

        def blocking_owner_close() -> None:
            close_entered.set()
            if not release_close.wait(timeout=5):
                raise AssertionError("concurrent owner close was not released")
            original_close()

        def close() -> None:
            try:
                manager.close(opened["handle"])
            except BaseException as exc:
                close_failures.append(exc)

        owner.close = blocking_owner_close  # type: ignore[method-assign]
        close_thread = threading.Thread(target=close)
        close_thread.start()
        self.assertTrue(close_entered.wait(timeout=5))
        started = time.monotonic()
        try:
            with self.assertRaises(StudioError) as raised:
                manager.shutdown()
        finally:
            release_close.set()
            close_thread.join(timeout=5)

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual("internal_error", raised.exception.code)
        self.assertEqual("Asset preview shutdown cleanup failed", raised.exception.message)
        self.assertFalse(close_thread.is_alive())
        self.assertEqual([], close_failures)
        self.assertTrue(owner.closed)
        manager.shutdown()

    def test_all_non_png_wav_media_are_rejected_before_owner_allocation(self) -> None:
        for media_type in (
            "application/json",
            "font/otf",
            "font/ttf",
            "image/jpeg",
            "image/webp",
            "model/gltf-binary",
            "text/x-glsl",
        ):
            with self.subTest(media_type=media_type):
                authority = _fake_authority([b"payload"])
                authority.media_type = media_type
                catalog = _FakeCatalog(authority)
                allocations = mock.Mock(side_effect=AssertionError("owner allocated"))
                manager = preview_module.AssetPreviewManager(
                    catalog,
                    _owner_factory=allocations,
                    _start_reaper=False,
                )
                with self.assertRaisesRegex(StudioError, "not previewable"):
                    manager.open(
                        "workspace_01",
                        "a" * 64,
                        "asset_" + "b" * 64,
                    )
                allocations.assert_not_called()
                manager.shutdown()


class AssetPreviewManagerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.world = self.root / "world"
        self.asset_root = self.world / "assets/renderpack"
        self.snapshot_parent = self.root / "snapshots"
        self.snapshot_parent.mkdir()
        self._install_fixture()
        self.store = StudioStore(self.root / "studio-data")
        self.addCleanup(self.store.close)
        self.service = StudioService(self.store)
        self.addCleanup(self.service.close)
        self.service.workspaces.register(
            {
                "workspace_id": "workspace_01",
                "forge_root": str(FORGE_ROOT),
                "world_root": str(self.world),
            }
        )
        self.catalog = self.service.assets

    def _install_fixture(self) -> None:
        create_world_project(
            self.world,
            world_id="foundation_slice",
            title="Studio Assets",
            language="en",
        )
        shutil.copytree(FORGE_ROOT / "examples/m5-neutral/renderpack", self.asset_root)
        worldpack_source = FORGE_ROOT / "content/compiled/foundation.worldpack.json"
        worldpack = self.world / "content/compiled/foundation.worldpack.json"
        worldpack.parent.mkdir(parents=True)
        shutil.copyfile(worldpack_source, worldpack)
        prefix = self.asset_root.relative_to(self.world).as_posix()
        status_path = self.world / ".worldforge/status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status.update(
            {
                "completed_phases": [phase.id for phase in PHASES[:13]],
                "current_phase": PHASES[13].id,
                "revision": 13,
                "canon_locked": True,
                "worldpack_hash": json.loads(worldpack.read_text(encoding="utf-8"))["content_hash"],
                "worldpack_path": worldpack.relative_to(self.world).as_posix(),
                "asset_target": f"{prefix}/target.json",
                "visual_bible": f"{prefix}/bibles/visual.json",
                "audio_bible": f"{prefix}/bibles/audio.json",
                "asset_inventory": f"{prefix}/inventory/assets.json",
                "asset_manifest": f"{prefix}/manifest.json",
            }
        )
        status_path.write_bytes(canonical_json_bytes(status))

    def _entries(self) -> tuple[str, list[dict[str, object]]]:
        page = self.catalog.list("workspace_01")
        return str(page["manifest_revision"]), list(page["entries"])

    def test_real_png_and_wav_snapshots_stream_without_paths_or_leftovers(self) -> None:
        revision, entries = self._entries()
        owner_allocations = 0

        def owner_factory() -> snapshot_module.ResourceSnapshotOwner:
            nonlocal owner_allocations
            owner_allocations += 1
            return snapshot_module.ResourceSnapshotOwner()

        with mock.patch.object(
            snapshot_module.tempfile,
            "gettempdir",
            return_value=str(self.snapshot_parent),
        ):
            manager = preview_module.AssetPreviewManager(
                self.catalog,
                _owner_factory=owner_factory,
                _start_reaper=False,
            )
            for media_type in ("image/png", "audio/wav"):
                entry = next(
                    item
                    for item in entries
                    if item["category"] == "runtime_output" and item["media_type"] == media_type
                )
                opened = manager.open("workspace_01", revision, entry["entry_id"])
                self.assertEqual(media_type, opened["media_type"])
                self.assertNotIn(str(self.world), repr(opened))
                sequence = 0
                payload = bytearray()
                while True:
                    chunk = manager.read(opened["handle"], sequence)
                    payload.extend(chunk["payload"])
                    if chunk["eof"]:
                        self.assertEqual(chunk, manager.read(opened["handle"], sequence))
                        break
                    sequence += 1
                self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest())
                manager.close(opened["handle"])
            manager.shutdown()

        self.assertEqual(2, owner_allocations)
        self.assertEqual([], list(self.snapshot_parent.iterdir()))

    def test_nonpreview_media_is_rejected_before_snapshot_allocation(self) -> None:
        revision, entries = self._entries()
        allocation = mock.Mock(side_effect=AssertionError("snapshot allocated"))
        manager = preview_module.AssetPreviewManager(
            self.catalog,
            _owner_factory=allocation,
            _start_reaper=False,
        )
        rejected = [
            next(item for item in entries if item["media_type"] == "application/json"),
            next(
                item
                for item in entries
                if item["category"] == "runtime_output"
                and item["media_type"] in {"font/otf", "font/ttf"}
            ),
        ]
        for entry in rejected:
            with self.assertRaisesRegex(StudioError, "not previewable"):
                manager.open("workspace_01", revision, entry["entry_id"])
        allocation.assert_not_called()
        manager.shutdown()

    def test_service_protocol_round_trips_png_wav_and_rejects_font(self) -> None:
        revision, entries = self._entries()
        request_index = 0

        def request(method: str, params: dict[str, object]) -> dict[str, object]:
            nonlocal request_index
            request_index += 1
            return self.service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 1,
                    "kind": "request",
                    "request_id": f"preview-{request_index}",
                    "method": method,
                    "params": params,
                }
            )

        with mock.patch.object(
            snapshot_module.tempfile,
            "gettempdir",
            return_value=str(self.snapshot_parent),
        ):
            for media_type in ("image/png", "audio/wav"):
                entry = next(
                    item
                    for item in entries
                    if item["category"] == "runtime_output" and item["media_type"] == media_type
                )
                opened = request(
                    "asset.preview.open",
                    {
                        "workspace_id": "workspace_01",
                        "manifest_revision": revision,
                        "entry_id": entry["entry_id"],
                    },
                )
                self.assertEqual(
                    {
                        "handle",
                        "manifest_revision",
                        "entry_id",
                        "media_type",
                        "byte_length",
                        "sha256",
                        "chunk_bytes",
                    },
                    set(opened["result"]),
                )
                self.assertEqual(65_536, opened["result"]["chunk_bytes"])
                self.assertNotIn(str(self.world), repr(opened))
                handle = opened["result"]["handle"]
                payload = bytearray()
                sequence = 0
                while True:
                    read = request(
                        "asset.preview.read",
                        {"handle": handle, "sequence": sequence},
                    )
                    self.assertNotIn("payload", read["result"])
                    self.assertNotIn("path", read["result"])
                    encoded = read["result"]["data_base64"]
                    chunk = base64.b64decode(encoded, validate=True)
                    self.assertEqual(encoded, base64.b64encode(chunk).decode("ascii"))
                    self.assertEqual(len(chunk), read["result"]["byte_length"])
                    payload.extend(chunk)
                    if read["result"]["eof"]:
                        break
                    self.assertEqual(65_536, len(chunk))
                    sequence += 1
                self.assertEqual(entry["sha256"], hashlib.sha256(payload).hexdigest())
                closed = request("asset.preview.close", {"handle": handle})
                self.assertEqual({"handle": handle, "closed": True}, closed["result"])

            for media_type in ("font/ttf",):
                entry = next(
                    item
                    for item in entries
                    if item["category"] == "runtime_output" and item["media_type"] == media_type
                )
                with (
                    self.subTest(media_type=media_type),
                    self.assertRaisesRegex(
                        StudioError,
                        "not previewable",
                    ),
                ):
                    request(
                        "asset.preview.open",
                        {
                            "workspace_id": "workspace_01",
                            "manifest_revision": revision,
                            "entry_id": entry["entry_id"],
                        },
                    )

        self.assertEqual([], list(self.snapshot_parent.iterdir()))
