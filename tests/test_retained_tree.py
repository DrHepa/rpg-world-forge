from __future__ import annotations

import inspect
import os
import stat
import tempfile
import unittest
from pathlib import Path

from worldforge.contract_catalog import LEGACY_SHARE_DIRECTORY
from worldforge.file_stat import WindowsFileStat


class RetainedTreeTests(unittest.TestCase):
    def test_direct_file_census_accepts_exact_capacity_and_fails_on_one_more(self) -> None:
        from worldforge.retained_tree import (
            RetainedTreeCapacityError,
            capture_retained_directory_file_census,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "history"
            root.mkdir()
            for index in range(3):
                (root / f"{index:064x}.json").write_bytes(b"{}\n")

            snapshot = capture_retained_directory_file_census(root, maximum_entries=3)
            self.assertEqual(tuple(f"{index:064x}.json" for index in range(3)), snapshot.names)

            (root / f"{3:064x}.json").write_bytes(b"{}\n")
            with self.assertRaisesRegex(RetainedTreeCapacityError, "exceeds 3 entries"):
                capture_retained_directory_file_census(root, maximum_entries=3)

    def test_direct_file_census_rejects_unsafe_entry_at_capacity_boundary(self) -> None:
        from worldforge.retained_tree import (
            RetainedTreeError,
            capture_retained_directory_file_census,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "history"
            root.mkdir()
            for index in range(3):
                (root / f"{index:064x}.json").write_bytes(b"{}\n")
            (root / f"{3:064x}.json").mkdir()

            with self.assertRaisesRegex(RetainedTreeError, "regular file"):
                capture_retained_directory_file_census(root, maximum_entries=3)

    def test_direct_file_census_detects_identity_replacement_and_ancestry_change(self) -> None:
        from worldforge.retained_tree import (
            RetainedTreeError,
            capture_retained_directory_file_census,
        )

        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary) / "parent"
            root = parent / "history"
            root.mkdir(parents=True)
            target = root / f"{0:064x}.json"
            target.write_bytes(b"{}\n")

            def replace_file(event: str, _relative: str | None) -> None:
                if event != "before_final_verification":
                    return
                replacement = root / "replacement.json"
                replacement.write_bytes(b"{}\n")
                os.replace(replacement, target)

            with self.assertRaisesRegex(RetainedTreeError, "changed"):
                capture_retained_directory_file_census(
                    root,
                    maximum_entries=3,
                    verification_hook=replace_file,
                )

        if os.name != "posix":
            return
        with tempfile.TemporaryDirectory() as temporary:
            ancestor = Path(temporary) / "ancestor"
            root = ancestor / "history"
            root.mkdir(parents=True)
            (root / f"{0:064x}.json").write_bytes(b"{}\n")

            def replace_ancestry(event: str, _relative: str | None) -> None:
                if event != "before_final_verification":
                    return
                moved = Path(temporary) / "moved"
                ancestor.rename(moved)
                ancestor.mkdir()

            with self.assertRaisesRegex(RetainedTreeError, "ancestry changed"):
                capture_retained_directory_file_census(
                    root,
                    maximum_entries=3,
                    verification_hook=replace_ancestry,
                )

    def test_direct_file_census_is_bound_to_its_retained_authority(self) -> None:
        from worldforge.retained_tree import (
            RetainedTreeError,
            capture_retained_directory_file_census,
        )

        with tempfile.TemporaryDirectory() as temporary:
            authority = Path(temporary) / "project"
            root = authority / ".worldforge" / "artifact_history"
            root.mkdir(parents=True)
            (root / f"{0:064x}.json").write_bytes(b"{}\n")
            authority_info = os.stat(authority, follow_symlinks=False)
            authority_identity = (authority_info.st_dev, authority_info.st_ino)

            snapshot = capture_retained_directory_file_census(
                root,
                maximum_entries=3,
                authority_root=authority,
                expected_authority_identity=authority_identity,
            )
            self.assertEqual((f"{0:064x}.json",), snapshot.names)

            with self.assertRaisesRegex(RetainedTreeError, "authority changed"):
                capture_retained_directory_file_census(
                    root,
                    maximum_entries=3,
                    authority_root=authority,
                    expected_authority_identity=(
                        authority_identity[0],
                        authority_identity[1] + 1,
                    ),
                )

    def test_named_child_capture_uses_one_retained_container_census(self) -> None:
        from worldforge.retained_tree import capture_retained_named_child_trees

        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "prefix"
            canonical = prefix / "share/world-forge"
            canonical.mkdir(parents=True)
            (canonical / "payload.txt").write_bytes(b"canonical")

            snapshots = capture_retained_named_child_trees(
                prefix,
                container_name="share",
                child_names=("world-forge", LEGACY_SHARE_DIRECTORY.name),
            )

        self.assertEqual(b"canonical", snapshots["world-forge"].files["payload.txt"])
        self.assertIsNone(snapshots[LEGACY_SHARE_DIRECTORY.name])

    def test_windows_reparse_directories_and_files_fail_closed(self) -> None:
        from worldforge.retained_tree import RetainedTreeError, _entry_kind

        for mode in (stat.S_IFDIR | 0o755, stat.S_IFREG | 0o644):
            with self.subTest(mode=mode):
                info = WindowsFileStat(
                    st_mode=mode,
                    st_dev=1,
                    st_ino=2,
                    st_nlink=1,
                    st_size=0,
                    st_mtime_ns=3,
                    st_ctime_ns=4,
                    st_file_attributes=0x00000400,
                )
                with self.assertRaisesRegex(RetainedTreeError, "linked or reparse"):
                    _entry_kind(info, "unsafe")

    def test_shared_tree_uses_retained_native_windows_enumeration(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "src/worldforge/retained_tree.py"
        ).read_text(encoding="utf-8")

        self.assertIn("NtQueryDirectoryFile", source)
        self.assertIn("windows_handle_file_stat", source)
        self.assertIn("_FILE_OPEN_REPARSE_POINT", source)
        self.assertNotIn("os.walk(", source)

    def test_named_child_capture_has_a_native_windows_retention_seam(self) -> None:
        from worldforge import retained_tree

        source = "\n".join(
            (
                inspect.getsource(retained_tree._windows_direct_census),
                inspect.getsource(retained_tree._capture_named_windows),
            )
        )
        dispatcher = inspect.getsource(retained_tree.capture_retained_named_child_trees)

        self.assertIn("_WindowsTreeApi", source)
        self.assertIn("open_directory_entries", source)
        self.assertIn("windows_handle_file_stat", source)
        self.assertIn("capture_retained_tree", source)
        self.assertNotIn(".exists(", source)
        if os.name == "nt":
            self.assertIn("_capture_named_windows", dispatcher)


if __name__ == "__main__":
    unittest.main()
