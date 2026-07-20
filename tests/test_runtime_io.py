from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.content.renderpack import RenderPackError, load_clipset, load_renderpack
from isoworld.persistence import PersistenceError, load_game, load_replay
from isoworld.runtime_io import RuntimeIOError, read_json_object


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


if __name__ == "__main__":
    unittest.main()
