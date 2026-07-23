from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import isoworld.content.resource_snapshot as snapshot_module
from isoworld.content.file_stat import WindowsFileStat, descriptor_file_stat
from isoworld.content.resource_snapshot import (
    ResourceSnapshotChunk,
    ResourceSnapshotError,
    ResourceSnapshotOwner,
)

CHUNK_BYTES = 64 * 1024


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        written += os.write(descriptor, payload[written:])


def _seed_snapshot(
    owner: ResourceSnapshotOwner,
    relative: PurePosixPath,
    payload: bytes,
) -> Path:
    target, parent_relative, descriptor, _ = owner._open_target(relative)
    try:
        _write_all(descriptor, payload)
        if os.name == "posix":
            os.fchmod(descriptor, 0o400)
        else:
            os.chmod(target, stat.S_IREAD)
        os.fsync(descriptor)
        sealed = descriptor_file_stat(descriptor)
        record = snapshot_module._file_record(
            sealed,
            hashlib.sha256(payload).hexdigest(),
        )
        snapshot_module._validate_file_privacy(target, sealed)
        current = owner._entry_stat(parent_relative, relative.name)
        if not snapshot_module._stat_matches_record(current, record):
            raise AssertionError("test snapshot changed while it was seeded")
        owner._files[relative] = record
        return target
    finally:
        os.close(descriptor)


def _refresh_record(
    owner: ResourceSnapshotOwner,
    relative: PurePosixPath,
    target: Path,
) -> None:
    target.chmod(0o400 if os.name == "posix" else stat.S_IREAD)
    current = snapshot_module.path_file_stat(target)
    owner._files[relative] = snapshot_module._file_record(
        current,
        hashlib.sha256(target.read_bytes()).hexdigest(),
    )


def _read_all(reader: object) -> bytes:
    chunks = []
    while not chunks or not chunks[-1].eof:
        chunks.append(reader.read_next())
    return b"".join(chunk.payload for chunk in chunks)


class ResourceSnapshotReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.snapshot_parent = Path(self.temporary_directory.name) / "snapshots"
        self.snapshot_parent.mkdir()

    def _owner(self) -> ResourceSnapshotOwner:
        with patch.object(
            snapshot_module.tempfile,
            "gettempdir",
            return_value=str(self.snapshot_parent),
        ):
            return ResourceSnapshotOwner()

    def test_reads_fixed_chunks_with_cumulative_integrity_and_owned_surface(self) -> None:
        payload = bytes(range(251)) * 300
        owner = self._owner()
        relative = PurePosixPath("asset.bin")
        _seed_snapshot(owner, relative, payload)
        reader = owner.open_reader(relative)

        try:
            self.assertEqual(len(payload), reader.size)
            self.assertEqual(hashlib.sha256(payload).hexdigest(), reader.sha256)
            self.assertFalse(reader.closed)
            assert reader._descriptor is not None
            self.assertFalse(os.get_inheritable(reader._descriptor))
            for forbidden in ("path", "offset", "seek", "fileno"):
                self.assertFalse(hasattr(reader, forbidden), forbidden)

            first = reader.read_next()
            self.assertIsInstance(first, ResourceSnapshotChunk)
            self.assertEqual(0, first.sequence)
            self.assertEqual(payload[:CHUNK_BYTES], first.payload)
            self.assertEqual(CHUNK_BYTES, first.cumulative_bytes)
            self.assertEqual(
                hashlib.sha256(payload[:CHUNK_BYTES]).hexdigest(),
                first.cumulative_sha256,
            )
            self.assertFalse(first.eof)

            final = reader.read_next()
            self.assertEqual(1, final.sequence)
            self.assertEqual(payload[CHUNK_BYTES:], final.payload)
            self.assertEqual(len(payload), final.cumulative_bytes)
            self.assertEqual(hashlib.sha256(payload).hexdigest(), final.cumulative_sha256)
            self.assertTrue(final.eof)
            with self.assertRaisesRegex(ResourceSnapshotError, "exhausted"):
                reader.read_next()

            with self.assertRaises(FrozenInstanceError):
                final.eof = False  # type: ignore[misc]
        finally:
            reader.close()
            owner.close()

        self.assertEqual([], list(self.snapshot_parent.iterdir()))

    def test_exact_multiple_and_empty_snapshots_need_no_extra_empty_read(self) -> None:
        cases = (
            ("empty.bin", b"", 1),
            ("one.bin", b"a" * CHUNK_BYTES, 1),
            ("two.bin", b"b" * (2 * CHUNK_BYTES), 2),
        )
        for name, payload, expected_calls in cases:
            with self.subTest(name=name):
                owner = self._owner()
                relative = PurePosixPath(name)
                _seed_snapshot(owner, relative, payload)
                reader = owner.open_reader(relative)
                real_read = snapshot_module.os.read
                try:
                    with patch.object(
                        snapshot_module.os,
                        "read",
                        wraps=real_read,
                    ) as read:
                        chunks = []
                        while not chunks or not chunks[-1].eof:
                            chunks.append(reader.read_next())
                        with self.assertRaisesRegex(ResourceSnapshotError, "exhausted"):
                            reader.read_next()

                    self.assertEqual(expected_calls if payload else 0, read.call_count)
                    self.assertEqual(payload, b"".join(chunk.payload for chunk in chunks))
                    self.assertTrue(chunks[-1].eof)
                    self.assertEqual(len(payload), chunks[-1].cumulative_bytes)
                    self.assertEqual(
                        hashlib.sha256(payload).hexdigest(),
                        chunks[-1].cumulative_sha256,
                    )
                    if payload:
                        self.assertTrue(all(chunk.payload for chunk in chunks))
                    else:
                        self.assertEqual(b"", chunks[0].payload)
                finally:
                    reader.close()
                    owner.close()

        self.assertEqual([], list(self.snapshot_parent.iterdir()))

    def test_owner_refuses_close_until_its_only_reader_is_closed(self) -> None:
        owner = self._owner()
        relative = PurePosixPath("asset.bin")
        _seed_snapshot(owner, relative, b"payload")
        reader = owner.open_reader(relative)

        with self.assertRaisesRegex(ResourceSnapshotError, "reader is still open"):
            owner.close()
        self.assertFalse(owner.closed)
        self.assertTrue(owner.root.exists())

        reader.close()
        with self.assertRaisesRegex(ResourceSnapshotError, "already issued"):
            owner.open_reader(relative)
        owner.close()

        self.assertTrue(owner.closed)
        self.assertEqual([], list(self.snapshot_parent.iterdir()))

    def test_final_hash_mismatch_and_early_eof_consume_the_reader(self) -> None:
        owner = self._owner()
        relative = PurePosixPath("mismatch.bin")
        _seed_snapshot(owner, relative, b"payload")
        reader = owner.open_reader(relative)
        reader._record = replace(reader._record, sha256="0" * 64)

        with self.assertRaisesRegex(ResourceSnapshotError, "integrity"):
            reader.read_next()
        self.assertTrue(reader.closed)
        reader.close()
        owner.close()

        owner = self._owner()
        relative = PurePosixPath("short.bin")
        _seed_snapshot(owner, relative, b"payload")
        reader = owner.open_reader(relative)
        with (
            patch.object(snapshot_module.os, "read", return_value=b""),
            self.assertRaisesRegex(ResourceSnapshotError, "ended before"),
        ):
            reader.read_next()
        self.assertTrue(reader.closed)
        reader.close()
        owner.close()

        self.assertEqual([], list(self.snapshot_parent.iterdir()))

    @unittest.skipUnless(os.name == "posix", "POSIX in-place drift mutation")
    def test_descriptor_drift_aborts_before_returning_more_bytes(self) -> None:
        owner = self._owner()
        relative = PurePosixPath("drift.bin")
        target = _seed_snapshot(owner, relative, b"a" * (CHUNK_BYTES + 1))
        reader = owner.open_reader(relative)
        first = reader.read_next()
        self.assertFalse(first.eof)

        target.chmod(0o600)
        with target.open("r+b") as changed:
            changed.seek(CHUNK_BYTES)
            changed.write(b"z")
            changed.flush()
            os.fsync(changed.fileno())
        target.chmod(0o400)

        with self.assertRaisesRegex(ResourceSnapshotError, "changed"):
            reader.read_next()
        self.assertTrue(reader.closed)

        current = snapshot_module.path_file_stat(target)
        owner._files[relative] = snapshot_module._file_record(
            current,
            hashlib.sha256(target.read_bytes()).hexdigest(),
        )
        owner.close()
        self.assertEqual([], list(self.snapshot_parent.iterdir()))

    def test_source_replacement_or_deletion_cannot_change_snapshot_bytes(self) -> None:
        payload = b'{"stable":true}'
        for mutation in ("delete", "replace"):
            with self.subTest(mutation=mutation):
                source_root = Path(self.temporary_directory.name) / f"source-{mutation}"
                source_root.mkdir()
                source = source_root / "asset.json"
                source.write_bytes(payload)
                relative = PurePosixPath("asset.json")
                owner = self._owner()
                owner.materialize(
                    source_root,
                    relative,
                    "application/json",
                    limit=len(payload),
                )

                source.unlink()
                if mutation == "replace":
                    source.write_bytes(b'{"stable":false}')

                reader = owner.open_reader(relative)
                try:
                    self.assertEqual(payload, _read_all(reader))
                finally:
                    reader.close()
                    owner.close()

        self.assertEqual([], list(self.snapshot_parent.iterdir()))

    @unittest.skipUnless(os.name == "posix", "POSIX link and replacement semantics")
    def test_rejects_modified_replaced_symlinked_and_hardlinked_snapshots(self) -> None:
        payload = b"identity-bound"
        for attack in ("modified", "replaced", "symlinked", "hardlinked"):
            with self.subTest(attack=attack):
                owner = self._owner()
                relative = PurePosixPath(f"{attack}.bin")
                target = _seed_snapshot(owner, relative, payload)
                backup = self.snapshot_parent / f"{attack}-backup.bin"
                hardlink = self.snapshot_parent / f"{attack}-link.bin"
                try:
                    if attack == "modified":
                        target.chmod(0o600)
                        target.write_bytes(b"tampered")
                        target.chmod(0o400)
                    elif attack == "replaced":
                        target.rename(backup)
                        target.write_bytes(payload)
                        target.chmod(0o400)
                    elif attack == "symlinked":
                        target.rename(backup)
                        target.symlink_to(backup)
                    else:
                        os.link(target, hardlink)

                    with self.assertRaisesRegex(
                        ResourceSnapshotError,
                        "state changed|changed before|content changed",
                    ):
                        owner.open_reader(relative)
                finally:
                    if attack == "modified":
                        target.chmod(0o600)
                        target.write_bytes(payload)
                    elif attack in {"replaced", "symlinked"}:
                        target.unlink(missing_ok=True)
                        backup.rename(target)
                    else:
                        hardlink.unlink(missing_ok=True)
                    _refresh_record(owner, relative, target)
                    owner.close()

        self.assertEqual([], list(self.snapshot_parent.iterdir()))

    @unittest.skipUnless(os.name == "posix", "POSIX descriptor close semantics")
    def test_reader_close_is_consumed_once_while_owner_cleanup_stays_retryable(self) -> None:
        owner = self._owner()
        relative = PurePosixPath("close.bin")
        _seed_snapshot(owner, relative, b"payload")
        reader = owner.open_reader(relative)
        descriptor = reader._descriptor
        assert descriptor is not None
        real_close = snapshot_module.os.close
        close_attempts: list[int] = []

        def close_then_fail(candidate: int) -> None:
            close_attempts.append(candidate)
            real_close(candidate)
            raise OSError("injected reader close failure")

        with (
            patch.object(
                snapshot_module,
                "_close_reader_descriptor",
                side_effect=close_then_fail,
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "reader close failure"),
        ):
            reader.close()

        self.assertTrue(reader.closed)
        reader.close()
        self.assertEqual([descriptor], close_attempts)

        real_unlink = snapshot_module.os.unlink
        unlink_failed = False

        def fail_first_claim_unlink(path: object, *args: object, **kwargs: object) -> None:
            nonlocal unlink_failed
            if not unlink_failed and str(path).startswith(".isoworld-delete-"):
                unlink_failed = True
                raise OSError("injected cleanup unlink failure")
            real_unlink(path, *args, **kwargs)

        with (
            patch.object(
                snapshot_module.os,
                "unlink",
                side_effect=fail_first_claim_unlink,
            ),
            self.assertRaisesRegex(ResourceSnapshotError, "cleanup unlink failure"),
        ):
            owner.close()

        self.assertFalse(owner.closed)
        self.assertTrue(owner._active_root.exists())
        self.assertIn(relative, owner._files)
        owner.close()
        self.assertTrue(owner.closed)
        self.assertEqual([], list(self.snapshot_parent.iterdir()))

    @staticmethod
    def _windows_state(
        info: os.stat_result,
        *,
        attributes: int = stat.FILE_ATTRIBUTE_READONLY,
        inode_delta: int = 0,
    ) -> WindowsFileStat:
        return WindowsFileStat(
            st_mode=stat.S_IFREG | stat.S_IREAD,
            st_dev=info.st_dev,
            st_ino=info.st_ino + inode_delta,
            st_nlink=info.st_nlink,
            st_size=info.st_size,
            st_mtime_ns=info.st_mtime_ns,
            st_ctime_ns=info.st_ctime_ns,
            st_file_attributes=attributes,
        )

    @unittest.skipUnless(os.name == "posix", "mocked Windows descriptor semantics")
    def test_windows_reader_uses_identity_noinherit_and_closes_before_delete(self) -> None:
        owner = self._owner()
        relative = PurePosixPath("windows.bin")
        target = _seed_snapshot(owner, relative, b"payload")
        real_path_stat = os.stat
        real_descriptor_stat = os.fstat

        def path_state(candidate: object) -> WindowsFileStat:
            return self._windows_state(real_path_stat(candidate, follow_symlinks=False))

        def descriptor_state(descriptor: int) -> WindowsFileStat:
            return self._windows_state(real_descriptor_stat(descriptor))

        owner._files[relative] = snapshot_module._file_record(
            path_state(target),
            hashlib.sha256(b"payload").hexdigest(),
        )
        real_open_existing = snapshot_module._open_existing_file
        real_set_inheritable = os.set_inheritable
        real_get_inheritable = os.get_inheritable
        fake_noinherit = 1 << 29
        opened_flags: list[int] = []

        def open_without_fake_flag(**kwargs: object) -> int:
            flags = int(kwargs["flags"])
            opened_flags.append(flags)
            kwargs["flags"] = flags & ~fake_noinherit
            return real_open_existing(**kwargs)

        with (
            patch.object(snapshot_module, "_platform_name", return_value="nt"),
            patch.object(snapshot_module, "path_file_stat", side_effect=path_state),
            patch.object(
                snapshot_module,
                "descriptor_file_stat",
                side_effect=descriptor_state,
            ),
            patch.object(snapshot_module.os, "O_NOINHERIT", fake_noinherit, create=True),
            patch.object(
                snapshot_module,
                "_open_existing_file",
                side_effect=open_without_fake_flag,
            ),
            patch.object(
                snapshot_module.os,
                "set_inheritable",
                side_effect=real_set_inheritable,
            ) as set_inheritable,
            patch.object(
                snapshot_module.os,
                "get_inheritable",
                side_effect=real_get_inheritable,
            ),
        ):
            reader = owner.open_reader(relative)

        self.assertEqual(1, len(opened_flags))
        self.assertTrue(opened_flags[0] & fake_noinherit)
        set_inheritable.assert_called_once_with(reader._descriptor, False)
        assert reader._descriptor is not None
        self.assertFalse(real_get_inheritable(reader._descriptor))
        self.assertEqual(b"payload", _read_all(reader))

        events: list[str] = []
        real_reader_close = snapshot_module._close_reader_descriptor
        real_unlink = snapshot_module.os.unlink

        def record_reader_close(descriptor: int) -> None:
            events.append("reader-close")
            real_reader_close(descriptor)

        def record_unlink(path: object, *args: object, **kwargs: object) -> None:
            events.append("delete")
            real_unlink(path, *args, **kwargs)

        with (
            patch.object(
                snapshot_module,
                "_close_reader_descriptor",
                side_effect=record_reader_close,
            ),
            patch.object(snapshot_module.os, "unlink", side_effect=record_unlink),
        ):
            reader.close()
            owner.close()

        self.assertLess(events.index("reader-close"), events.index("delete"))
        self.assertEqual([], list(self.snapshot_parent.iterdir()))

    @unittest.skipUnless(os.name == "posix", "mocked Windows descriptor semantics")
    def test_windows_reader_rejects_identity_drift_and_reparse_descriptors(self) -> None:
        cases = ("identity", "reparse")
        real_path_stat = os.stat
        real_descriptor_stat = os.fstat
        for attack in cases:
            with self.subTest(attack=attack):
                current_attack = attack
                owner = self._owner()
                relative = PurePosixPath(f"windows-{attack}.bin")
                target = _seed_snapshot(owner, relative, b"payload")
                descriptor_calls = 0

                def path_state(candidate: object) -> WindowsFileStat:
                    return self._windows_state(real_path_stat(candidate, follow_symlinks=False))

                def descriptor_state(
                    descriptor: int,
                    attack_kind: str = current_attack,
                ) -> WindowsFileStat:
                    nonlocal descriptor_calls
                    descriptor_calls += 1
                    info = real_descriptor_stat(descriptor)
                    if descriptor_calls == 3 and attack_kind == "reparse":
                        return self._windows_state(
                            info,
                            attributes=(
                                stat.FILE_ATTRIBUTE_READONLY | stat.FILE_ATTRIBUTE_REPARSE_POINT
                            ),
                        )
                    if descriptor_calls == 4 and attack_kind == "identity":
                        return self._windows_state(info, inode_delta=1)
                    return self._windows_state(info)

                owner._files[relative] = snapshot_module._file_record(
                    path_state(target),
                    hashlib.sha256(b"payload").hexdigest(),
                )
                with (
                    patch.object(snapshot_module, "_platform_name", return_value="nt"),
                    patch.object(
                        snapshot_module,
                        "path_file_stat",
                        side_effect=path_state,
                    ),
                    patch.object(
                        snapshot_module,
                        "descriptor_file_stat",
                        side_effect=descriptor_state,
                    ),
                    self.assertRaisesRegex(ResourceSnapshotError, "descriptor changed"),
                ):
                    owner.open_reader(relative)

                self.assertIsNone(owner._reader)
                owner.close()

        self.assertEqual([], list(self.snapshot_parent.iterdir()))
