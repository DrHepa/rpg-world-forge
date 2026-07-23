from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.changesets import ChangesetManager, _pinned_operation_parents
from worldforge.studio.errors import StudioError
from worldforge.studio.storage import StudioStore, encode_json
from worldforge.studio.workspaces import WorkspaceManager
from worldforge.world_lock import exclusive_world_lifecycle


class StudioChangesetV2Tests(unittest.TestCase):
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

    def _replace(self, manager: ChangesetManager, workspace_id: str) -> dict[str, object]:
        return manager.create(
            {
                "workspace_id": workspace_id,
                "operations": [
                    {
                        "path": "source/lore.txt",
                        "operation": "replace",
                        "content": "proposed café\n",
                    }
                ],
            }
        )

    def test_new_changeset_retains_both_sides_and_diff_ignores_workspace_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            try:
                source = world / "source/lore.txt"
                source.write_text("base café\n", encoding="utf-8")
                manager = ChangesetManager(store)

                staged = self._replace(manager, workspace_id)
                first = manager.diff(staged["changeset_id"])
                source.write_text("external mutation\n", encoding="utf-8")
                second = manager.diff(staged["changeset_id"])

                self.assertEqual(2, staged["format_version"])
                self.assertEqual(len("base café\n".encode()), staged["operations"][0]["base_size"])
                self.assertRegex(staged["review_sha256"], r"^[0-9a-f]{64}$")
                self.assertEqual(first, second)
                self.assertTrue(first["available"])
                operation = staged["operations"][0]
                self.assertEqual(
                    "base café\n".encode(),
                    store.blob_path(operation["base_sha256"]).read_bytes(),
                )
                self.assertEqual(
                    "proposed café\n".encode(),
                    store.blob_path(operation["proposed_sha256"]).read_bytes(),
                )
            finally:
                store.close()

    def test_v2_actions_require_the_exact_review_hash_and_v1_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            try:
                source = world / "source/lore.txt"
                source.write_text("base\n", encoding="utf-8")
                manager = ChangesetManager(store)
                staged = self._replace(manager, workspace_id)

                with self.assertRaisesRegex(StudioError, "expected_review_sha256"):
                    manager.approve(staged["changeset_id"])
                with self.assertRaisesRegex(StudioError, "review hash"):
                    manager.approve(staged["changeset_id"], expected_review_sha256="0" * 64)
                approved = manager.approve(
                    staged["changeset_id"], expected_review_sha256=staged["review_sha256"]
                )
                with self.assertRaisesRegex(StudioError, "expected_review_sha256"):
                    manager.apply(approved["changeset_id"])

                legacy = {
                    **staged,
                    "format_version": 1,
                    "operations": [
                        {
                            key: value
                            for key, value in staged["operations"][0].items()
                            if key != "base_size"
                        }
                    ],
                }
                legacy.pop("review_sha256")
                legacy["changeset_id"] = "legacy_change"
                legacy["status"] = "staged"
                with store.connection:
                    store.connection.execute(
                        "INSERT INTO changesets "
                        "(changeset_id, workspace_id, status, record_json) VALUES (?, ?, ?, ?)",
                        ("legacy_change", workspace_id, "staged", encode_json(legacy)),
                    )
                unavailable = manager.diff("legacy_change")
                self.assertEqual(
                    {
                        "changeset_id": "legacy_change",
                        "changeset_format_version": 1,
                        "available": False,
                        "unavailable_reason": "legacy_base_bytes_not_retained",
                        "review_sha256": None,
                        "operations": [],
                    },
                    unavailable,
                )
                self.assertEqual("approved", manager.approve("legacy_change")["status"])
            finally:
                store.close()

    def test_diff_rejects_missing_or_changed_owned_cas_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            try:
                (world / "source/lore.txt").write_text("base\n", encoding="utf-8")
                manager = ChangesetManager(store)
                first = self._replace(manager, workspace_id)
                proposed = store.blob_path(first["operations"][0]["proposed_sha256"])
                proposed.unlink()
                with self.assertRaisesRegex(StudioError, "blob"):
                    manager.diff(first["changeset_id"])

                second = self._replace(manager, workspace_id)
                base = store.blob_path(second["operations"][0]["base_sha256"])
                base.write_bytes(b"other\n")
                with self.assertRaisesRegex(StudioError, "blob"):
                    manager.diff(second["changeset_id"])
            finally:
                store.close()

    def test_apply_claim_blocks_reject_and_orphan_without_journal_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            (world / "source/lore.txt").write_text("base\n", encoding="utf-8")
            manager = ChangesetManager(store)
            staged = self._replace(manager, workspace_id)
            review = staged["review_sha256"]
            manager.approve(staged["changeset_id"], expected_review_sha256=review)

            with StudioStore(temp / "data", mode="secondary") as secondary:
                observer = ChangesetManager(secondary, recover=False)
                original_prepare = manager._prepare_journal

                def reject_after_claim(*args: object, **kwargs: object) -> dict[str, object]:
                    with self.assertRaisesRegex(StudioError, "applying"):
                        observer.reject(staged["changeset_id"], expected_review_sha256=review)
                    return original_prepare(*args, **kwargs)

                with patch.object(manager, "_prepare_journal", side_effect=reject_after_claim):
                    applied = manager.apply(staged["changeset_id"], expected_review_sha256=review)
                self.assertEqual("applied", applied["status"])

            orphan = manager.create(
                {
                    "workspace_id": workspace_id,
                    "operations": [
                        {
                            "path": "source/orphan.txt",
                            "operation": "create",
                            "content": "orphan\n",
                        }
                    ],
                }
            )
            manager.approve(orphan["changeset_id"], expected_review_sha256=orphan["review_sha256"])
            with patch.object(manager, "_write_journal", side_effect=SystemExit("crash")):
                with self.assertRaises(SystemExit):
                    manager.apply(
                        orphan["changeset_id"],
                        expected_review_sha256=orphan["review_sha256"],
                    )
            self.assertEqual("applying", manager.get(orphan["changeset_id"])["status"])
            store.close()

            with StudioStore(temp / "data") as reopened:
                recovered = ChangesetManager(reopened)
                self.assertEqual("approved", recovered.get(orphan["changeset_id"])["status"])
                self.assertFalse((world / "source/orphan.txt").exists())

    def test_recovery_rejects_applied_record_before_touching_noncommitted_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            manager = ChangesetManager(store)
            staged = manager.create(
                {
                    "workspace_id": workspace_id,
                    "operations": [
                        {
                            "path": "source/recovery-guard.txt",
                            "operation": "create",
                            "content": "owned stage\n",
                        }
                    ],
                }
            )
            approved = manager.approve(
                staged["changeset_id"], expected_review_sha256=staged["review_sha256"]
            )
            claimed = manager._claim_apply(approved)
            identity = WorkspaceManager(store).root_identity(workspace_id, "world_root")
            assert identity is not None
            journal_path = store.journals_dir / f"{staged['changeset_id']}.json"
            with exclusive_world_lifecycle(world):
                journal = manager._prepare_journal(claimed, world, identity)
                manager._write_journal(journal_path, journal)
                with _pinned_operation_parents(journal, world) as parents:
                    manager._prepare_stages(journal_path, journal, parents)
                    journal["state"] = "prepared"
                    manager._write_journal(journal_path, journal)
            stage_path = world / "source" / journal["operations"][0]["stage_name"]
            self.assertEqual(b"owned stage\n", stage_path.read_bytes())
            applied = {**claimed, "status": "applied"}
            with store.connection:
                store.connection.execute(
                    "UPDATE changesets SET status = 'applied', record_json = ? "
                    "WHERE changeset_id = ?",
                    (encode_json(applied), staged["changeset_id"]),
                )
            store.close()

            reopened = StudioStore(temp / "data")
            try:
                with self.assertRaisesRegex(StudioError, "incompatible"):
                    ChangesetManager(reopened)
                self.assertEqual(b"owned stage\n", stage_path.read_bytes())
                self.assertTrue(journal_path.is_file())
            finally:
                reopened.close()

    def test_recovery_never_unlinks_unverified_stage_without_recorded_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            manager = ChangesetManager(store)
            staged = manager.create(
                {
                    "workspace_id": workspace_id,
                    "operations": [
                        {
                            "path": "source/recovery-stage.txt",
                            "operation": "create",
                            "content": "authorized\n",
                        }
                    ],
                }
            )
            approved = manager.approve(
                staged["changeset_id"], expected_review_sha256=staged["review_sha256"]
            )
            claimed = manager._claim_apply(approved)
            identity = WorkspaceManager(store).root_identity(workspace_id, "world_root")
            assert identity is not None
            journal_path = store.journals_dir / f"{staged['changeset_id']}.json"
            with exclusive_world_lifecycle(world):
                journal = manager._prepare_journal(claimed, world, identity)
                manager._write_journal(journal_path, journal)
            operation = journal["operations"][0]
            self.assertIsNone(operation["stage_identity"])
            stage_path = world / "source" / operation["stage_name"]
            stage_path.write_bytes(b"unowned replacement\n")
            store.close()

            reopened = StudioStore(temp / "data")
            try:
                with self.assertRaisesRegex(StudioError, "stage changed"):
                    ChangesetManager(reopened)
                self.assertEqual(b"unowned replacement\n", stage_path.read_bytes())
                self.assertTrue(journal_path.is_file())
                self.assertEqual(
                    "applying",
                    ChangesetManager(reopened, recover=False).get(staged["changeset_id"])["status"],
                )
            finally:
                reopened.close()

    def test_recovery_cleans_authorized_stage_without_recorded_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world, store, workspace_id = self._workspace(temp)
            manager = ChangesetManager(store)
            staged = manager.create(
                {
                    "workspace_id": workspace_id,
                    "operations": [
                        {
                            "path": "source/recovery-owned-stage.txt",
                            "operation": "create",
                            "content": "authorized\n",
                        }
                    ],
                }
            )
            approved = manager.approve(
                staged["changeset_id"], expected_review_sha256=staged["review_sha256"]
            )
            claimed = manager._claim_apply(approved)
            identity = WorkspaceManager(store).root_identity(workspace_id, "world_root")
            assert identity is not None
            journal_path = store.journals_dir / f"{staged['changeset_id']}.json"
            with exclusive_world_lifecycle(world):
                journal = manager._prepare_journal(claimed, world, identity)
                manager._write_journal(journal_path, journal)
            operation = journal["operations"][0]
            self.assertIsNone(operation["stage_identity"])
            stage_path = world / "source" / operation["stage_name"]
            stage_path.write_bytes(b"authorized\n")
            store.close()

            with StudioStore(temp / "data") as reopened:
                recovered = ChangesetManager(reopened)
                self.assertEqual("approved", recovered.get(staged["changeset_id"])["status"])
                self.assertFalse(stage_path.exists())
                self.assertFalse(journal_path.exists())
                self.assertFalse((world / "source/recovery-owned-stage.txt").exists())


if __name__ == "__main__":
    unittest.main()
