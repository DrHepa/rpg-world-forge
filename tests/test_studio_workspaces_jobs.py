from __future__ import annotations

import ctypes
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import worldforge.studio.workspaces as workspaces_module
from worldforge.repository_boundary import FORGE_ROOT
from worldforge.retained_tree import _WindowsTreeApi
from worldforge.scaffold import create_world_project
from worldforge.studio.errors import StudioError
from worldforge.studio.jobs import JobManager
from worldforge.studio.storage import StudioStore, encode_json
from worldforge.studio.workspaces import (
    WorkspaceManager,
    _overlaps,
    _pinned_ancestor_identities,
)


class StudioWorkspacesAndJobsTests(unittest.TestCase):
    def test_windows_workspace_walker_reuses_retained_tree_open_contract(self) -> None:
        self.assertTrue(issubclass(workspaces_module._WindowsRelativeDirectoryApi, _WindowsTreeApi))
        share_mode = getattr(
            workspaces_module._WindowsRelativeDirectoryApi,
            "_FILE_SHARE_MODE",
            workspaces_module._WindowsRelativeDirectoryApi._FILE_SHARE_ALL,
        )
        self.assertEqual(0x00000001 | 0x00000002, share_mode)
        self.assertFalse(share_mode & 0x00000004)  # FILE_SHARE_DELETE
        self.assertEqual(
            _WindowsTreeApi._FILE_OPEN_FOR_BACKUP_INTENT,
            workspaces_module._WindowsRelativeDirectoryApi._FILE_OPEN_FOR_BACKUP_INTENT,
        )

    def test_windows_workspace_anchor_allows_delete_only_at_external_drive_anchor(self) -> None:
        api = object.__new__(workspaces_module._WindowsRelativeDirectoryApi)
        api._invalid_handle = ctypes.c_void_p(-1).value
        captured: list[tuple[str, int, int, int, int]] = []

        def create_file(
            path: str,
            access: int,
            share: int,
            _security: object,
            disposition: int,
            flags: int,
            _template: object,
        ) -> int:
            captured.append((path, access, share, disposition, flags))
            return 91

        api._create_file = create_file
        api.state = lambda *_args, **_kwargs: SimpleNamespace()

        anchor = Path("D:/")
        self.assertEqual(91, api.open_anchor(anchor, context="candidate artifact root"))
        self.assertEqual(1, len(captured))
        path, _access, share, disposition, flags = captured[0]
        self.assertEqual(str(anchor), path)
        self.assertEqual(api._FILE_SHARE_ALL, share)
        self.assertTrue(share & 0x00000004)  # FILE_SHARE_DELETE
        self.assertEqual(api._OPEN_EXISTING, disposition)
        self.assertTrue(flags & api._FILE_FLAG_OPEN_REPARSE_POINT)
        self.assertTrue(flags & api._FILE_FLAG_BACKUP_SEMANTICS)

    def test_windows_workspace_walker_maps_ntstatus_without_exposing_the_root(self) -> None:
        api = object.__new__(workspaces_module._WindowsRelativeDirectoryApi)
        api._invalid_handle = ctypes.c_void_p(-1).value
        denied = ctypes.c_int32(0xC0000043).value
        mapped_statuses: list[int] = []
        shares: list[int] = []

        def denied_open(*args: object) -> int:
            shares.append(int(args[6]))
            return denied

        api._nt_create_file = denied_open

        def map_status(status: ctypes.c_long) -> int:
            mapped_statuses.append(int(status.value))
            return 32

        api._rtl_nt_status_to_dos_error = map_status
        with (
            patch.object(
                ctypes,
                "WinError",
                return_value=OSError(13, "sharing violation"),
                create=True,
            ),
            self.assertRaisesRegex(
                OSError,
                "could not retain Windows tree entry candidate",
            ) as caught,
        ):
            api.open_relative(
                17,
                "candidate",
                context="candidate artifact root D:\\a\\_temp",
                directory=True,
            )

        self.assertEqual([denied], mapped_statuses)
        self.assertEqual([0x00000001 | 0x00000002], shares)
        self.assertNotIn("D:\\a\\_temp", str(caught.exception))

    def test_windows_workspace_ancestry_rejects_visible_parent_substitution(self) -> None:
        candidate_path = Path.cwd() / "runner" / "_temp" / "candidate"
        original = ((7, 1), (7, 2), (7, 3))
        substituted = ((7, 1), (7, 20), (7, 30))
        walks = iter(
            (
                ([11, 12, 13], original),
                ([21, 22, 23], substituted),
            )
        )
        api = SimpleNamespace()

        with (
            patch.object(workspaces_module.os, "name", "nt"),
            patch.object(
                workspaces_module,
                "_WindowsRelativeDirectoryApi",
                return_value=api,
            ),
            patch.object(
                workspaces_module,
                "_open_windows_ancestry",
                side_effect=lambda *_args, **_kwargs: next(walks),
            ) as open_ancestry,
            patch.object(workspaces_module, "_close_windows_handles") as close_handles,
            self.assertRaisesRegex(StudioError, "identity changed"),
        ):
            with _pinned_ancestor_identities(
                candidate_path,
                context="candidate artifact root",
            ) as identities:
                self.assertEqual(original, identities)

        self.assertEqual(2, open_ancestry.call_count)
        self.assertEqual(
            [([21, 22, 23],), ([11, 12, 13],)],
            [call.args[1:] for call in close_handles.call_args_list],
        )

    def test_windows_workspace_ancestry_blocks_aba_swap_use_restore(self) -> None:
        left = Path.cwd() / "runner" / "_temp" / "world"
        right = left / "child"
        original_left = ((1, 1), (7, 10), (7, 20))
        replacement_right = ((1, 1), (7, 99), (7, 100), (7, 101))
        api = object.__new__(workspaces_module._WindowsRelativeDirectoryApi)
        events: list[str] = []
        walk_index = 0

        def walk(
            active_api: object,
            _path: Path,
            *,
            context: str,
        ) -> tuple[list[int], tuple[tuple[int, int], ...]]:
            nonlocal walk_index
            self.assertIs(api, active_api)
            current = walk_index
            walk_index += 1
            if current == 0:
                events.append("original_pinned")
                return [11, 12, 13], original_left
            if current == 1:
                events.append("swap_attempted")
                share_mode = getattr(active_api, "_FILE_SHARE_MODE", active_api._FILE_SHARE_ALL)
                if not share_mode & 0x00000004:  # FILE_SHARE_DELETE
                    raise PermissionError("retained ancestor blocks the rename")
                events.append("replacement_used")
                return [21, 22, 23, 24], replacement_right
            if current == 2:
                events.append("replacement_verified")
                return [31, 32, 33, 34], replacement_right
            if current == 3:
                events.append("original_restored")
                return [41, 42, 43], original_left
            self.fail(f"unexpected Windows ancestry walk for {context}")

        with (
            patch.object(workspaces_module.os, "name", "nt"),
            patch.object(
                workspaces_module,
                "_WindowsRelativeDirectoryApi",
                return_value=api,
            ),
            patch.object(workspaces_module, "_open_windows_ancestry", side_effect=walk),
            patch.object(workspaces_module, "_close_windows_handles"),
            self.assertRaisesRegex(StudioError, "Could not inspect workspace boundary"),
        ):
            _overlaps(left, right)

        self.assertEqual(["original_pinned", "swap_attempted"], events)

    def test_overlap_uses_directory_identity_across_path_aliases(self) -> None:
        short_data = Path("short-alias/world/.studio-data")
        long_world = Path("long-alias/world")
        shared_world_identity = (7, 11)

        @contextmanager
        def identities(path: Path, *, context: str):
            del context
            if path == short_data:
                yield ((1, 1), shared_world_identity, (7, 12))
            else:
                yield ((2, 2), shared_world_identity)

        with patch(
            "worldforge.studio.workspaces._pinned_ancestor_identities",
            side_effect=identities,
        ):
            self.assertTrue(_overlaps(short_data, long_world))

    @unittest.skipUnless(os.name == "posix", "POSIX no-follow ancestry required")
    def test_overlap_walks_root_to_leaf_and_rejects_linked_ancestors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            real = temp / "real"
            nested = real / "nested"
            nested.mkdir(parents=True)
            expected: list[object] = [Path(nested.anchor), *nested.parts[1:]]
            observed: list[object] = []
            real_open = os.open

            def record_open(
                path: os.PathLike[str] | str,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                observed.append(path)
                if dir_fd is None:
                    return real_open(path, flags, mode)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with patch(
                "worldforge.studio.workspaces.os.open",
                side_effect=record_open,
            ):
                with _pinned_ancestor_identities(nested, context="test ancestry") as identities:
                    self.assertEqual(len(expected), len(identities))
            self.assertEqual(expected, observed[: len(expected)])

            alias = temp / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(StudioError):
                _overlaps(alias / "nested", real)

    def test_registers_canonical_roots_and_rejects_duplicates_and_nesting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world = temp / "world"
            create_world_project(world, world_id="studio_world", title="Studio", language="en")
            with StudioStore(temp / "data") as store:
                manager = WorkspaceManager(store)
                record = manager.register(
                    {
                        "workspace_id": "workspace_01",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(world),
                    }
                )
                self.assertEqual(str(world.resolve()), record["world_root"])
                self.assertEqual(record, manager.get("workspace_01"))
                self.assertEqual([record], manager.list())

                generated = manager.register(
                    {
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(
                            self._make_second_world(temp, world_id="generated_world")
                        ),
                    }
                )
                self.assertRegex(generated["workspace_id"], r"^[a-z][a-z0-9_-]{1,63}$")

                with self.assertRaisesRegex(StudioError, "already registered"):
                    manager.register(
                        {
                            "workspace_id": "workspace_02",
                            "forge_root": str(FORGE_ROOT),
                            "world_root": str(world),
                        }
                    )

                (world / "game").mkdir()
                with self.assertRaisesRegex(StudioError, "overlap"):
                    manager.register(
                        {
                            "workspace_id": "workspace_03",
                            "forge_root": str(FORGE_ROOT),
                            "world_root": str(world),
                            "game_root": str(world / "game"),
                        }
                    )

            with StudioStore(world / ".studio-data") as nested_store:
                with self.assertRaisesRegex(StudioError, "data directory"):
                    WorkspaceManager(nested_store).register(
                        {
                            "workspace_id": "workspace_04",
                            "forge_root": str(FORGE_ROOT),
                            "world_root": str(world),
                        }
                    )

    @staticmethod
    def _make_second_world(temp: Path, *, world_id: str) -> Path:
        world = temp / world_id
        create_world_project(world, world_id=world_id, title="Generated", language="en")
        return world

    def test_job_state_machine_and_startup_orphaning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world = temp / "world"
            create_world_project(world, world_id="studio_world", title="Studio", language="en")
            data_dir = temp / "data"
            with StudioStore(data_dir) as store:
                workspace = WorkspaceManager(store).register(
                    {
                        "workspace_id": "workspace_01",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(world),
                    }
                )
                jobs = JobManager(store)
                job = jobs.create(
                    {
                        "workspace_id": workspace["workspace_id"],
                        "operation": "runtime.headless",
                        "input": {"worldpack": "build/worldpack.json", "ticks": 0},
                    }
                )
                self.assertEqual(2, job["format_version"])
                running = jobs.claim_next()
                self.assertIsNotNone(running)
                assert running is not None
                self.assertEqual("running", running["state"])
                with self.assertRaisesRegex(StudioError, "owned by the Studio executor"):
                    jobs.transition(job["job_id"], {"state": "succeeded", "result": {}})

            with StudioStore(data_dir) as reopened:
                orphaned = JobManager(reopened).get(job["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                canceled = JobManager(reopened).cancel(job["job_id"])
                self.assertEqual("canceled", canceled["state"])

    def test_claim_skips_legacy_v1_and_preserves_managed_v2_fifo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world = temp / "world"
            create_world_project(world, world_id="studio_world", title="Studio", language="en")
            with StudioStore(temp / "data") as store:
                WorkspaceManager(store).register(
                    {
                        "workspace_id": "workspace_01",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(world),
                    }
                )
                jobs = JobManager(store)
                timestamp = "2026-07-22T12:00:00Z"

                def insert_legacy(
                    job_id: str,
                    operation: str,
                    job_input: dict[str, object],
                    *,
                    state: str = "queued",
                ) -> None:
                    record = {
                        "format": "rpg-world-forge.studio_job",
                        "format_version": 1,
                        "job_id": job_id,
                        "workspace_id": "workspace_01",
                        "operation": operation,
                        "state": state,
                        "input": job_input,
                        "result": None,
                        "error": None,
                        "created_at": timestamp,
                        "updated_at": timestamp,
                    }
                    store.connection.execute(
                        "INSERT INTO jobs "
                        "(job_id, workspace_id, state, record_json) VALUES (?, ?, ?, ?)",
                        (job_id, "workspace_01", state, encode_json(record)),
                    )
                    store.connection.commit()

                insert_legacy(
                    "legacy_running",
                    "forge.validate",
                    {"profile": "release"},
                    state="running",
                )
                self.assertEqual("canceled", jobs.cancel("legacy_running")["state"])
                insert_legacy("legacy_first", "forge.validate", {"profile": "release"})
                managed_first = jobs.create(
                    {
                        "job_id": "managed_first",
                        "workspace_id": "workspace_01",
                        "operation": "runtime.headless",
                        "input": {"worldpack": "build/first.json", "ticks": 0},
                    }
                )
                insert_legacy(
                    "legacy_managed_name",
                    "runtime.headless",
                    {"legacy_command": "headless --old-contract"},
                )
                managed_second = jobs.create(
                    {
                        "job_id": "managed_second",
                        "workspace_id": "workspace_01",
                        "operation": "runtime.headless",
                        "input": {"worldpack": "build/second.json", "ticks": 0},
                    }
                )
                store.connection.commit()

                first_claim = jobs.claim_next()
                self.assertIsNotNone(first_claim)
                assert first_claim is not None
                self.assertEqual(managed_first["job_id"], first_claim["job_id"])
                jobs.finish(first_claim["job_id"], "canceled")
                second_claim = jobs.claim_next()
                self.assertIsNotNone(second_claim)
                assert second_claim is not None
                self.assertEqual(managed_second["job_id"], second_claim["job_id"])
                jobs.finish(second_claim["job_id"], "canceled")
                self.assertIsNone(jobs.claim_next())

                self.assertEqual("queued", jobs.get("legacy_first")["state"])
                self.assertEqual("queued", jobs.get("legacy_managed_name")["state"])
                self.assertEqual("canceled", jobs.cancel("legacy_first")["state"])
                self.assertEqual("canceled", jobs.cancel("legacy_managed_name")["state"])


if __name__ == "__main__":
    unittest.main()
