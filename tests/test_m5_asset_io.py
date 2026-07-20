from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import worldforge.asset_io as asset_io_module
from worldforge.asset_io import (
    AssetContractError,
    bind_content_hash,
    encoded_json,
    read_json_object,
    write_json_atomic,
)


class AssetIOTests(unittest.TestCase):
    def test_reader_rejects_non_finite_json_numbers(self) -> None:
        for literal in ("NaN", "Infinity", "-Infinity", "1e999"):
            with self.subTest(literal=literal), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "contract.json"
                path.write_text(f'{{"value": {literal}}}\n', encoding="utf-8")

                with self.assertRaisesRegex(AssetContractError, "non-finite JSON number"):
                    read_json_object(path)

    def test_writer_rejects_non_finite_json_numbers_without_creating_output(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "new/output.json"

                with self.assertRaisesRegex(AssetContractError, "strict JSON"):
                    write_json_atomic(path, {"value": value})

                self.assertFalse(path.exists())
                self.assertFalse(path.parent.exists())

    def test_writer_rejects_symbolic_link_parent_without_external_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            output_parent = root / "outputs"
            output_parent.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(AssetContractError, "safe directory"):
                write_json_atomic(output_parent / "leak.json", {"safe": True})

            self.assertFalse((outside / "leak.json").exists())

    def test_writer_creates_verified_real_parents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "real/nested/output.json"

            write_json_atomic(path, {"safe": True})

            self.assertEqual({"safe": True}, read_json_object(path))

    def test_writer_does_not_replace_file_created_at_publish_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "contract.json"
            concurrent = {"owner": "concurrent"}
            original_link = asset_io_module.os.link

            def create_destination_then_link(
                source: str | Path,
                target: str | Path,
                **kwargs: object,
            ) -> None:
                destination.write_bytes(encoded_json(concurrent))
                original_link(source, target, **kwargs)

            with (
                patch.object(
                    asset_io_module.os,
                    "link",
                    side_effect=create_destination_then_link,
                ),
                self.assertRaisesRegex(AssetContractError, "Refusing to overwrite"),
            ):
                write_json_atomic(destination, {"owner": "forge"})

            self.assertEqual(concurrent, read_json_object(destination))
            self.assertEqual([], list(destination.parent.glob(".contract.json.*")))

    def test_writer_cas_rechecks_hash_after_lock_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "contract.json"
            initial = bind_content_hash({"value": "initial"})
            concurrent = bind_content_hash({"value": "concurrent"})
            write_json_atomic(destination, initial)
            original_read = asset_io_module._read_json_object_entry

            def replace_before_cas_read(
                parent_fd: int | None,
                parent: Path,
                name: str,
            ) -> dict[str, object]:
                destination.write_bytes(encoded_json(concurrent))
                return original_read(parent_fd, parent, name)

            with (
                patch.object(
                    asset_io_module,
                    "_read_json_object_entry",
                    side_effect=replace_before_cas_read,
                ),
                self.assertRaisesRegex(AssetContractError, "Content changed before publishing"),
            ):
                write_json_atomic(
                    destination,
                    bind_content_hash({"value": "forge"}),
                    overwrite=True,
                    expected_content_hash=initial["content_hash"],
                )

            self.assertEqual(concurrent, read_json_object(destination))
            self.assertFalse((destination.parent / ".contract.json.lock").exists())

    def test_writer_rejects_parent_replaced_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "publish/contract.json"
            moved = root / "publish-original"
            outside = root / "outside"
            outside.mkdir()
            original_prepare = asset_io_module.prepare_output_path

            def replace_parent_after_prepare(path: str | Path) -> Path:
                prepared = original_prepare(path)
                prepared.parent.rename(moved)
                prepared.parent.symlink_to(outside, target_is_directory=True)
                return prepared

            with (
                patch.object(
                    asset_io_module,
                    "prepare_output_path",
                    side_effect=replace_parent_after_prepare,
                ),
                self.assertRaisesRegex(AssetContractError, "safe directory|changed"),
            ):
                write_json_atomic(destination, {"owner": "forge"})

            self.assertFalse((outside / destination.name).exists())
            self.assertEqual([], list(outside.iterdir()))
            self.assertEqual([], list(moved.iterdir()))


if __name__ == "__main__":
    unittest.main()
