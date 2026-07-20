from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import isoworld.runtime_io as runtime_io_module
from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.content.renderpack import RenderPackError, load_clipset, load_renderpack
from isoworld.persistence import (
    PersistenceError,
    load_game,
    load_replay,
    save_game,
    write_replay,
)
from isoworld.runtime_io import RuntimeIOError, read_json_object, write_json_atomic
from isoworld.world.state import initial_world_state


class RuntimeJSONLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def write_bytes(self, name: str, payload: bytes) -> Path:
        path = self.root / name
        path.write_bytes(payload)
        return path

    def test_reads_valid_json_object_and_preserves_numbers(self) -> None:
        path = self.write_bytes(
            "runtime.json",
            b'{"count": 9007199254740993, "ratio": 1.25, "nested": {"ok": true}}',
        )

        value = read_json_object(path)

        self.assertEqual(
            value,
            {
                "count": 9007199254740993,
                "ratio": 1.25,
                "nested": {"ok": True},
            },
        )
        self.assertIsInstance(value["count"], int)
        self.assertIsInstance(value["ratio"], float)

    def test_rejects_duplicate_root_key(self) -> None:
        path = self.write_bytes("duplicate.json", b'{"value": 1, "value": 2}')

        with self.assertRaisesRegex(RuntimeIOError, "duplicate JSON object key"):
            read_json_object(path)

    def test_rejects_duplicate_nested_key(self) -> None:
        path = self.write_bytes("duplicate.json", b'{"nested": {"value": 1, "value": 2}}')

        with self.assertRaisesRegex(RuntimeIOError, "duplicate JSON object key"):
            read_json_object(path)

    def test_rejects_non_finite_json_constants(self) -> None:
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                path = self.write_bytes(
                    "non-finite.json",
                    f'{{"value": {constant}}}'.encode(),
                )

                with self.assertRaisesRegex(RuntimeIOError, "non-finite JSON number"):
                    read_json_object(path)

    def test_rejects_nested_float_overflow(self) -> None:
        path = self.write_bytes("overflow.json", b'{"nested": [{"value": 1e400}]}')

        with self.assertRaisesRegex(RuntimeIOError, "non-finite JSON number"):
            read_json_object(path)

    def test_rejects_invalid_utf8(self) -> None:
        path = self.write_bytes("invalid-utf8.json", b'{"value": "\xff"}')

        with self.assertRaisesRegex(RuntimeIOError, "Could not read"):
            read_json_object(path)

    def test_rejects_non_object_roots(self) -> None:
        roots = (b"[]", b'"text"', b"null", b"42", b"1.25")
        for index, payload in enumerate(roots):
            with self.subTest(payload=payload):
                path = self.write_bytes(f"root-{index}.json", payload)

                with self.assertRaisesRegex(RuntimeIOError, "must contain a JSON object"):
                    read_json_object(path)

    def test_rejects_missing_file(self) -> None:
        with self.assertRaisesRegex(RuntimeIOError, "Could not read"):
            read_json_object(self.root / "missing.json")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX FIFO support required")
    def test_rejects_fifo_without_waiting_for_a_writer(self) -> None:
        path = self.root / "runtime.fifo"
        os.mkfifo(path)

        with self.assertRaisesRegex(RuntimeIOError, "not a standalone regular file"):
            read_json_object(path)

    def test_rejects_unreadable_file(self) -> None:
        path = self.write_bytes("runtime.json", b"{}")

        with patch("isoworld.runtime_io.os.open", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(RuntimeIOError, "denied"):
                read_json_object(path)

    def test_rejects_oversized_file(self) -> None:
        path = self.write_bytes("runtime.json", b'{"value": 1}')

        with self.assertRaisesRegex(RuntimeIOError, "exceeds the 4-byte limit"):
            read_json_object(path, limit=4)

    def test_worldpack_boundary_uses_strict_json(self) -> None:
        path = self.write_bytes("worldpack.json", b'{"format": 1, "format": 2}')

        with self.assertRaisesRegex(WorldPackError, "duplicate JSON object key"):
            load_worldpack(path)

    def test_renderpack_boundary_uses_strict_json(self) -> None:
        path = self.write_bytes("renderpack.json", b'{"format": 1, "format": 2}')

        with self.assertRaisesRegex(RenderPackError, "duplicate JSON object key"):
            load_renderpack(path, object())

    def test_clipset_boundary_uses_strict_json(self) -> None:
        path = self.write_bytes("clipset.json", b'{"format": 1, "format": 2}')

        with self.assertRaisesRegex(RenderPackError, "duplicate JSON object key"):
            load_clipset(path)

    def test_save_boundary_uses_strict_json(self) -> None:
        path = self.write_bytes("save.json", b'{"format": 1, "format": 2}')

        with self.assertRaisesRegex(PersistenceError, "duplicate JSON object key"):
            load_game(path, object())

    def test_replay_boundary_uses_strict_json(self) -> None:
        path = self.write_bytes("replay.json", b'{"format": 1, "format": 2}')

        with self.assertRaisesRegex(PersistenceError, "duplicate JSON object key"):
            load_replay(path, object())


class AtomicRuntimeWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def active_temporaries(self, destination: Path) -> list[Path]:
        return list(destination.parent.glob(f".{destination.name}.tmp.*"))

    def assert_no_active_temporaries(self, destination: Path) -> None:
        self.assertEqual([], self.active_temporaries(destination))

    def lock_path(self, destination: Path) -> Path:
        return destination.with_name(f".{destination.name}.lock")

    def test_preserves_json_bytes_and_uses_a_safe_persistent_lock(self) -> None:
        path = self.root / "state.json"

        write_json_atomic(path, {"unicode": "á", "count": 2})

        expected = (
            json.dumps(
                {"unicode": "á", "count": 2},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        self.assertEqual(expected.encode("utf-8"), path.read_bytes())
        lock = self.lock_path(path)
        info = lock.lstat()
        self.assertTrue(lock.is_file())
        self.assertEqual(1, info.st_nlink)
        self.assertEqual(b"\0", lock.read_bytes())
        self.assert_no_active_temporaries(path)

    def test_replaces_an_existing_standalone_regular_file(self) -> None:
        path = self.root / "state.json"
        path.write_text('{"version": 1}\n', encoding="utf-8")

        write_json_atomic(path, {"version": 2})

        self.assertEqual({"version": 2}, read_json_object(path))
        self.assert_no_active_temporaries(path)

    def test_windows_style_path_fallback_revalidates_a_safe_parent(self) -> None:
        path = self.root / "state.json"
        path.write_text('{"version": 1}\n', encoding="utf-8")

        with patch("isoworld.runtime_io._DIR_FD_PUBLICATION", False):
            write_json_atomic(path, {"version": 2})

        self.assertEqual({"version": 2}, read_json_object(path))
        self.assert_no_active_temporaries(path)

    def test_concurrent_writer_is_rejected_by_os_managed_lock(self) -> None:
        path = self.root / "state.json"
        write_json_atomic(path, {"writer": "initial"})
        acquired = threading.Event()
        release = threading.Event()
        original_acquire = runtime_io_module._acquire_os_lock

        def hold_first_writer(descriptor: int) -> None:
            original_acquire(descriptor)
            if not acquired.is_set():
                acquired.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("test lock release timed out")

        def attempt(writer: str) -> tuple[str, str]:
            try:
                write_json_atomic(path, {"writer": writer})
            except RuntimeIOError:
                return "rejected", writer
            return "published", writer

        with (
            patch("isoworld.runtime_io._acquire_os_lock", side_effect=hold_first_writer),
            ThreadPoolExecutor(max_workers=1) as executor,
        ):
            first = executor.submit(attempt, "first")
            self.assertTrue(acquired.wait(timeout=5))
            second = attempt("second")
            release.set()
            first_result = first.result(timeout=5)

        self.assertEqual(("published", "first"), first_result)
        self.assertEqual(("rejected", "second"), second)
        self.assertEqual({"writer": "first"}, read_json_object(path))
        self.assert_no_active_temporaries(path)

    def test_partial_write_failure_keeps_target_and_retains_owned_temp(self) -> None:
        path = self.root / "state.json"
        write_json_atomic(path, {"version": 1})
        original = path.read_bytes()

        def partial_write(target: object, payload: bytes) -> None:
            target.write(payload[:7])
            raise OSError("injected partial write")

        with (
            patch("isoworld.runtime_io._write_all", side_effect=partial_write),
            self.assertRaisesRegex(RuntimeIOError, "injected partial write"),
        ):
            write_json_atomic(path, {"version": 2})

        self.assertEqual(original, path.read_bytes())
        retained = self.active_temporaries(path)
        self.assertEqual(1, len(retained))
        self.assertEqual(7, retained[0].stat().st_size)

    def test_file_fsync_failure_keeps_target_and_retains_complete_temp(self) -> None:
        path = self.root / "state.json"
        write_json_atomic(path, {"version": 1})
        original = path.read_bytes()

        with (
            patch("isoworld.runtime_io.os.fsync", side_effect=OSError("injected file fsync")),
            self.assertRaisesRegex(RuntimeIOError, "injected file fsync"),
        ):
            write_json_atomic(path, {"version": 2})

        self.assertEqual(original, path.read_bytes())
        retained = self.active_temporaries(path)
        self.assertEqual(1, len(retained))
        self.assertEqual({"version": 2}, read_json_object(retained[0]))

    def test_replace_failure_keeps_target_and_retains_temp(self) -> None:
        path = self.root / "state.json"
        write_json_atomic(path, {"version": 1})
        original = path.read_bytes()

        with (
            patch(
                "isoworld.runtime_io._replace_entry",
                side_effect=OSError("injected replace"),
            ),
            self.assertRaisesRegex(RuntimeIOError, "injected replace"),
        ):
            write_json_atomic(path, {"version": 2})

        self.assertEqual(original, path.read_bytes())
        retained = self.active_temporaries(path)
        self.assertEqual(1, len(retained))
        self.assertEqual({"version": 2}, read_json_object(retained[0]))
        self.assertEqual(b"\0", self.lock_path(path).read_bytes())

    def test_parent_fsync_failure_reports_indeterminate_but_valid_atomic_result(self) -> None:
        path = self.root / "state.json"
        write_json_atomic(path, {"version": 1})

        with (
            patch(
                "isoworld.runtime_io._fsync_parent",
                side_effect=OSError("injected parent fsync"),
            ),
            self.assertRaisesRegex(RuntimeIOError, "injected parent fsync"),
        ):
            write_json_atomic(path, {"version": 2})

        self.assertEqual({"version": 2}, read_json_object(path))
        self.assert_no_active_temporaries(path)

    def test_refuses_symlink_target_without_changing_referent(self) -> None:
        referent = self.root / "referent.json"
        referent.write_text('{"safe": true}\n', encoding="utf-8")
        destination = self.root / "state.json"
        try:
            destination.symlink_to(referent.name)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        with self.assertRaisesRegex(RuntimeIOError, "symbolic link"):
            write_json_atomic(destination, {"safe": False})

        self.assertEqual('{"safe": true}\n', referent.read_text(encoding="utf-8"))
        self.assertTrue(destination.is_symlink())

    def test_refuses_symlink_parent(self) -> None:
        real_parent = self.root / "real"
        real_parent.mkdir()
        linked_parent = self.root / "linked"
        try:
            linked_parent.symlink_to(real_parent, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")

        with self.assertRaisesRegex(RuntimeIOError, "safe directory"):
            write_json_atomic(linked_parent / "state.json", {"safe": True})

        self.assertFalse((real_parent / "state.json").exists())

    def test_refuses_non_regular_target(self) -> None:
        destination = self.root / "state.json"
        destination.mkdir()

        with self.assertRaisesRegex(RuntimeIOError, "non-regular"):
            write_json_atomic(destination, {"safe": True})

    def test_refuses_hard_linked_target(self) -> None:
        destination = self.root / "state.json"
        destination.write_text('{"safe": true}\n', encoding="utf-8")
        alias = self.root / "alias.json"
        try:
            os.link(destination, alias)
        except OSError as exc:
            self.skipTest(f"hard links unavailable: {exc}")

        with self.assertRaisesRegex(RuntimeIOError, "hard-linked"):
            write_json_atomic(destination, {"safe": False})

        self.assertEqual('{"safe": true}\n', destination.read_text(encoding="utf-8"))
        self.assertEqual(destination.read_bytes(), alias.read_bytes())

    def test_refuses_symlink_lock_sidecar(self) -> None:
        destination = self.root / "state.json"
        destination.write_text('{"safe": true}\n', encoding="utf-8")
        referent = self.root / "lock-referent"
        referent.write_bytes(b"\0")
        try:
            self.lock_path(destination).symlink_to(referent.name)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

        with self.assertRaisesRegex(RuntimeIOError, "symbolic link"):
            write_json_atomic(destination, {"safe": False})

        self.assertEqual(b"\0", referent.read_bytes())
        self.assertEqual({"safe": True}, read_json_object(destination))

    def test_refuses_hard_linked_lock_sidecar(self) -> None:
        destination = self.root / "state.json"
        destination.write_text('{"safe": true}\n', encoding="utf-8")
        lock = self.lock_path(destination)
        lock.write_bytes(b"\0")
        alias = self.root / "lock-alias"
        try:
            os.link(lock, alias)
        except OSError as exc:
            self.skipTest(f"hard links unavailable: {exc}")

        with self.assertRaisesRegex(RuntimeIOError, "hard-linked"):
            write_json_atomic(destination, {"safe": False})

        self.assertEqual({"safe": True}, read_json_object(destination))
        self.assertEqual(b"\0", alias.read_bytes())

    def test_foreign_temp_swap_survives_without_any_unlink(self) -> None:
        destination = self.root / "state.json"
        write_json_atomic(destination, {"safe": True})
        original_verify = runtime_io_module._verify_owned_entry
        swapped: list[Path] = []

        def swap_before_verify(
            parent_fd: int | None,
            parent: Path,
            name: str,
            identity: tuple[int, int],
        ) -> None:
            if not swapped and name.startswith(f".{destination.name}.tmp."):
                temporary = parent / name
                foreign = parent / f"foreign-{name}"
                foreign.write_bytes(b"foreign")
                os.replace(foreign, temporary)
                swapped.append(temporary)
            original_verify(parent_fd, parent, name, identity)

        with (
            patch(
                "isoworld.runtime_io._verify_owned_entry",
                side_effect=swap_before_verify,
            ),
            patch("isoworld.runtime_io.os.unlink") as unlink,
            self.assertRaisesRegex(RuntimeIOError, "changed before publication"),
        ):
            write_json_atomic(destination, {"safe": False})

        unlink.assert_not_called()
        self.assertEqual({"safe": True}, read_json_object(destination))
        self.assertEqual(1, len(swapped))
        self.assertEqual(b"foreign", swapped[0].read_bytes())

    @unittest.skipUnless(
        runtime_io_module._DIR_FD_PUBLICATION,
        "POSIX dir-fd publication required",
    )
    def test_posix_parent_rename_after_publication_is_not_reported_as_success(self) -> None:
        parent = self.root / "parent"
        destination = parent / "state.json"
        write_json_atomic(destination, {"version": 1})
        moved = self.root / "moved"
        original_fsync_parent = runtime_io_module._fsync_parent

        def rename_after_fsync(parent_fd: int | None) -> None:
            original_fsync_parent(parent_fd)
            parent.rename(moved)
            parent.mkdir()

        with (
            patch("isoworld.runtime_io._fsync_parent", side_effect=rename_after_fsync),
            self.assertRaisesRegex(RuntimeIOError, "parent changed"),
        ):
            write_json_atomic(destination, {"version": 2})

        self.assertFalse(destination.exists())
        self.assertEqual({"version": 2}, read_json_object(moved / "state.json"))
        self.assert_no_active_temporaries(moved / "state.json")

    def test_path_fallback_parent_swap_before_temp_creation_fails_closed(self) -> None:
        parent = self.root / "parent"
        destination = parent / "state.json"
        with patch("isoworld.runtime_io._DIR_FD_PUBLICATION", False):
            write_json_atomic(destination, {"version": 1})
        moved = self.root / "moved"
        original_create = runtime_io_module._create_temporary_entry

        def swap_then_create(
            parent_fd: int | None,
            output_parent: Path,
            prefix: str,
            parent_identity: tuple[int, int],
        ) -> tuple[int, str]:
            self.assertIsNone(parent_fd)
            output_parent.rename(moved)
            output_parent.mkdir()
            return original_create(parent_fd, output_parent, prefix, parent_identity)

        with (
            patch("isoworld.runtime_io._DIR_FD_PUBLICATION", False),
            patch(
                "isoworld.runtime_io._create_temporary_entry",
                side_effect=swap_then_create,
            ),
            self.assertRaisesRegex(RuntimeIOError, "parent changed"),
        ):
            write_json_atomic(destination, {"version": 2})

        self.assertFalse(destination.exists())
        self.assertEqual({"version": 1}, read_json_object(moved / "state.json"))
        self.assert_no_active_temporaries(destination)
        self.assert_no_active_temporaries(moved / "state.json")

    def test_save_and_replay_round_trip_through_atomic_writer(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        pack = load_worldpack(repository / "content/compiled/foundation.worldpack.json")
        state = initial_world_state(pack)
        save_path = self.root / "nested" / "save.json"
        replay_path = self.root / "nested" / "replay.json"

        save_game(save_path, state, pack)
        write_replay(replay_path, [], state, pack)

        self.assertEqual(state, load_game(save_path, pack))
        actions, replayed = load_replay(replay_path, pack)
        self.assertEqual([], actions)
        self.assertEqual(state, replayed)
        self.assert_no_active_temporaries(save_path)
        self.assert_no_active_temporaries(replay_path)


if __name__ == "__main__":
    unittest.main()
