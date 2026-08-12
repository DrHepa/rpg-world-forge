from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import struct
import tempfile
import threading
import unittest
import wave
import zlib
from collections import deque
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

from isoworld.content.resource_snapshot import ResourceSnapshotChunk
from worldforge.studio.contracts import PROTOCOL_FORMAT
from worldforge.studio.errors import StudioError, conflict

_EXPECTED_STUDIO_METHODS_BY_VERSION = {
    1: frozenset(
        {
            "service.initialize",
            "workspace.register",
            "workspace.list",
            "workspace.get",
            "workspace.overview",
            "source.list",
            "source.read",
            "asset.catalog.list",
            "asset.catalog.inspect",
            "asset.preview.open",
            "asset.preview.read",
            "asset.preview.close",
            "world.validate",
            "world.analyze",
            "events.list",
            "changeset.create",
            "changeset.get",
            "changeset.list",
            "changeset.diff",
            "changeset.approve",
            "changeset.reject",
            "changeset.apply",
            "job.create",
            "job.get",
            "job.list",
            "job.transition",
            "job.cancel",
        }
    ),
    2: frozenset(
        {
            "service.initialize",
            "external_grant.create",
            "external_grant.get",
            "external_grant.revoke",
            "job.create",
            "job.get",
            "job.list",
            "job.cancel",
            "job.recover",
        }
    ),
    3: frozenset(
        {
            "service.initialize",
            "creation_root_grant.create",
            "creation_root_grant.get",
            "creation_root_grant.revoke",
            "creation_workspace.create",
            "creation_workspace.recover",
            "creation_workspace.register",
            "creation_workspace.get",
            "creation_workspace.list",
            "creation_workspace.open",
            "creation_document.list",
            "creation_document.read",
            "creation_changeset.create",
            "creation_changeset.get",
            "creation_changeset.list",
            "creation_changeset.diff",
            "creation_changeset.approve",
            "creation_changeset.reject",
            "creation_changeset.apply",
            "creation_changeset.recover",
            "creation_workflow.get",
            "creation_workflow.reconcile",
            "creation_phase.read",
            "creation_phase.validate",
            "creation_phase.complete",
            "creation_phase.reopen",
            "creation_readiness.inspect",
        }
    ),
    4: frozenset(
        {
            "service.initialize",
            "creation_artifact.inspect",
            "creation_artifact.list",
            "creation_event.list",
            "creation_evidence.inspect",
            "creation_job.cancel",
            "creation_job.create",
            "creation_job.get",
            "creation_job.list",
            "creation_job.recover",
            "creation_output_grant.create",
            "creation_output_grant.get",
            "creation_output_grant.list",
            "creation_output_grant.revoke",
            "creation_preview.close",
            "creation_preview.open",
            "creation_preview.read",
        }
    ),
}


class _Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _FakeResolver:
    def __init__(self, authority: object) -> None:
        self.authority = authority
        self.assertions = 0
        self.fail_assertion: int | None = None

    def resolve(self, params: object) -> object:
        if not isinstance(params, dict):
            raise AssertionError(params)
        return self.authority

    def assert_current(self, authority: object) -> None:
        if authority is not self.authority:
            raise AssertionError("authority identity changed")
        self.assertions += 1
        if self.assertions == self.fail_assertion:
            raise conflict("injected creation preview authority drift")


class _FakeReader:
    def __init__(self, payloads: list[bytes]) -> None:
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
        self.close_calls = 0
        self.read_calls = 0

    def read_next(self) -> ResourceSnapshotChunk:
        self.read_calls += 1
        return self.chunks.popleft()

    def close(self) -> None:
        self.close_calls += 1
        if self.closed:
            raise AssertionError("reader closed more than once")
        self.closed = True


class _FakeOwner:
    def __init__(self, reader: _FakeReader) -> None:
        self.reader = reader
        self.closed = False
        self.close_calls = 0
        self.materialize_calls = 0

    def materialize(
        self,
        root: Path,
        relative: PurePosixPath,
        media_type: str,
        *,
        limit: int,
    ) -> object:
        self.materialize_calls += 1
        if root.name != "assetpack" or relative.as_posix() != "assets/ui/board.png":
            raise AssertionError((root, relative))
        if media_type not in {"image/png", "audio/wav"} or limit != self.reader.size:
            raise AssertionError((media_type, limit))
        return SimpleNamespace(sha256=self.reader.sha256)

    def open_reader(self, relative: PurePosixPath) -> _FakeReader:
        if relative.as_posix() != "assets/ui/board.png":
            raise AssertionError(relative)
        return self.reader

    def close(self) -> None:
        self.close_calls += 1
        if self.closed:
            raise AssertionError("owner closed more than once")
        self.closed = True


class CreationPreviewContractTests(unittest.TestCase):
    def test_v4_contract_is_new_closed_pathless_and_does_not_broaden_v2_preview(self) -> None:
        from worldforge.studio.contracts import (
            METHODS_V2,
            METHODS_V4,
            validate_studio_creation_preview,
            validate_studio_protocol_envelope,
        )

        record = {
            "format": "world-forge.studio_creation_preview",
            "format_version": 1,
            "handle": "A" * 43,
            "workspace_id": "workspace_puzzle",
            "assetpack_artifact_id": "artifact_assetpack",
            "output_grant_id": "grant_assetpack",
            "output_grant_generation": 2,
            "asset_id": "board_ui",
            "media_type": "image/png",
            "byte_length": 70,
            "sha256": "a" * 64,
            "chunk_bytes": 65536,
            "metadata": {"kind": "png", "width": 4, "height": 4, "mode": "rgba8"},
        }
        self.assertEqual(record, validate_studio_creation_preview(record))
        self.assertTrue(
            {"creation_preview.open", "creation_preview.read", "creation_preview.close"}
            <= METHODS_V4
        )
        self.assertTrue(
            {"creation_preview.open", "creation_preview.read", "creation_preview.close"}.isdisjoint(
                METHODS_V2
            )
        )
        leaked = {**record, "path": "/private/assetpack/assets/ui/board.png"}
        with self.assertRaisesRegex(ValueError, "fields"):
            validate_studio_creation_preview(leaked)

        request = {
            "protocol": PROTOCOL_FORMAT,
            "protocol_version": 4,
            "kind": "request",
            "request_id": "preview-open",
            "method": "creation_preview.open",
            "params": {
                "workspace_id": "workspace_puzzle",
                "expected_root_generation": 0,
                "expected_source_revision": "b" * 64,
                "expected_workflow_status_hash": None,
                "expected_artifact_snapshot_hash": "c" * 64,
                "assetpack_artifact_id": "artifact_assetpack",
                "output_grant_id": "grant_assetpack",
                "expected_output_grant_generation": 2,
                "asset_id": "board_ui",
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        for forbidden in ("path", "runtime_path", "chunk_bytes", "offset", "format"):
            invalid = json.loads(json.dumps(request))
            invalid["params"][forbidden] = "/private/value"
            with self.subTest(forbidden=forbidden), self.assertRaisesRegex(ValueError, "fields"):
                validate_studio_protocol_envelope(invalid)

    def test_published_schema_and_catalog_register_preview_contract(self) -> None:
        from worldforge.studio.contracts import METHODS, METHODS_V2, METHODS_V3, METHODS_V4

        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "schemas/studio-creation-preview.schema.json").read_text(encoding="utf-8")
        )
        catalog = json.loads((root / "contracts/catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(
            "world-forge.studio_creation_preview",
            schema["properties"]["format"]["const"],
        )
        python_methods = {
            1: METHODS,
            2: METHODS_V2,
            3: METHODS_V3,
            4: METHODS_V4,
        }
        for version, expected in _EXPECTED_STUDIO_METHODS_BY_VERSION.items():
            suffix = "" if version == 1 else f"-v{version}"
            protocol = json.loads(
                (root / f"schemas/studio-protocol{suffix}.schema.json").read_text(encoding="utf-8")
            )
            published = protocol["$defs"]["method"]["enum"]
            with self.subTest(protocol_version=version):
                self.assertEqual(len(expected), len(published))
                self.assertEqual(expected, set(published))
                self.assertEqual(expected, set(python_methods[version]))
        self.assertEqual(17, len(_EXPECTED_STUDIO_METHODS_BY_VERSION[4]))
        self.assertIn(
            "creation_output_grant.list",
            _EXPECTED_STUDIO_METHODS_BY_VERSION[4],
        )
        entries = [
            item
            for item in catalog["contracts"]
            if item["format"] == "world-forge.studio_creation_preview" and item["version"] == 1
        ]
        self.assertEqual(1, len(entries))
        self.assertIn("tests/test_studio_creation_previews_v4.py", entries[0]["tests"])


class CreationPreviewAuthorityResolverTests(unittest.TestCase):
    def test_resolver_binds_candidate_grant_verified_tree_and_detects_drift(self) -> None:
        from worldforge.studio.creation_previews import CreationPreviewAuthorityResolver

        manifest = {
            "format": "world-forge.assetpack",
            "format_version": 1,
            "assetpack_id": "assetpack_puzzle",
            "content_hash": "d" * 64,
            "inventory": {"content_hash": "e" * 64},
            "assets": [
                {
                    "asset": {"asset_id": "board_ui", "content_hash": "f" * 64},
                    "outputs": [
                        {
                            "role": "texture",
                            "media_type": "image/png",
                            "runtime_path": "assets/ui/board.png",
                            "sha256": "1" * 64,
                            "size_bytes": 7,
                            "metadata": {
                                "kind": "png",
                                "width": 1,
                                "height": 1,
                                "mode": "rgba8",
                            },
                        }
                    ],
                }
            ],
        }
        inspection = {
            "authority": {
                "workspace_id": "workspace_puzzle",
                "root_generation": 0,
                "source_revision": "a" * 64,
                "workflow_status_hash": None,
            },
            "artifact_snapshot_hash": "b" * 64,
            "artifact": {
                "lifecycle": "candidate",
                "subject": {
                    "format": "world-forge.assetpack",
                    "format_version": 1,
                    "id": "assetpack_puzzle",
                    "content_hash": "d" * 64,
                },
            },
        }
        grant = {
            "workspace_id": "workspace_puzzle",
            "kind": "generic_assetpack_directory",
            "state": "published",
            "generation": 2,
            "publication": {
                "format": "world-forge.assetpack",
                "format_version": 1,
                "id": "assetpack_puzzle",
                "content_hash": "d" * 64,
                "inventory_hash": "e" * 64,
            },
        }
        binding = {
            "path": "/private/assetpack",
            "parent_identity": (1, 2),
            "published_identity": (3, 4),
            "expected_manifest_hash": "2" * 64,
            "expected_tree_hash": "d" * 64,
        }

        class Evidence:
            def inspect(self, _params: object) -> object:
                return inspection

        class Artifacts:
            def get_document(self, _workspace_id: str, _artifact_id: str) -> object:
                return manifest

        class Grants:
            def get(self, _grant_id: str) -> object:
                return grant

            def published_binding(self, **_kwargs: object) -> object:
                return binding

        class Verified:
            def __enter__(self) -> object:
                return SimpleNamespace(manifest=manifest, root_identity=(3, 4))

            def __exit__(self, *_args: object) -> None:
                return None

        params = CreationPreviewManagerTests._params()
        resolver = CreationPreviewAuthorityResolver(Evidence(), Artifacts(), Grants())
        tree_guard = (("", "directory", (1, 2, 3)),)
        with (
            patch(
                "worldforge.studio.creation_previews._capture_tree_guard",
                return_value=tree_guard,
            ),
            patch(
                "worldforge.studio.creation_previews.verify_generic_assetpack",
                return_value=Verified(),
            ),
        ):
            authority = resolver.resolve(params)
            self.assertEqual("board_ui", authority.asset_id)
            self.assertEqual(PurePosixPath("assets/ui/board.png"), authority.runtime_path)
            resolver.assert_current(authority)
            grant["generation"] = 3
            with self.assertRaisesRegex(StudioError, "changed"):
                resolver.assert_current(authority)


class CreationPreviewManagerTests(unittest.TestCase):
    def _manager(
        self,
        payloads: list[bytes],
        *,
        media_type: str = "image/png",
        clock: _Clock | None = None,
        policy: object | None = None,
        registration_hook: object | None = None,
    ) -> tuple[object, _FakeResolver, _FakeReader, list[_FakeOwner]]:
        from worldforge.studio.creation_previews import (
            CreationPreviewManager,
            ResolvedCreationPreviewAuthority,
        )

        reader = _FakeReader(payloads)
        authority = ResolvedCreationPreviewAuthority(
            workspace_id="workspace_puzzle",
            authority={
                "workspace_id": "workspace_puzzle",
                "root_generation": 0,
                "source_revision": "a" * 64,
                "workflow_status_hash": None,
            },
            artifact_snapshot_hash="b" * 64,
            assetpack_artifact_id="artifact_assetpack",
            output_grant_id="grant_assetpack",
            output_grant_generation=2,
            asset_id="board_ui",
            assetpack_root=Path("/private/assetpack"),
            runtime_path=PurePosixPath("assets/ui/board.png"),
            role="texture" if media_type == "image/png" else "audio",
            media_type=media_type,
            byte_length=reader.size,
            sha256=reader.sha256,
            metadata=(
                {"kind": "png", "width": 4, "height": 4, "mode": "rgba8"}
                if media_type == "image/png"
                else {
                    "kind": "wav_pcm16",
                    "channels": 1,
                    "sample_rate": 8000,
                    "frames": 1,
                    "sample_width": 2,
                }
            ),
            private_guard={"root_identity": (1, 2), "tree": "guard"},
        )
        resolver = _FakeResolver(authority)
        owners: list[_FakeOwner] = []

        def owner_factory() -> _FakeOwner:
            owner = _FakeOwner(reader)
            owners.append(owner)
            return owner

        manager = CreationPreviewManager(
            resolver,
            _clock=clock or _Clock(),
            _policy=policy,
            _owner_factory=owner_factory,
            _token_factory=lambda: "A" * 43,
            _registration_hook=registration_hook,
            _start_reaper=False,
        )
        return manager, resolver, reader, owners

    @staticmethod
    def _params() -> dict[str, object]:
        return {
            "workspace_id": "workspace_puzzle",
            "expected_root_generation": 0,
            "expected_source_revision": "a" * 64,
            "expected_workflow_status_hash": None,
            "expected_artifact_snapshot_hash": "b" * 64,
            "assetpack_artifact_id": "artifact_assetpack",
            "output_grant_id": "grant_assetpack",
            "expected_output_grant_generation": 2,
            "asset_id": "board_ui",
        }

    def test_png_stream_is_fixed_sequential_replayable_once_and_close_is_explicit(self) -> None:
        first = b"a" * (64 * 1024)
        final = b"tail"
        manager, resolver, reader, owners = self._manager([first, final])
        opened = manager.open(self._params())
        self.assertEqual("world-forge.studio_creation_preview", opened["format"])
        self.assertEqual(64 * 1024 + 4, opened["byte_length"])
        self.assertNotIn("path", json.dumps(opened).lower())

        zero = manager.read(opened["handle"], 0)
        self.assertEqual(first, zero["payload"])
        self.assertFalse(zero["eof"])
        replay = manager.read(opened["handle"], 0)
        self.assertEqual(zero, replay)
        self.assertEqual(1, reader.read_calls)
        one = manager.read(opened["handle"], 1)
        self.assertEqual(final, one["payload"])
        self.assertTrue(one["eof"])
        with self.assertRaisesRegex(StudioError, "sequence"):
            manager.read(opened["handle"], 2)
        self.assertEqual(2, reader.read_calls)
        with self.assertRaisesRegex(StudioError, "sequence"):
            manager.read(opened["handle"], 0)
        self.assertTrue(manager.close(opened["handle"]))
        self.assertTrue(manager.close(opened["handle"]))
        with self.assertRaisesRegex(StudioError, "unavailable"):
            manager.read(opened["handle"], 1)
        self.assertEqual(1, reader.close_calls)
        self.assertEqual(1, owners[0].close_calls)
        self.assertGreaterEqual(resolver.assertions, 8)
        manager.shutdown()

    def test_real_snapshot_owner_validates_and_streams_the_same_png_bytes(self) -> None:
        def png_chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        payload = (
            b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + png_chunk(b"IDAT", zlib.compress(b"\0\x20\x40\x60\xff", level=9))
            + png_chunk(b"IEND", b"")
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "assetpack"
            target = root / "assets" / "ui" / "board.png"
            target.parent.mkdir(parents=True)
            target.write_bytes(payload)
            _fake, resolver, _reader, _owners = self._manager([payload])
            _fake.shutdown()
            resolver.authority = replace(
                resolver.authority,
                assetpack_root=root,
                byte_length=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
            from worldforge.studio.creation_previews import CreationPreviewManager

            manager = CreationPreviewManager(
                resolver,
                _token_factory=lambda: "G" * 43,
                _start_reaper=False,
            )
            opened = manager.open(self._params())
            chunk = manager.read(opened["handle"], 0)
            self.assertEqual(payload, chunk["payload"])
            self.assertTrue(chunk["eof"])
            manager.close(opened["handle"])
            manager.shutdown()

    def test_real_snapshot_owner_validates_pcm16_wav(self) -> None:
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(8000)
            target.writeframes(b"\x00\x00" * 8)
        payload = output.getvalue()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "assetpack"
            target = root / "assets" / "audio" / "cue.wav"
            target.parent.mkdir(parents=True)
            target.write_bytes(payload)
            _fake, resolver, _reader, _owners = self._manager(
                [payload],
                media_type="audio/wav",
            )
            _fake.shutdown()
            resolver.authority = replace(
                resolver.authority,
                assetpack_root=root,
                runtime_path=PurePosixPath("assets/audio/cue.wav"),
                byte_length=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
            from worldforge.studio.creation_previews import CreationPreviewManager

            manager = CreationPreviewManager(
                resolver,
                _token_factory=lambda: "H" * 43,
                _start_reaper=False,
            )
            opened = manager.open(self._params())
            self.assertEqual("audio/wav", opened["media_type"])
            self.assertEqual(payload, manager.read(opened["handle"], 0)["payload"])
            manager.close(opened["handle"])
            manager.shutdown()

    def test_close_waits_for_one_in_flight_read_and_releases_once(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class BlockingReader(_FakeReader):
            def read_next(self) -> ResourceSnapshotChunk:
                started.set()
                if not release.wait(timeout=2):
                    raise AssertionError("read was not released")
                return super().read_next()

        payload = b"concurrent"
        reader = BlockingReader([payload])
        digest = hashlib.sha256(payload).hexdigest()
        from worldforge.studio.creation_previews import (
            CreationPreviewManager,
            ResolvedCreationPreviewAuthority,
        )

        authority = ResolvedCreationPreviewAuthority(
            workspace_id="workspace_puzzle",
            authority={
                "workspace_id": "workspace_puzzle",
                "root_generation": 0,
                "source_revision": "a" * 64,
                "workflow_status_hash": None,
            },
            artifact_snapshot_hash="b" * 64,
            assetpack_artifact_id="artifact_assetpack",
            output_grant_id="grant_assetpack",
            output_grant_generation=2,
            asset_id="board_ui",
            assetpack_root=Path("/private/assetpack"),
            runtime_path=PurePosixPath("assets/ui/board.png"),
            role="texture",
            media_type="image/png",
            byte_length=len(payload),
            sha256=digest,
            metadata={"kind": "png", "width": 1, "height": 1, "mode": "rgba8"},
            private_guard={"root_identity": (1, 2)},
        )
        resolver = _FakeResolver(authority)
        owner = _FakeOwner(reader)
        manager = CreationPreviewManager(
            resolver,
            _owner_factory=lambda: owner,
            _token_factory=lambda: "F" * 43,
            _start_reaper=False,
        )
        opened = manager.open(self._params())
        outcomes: list[object] = []

        def run_read() -> None:
            try:
                outcomes.append(manager.read(opened["handle"], 0))
            except StudioError as exc:
                outcomes.append(exc)

        read_thread = threading.Thread(
            target=run_read,
        )
        close_thread = threading.Thread(
            target=lambda: outcomes.append(manager.close(opened["handle"])),
        )
        read_thread.start()
        self.assertTrue(started.wait(timeout=2))
        close_thread.start()
        release.set()
        read_thread.join(timeout=2)
        close_thread.join(timeout=2)
        self.assertFalse(read_thread.is_alive())
        self.assertFalse(close_thread.is_alive())
        self.assertEqual(1, reader.close_calls)
        self.assertEqual(1, owner.close_calls)
        self.assertTrue(any(item is True for item in outcomes))
        self.assertTrue(
            any(isinstance(item, StudioError) and item.code == "not_found" for item in outcomes)
        )
        manager.shutdown()

    def test_close_waits_for_competing_reaper_cleanup_and_remains_idempotent(self) -> None:
        from worldforge.studio.creation_previews import _CreationPreviewPolicy

        clock = _Clock()
        policy = _CreationPreviewPolicy(
            idle_seconds=1,
            lifetime_seconds=10,
            reaper_seconds=1,
            shutdown_wait_seconds=2,
        )
        manager, _resolver, reader, owners = self._manager(
            [b"payload"],
            clock=clock,
            policy=policy,
        )
        opened = manager.open(self._params())
        owner = owners[0]
        cleanup_started = threading.Event()
        release_cleanup = threading.Event()
        original_close = owner.close

        def blocking_close() -> None:
            cleanup_started.set()
            if not release_cleanup.wait(timeout=2):
                raise AssertionError("competing cleanup was not released")
            original_close()

        owner.close = blocking_close  # type: ignore[method-assign]
        clock.advance(1)
        reaper_thread = threading.Thread(target=manager._reap_once)
        reaper_thread.start()
        self.assertTrue(cleanup_started.wait(timeout=2))

        release_thread = threading.Thread(
            target=lambda: (threading.Event().wait(0.05), release_cleanup.set())
        )
        release_thread.start()
        self.assertTrue(manager.close(opened["handle"]))
        reaper_thread.join(timeout=2)
        release_thread.join(timeout=2)
        self.assertFalse(reaper_thread.is_alive())
        self.assertFalse(release_thread.is_alive())
        self.assertTrue(manager.close(opened["handle"]))
        self.assertEqual(1, reader.close_calls)
        self.assertEqual(1, owner.close_calls)
        manager.shutdown()

    def test_wav_and_exact_64_mib_boundary_are_supported_but_larger_is_rejected(self) -> None:
        from worldforge.studio.creation_previews import _CreationPreviewPolicy

        manager, _resolver, _reader, _owners = self._manager([b"RIFF"], media_type="audio/wav")
        opened = manager.open(self._params())
        self.assertEqual("audio/wav", opened["media_type"])
        self.assertEqual("wav_pcm16", opened["metadata"]["kind"])
        manager.close(opened["handle"])
        manager.shutdown()

        boundary_policy = _CreationPreviewPolicy(max_artifact_bytes=64 * 1024 * 1024)
        manager, resolver, reader, owners = self._manager(
            [b"x"],
            policy=boundary_policy,
        )
        reader.size = 64 * 1024 * 1024
        reader.sha256 = "f" * 64
        resolver.authority = replace(
            resolver.authority,
            byte_length=64 * 1024 * 1024,
            sha256=reader.sha256,
        )
        opened = manager.open(self._params())
        self.assertEqual(64 * 1024 * 1024, opened["byte_length"])
        manager.close(opened["handle"])
        self.assertEqual(1, reader.close_calls)
        self.assertEqual(1, owners[0].close_calls)
        manager.shutdown()

        manager, resolver, _reader, owners = self._manager([b"x"])
        resolver.authority = replace(resolver.authority, byte_length=64 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(StudioError, "quota"):
            manager.open(self._params())
        self.assertEqual([], owners)
        manager.shutdown()

        manager, resolver, _reader, owners = self._manager([b"x"])
        resolver.authority = replace(resolver.authority, metadata=None)
        with self.assertRaisesRegex(StudioError, "metadata"):
            manager.open(self._params())
        self.assertEqual([], owners)
        manager.shutdown()

    def test_quota_expiry_authority_drift_and_registration_failure_close_exactly_once(self) -> None:
        from worldforge.studio.creation_previews import _CreationPreviewPolicy

        clock = _Clock()
        policy = _CreationPreviewPolicy(
            max_workspace_handles=1,
            max_global_handles=1,
            idle_seconds=2,
            lifetime_seconds=4,
            reaper_seconds=1,
        )
        manager, resolver, reader, owners = self._manager([b"payload"], clock=clock, policy=policy)
        opened = manager.open(self._params())
        with self.assertRaisesRegex(StudioError, "quota"):
            manager.open(self._params())
        clock.advance(2)
        manager._reap_once()
        with self.assertRaisesRegex(StudioError, "unavailable"):
            manager.read(opened["handle"], 0)
        self.assertEqual(1, reader.close_calls)
        self.assertEqual(1, owners[0].close_calls)
        manager.shutdown()

        manager, resolver, reader, owners = self._manager([b"payload"])
        resolver.fail_assertion = 3
        opened = manager.open(self._params())
        with self.assertRaisesRegex(StudioError, "drift|failed"):
            manager.read(opened["handle"], 0)
        self.assertEqual(1, reader.close_calls)
        self.assertEqual(1, owners[0].close_calls)
        manager.shutdown()

        def fail_registration(_lease: object) -> None:
            raise RuntimeError("registration failed")

        manager, _resolver, reader, owners = self._manager(
            [b"payload"], registration_hook=fail_registration
        )
        with self.assertRaisesRegex(StudioError, "opening"):
            manager.open(self._params())
        self.assertEqual(1, reader.close_calls)
        self.assertEqual(1, owners[0].close_calls)
        manager.shutdown()


class CreationPreviewFilesystemGuardTests(unittest.TestCase):
    def test_tree_guard_accepts_regular_tree_and_rejects_links_and_aliases(self) -> None:
        from worldforge.studio.creation_previews import _capture_tree_guard

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "assetpack"
            root.mkdir()
            asset = root / "board.png"
            asset.write_bytes(b"png")
            self.assertEqual("board.png", _capture_tree_guard(root)[1][0])

            hardlink = root / "copy.png"
            os.link(asset, hardlink)
            with self.assertRaisesRegex(StudioError, "unsafe"):
                _capture_tree_guard(root)
            hardlink.unlink()

            symlink = root / "linked.png"
            try:
                symlink.symlink_to(asset.name)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaisesRegex(StudioError, "link|reparse"):
                    _capture_tree_guard(root)
                symlink.unlink()

            (root / "BOARD.PNG").write_bytes(b"alias")
            with self.assertRaisesRegex(StudioError, "alias"):
                _capture_tree_guard(root)
            (root / "BOARD.PNG").unlink()

            decomposed = "e\N{COMBINING ACUTE ACCENT}.png"
            (root / decomposed).write_bytes(b"nfd")
            with self.assertRaisesRegex(StudioError, "portable"):
                _capture_tree_guard(root)

    def test_tree_guard_rejects_windows_reparse_attribute(self) -> None:
        import worldforge.studio.creation_previews as previews

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "assetpack"
            root.mkdir()
            target = root / "board.png"
            target.write_bytes(b"png")
            real_stat = previews.path_file_stat

            def reparse_stat(path: Path) -> object:
                info = real_stat(path)
                if Path(path).name != "board.png":
                    return info
                return SimpleNamespace(
                    st_dev=info.st_dev,
                    st_ino=info.st_ino,
                    st_mode=info.st_mode,
                    st_nlink=info.st_nlink,
                    st_size=info.st_size,
                    st_mtime_ns=info.st_mtime_ns,
                    st_ctime_ns=info.st_ctime_ns,
                    st_file_attributes=0x400,
                )

            with (
                patch.object(previews, "path_file_stat", side_effect=reparse_stat),
                self.assertRaisesRegex(StudioError, "reparse"),
            ):
                previews._capture_tree_guard(root)


class CreationPreviewServiceTests(unittest.TestCase):
    def test_published_generic_assetpack_opens_through_complete_authority_chain(self) -> None:
        from tests.test_studio_creation_asset_release_v11 import (
            _release_candidates,
            _review_processed_outputs,
            _snapshot,
        )
        from tests.test_studio_creation_asset_seal_v4 import (
            _prepare_processed_creation_service,
        )

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace, _before, qa_ids = _prepare_processed_creation_service(base)
            try:
                review_ids = _review_processed_outputs(service, workspace, list(qa_ids))
                manifest, assetpack = _release_candidates(
                    service,
                    workspace,
                    review_ids,
                    manifest_id="preview_release_manifest",
                )
                output_parent = base / "outputs"
                output_parent.mkdir()
                grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_preview_assetpack",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "generic_assetpack_directory",
                        "display_name": "preview-assets",
                        "path": str(output_parent / "preview-assets"),
                    }
                )
                current = _snapshot(service, workspace)
                queued = service.creation_jobs.create_asset_release_authorize(
                    {
                        "job_id": "job_authorize_preview_assetpack",
                        "workspace_id": workspace["workspace_id"],
                        "operation": "asset.release.authorize",
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                        "review_receipt_artifact_ids": review_ids,
                        "manifest_id": manifest["manifest_id"],
                        "assetpack_id": assetpack["assetpack_id"],
                        "release_authority_id": "preview_release_authority",
                        "blockers": [],
                        "target_grant_id": grant["grant_id"],
                        "expected_target_grant_generation": grant["generation"],
                    }
                )
                self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                self.assertEqual("succeeded", service.creation_jobs.get(queued["job_id"])["state"])
                published = service.creation_output_grants.get(grant["grant_id"])
                after = service.creation_evidence.list(
                    {
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": None,
                        "lifecycle": None,
                        "cursor": None,
                        "limit": 64,
                    }
                )
                assetpack_artifacts = [
                    artifact
                    for artifact in after["artifacts"]
                    if artifact["lifecycle"] == "candidate"
                    and artifact["subject"]["format"] == "world-forge.assetpack"
                ]
                self.assertEqual(1, len(assetpack_artifacts))
                params = {
                    "workspace_id": workspace["workspace_id"],
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": after["artifact_snapshot_hash"],
                    "assetpack_artifact_id": assetpack_artifacts[0]["artifact_id"],
                    "output_grant_id": grant["grant_id"],
                    "expected_output_grant_generation": published["generation"],
                    "asset_id": "board_ui",
                }
                opened = service.handle(
                    {
                        "protocol": PROTOCOL_FORMAT,
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "open-published-preview",
                        "method": "creation_preview.open",
                        "params": params,
                    }
                )["result"]["preview"]
                self.assertNotIn(str(output_parent), json.dumps(opened))
                read = service.handle(
                    {
                        "protocol": PROTOCOL_FORMAT,
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "read-published-preview",
                        "method": "creation_preview.read",
                        "params": {"handle": opened["handle"], "sequence": 0},
                    }
                )["result"]
                payload = base64.b64decode(read["data_base64"], validate=True)
                self.assertEqual(opened["sha256"], hashlib.sha256(payload).hexdigest())
                self.assertTrue(read["eof"])
                closed = service.handle(
                    {
                        "protocol": PROTOCOL_FORMAT,
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "close-published-preview",
                        "method": "creation_preview.close",
                        "params": {"handle": opened["handle"]},
                    }
                )["result"]
                self.assertEqual({"handle": opened["handle"], "closed": True}, closed)
            finally:
                service.close()
                service.store.close()

    def test_constructor_rolls_back_creation_preview_after_later_failure(self) -> None:
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        events: list[str] = []

        class PreviewManager:
            def __init__(self, _resolver: object) -> None:
                events.append("open")

            def shutdown(self) -> None:
                events.append("shutdown")

        with (
            tempfile.TemporaryDirectory() as temporary,
            StudioStore(Path(temporary) / "studio") as store,
            patch("worldforge.studio.service.CreationPreviewManager", PreviewManager),
            patch(
                "worldforge.studio.service.CreationJobManager",
                side_effect=StudioError("internal_error", "injected later startup failure"),
            ),
            self.assertRaisesRegex(StudioError, "injected later startup failure"),
        ):
            StudioService(store)
        self.assertEqual(["open", "shutdown"], events)

    def test_service_retries_only_failed_creation_preview_shutdown_stage(self) -> None:
        from worldforge.studio.service import StudioService

        class Manager:
            def __init__(self, *, fail_first: bool = False) -> None:
                self.calls = 0
                self.fail_first = fail_first

            def shutdown(self) -> None:
                self.calls += 1
                if self.fail_first and self.calls == 1:
                    raise StudioError("internal_error", "injected creation preview shutdown")

        legacy = Manager()
        creation = Manager(fail_first=True)
        service = object.__new__(StudioService)
        service._closed = False
        service._preview_shutdown = False
        service._creation_preview_shutdown = False
        service.asset_previews = legacy
        service.creation_previews = creation
        with self.assertRaisesRegex(StudioError, "injected"):
            service.close()
        service.close()
        service.close()
        self.assertEqual(1, legacy.calls)
        self.assertEqual(2, creation.calls)

    def test_v4_service_exposes_fixed_preview_methods_and_canonical_base64(self) -> None:
        from worldforge.studio.creation_previews import ResolvedCreationPreviewAuthority
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            store = StudioStore(Path(temporary) / "studio")
            service = StudioService(store)
            try:
                initialized = service.handle(
                    {
                        "protocol": PROTOCOL_FORMAT,
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "initialize-preview",
                        "method": "service.initialize",
                        "params": {},
                    }
                )["result"]
                self.assertTrue(initialized["capabilities"]["creation_asset_previews"])
                self.assertTrue(
                    {"creation_preview.open", "creation_preview.read", "creation_preview.close"}
                    <= set(initialized["methods"])
                )

                payload = b"preview"
                digest = hashlib.sha256(payload).hexdigest()
                reader = _FakeReader([payload])
                owner = _FakeOwner(reader)
                authority = ResolvedCreationPreviewAuthority(
                    workspace_id="workspace_puzzle",
                    authority={
                        "workspace_id": "workspace_puzzle",
                        "root_generation": 0,
                        "source_revision": "a" * 64,
                        "workflow_status_hash": None,
                    },
                    artifact_snapshot_hash="b" * 64,
                    assetpack_artifact_id="artifact_assetpack",
                    output_grant_id="grant_assetpack",
                    output_grant_generation=2,
                    asset_id="board_ui",
                    assetpack_root=Path("/private/assetpack"),
                    runtime_path=PurePosixPath("assets/ui/board.png"),
                    role="texture",
                    media_type="image/png",
                    byte_length=len(payload),
                    sha256=digest,
                    metadata={"kind": "png", "width": 1, "height": 1, "mode": "rgba8"},
                    private_guard={"root_identity": (1, 2)},
                )
                resolver = _FakeResolver(authority)
                from worldforge.studio.creation_previews import CreationPreviewManager

                service.creation_previews.shutdown()
                service.creation_previews = CreationPreviewManager(
                    resolver,
                    _owner_factory=lambda: owner,
                    _token_factory=lambda: "A" * 43,
                    _start_reaper=False,
                )
                params = CreationPreviewManagerTests._params()
                opened = service.handle(
                    {
                        "protocol": PROTOCOL_FORMAT,
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "open-preview",
                        "method": "creation_preview.open",
                        "params": params,
                    }
                )
                self.assertNotIn("/private", json.dumps(opened))
                handle = opened["result"]["preview"]["handle"]
                chunk = service.handle(
                    {
                        "protocol": PROTOCOL_FORMAT,
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "read-preview",
                        "method": "creation_preview.read",
                        "params": {"handle": handle, "sequence": 0},
                    }
                )
                self.assertEqual(
                    base64.b64encode(payload).decode("ascii"), chunk["result"]["data_base64"]
                )
                self.assertEqual(
                    payload, base64.b64decode(chunk["result"]["data_base64"], validate=True)
                )
                closed = service.handle(
                    {
                        "protocol": PROTOCOL_FORMAT,
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "close-preview",
                        "method": "creation_preview.close",
                        "params": {"handle": handle},
                    }
                )
                self.assertEqual({"handle": handle, "closed": True}, closed["result"])
            finally:
                service.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
