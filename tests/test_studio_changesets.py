from __future__ import annotations

import os
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.changesets import ChangesetManager, _pinned_operation_parents
from worldforge.studio.errors import StudioError
from worldforge.studio.storage import StudioStore
from worldforge.studio.workspaces import WorkspaceManager
from worldforge.world_lock import exclusive_world_lifecycle


class StudioChangesetTests(unittest.TestCase):
    def _workspace(self, temp: Path) -> tuple[Path, StudioStore, str]:
        world = temp / "world"
        create_world_project(world, world_id="studio_world", title="Studio", language="en")
        store = StudioStore(temp / "data")
        workspace = WorkspaceManager(store).register(
            {
                "workspace_id": "workspace_01",
                "forge_root": str(FORGE_ROOT),
                "world_root": str(world),
            }
        )
        return world, store, workspace["workspace_id"]

    def test_approved_create_replace_delete_applies_and_preserves_blobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            try:
                existing = world / "source/lore.txt"
                doomed = world / "source/remove.txt"
                existing.write_text("old\n", encoding="utf-8")
                doomed.write_text("remove\n", encoding="utf-8")
                manager = ChangesetManager(store)
                staged = manager.create(
                    {
                        "workspace_id": workspace_id,
                        "operations": [
                            {"path": "source/new.txt", "operation": "create", "content": "new\n"},
                            {
                                "path": "source/lore.txt",
                                "operation": "replace",
                                "content": "updated\n",
                            },
                            {"path": "source/remove.txt", "operation": "delete"},
                        ],
                    }
                )
                self.assertEqual("staged", staged["status"])
                self.assertEqual(
                    "approved",
                    manager.approve(
                        staged["changeset_id"],
                        expected_review_sha256=staged["review_sha256"],
                    )["status"],
                )
                applied = manager.apply(
                    staged["changeset_id"],
                    expected_review_sha256=staged["review_sha256"],
                )

                self.assertEqual("applied", applied["status"])
                self.assertEqual("new\n", (world / "source/new.txt").read_text(encoding="utf-8"))
                self.assertEqual("updated\n", existing.read_text(encoding="utf-8"))
                self.assertFalse(doomed.exists())
                for operation in staged["operations"]:
                    digest = operation["proposed_sha256"]
                    if digest is not None:
                        self.assertTrue(store.blob_path(digest).is_file())
            finally:
                store.close()

    def test_rejects_unsafe_paths_links_hardlinks_and_casefold_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            try:
                manager = ChangesetManager(store)
                for path in ("../escape.txt", "source/../escape.txt", "SOURCE/new.txt"):
                    with self.subTest(path=path), self.assertRaises(StudioError):
                        manager.create(
                            {
                                "workspace_id": workspace_id,
                                "operations": [
                                    {"path": path, "operation": "create", "content": "x"}
                                ],
                            }
                        )

                target = world / "source/linked.txt"
                target.write_text("linked", encoding="utf-8")
                hardlink = world / "source/linked-copy.txt"
                try:
                    os.link(target, hardlink)
                except (OSError, NotImplementedError):
                    self.skipTest("hardlinks are unavailable")
                with self.assertRaisesRegex(StudioError, "hard link"):
                    manager.create(
                        {
                            "workspace_id": workspace_id,
                            "operations": [
                                {
                                    "path": "source/linked.txt",
                                    "operation": "replace",
                                    "content": "new",
                                }
                            ],
                        }
                    )

                case_target = world / "source/Case.txt"
                case_target.write_text("case", encoding="utf-8")
                with self.assertRaisesRegex(StudioError, "casefold"):
                    manager.create(
                        {
                            "workspace_id": workspace_id,
                            "operations": [
                                {
                                    "path": "source/case.txt",
                                    "operation": "create",
                                    "content": "collision",
                                }
                            ],
                        }
                    )

                (world / "source/case.txt").write_text("other case", encoding="utf-8")
                with self.assertRaisesRegex(StudioError, "collision"):
                    manager.create(
                        {
                            "workspace_id": workspace_id,
                            "operations": [
                                {
                                    "path": "source/Case.txt",
                                    "operation": "replace",
                                    "content": "ambiguous",
                                }
                            ],
                        }
                    )

                composed = "caf\N{LATIN SMALL LETTER E WITH ACUTE}.txt"
                decomposed = unicodedata.normalize("NFD", composed)
                (world / "source" / composed).write_text("composed", encoding="utf-8")
                (world / "source" / decomposed).write_text("decomposed", encoding="utf-8")
                with self.assertRaisesRegex(StudioError, "collision"):
                    manager.create(
                        {
                            "workspace_id": workspace_id,
                            "operations": [
                                {
                                    "path": f"source/{composed}",
                                    "operation": "replace",
                                    "content": "ambiguous",
                                }
                            ],
                        }
                    )

                non_utf8 = world / "source/not-utf8.txt"
                non_utf8.write_bytes(b"\xff")
                with self.assertRaisesRegex(StudioError, "UTF-8"):
                    manager.create(
                        {
                            "workspace_id": workspace_id,
                            "operations": [
                                {
                                    "path": "source/not-utf8.txt",
                                    "operation": "replace",
                                    "content": "safe",
                                }
                            ],
                        }
                    )

                symlink = world / "source/alias.txt"
                try:
                    symlink.symlink_to(case_target)
                except (OSError, NotImplementedError):
                    pass
                else:
                    with self.assertRaises(StudioError):
                        manager.create(
                            {
                                "workspace_id": workspace_id,
                                "operations": [
                                    {
                                        "path": "source/alias.txt",
                                        "operation": "replace",
                                        "content": "unsafe",
                                    }
                                ],
                            }
                        )
            finally:
                store.close()

    def test_base_conflict_and_partial_failure_roll_back_without_clobbering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            try:
                first = world / "source/first.txt"
                second = world / "source/second.txt"
                first.write_text("first\n", encoding="utf-8")
                second.write_text("second\n", encoding="utf-8")
                manager = ChangesetManager(store)
                conflict = manager.create(
                    {
                        "workspace_id": workspace_id,
                        "operations": [
                            {
                                "path": "source/first.txt",
                                "operation": "replace",
                                "content": "changed\n",
                            }
                        ],
                    }
                )
                manager.approve(
                    conflict["changeset_id"],
                    expected_review_sha256=conflict["review_sha256"],
                )
                first.write_text("external\n", encoding="utf-8")
                with self.assertRaisesRegex(StudioError, "base"):
                    manager.apply(
                        conflict["changeset_id"],
                        expected_review_sha256=conflict["review_sha256"],
                    )
                self.assertEqual("external\n", first.read_text(encoding="utf-8"))

                first.write_text("first\n", encoding="utf-8")
                staged = manager.create(
                    {
                        "workspace_id": workspace_id,
                        "operations": [
                            {
                                "path": "source/first.txt",
                                "operation": "replace",
                                "content": "new first\n",
                            },
                            {
                                "path": "source/second.txt",
                                "operation": "replace",
                                "content": "new second\n",
                            },
                        ],
                    }
                )
                manager.approve(
                    staged["changeset_id"],
                    expected_review_sha256=staged["review_sha256"],
                )
                original_publish = manager._publish_stage
                calls = 0

                def fail_second(*args: object, **kwargs: object) -> None:
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise OSError("simulated publication failure")
                    original_publish(*args, **kwargs)

                with patch.object(manager, "_publish_stage", side_effect=fail_second):
                    with self.assertRaisesRegex(StudioError, "rolled back"):
                        manager.apply(
                            staged["changeset_id"],
                            expected_review_sha256=staged["review_sha256"],
                        )
                self.assertEqual("first\n", first.read_text(encoding="utf-8"))
                self.assertEqual("second\n", second.read_text(encoding="utf-8"))
            finally:
                store.close()

    def test_startup_rolls_back_incomplete_journal_and_finishes_committed_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            target = world / "source/recovery.txt"
            target.write_text("base\n", encoding="utf-8")
            manager = ChangesetManager(store)
            staged = manager.create(
                {
                    "workspace_id": workspace_id,
                    "operations": [
                        {
                            "path": "source/recovery.txt",
                            "operation": "replace",
                            "content": "interrupted\n",
                        }
                    ],
                }
            )
            approved = manager.approve(
                staged["changeset_id"],
                expected_review_sha256=staged["review_sha256"],
            )
            approved = manager._claim_apply(approved)
            identity = WorkspaceManager(store).root_identity(workspace_id, "world_root")
            assert identity is not None
            journal_path = store.journals_dir / f"{staged['changeset_id']}.json"
            with exclusive_world_lifecycle(world):
                journal = manager._prepare_journal(approved, world, identity)
                manager._write_journal(journal_path, journal)
                with _pinned_operation_parents(journal, world) as parents:
                    manager._prepare_stages(journal_path, journal, parents)
                    journal["state"] = "prepared"
                    manager._write_journal(journal_path, journal)
                    manager._apply_operation(journal["operations"][0], parents[0])
                    journal["state"] = "applying"
                    manager._write_journal(journal_path, journal)
            store.close()

            with StudioStore(temp / "data") as reopened:
                recovered = ChangesetManager(reopened)
                self.assertEqual("base\n", target.read_text(encoding="utf-8"))
                self.assertEqual("approved", recovered.get(staged["changeset_id"])["status"])
                self.assertFalse(journal_path.exists())

                committed = recovered.create(
                    {
                        "workspace_id": workspace_id,
                        "operations": [
                            {
                                "path": "source/recovery.txt",
                                "operation": "replace",
                                "content": "committed\n",
                            }
                        ],
                    }
                )
                committed = recovered.approve(
                    committed["changeset_id"],
                    expected_review_sha256=committed["review_sha256"],
                )
                committed = recovered._claim_apply(committed)
                committed_path = reopened.journals_dir / f"{committed['changeset_id']}.json"
                with exclusive_world_lifecycle(world):
                    journal = recovered._prepare_journal(committed, world, identity)
                    recovered._write_journal(committed_path, journal)
                    with _pinned_operation_parents(journal, world) as parents:
                        recovered._prepare_stages(committed_path, journal, parents)
                        journal["state"] = "prepared"
                        recovered._write_journal(committed_path, journal)
                        recovered._apply_operation(journal["operations"][0], parents[0])
                        journal["operations"][0]["applied"] = True
                        journal["state"] = "files_committed"
                        recovered._write_journal(committed_path, journal)

            with StudioStore(temp / "data") as reopened:
                recovered = ChangesetManager(reopened)
                self.assertEqual("committed\n", target.read_text(encoding="utf-8"))
                self.assertEqual("applied", recovered.get(committed["changeset_id"])["status"])
                self.assertFalse(committed_path.exists())

    def test_stage_journal_failure_cleans_reserved_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            try:
                manager = ChangesetManager(store)
                staged = manager.create(
                    {
                        "workspace_id": workspace_id,
                        "operations": [
                            {
                                "path": "source/new.txt",
                                "operation": "create",
                                "content": "new\n",
                            }
                        ],
                    }
                )
                manager.approve(
                    staged["changeset_id"],
                    expected_review_sha256=staged["review_sha256"],
                )
                original_write = manager._write_journal

                def fail_after_stage(path: Path, journal: dict[str, object]) -> None:
                    operations = journal.get("operations")
                    if (
                        journal.get("state") == "preparing"
                        and isinstance(operations, list)
                        and any(item.get("stage_identity") for item in operations)
                    ):
                        raise OSError("simulated journal update failure")
                    original_write(path, journal)

                with patch.object(manager, "_write_journal", side_effect=fail_after_stage):
                    with self.assertRaisesRegex(StudioError, "rolled back"):
                        manager.apply(
                            staged["changeset_id"],
                            expected_review_sha256=staged["review_sha256"],
                        )
                self.assertFalse((world / "source/new.txt").exists())
                self.assertEqual([], list((world / "source").glob(".worldforge-studio-*")))
                self.assertEqual([], list(store.journals_dir.glob("*.json")))
            finally:
                store.close()

    @unittest.skipUnless(os.name == "posix", "POSIX dir-fd containment semantics required")
    def test_parent_replacement_during_stage_creation_cannot_redirect_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            parent = world / "source/lore"
            moved = world / "source/lore-original"
            parent.mkdir()
            try:
                manager = ChangesetManager(store)
                staged = manager.create(
                    {
                        "workspace_id": workspace_id,
                        "operations": [
                            {
                                "path": "source/lore/new.txt",
                                "operation": "create",
                                "content": "new\n",
                            }
                        ],
                    }
                )
                manager.approve(
                    staged["changeset_id"],
                    expected_review_sha256=staged["review_sha256"],
                )
                real_open = os.open
                replaced = False

                def replace_before_open(
                    path: os.PathLike[str] | str,
                    flags: int,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    nonlocal replaced
                    if not replaced and dir_fd is not None and str(path).endswith(".stage"):
                        parent.rename(moved)
                        parent.mkdir()
                        replaced = True
                    if dir_fd is None:
                        return real_open(path, flags, mode)
                    return real_open(path, flags, mode, dir_fd=dir_fd)

                with patch("worldforge.studio.changesets.os.open", side_effect=replace_before_open):
                    with self.assertRaisesRegex(StudioError, "directory identity changed"):
                        manager.apply(
                            staged["changeset_id"],
                            expected_review_sha256=staged["review_sha256"],
                        )

                self.assertTrue(replaced)
                self.assertFalse((parent / "new.txt").exists())
                self.assertFalse((moved / "new.txt").exists())
                self.assertEqual([], list(moved.glob(".worldforge-studio-*")))
                self.assertEqual([], list(store.journals_dir.glob("*.json")))
            finally:
                store.close()

    def test_committed_finalization_runs_while_world_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            try:
                manager = ChangesetManager(store)
                staged = manager.create(
                    {
                        "workspace_id": workspace_id,
                        "operations": [
                            {
                                "path": "source/new.txt",
                                "operation": "create",
                                "content": "new\n",
                            }
                        ],
                    }
                )
                manager.approve(
                    staged["changeset_id"],
                    expected_review_sha256=staged["review_sha256"],
                )
                original_finalize = manager._finalize_committed

                def assert_locked(*args: object, **kwargs: object) -> dict[str, object]:
                    self.assertTrue((world / ".worldforge/lifecycle.lock").is_file())
                    return original_finalize(*args, **kwargs)

                with patch.object(manager, "_finalize_committed", side_effect=assert_locked):
                    manager.apply(
                        staged["changeset_id"],
                        expected_review_sha256=staged["review_sha256"],
                    )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
