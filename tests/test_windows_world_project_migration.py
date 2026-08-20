from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class _InjectedCrash(RuntimeError):
    pass


@dataclass
class _FakeWindowsFile:
    payload: bytes
    identity: tuple[int, int]
    change_time_ns: int


class _FakeWindowsNativeApi:
    def __init__(self, source: bytes) -> None:
        self.entries = {
            "project.json": _FakeWindowsFile(source, (7, 101), 1_000),
        }
        self.handles: dict[int, _FakeWindowsFile] = {}
        self.open_names: dict[int, str] = {}
        self.handle_write_access: dict[int, bool] = {}
        self.handle_delete_access: dict[int, bool] = {}
        self.handle_share_write: dict[int, bool] = {}
        self.handle_share_delete: dict[int, bool] = {}
        self.open_contracts: list[tuple[str, bool, bool, bool, bool, bool]] = []
        self.next_handle = 10
        self.next_identity = 200
        self.events: list[str] = []
        self.fail_sealed_name: str | None = None
        self.mutate_change_time_on_sealed_source_open = False
        self.fail_strict_read = False
        self.fail_close_after_disposition = False
        self.fail_absence_open = False
        self.fail_verification_close = False
        self.retain_name_after_disposition = False
        self.verification_handles: set[int] = set()
        self.fail_close_names: set[str] = set()
        self.legacy_identity_reads = 0
        self.disposition_attempted = False
        self.fail_rename_once = False

    def _open(
        self,
        name: str,
        *,
        write: bool = False,
        delete: bool = False,
        share_write: bool = True,
        share_delete: bool = False,
    ) -> int:
        try:
            entry = self.entries[name]
        except KeyError as exc:
            raise FileNotFoundError(name) from exc
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = entry
        self.open_names[handle] = name
        self.handle_write_access[handle] = write
        self.handle_delete_access[handle] = delete
        self.handle_share_write[handle] = share_write
        self.handle_share_delete[handle] = share_delete
        return handle

    def _require_share_compatible(
        self,
        entry: _FakeWindowsFile,
        *,
        write: bool,
        delete: bool,
        share_write: bool,
        share_delete: bool,
    ) -> None:
        from worldforge.asset_io import AssetContractError

        for handle, opened in self.handles.items():
            if opened is not entry:
                continue
            if write and not self.handle_share_write[handle]:
                raise AssetContractError("injected Windows sharing violation for write access")
            if delete and not self.handle_share_delete[handle]:
                raise AssetContractError("injected Windows sharing violation for delete access")
            if self.handle_write_access[handle] and not share_write:
                raise AssetContractError("injected Windows sharing violation for shared write")
            if self.handle_delete_access[handle] and not share_delete:
                raise AssetContractError("injected Windows sharing violation for shared delete")

    def _links(self, entry: _FakeWindowsFile) -> int:
        return sum(value is entry for value in self.entries.values())

    def migration_volume_capabilities(self, _root_handle: int, _root_path: Path):
        from worldforge.windows_project_migration import WindowsMigrationCapabilities

        return WindowsMigrationCapabilities(
            platform="nt",
            filesystem="NTFS",
            local_fixed_volume=True,
            file_id_128=True,
            hard_links=True,
            posix_unlink_rename=True,
            flushable_directories=True,
            rename_info_ex=True,
            disposition_info_ex=True,
        )

    def open_existing_file_strict(
        self,
        _parent: int,
        name: str,
        *,
        sealed: bool = False,
        delete: bool = False,
        share_delete: bool = False,
        write: bool = False,
    ) -> int:
        share_write = not (sealed or delete or write)
        self.events.append(
            f"open:{name}:sealed={sealed}:delete={delete}:share_delete={share_delete}:write={write}"
        )
        self.open_contracts.append((name, sealed, delete, share_write, share_delete, write))
        if sealed and name == self.fail_sealed_name:
            from worldforge.windows_project_migration import WindowsMigrationStateError

            raise WindowsMigrationStateError(f"injected seal failure for {name}")
        if sealed and delete and name == "project.json":
            if self.mutate_change_time_on_sealed_source_open:
                self.entries[name].change_time_ns += 1
        if share_delete and self.fail_absence_open:
            raise RuntimeError("injected absence verification failure")
        entry = self.entries.get(name)
        if entry is not None:
            self._require_share_compatible(
                entry,
                write=write,
                delete=delete,
                share_write=share_write,
                share_delete=share_delete,
            )
        handle = self._open(
            name,
            write=write,
            delete=delete,
            share_write=share_write,
            share_delete=share_delete,
        )
        if share_delete:
            self.verification_handles.add(handle)
        return handle

    def read_strict_bound_bytes(
        self,
        handle: int,
        *,
        limit: int,
        context: str,
    ):
        from worldforge.asset_io import AssetContractError, BoundFileBytes

        self.events.append(f"read-strict:{context}")
        if self.fail_strict_read:
            raise AssetContractError("Strict Windows 128-bit FileIdInfo is unavailable")

        entry = self.handles[handle]
        if len(entry.payload) > limit:
            raise ValueError(context)
        return (
            BoundFileBytes(
                entry.payload,
                entry.identity,
                len(entry.payload),
                entry.change_time_ns,
            ),
            self._links(entry),
        )

    def strict_entry_info(self, handle: int, *, context: str):
        entry = self.handles[handle]
        return SimpleNamespace(
            st_dev=entry.identity[0],
            st_ino=entry.identity[1],
            st_nlink=self._links(entry),
            st_size=len(entry.payload),
            st_ctime_ns=entry.change_time_ns,
        )

    def entry_info(self, handle: int, *, context: str):
        self.legacy_identity_reads += 1
        return self.strict_entry_info(handle, context=context)

    def create_file(self, _parent: int, name: str) -> int:
        if name in self.entries:
            raise FileExistsError(name)
        entry = _FakeWindowsFile(b"", (7, self.next_identity), 2_000)
        self.next_identity += 1
        self.entries[name] = entry
        self.events.append(f"create:{name}")
        return self._open(name, write=True)

    def write_strict_bytes(self, handle: int, payload: bytes, *, context: str) -> None:
        entry = self.handles[handle]
        entry.payload = payload
        entry.change_time_ns += 1
        self.events.append(f"write:{context}")

    def append_strict_journal_frame(
        self,
        handle: int,
        *,
        expected_size: int,
        truncate_to: int | None,
        frame: bytes,
        context: str,
    ) -> None:
        entry = self.handles[handle]
        if len(entry.payload) != expected_size:
            raise AssertionError("journal size changed before append")
        if truncate_to is not None:
            entry.payload = entry.payload[:truncate_to]
        entry.payload += frame
        entry.change_time_ns += 1
        self.events.append(f"append-strict:{context}")

    def flush_handle(self, handle: int, *, context: str) -> None:
        if not self.handle_write_access.get(handle, False):
            from worldforge.asset_io import AssetContractError

            raise AssetContractError("injected flush access denied without GENERIC_WRITE")
        self.events.append(f"flush:{context}:{handle}")

    def create_source_hard_link(self, destination: Path, source: Path) -> None:
        if destination.name in self.entries:
            raise FileExistsError(destination.name)
        self.entries[destination.name] = self.entries[source.name]
        self.entries[source.name].change_time_ns += 1
        self.events.append("retain")

    def rename_ex(self, handle: int, _parent_handle: int, destination_name: str) -> None:
        if self.fail_rename_once:
            self.fail_rename_once = False
            from worldforge.asset_io import AssetContractError

            raise AssetContractError("injected FileRenameInfoEx failure")
        entry = self.handles[handle]
        destination = self.entries.get(destination_name)
        if destination is not None:
            for opened_handle, opened_entry in self.handles.items():
                if opened_entry is destination and not self.handle_share_delete[opened_handle]:
                    from worldforge.asset_io import AssetContractError

                    raise AssetContractError(
                        "injected Windows sharing violation 32 for destination replacement"
                    )
        source_name = next(
            name
            for name, candidate in self.entries.items()
            if candidate is entry and name != destination_name
        )
        self.entries.pop(destination_name, None)
        self.entries[destination_name] = entry
        del self.entries[source_name]
        self.events.append("rename-ex")

    def dispose_ex(self, handle: int) -> None:
        self.disposition_attempted = True
        entry = self.handles[handle]
        retained = [name for name, candidate in self.entries.items() if candidate is entry]
        if len(retained) != 1:
            raise AssertionError(retained)
        if not self.retain_name_after_disposition:
            del self.entries[retained[0]]
        self.events.append("dispose-ex")

    def close(self, handle: int) -> None:
        self.events.append(f"close:{handle}")
        if self.disposition_attempted and self.fail_close_after_disposition:
            from worldforge.asset_io import AssetContractError

            raise AssetContractError("injected close failure after disposition")
        if handle in self.verification_handles and self.fail_verification_close:
            raise RuntimeError("injected verification handle close failure")
        if self.open_names.get(handle) in self.fail_close_names:
            raise RuntimeError("injected retained evidence close failure")
        self.handles.pop(handle, None)
        self.open_names.pop(handle, None)
        self.handle_write_access.pop(handle, None)
        self.handle_delete_access.pop(handle, None)
        self.handle_share_write.pop(handle, None)
        self.handle_share_delete.pop(handle, None)


class _FakeWindowsLease:
    def __init__(self, api: _FakeWindowsNativeApi) -> None:
        self.root = Path("C:/world")
        self.control_path = self.root / ".worldforge"
        self.root_handle = 1
        self.control_handle = 2
        self.api = api
        self.assertions = 0
        self.flushes = 0
        self.fail_flush = False
        self.fail_assertion_at: int | None = None

    def assert_current(self) -> None:
        self.assertions += 1
        if self.assertions == self.fail_assertion_at:
            from worldforge.workflow import WorkflowError

            raise WorkflowError("injected retained ancestry failure")

    def flush_control(self) -> None:
        self.flushes += 1
        self.api.events.append("flush-control")
        if self.fail_flush:
            from worldforge.asset_io import AssetContractError

            raise AssetContractError("injected directory flush failure after disposition")
        self.assert_current()


class _FakeCommitApi:
    def __init__(
        self,
        *,
        state: str = "source_only",
        capability_error: Exception | None = None,
        seal_error: Exception | None = None,
        stage_error: Exception | None = None,
        publish_error: Exception | None = None,
        ambiguous_after_publish: bool = False,
    ) -> None:
        self.state = state
        self.capability_error = capability_error
        self.seal_error = seal_error
        self.stage_error = stage_error
        self.publish_error = publish_error
        self.ambiguous_after_publish = ambiguous_after_publish
        self.events: list[str] = []

    def preflight(self) -> None:
        self.events.append("preflight")
        if self.capability_error is not None:
            raise self.capability_error

    def observe(self):
        from worldforge.windows_project_migration import WindowsCommitObservation

        self.events.append("observe")
        values = {
            "source_only": ("source", None, None, 1, None),
            "staged": ("source", None, "target", 1, None),
            "retained": ("source", "source", "target", 2, 2),
            "committed": ("target", "source", None, 1, 1),
            "ambiguous": ("other", "source", None, None, 1),
        }[self.state]
        return WindowsCommitObservation(
            visible_role=values[0],
            retained_role=values[1],
            staged_role=values[2],
            visible_link_count=values[3],
            retained_link_count=values[4],
            staged_link_count=1 if values[2] is not None else None,
        )

    def seal_source_share_read_only(self) -> None:
        self.events.append("seal:share-read-only")
        if self.seal_error is not None:
            raise self.seal_error

    def release_source_seal(self) -> None:
        self.events.append("release-seal")

    def create_durable_target_stage(self) -> None:
        self.events.append("stage-target")
        if self.stage_error is not None:
            raise self.stage_error
        self.state = "staged"

    def create_durable_source_retention(self) -> None:
        self.events.append("retain-source")
        self.state = "retained"

    def publish_target_stage(self) -> None:
        self.events.append("publish-target")
        if self.ambiguous_after_publish:
            self.state = "ambiguous"
        elif self.publish_error is None:
            self.state = "committed"
        if self.publish_error is not None:
            raise self.publish_error

    def verify_and_flush_committed(self) -> None:
        self.events.append("verify-flush-commit")


class WindowsProjectMigrationPolicyTests(unittest.TestCase):
    def test_strict_windows_record_removal_uses_file_id_info_and_disposition_ex(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import canonical_journal_record

        payload = canonical_journal_record({"record": "backup"})
        native = _FakeWindowsNativeApi(b"unused")
        native.entries["project-migration.backup.json"] = _FakeWindowsFile(
            payload,
            (7, 303),
            3_000,
        )
        lease = _FakeWindowsLease(native)

        migration._remove_windows_migration_record(
            lease,
            name="project-migration.backup.json",
            expected_identity=(7, 303),
            expected_payload=payload,
            expected_history=None,
            max_record_bytes=8 * 1024 * 1024,
            max_file_bytes=32 * 1024 * 1024,
            context="backup",
        )

        self.assertNotIn("project-migration.backup.json", native.entries)
        self.assertEqual(2, sum(event.startswith("read-strict:") for event in native.events))
        self.assertIn("dispose-ex", native.events)
        self.assertIn("flush-control", native.events)
        disposition_index = native.events.index("dispose-ex")
        close_index = next(
            index
            for index, event in enumerate(native.events)
            if index > disposition_index and event.startswith("close:")
        )
        self.assertLess(disposition_index, close_index)
        self.assertLess(close_index, native.events.index("flush-control"))

    def test_strict_windows_record_removal_never_uses_a_legacy_identity_fallback(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import canonical_journal_record
        from worldforge.directory_publish import DirectoryPublishError

        payload = canonical_journal_record({"record": "journal"})
        native = _FakeWindowsNativeApi(b"unused")
        native.entries["project-migration.journal.json"] = _FakeWindowsFile(
            payload,
            (7, 304),
            3_100,
        )
        native.fail_strict_read = True

        with self.assertRaisesRegex(DirectoryPublishError, "128-bit FileIdInfo"):
            migration._remove_windows_migration_record(
                _FakeWindowsLease(native),
                name="project-migration.journal.json",
                expected_identity=(7, 304),
                expected_payload=payload,
                expected_history=None,
                max_record_bytes=8 * 1024 * 1024,
                max_file_bytes=32 * 1024 * 1024,
                context="journal",
            )

        self.assertNotIn("dispose-ex", native.events)
        self.assertFalse(native.disposition_attempted)

    def test_windows_authorization_loads_reject_legacy_identity_fallback(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import canonical_journal_record

        cases = (
            (
                "backup",
                "project-migration.backup.json",
                lambda path, lease: migration._read_backup(
                    path,
                    expected_source_hash="a" * 64,
                    lease=lease,
                ),
            ),
            (
                "journal",
                "project-migration.journal.json",
                lambda path, lease: migration._read_journal(
                    path,
                    source_hash="a" * 64,
                    target_hash="b" * 64,
                    backup_identity=(7, 1),
                    operation_id="c" * 64,
                    lease=lease,
                ),
            ),
            (
                "cleanup journal",
                "project-migration.journal.json",
                lambda path, lease: migration._read_journal_after_backup_cleanup(
                    path,
                    expected_source_hash="a" * 64,
                    lease=lease,
                ),
            ),
            (
                "evidence",
                "project-migration-v3.evidence.json",
                lambda path, lease: migration._read_evidence(
                    path,
                    source_hash="a" * 64,
                    target_hash="b" * 64,
                    target_identity=(7, 2),
                    operation_id="c" * 64,
                    lease=lease,
                ),
            ),
        )
        for context, name, load in cases:
            with self.subTest(context=context):
                native = _FakeWindowsNativeApi(b"unused")
                native.entries[name] = _FakeWindowsFile(
                    canonical_journal_record({"record": context}),
                    (7, 401),
                    4_000,
                )
                native.fail_strict_read = True
                lease = _FakeWindowsLease(native)

                with patch(
                    "worldforge.world_project_migration.WindowsRetainedWorldLifecycle",
                    _FakeWindowsLease,
                ):
                    with self.assertRaises(migration.WorldProjectMigrationError) as raised:
                        load(lease.control_path / name, lease)

                self.assertEqual(
                    "world_project_migration_state_diverged",
                    raised.exception.reason_code,
                )
                self.assertEqual(0, native.legacy_identity_reads)
                self.assertFalse(native.disposition_attempted)
                self.assertEqual(
                    1,
                    sum(event.startswith("close:") for event in native.events),
                )

    def test_windows_journal_append_uses_only_strict_retained_authorization(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import (
            canonical_journal_record,
            journal_frame,
        )

        first = canonical_journal_record({"state": "prepared"})
        updated = {"state": "replaced"}
        updated_payload = canonical_journal_record(updated)
        name = "project-migration.journal.json"

        unavailable = _FakeWindowsNativeApi(b"unused")
        unavailable.entries[name] = _FakeWindowsFile(first, (7, 410), 5_100)
        unavailable.fail_strict_read = True
        unavailable_lease = _FakeWindowsLease(unavailable)
        with patch(
            "worldforge.world_project_migration.WindowsRetainedWorldLifecycle",
            _FakeWindowsLease,
        ):
            with self.assertRaises(migration.WorldProjectMigrationError) as raised:
                migration._append_journal(
                    unavailable_lease.control_path / name,
                    lease=unavailable_lease,
                    identity=(7, 410),
                    history=(first,),
                    updated=updated,
                    repair_partial_tail=False,
                )
        self.assertEqual(
            "world_project_migration_state_diverged",
            raised.exception.reason_code,
        )
        self.assertEqual(first, unavailable.entries[name].payload)
        self.assertEqual(0, unavailable.legacy_identity_reads)
        self.assertTrue(any(event.startswith("read-strict:") for event in unavailable.events))
        self.assertNotIn(
            "append-strict:Windows migration journal transition",
            unavailable.events,
        )
        self.assertFalse(unavailable.handles)
        self.assertEqual(
            1,
            sum(event.startswith("close:") for event in unavailable.events),
        )

        for partial_tail in (False, True):
            with self.subTest(partial_tail=partial_tail):
                native = _FakeWindowsNativeApi(b"unused")
                frame = journal_frame(updated_payload)
                before = first + (frame[:11] if partial_tail else b"")
                native.entries[name] = _FakeWindowsFile(before, (7, 411), 5_200)
                lease = _FakeWindowsLease(native)
                with patch(
                    "worldforge.world_project_migration.WindowsRetainedWorldLifecycle",
                    _FakeWindowsLease,
                ):
                    result = migration._append_journal(
                        lease.control_path / name,
                        lease=lease,
                        identity=(7, 411),
                        history=(first,),
                        updated=updated,
                        repair_partial_tail=partial_tail,
                    )

                self.assertEqual((first, updated_payload), result)
                self.assertEqual(first + frame, native.entries[name].payload)
                self.assertEqual(0, native.legacy_identity_reads)
                self.assertIn(
                    "append-strict:Windows migration journal transition",
                    native.events,
                )
                self.assertTrue(
                    any(
                        event.endswith("write=True")
                        for event in native.events
                        if event.startswith(f"open:{name}:")
                    )
                )
                self.assertIn("flush-control", native.events)
                self.assertFalse(native.handles)
                closed_handles = [
                    event.removeprefix("close:")
                    for event in native.events
                    if event.startswith("close:")
                ]
                self.assertEqual(2, len(closed_handles))
                self.assertEqual(len(closed_handles), len(set(closed_handles)))

        mismatched = _FakeWindowsNativeApi(b"unused")
        mismatched_tail = journal_frame(
            canonical_journal_record({"state": "different transition"})
        )[:100]
        mismatched.entries[name] = _FakeWindowsFile(
            first + mismatched_tail,
            (7, 412),
            5_300,
        )
        mismatched_lease = _FakeWindowsLease(mismatched)
        with patch(
            "worldforge.world_project_migration.WindowsRetainedWorldLifecycle",
            _FakeWindowsLease,
        ):
            with self.assertRaises(migration.WorldProjectMigrationError) as raised:
                migration._append_journal(
                    mismatched_lease.control_path / name,
                    lease=mismatched_lease,
                    identity=(7, 412),
                    history=(first,),
                    updated=updated,
                    repair_partial_tail=True,
                )
        self.assertEqual(
            "world_project_migration_state_diverged",
            raised.exception.reason_code,
        )
        self.assertEqual(first + mismatched_tail, mismatched.entries[name].payload)
        self.assertEqual(0, mismatched.legacy_identity_reads)
        self.assertFalse(mismatched.handles)
        self.assertNotIn(
            "append-strict:Windows migration journal transition",
            mismatched.events,
        )

    def test_retained_evidence_revalidation_classifies_the_cleanup_boundary(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import canonical_journal_record

        evidence_name = "project-migration-v3.evidence.json"
        payload = canonical_journal_record({"record": "verified evidence"})
        for boundary in (
            "pre-mutation-replacement",
            "post-mutation-replacement",
            "post-mutation-disappearance",
            "post-mutation-content-change",
            "post-mutation-verification-close",
        ):
            with self.subTest(boundary=boundary):
                native = _FakeWindowsNativeApi(b"unused")
                original = _FakeWindowsFile(payload, (7, 402), 4_100)
                native.entries[evidence_name] = original
                lease = _FakeWindowsLease(native)
                anchor = migration._retain_windows_evidence(
                    lease,
                    name=evidence_name,
                    expected_identity=(7, 402),
                    expected_payload=payload,
                )
                state = migration._MigrationCleanupState(
                    cleanup_mutated=not boundary.startswith("pre-mutation")
                )
                if boundary.endswith("replacement"):
                    native.entries[evidence_name] = _FakeWindowsFile(
                        payload,
                        (7, 499),
                        4_200,
                    )
                elif boundary.endswith("disappearance"):
                    del native.entries[evidence_name]
                elif boundary.endswith("content-change"):
                    original.payload = canonical_journal_record({"record": "changed"})
                    original.change_time_ns += 1
                elif boundary.endswith("verification-close"):
                    native.fail_verification_close = True

                try:
                    with self.assertRaises(migration.WorldProjectMigrationError) as raised:
                        migration._revalidate_windows_evidence(
                            anchor,
                            state,
                            context="between backup and journal cleanup",
                        )
                    self.assertEqual(
                        (
                            "world_project_migration_state_diverged"
                            if boundary.startswith("pre-mutation")
                            else "world_project_migration_outcome_indeterminate"
                        ),
                        raised.exception.reason_code,
                    )
                finally:
                    migration._close_windows_evidence(
                        anchor,
                        state,
                        context="test cleanup",
                    )

    def test_post_cleanup_evidence_close_is_indeterminate_and_attempted_once(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import canonical_journal_record

        name = "project-migration-v3.evidence.json"
        payload = canonical_journal_record({"record": "verified evidence"})
        native = _FakeWindowsNativeApi(b"unused")
        native.entries[name] = _FakeWindowsFile(payload, (7, 403), 4_300)
        lease = _FakeWindowsLease(native)
        anchor = migration._retain_windows_evidence(
            lease,
            name=name,
            expected_identity=(7, 403),
            expected_payload=payload,
        )
        native.fail_close_names.add(name)
        state = migration._MigrationCleanupState(cleanup_mutated=True)

        with self.assertRaises(migration.WorldProjectMigrationError) as raised:
            migration._close_windows_evidence(
                anchor,
                state,
                context="final evidence",
            )
        self.assertEqual(
            "world_project_migration_outcome_indeterminate",
            raised.exception.reason_code,
        )
        close_events = [event for event in native.events if event.startswith("close:")]
        migration._close_windows_evidence(
            anchor,
            state,
            context="final evidence retry",
        )
        self.assertEqual(
            close_events,
            [event for event in native.events if event.startswith("close:")],
        )

    def test_cleanup_revalidates_evidence_between_backup_and_journal_removal(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import canonical_journal_record
        from worldforge.asset_io import BoundFileBytes

        source_payload = canonical_journal_record(
            {
                "format_version": 2,
                "tool_repository": "rpg-world-forge",
            }
        )
        source_hash = migration._sha256(source_payload)
        operation_id = "a" * 64
        backup_payload = migration._canonical_record(
            migration._backup_document(
                BoundFileBytes(
                    source_payload,
                    (7, 101),
                    len(source_payload),
                    1_000,
                ),
                operation_id=operation_id,
            )
        )
        journal_payload = canonical_journal_record({"state": "cleanup anchor"})
        evidence_payload = canonical_journal_record({"state": "verified evidence"})

        for attack in ("replacement", "disappearance"):
            with self.subTest(attack=attack):
                native = _FakeWindowsNativeApi(b"unused")
                native.entries["project-migration.backup.json"] = _FakeWindowsFile(
                    backup_payload,
                    (7, 404),
                    4_400,
                )
                native.entries["project-migration.journal.json"] = _FakeWindowsFile(
                    journal_payload,
                    (7, 405),
                    4_500,
                )
                native.entries["project-migration-v3.evidence.json"] = _FakeWindowsFile(
                    evidence_payload,
                    (7, 406),
                    4_600,
                )
                lease = _FakeWindowsLease(native)
                anchor = migration._retain_windows_evidence(
                    lease,
                    name="project-migration-v3.evidence.json",
                    expected_identity=(7, 406),
                    expected_payload=evidence_payload,
                )
                state = migration._MigrationCleanupState()

                def inject(
                    event: str,
                    *,
                    selected_attack: str = attack,
                    selected_native: _FakeWindowsNativeApi = native,
                ) -> None:
                    if event != "after_backup_removed":
                        return
                    if selected_attack == "replacement":
                        selected_native.entries["project-migration-v3.evidence.json"] = (
                            _FakeWindowsFile(evidence_payload, (7, 499), 4_700)
                        )
                    else:
                        del selected_native.entries["project-migration-v3.evidence.json"]

                with (
                    patch(
                        "worldforge.world_project_migration.WindowsRetainedWorldLifecycle",
                        _FakeWindowsLease,
                    ),
                    patch(
                        "worldforge.world_project_migration._migration_transition_hook",
                        side_effect=inject,
                    ),
                    self.assertRaises(migration.WorldProjectMigrationError) as raised,
                ):
                    migration._cleanup_authorized_records(
                        lease,
                        expected_source_hash=source_hash,
                        backup_was_present=True,
                        backup_identity=(7, 404),
                        backup_payload=backup_payload,
                        journal_identity=(7, 405),
                        journal_history=(journal_payload,),
                        windows_commit=None,
                        staged_source=None,
                        evidence_anchor=anchor,
                        cleanup_state=state,
                    )

                self.assertEqual(
                    "world_project_migration_outcome_indeterminate",
                    raised.exception.reason_code,
                )
                self.assertNotIn("project-migration.backup.json", native.entries)
                self.assertIn("project-migration.journal.json", native.entries)
                self.assertEqual(
                    1,
                    native.events.count(f"close:{anchor.handle}"),
                )

    def test_successful_cleanup_retains_evidence_and_closes_its_anchor_once(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import canonical_journal_record
        from worldforge.asset_io import BoundFileBytes

        source_payload = canonical_journal_record(
            {
                "format_version": 2,
                "tool_repository": "rpg-world-forge",
            }
        )
        source_hash = migration._sha256(source_payload)
        backup_payload = migration._canonical_record(
            migration._backup_document(
                BoundFileBytes(
                    source_payload,
                    (7, 101),
                    len(source_payload),
                    1_000,
                ),
                operation_id="b" * 64,
            )
        )
        journal_payload = canonical_journal_record({"state": "cleanup anchor"})
        evidence_payload = canonical_journal_record({"state": "verified evidence"})
        native = _FakeWindowsNativeApi(b"unused")
        native.entries["project-migration.backup.json"] = _FakeWindowsFile(
            backup_payload,
            (7, 407),
            4_800,
        )
        native.entries["project-migration.journal.json"] = _FakeWindowsFile(
            journal_payload,
            (7, 408),
            4_900,
        )
        evidence = _FakeWindowsFile(evidence_payload, (7, 409), 5_000)
        native.entries["project-migration-v3.evidence.json"] = evidence
        lease = _FakeWindowsLease(native)
        anchor = migration._retain_windows_evidence(
            lease,
            name="project-migration-v3.evidence.json",
            expected_identity=(7, 409),
            expected_payload=evidence_payload,
        )
        state = migration._MigrationCleanupState()

        with patch(
            "worldforge.world_project_migration.WindowsRetainedWorldLifecycle",
            _FakeWindowsLease,
        ):
            migration._cleanup_authorized_records(
                lease,
                expected_source_hash=source_hash,
                backup_was_present=True,
                backup_identity=(7, 407),
                backup_payload=backup_payload,
                journal_identity=(7, 408),
                journal_history=(journal_payload,),
                windows_commit=None,
                staged_source=None,
                evidence_anchor=anchor,
                cleanup_state=state,
            )

        self.assertNotIn("project-migration.backup.json", native.entries)
        self.assertNotIn("project-migration.journal.json", native.entries)
        self.assertIs(evidence, native.entries["project-migration-v3.evidence.json"])
        self.assertEqual(evidence_payload, evidence.payload)
        self.assertTrue(state.cleanup_mutated)
        self.assertEqual(1, native.events.count(f"close:{anchor.handle}"))

    def test_cleanup_revalidates_terminal_evidence_at_every_destructive_boundary(
        self,
    ) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import canonical_journal_record
        from worldforge.asset_io import BoundFileBytes

        source_payload = canonical_journal_record(
            {
                "format_version": 2,
                "tool_repository": "rpg-world-forge",
            }
        )
        source_hash = migration._sha256(source_payload)
        backup_payload = migration._canonical_record(
            migration._backup_document(
                BoundFileBytes(
                    source_payload,
                    (7, 101),
                    len(source_payload),
                    1_000,
                ),
                operation_id="c" * 64,
            )
        )
        journal_payload = canonical_journal_record({"state": "cleanup anchor"})
        evidence_payload = canonical_journal_record({"state": "verified evidence"})

        for boundary in (
            "after retained source cleanup",
            "after journal cleanup",
            "before migration success",
        ):
            with self.subTest(boundary=boundary):
                native = _FakeWindowsNativeApi(b"unused")
                native.entries["project-migration.backup.json"] = _FakeWindowsFile(
                    backup_payload,
                    (7, 420),
                    5_400,
                )
                native.entries["project-migration.journal.json"] = _FakeWindowsFile(
                    journal_payload,
                    (7, 421),
                    5_500,
                )
                native.entries["project-migration-v3.evidence.json"] = _FakeWindowsFile(
                    evidence_payload,
                    (7, 422),
                    5_600,
                )
                lease = _FakeWindowsLease(native)
                anchor = migration._retain_windows_evidence(
                    lease,
                    name="project-migration-v3.evidence.json",
                    expected_identity=(7, 422),
                    expected_payload=evidence_payload,
                )
                state = migration._MigrationCleanupState()
                original_revalidate = migration._revalidate_windows_evidence

                def replace_at_boundary(
                    record,
                    cleanup_state,
                    *,
                    context: str,
                    selected_boundary: str = boundary,
                    selected_native: _FakeWindowsNativeApi = native,
                    revalidate=original_revalidate,
                ) -> None:
                    if context == selected_boundary:
                        selected_native.entries["project-migration-v3.evidence.json"] = (
                            _FakeWindowsFile(evidence_payload, (7, 499), 5_700)
                        )
                    revalidate(record, cleanup_state, context=context)

                windows_commit = SimpleNamespace(delete_durable_source_retention=lambda: None)
                staged_source = BoundFileBytes(source_payload, (7, 101), len(source_payload))
                with (
                    patch(
                        "worldforge.world_project_migration.WindowsRetainedWorldLifecycle",
                        _FakeWindowsLease,
                    ),
                    patch(
                        "worldforge.world_project_migration._revalidate_windows_evidence",
                        side_effect=replace_at_boundary,
                    ),
                    self.assertRaises(migration.WorldProjectMigrationError) as raised,
                ):
                    migration._cleanup_authorized_records(
                        lease,
                        expected_source_hash=source_hash,
                        backup_was_present=True,
                        backup_identity=(7, 420),
                        backup_payload=backup_payload,
                        journal_identity=(7, 421),
                        journal_history=(journal_payload,),
                        windows_commit=windows_commit,
                        staged_source=staged_source,
                        evidence_anchor=anchor,
                        cleanup_state=state,
                    )

                self.assertEqual(
                    "world_project_migration_outcome_indeterminate",
                    raised.exception.reason_code,
                )
                self.assertEqual(1, native.events.count(f"close:{anchor.handle}"))

    def test_strict_windows_record_removal_binds_identity_links_and_full_history(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import (
            canonical_journal_record,
            journal_frame,
        )
        from worldforge.directory_publish import DirectoryPublishError

        first = canonical_journal_record({"state": "prepared"})
        latest = canonical_journal_record({"state": "verified"})
        raw = first + journal_frame(latest)
        native = _FakeWindowsNativeApi(b"unused")
        entry = _FakeWindowsFile(raw, (7, 307), 3_400)
        native.entries["project-migration.journal.json"] = entry
        native.entries["unexpected-hardlink.json"] = entry
        lease = _FakeWindowsLease(native)

        with self.assertRaisesRegex(DirectoryPublishError, "identity or content changed"):
            migration._remove_windows_migration_record(
                lease,
                name="project-migration.journal.json",
                expected_identity=(7, 307),
                expected_payload=latest,
                expected_history=(first, latest),
                max_record_bytes=8 * 1024 * 1024,
                max_file_bytes=32 * 1024 * 1024,
                context="journal",
            )
        self.assertFalse(native.disposition_attempted)

        del native.entries["unexpected-hardlink.json"]
        migration._remove_windows_migration_record(
            lease,
            name="project-migration.journal.json",
            expected_identity=(7, 307),
            expected_payload=latest,
            expected_history=(first, latest),
            max_record_bytes=8 * 1024 * 1024,
            max_file_bytes=32 * 1024 * 1024,
            context="journal",
        )
        self.assertNotIn("project-migration.journal.json", native.entries)

        changed = _FakeWindowsNativeApi(b"unused")
        changed.entries["project-migration.journal.json"] = _FakeWindowsFile(
            raw,
            (7, 308),
            3_500,
        )
        with self.assertRaisesRegex(DirectoryPublishError, "identity or content changed"):
            migration._remove_windows_migration_record(
                _FakeWindowsLease(changed),
                name="project-migration.journal.json",
                expected_identity=(7, 307),
                expected_payload=latest,
                expected_history=(first, latest),
                max_record_bytes=8 * 1024 * 1024,
                max_file_bytes=32 * 1024 * 1024,
                context="journal",
            )
        self.assertFalse(changed.disposition_attempted)

    def test_post_disposition_close_and_flush_failures_are_indeterminate(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import canonical_journal_record

        payload = canonical_journal_record({"record": "cleanup"})
        for boundary in ("close", "flush"):
            with self.subTest(boundary=boundary):
                native = _FakeWindowsNativeApi(b"unused")
                native.entries["project-migration.backup.json"] = _FakeWindowsFile(
                    payload,
                    (7, 305),
                    3_200,
                )
                lease = _FakeWindowsLease(native)
                native.fail_close_after_disposition = boundary == "close"
                lease.fail_flush = boundary == "flush"

                with self.assertRaises(migration.WorldProjectMigrationError) as raised:
                    with patch(
                        "worldforge.world_project_migration.WindowsRetainedWorldLifecycle",
                        _FakeWindowsLease,
                    ):
                        migration._remove_record(
                            lease.control_path / "project-migration.backup.json",
                            lease=lease,
                            identity=(7, 305),
                            payload=payload,
                            context="backup",
                        )

                self.assertEqual(
                    "world_project_migration_outcome_indeterminate",
                    raised.exception.reason_code,
                )
                self.assertTrue(native.disposition_attempted)

    def test_post_disposition_absence_verification_failures_are_indeterminate(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import canonical_journal_record

        payload = canonical_journal_record({"record": "cleanup"})
        for boundary in (
            "absence-open",
            "name-remained",
            "verification-close",
            "ancestry",
        ):
            with self.subTest(boundary=boundary):
                native = _FakeWindowsNativeApi(b"unused")
                native.entries["project-migration.backup.json"] = _FakeWindowsFile(
                    payload,
                    (7, 309),
                    3_600,
                )
                native.fail_absence_open = boundary == "absence-open"
                native.retain_name_after_disposition = boundary in {
                    "name-remained",
                    "verification-close",
                }
                native.fail_verification_close = boundary == "verification-close"
                lease = _FakeWindowsLease(native)
                lease.fail_assertion_at = 5 if boundary == "ancestry" else None

                with self.assertRaises(migration.WorldProjectMigrationError) as raised:
                    with patch(
                        "worldforge.world_project_migration.WindowsRetainedWorldLifecycle",
                        _FakeWindowsLease,
                    ):
                        migration._remove_record(
                            Path("C:/world/.worldforge/project-migration.backup.json"),
                            lease=lease,
                            identity=(7, 309),
                            payload=payload,
                            context="backup",
                        )

                self.assertEqual(
                    "world_project_migration_outcome_indeterminate",
                    raised.exception.reason_code,
                )
                self.assertTrue(native.disposition_attempted)

    def test_final_post_cleanup_ancestry_failure_is_indeterminate(self) -> None:
        import worldforge.world_project_migration as migration
        from worldforge.workflow import WorkflowError

        lease = SimpleNamespace(
            assert_current=lambda: (_ for _ in ()).throw(
                WorkflowError("World project root ancestry changed")
            )
        )
        with self.assertRaises(migration.WorldProjectMigrationError) as raised:
            migration._require_post_cleanup_ancestry(lease, context="journal")

        self.assertEqual(
            "world_project_migration_outcome_indeterminate",
            raised.exception.reason_code,
        )

    def test_pre_disposition_record_mismatch_remains_state_diverged(self) -> None:
        import worldforge.world_project_migration as migration
        from isoworld.content.publication_journal import canonical_journal_record

        payload = canonical_journal_record({"record": "expected"})
        native = _FakeWindowsNativeApi(b"unused")
        native.entries["project-migration.backup.json"] = _FakeWindowsFile(
            canonical_journal_record({"record": "changed"}),
            (7, 306),
            3_300,
        )
        lease = _FakeWindowsLease(native)

        with self.assertRaises(migration.WorldProjectMigrationError) as raised:
            with patch(
                "worldforge.world_project_migration.WindowsRetainedWorldLifecycle",
                _FakeWindowsLease,
            ):
                migration._remove_record(
                    lease.control_path / "project-migration.backup.json",
                    lease=lease,
                    identity=(7, 306),
                    payload=payload,
                    context="backup",
                )

        self.assertEqual(
            "world_project_migration_state_diverged",
            raised.exception.reason_code,
        )
        self.assertFalse(native.disposition_attempted)

    def test_windows_recovery_shape_retains_source_until_cleanup_authorized(self) -> None:
        import worldforge.world_project_migration as migration

        for state, role in (
            ("prepared", "target"),
            ("replaced", "source"),
            ("verified", "source"),
            ("cleanup_authorized", "source"),
            ("cleanup_authorized", None),
        ):
            with self.subTest(state=state, role=role):
                migration._validate_recovery_shape(
                    project_version=2 if state == "prepared" else 3,
                    journal_state=state,
                    backup_present=True,
                    staged_role=role,
                    evidence_present=state in {"verified", "cleanup_authorized"},
                    windows_commit_forward=True,
                )

        with self.assertRaisesRegex(
            migration.WorldProjectMigrationError,
            "transition-impossible",
        ):
            migration._validate_recovery_shape(
                project_version=3,
                journal_state="verified",
                backup_present=True,
                staged_role=None,
                evidence_present=True,
                windows_commit_forward=True,
            )

    def test_strict_windows_file_state_never_falls_back_off_platform(self) -> None:
        from worldforge.file_stat import windows_handle_file_stat_strict

        if os.name != "nt":
            with self.assertRaisesRegex(OSError, "unavailable"):
                windows_handle_file_stat_strict(1)
            return

        import msvcrt

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strict-id.bin"
            path.write_bytes(b"strict-file-id")
            descriptor = os.open(path, os.O_RDONLY | os.O_BINARY)
            try:
                info = windows_handle_file_stat_strict(msvcrt.get_osfhandle(descriptor))
            finally:
                os.close(descriptor)
            self.assertEqual(len(b"strict-file-id"), info.st_size)
            self.assertEqual(1, info.st_nlink)
            self.assertGreater(info.st_ino, 0)
            self.assertGreater(info.st_dev, 0)

    def test_preflight_reports_each_missing_native_capability(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsMigrationCapabilities,
            windows_migration_support_reason,
        )

        supported = WindowsMigrationCapabilities(
            platform="nt",
            filesystem="NTFS",
            local_fixed_volume=True,
            file_id_128=True,
            hard_links=True,
            posix_unlink_rename=True,
            flushable_directories=True,
            rename_info_ex=True,
            disposition_info_ex=True,
        )
        self.assertIsNone(windows_migration_support_reason(supported))
        cases = (
            (replace(supported, platform="posix"), "windows_platform_required"),
            (replace(supported, filesystem="ReFS"), "local_ntfs_required"),
            (replace(supported, local_fixed_volume=False), "local_ntfs_required"),
            (replace(supported, file_id_128=False), "file_id_128_unavailable"),
            (replace(supported, hard_links=False), "hard_links_unavailable"),
            (
                replace(supported, posix_unlink_rename=False),
                "posix_unlink_rename_unavailable",
            ),
            (
                replace(supported, flushable_directories=False),
                "directory_flush_unavailable",
            ),
            (replace(supported, rename_info_ex=False), "rename_info_ex_unavailable"),
            (
                replace(supported, disposition_info_ex=False),
                "disposition_info_ex_unavailable",
            ),
        )
        for capabilities, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, windows_migration_support_reason(capabilities))

    def test_observation_matrix_accepts_only_commit_forward_states(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsCommitObservation,
            WindowsMigrationOutcomeIndeterminate,
            classify_windows_commit_observation,
        )

        allowed = (
            (("source", None, None, 1, None, None), "stage_target"),
            (("source", None, "target", 1, None, 1), "retain_source"),
            (("source", "source", "target", 2, 2, 1), "publish_target"),
            (("target", "source", None, 1, 1, None), "committed"),
        )
        for values, expected in allowed:
            with self.subTest(values=values):
                observation = WindowsCommitObservation(
                    visible_role=values[0],
                    retained_role=values[1],
                    staged_role=values[2],
                    visible_link_count=values[3],
                    retained_link_count=values[4],
                    staged_link_count=values[5],
                )
                self.assertEqual(expected, classify_windows_commit_observation(observation))

        impossible = (
            ("target", None, None, 1, None, None),
            ("source", "source", None, 2, 2, None),
            ("source", "other", "target", 2, 2, 1),
            ("other", "source", None, None, 1, None),
            ("target", "source", "target", 1, 1, 1),
            ("source", None, "target", 1, None, 2),
        )
        for values in impossible:
            with self.subTest(values=values):
                observation = WindowsCommitObservation(
                    visible_role=values[0],
                    retained_role=values[1],
                    staged_role=values[2],
                    visible_link_count=values[3],
                    retained_link_count=values[4],
                    staged_link_count=values[5],
                )
                with self.assertRaises(WindowsMigrationOutcomeIndeterminate):
                    classify_windows_commit_observation(observation)

    def test_commit_orders_seal_stage_retention_and_publish(self) -> None:
        from worldforge.windows_project_migration import commit_windows_project

        api = _FakeCommitApi()
        observed_events: list[str] = []

        commit_windows_project(api, transition_hook=observed_events.append)

        self.assertEqual("committed", api.state)
        self.assertEqual(
            [
                "preflight",
                "observe",
                "seal:share-read-only",
                "observe",
                "stage-target",
                "observe",
                "retain-source",
                "observe",
                "publish-target",
                "observe",
                "verify-flush-commit",
                "release-seal",
            ],
            api.events,
        )
        self.assertEqual(
            [
                "after_windows_target_staged",
                "after_windows_retention_link",
                "before_windows_rename",
                "after_windows_rename_attempt",
                "after_windows_rename",
            ],
            observed_events,
        )

    def test_committed_partial_seal_failure_closes_every_handle_and_can_retry(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsMigrationStateError,
            WindowsProjectCommitApi,
            commit_windows_project,
        )

        source = b'{"format_version":2}\n'
        target = b'{"format_version":3}\n'
        operation_id = "c" * 64
        retention_name = f".project.json.migration.{operation_id}.exchange"
        native = _FakeWindowsNativeApi(source)
        retained = native.entries["project.json"]
        native.entries[retention_name] = retained
        native.entries["project.json"] = _FakeWindowsFile(target, (7, 202), 2_500)
        lease = _FakeWindowsLease(native)
        adapter = WindowsProjectCommitApi(
            lease,
            operation_id=operation_id,
            source_identity=(7, 101),
            source_sha256=__import__("hashlib").sha256(source).hexdigest(),
            source_change_time_ns=1_000,
            target_payload=target,
        )
        native.fail_sealed_name = retention_name

        with self.assertRaisesRegex(WindowsMigrationStateError, "injected seal failure"):
            commit_windows_project(adapter)

        self.assertEqual({}, native.handles)
        self.assertIsNone(adapter.source_seal_handle)
        self.assertIsNone(adapter.target_seal_handle)

        native.fail_sealed_name = None
        commit_windows_project(adapter, retain_seal=True)
        adapter.delete_durable_source_retention()
        adapter.release_source_seal()
        self.assertEqual({"project.json"}, set(native.entries))

    def test_source_change_time_must_still_match_when_the_seal_opens(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsMigrationStateError,
            WindowsProjectCommitApi,
            commit_windows_project,
        )

        source = b'{"format_version":2}\n'
        native = _FakeWindowsNativeApi(source)
        native.mutate_change_time_on_sealed_source_open = True
        adapter = WindowsProjectCommitApi(
            _FakeWindowsLease(native),
            operation_id="d" * 64,
            source_identity=(7, 101),
            source_sha256=__import__("hashlib").sha256(source).hexdigest(),
            source_change_time_ns=1_000,
            target_payload=b'{"format_version":3}\n',
        )

        with self.assertRaisesRegex(WindowsMigrationStateError, "change time diverged"):
            commit_windows_project(adapter)

        self.assertNotIn("create:.project.json.migration." + "d" * 64 + ".target", native.events)
        self.assertEqual({}, native.handles)

    def test_committed_recovery_is_sealed_before_verification(self) -> None:
        from worldforge.windows_project_migration import commit_windows_project

        api = _FakeCommitApi(state="committed")

        commit_windows_project(api)

        self.assertEqual(
            [
                "preflight",
                "observe",
                "seal:share-read-only",
                "verify-flush-commit",
                "release-seal",
            ],
            api.events,
        )

    def test_native_adapter_retains_exact_source_until_explicit_cleanup(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsProjectCommitApi,
            commit_windows_project,
        )

        source = b'{"format_version":2}\n'
        target = b'{"format_version":3}\n'
        native = _FakeWindowsNativeApi(source)
        lease = _FakeWindowsLease(native)
        adapter = WindowsProjectCommitApi(
            lease,
            operation_id="a" * 64,
            source_identity=(7, 101),
            source_sha256=__import__("hashlib").sha256(source).hexdigest(),
            source_change_time_ns=1_000,
            target_payload=target,
        )

        commit_windows_project(adapter, retain_seal=True)

        self.assertEqual(target, native.entries["project.json"].payload)
        self.assertEqual(
            source,
            native.entries[".project.json.migration." + "a" * 64 + ".exchange"].payload,
        )
        self.assertNotIn(
            ".project.json.migration." + "a" * 64 + ".target",
            native.entries,
        )
        flushed_open_events = [
            event for event in native.events if event.startswith("open:") and "write=True" in event
        ]
        self.assertGreaterEqual(len(flushed_open_events), 3)
        self.assertIsNotNone(adapter.target_identity)
        self.assertGreater(lease.flushes, 0)

        adapter.delete_durable_source_retention()
        adapter.release_source_seal()

        self.assertEqual({"project.json"}, set(native.entries))
        self.assertIn("dispose-ex", native.events)

    def test_only_exact_visible_source_seal_shares_delete_among_sealed_opens(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsProjectCommitApi,
            commit_windows_project,
        )

        source = b'{"format_version":2}\n'
        target = b'{"format_version":3}\n'
        native = _FakeWindowsNativeApi(source)
        adapter = WindowsProjectCommitApi(
            _FakeWindowsLease(native),
            operation_id="e" * 64,
            source_identity=(7, 101),
            source_sha256=__import__("hashlib").sha256(source).hexdigest(),
            source_change_time_ns=1_000,
            target_payload=target,
        )

        commit_windows_project(adapter, retain_seal=True)

        sealed = [contract for contract in native.open_contracts if contract[1]]
        self.assertIn(
            ("project.json", True, True, False, True, True),
            sealed,
        )
        self.assertTrue(all(not contract[3] for contract in sealed))
        self.assertEqual(
            [("project.json", True, True, False, True, True)],
            [contract for contract in sealed if contract[4]],
        )
        observations = [contract for contract in native.open_contracts if not contract[1]]
        self.assertGreater(len(observations), 0)
        self.assertTrue(any(contract[4] for contract in observations))
        adapter.delete_durable_source_retention()
        adapter.release_source_seal()

    def test_committed_recovery_seals_never_share_delete_or_write(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsProjectCommitApi,
            commit_windows_project,
        )

        source = b'{"format_version":2}\n'
        target = b'{"format_version":3}\n'
        operation_id = "f" * 64
        retention_name = f".project.json.migration.{operation_id}.exchange"
        native = _FakeWindowsNativeApi(source)
        retained = native.entries["project.json"]
        native.entries[retention_name] = retained
        native.entries["project.json"] = _FakeWindowsFile(target, (7, 202), 2_500)
        adapter = WindowsProjectCommitApi(
            _FakeWindowsLease(native),
            operation_id=operation_id,
            source_identity=(7, 101),
            source_sha256=__import__("hashlib").sha256(source).hexdigest(),
            source_change_time_ns=1_000,
            target_payload=target,
        )

        commit_windows_project(adapter, retain_seal=True)

        sealed = [contract for contract in native.open_contracts if contract[1]]
        self.assertEqual(
            {
                ("project.json", True, False, False, False, True),
                (retention_name, True, True, False, False, True),
            },
            set(sealed),
        )
        adapter.delete_durable_source_retention()
        adapter.release_source_seal()

    def test_native_adapter_recovers_after_retention_and_rename_boundaries(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsProjectCommitApi,
            commit_windows_project,
        )

        source = b'{"format_version":2}\n'
        target = b'{"format_version":3}\n'
        source_hash = __import__("hashlib").sha256(source).hexdigest()
        for boundary in ("after_windows_retention_link", "after_windows_rename"):
            with self.subTest(boundary=boundary):
                native = _FakeWindowsNativeApi(source)
                lease = _FakeWindowsLease(native)

                def adapter(
                    active_lease: _FakeWindowsLease = lease,
                ) -> WindowsProjectCommitApi:
                    return WindowsProjectCommitApi(
                        active_lease,
                        operation_id="b" * 64,
                        source_identity=(7, 101),
                        source_sha256=source_hash,
                        source_change_time_ns=1_000,
                        target_payload=target,
                    )

                first = adapter()

                def interrupt(event: str, expected_boundary: str = boundary) -> None:
                    if event == expected_boundary:
                        raise _InjectedCrash(event)

                with self.assertRaisesRegex(_InjectedCrash, boundary):
                    commit_windows_project(first, transition_hook=interrupt)

                recovered = adapter()
                commit_windows_project(recovered, retain_seal=True)
                self.assertEqual(target, native.entries["project.json"].payload)
                recovered.delete_durable_source_retention()
                recovered.release_source_seal()
                self.assertEqual({"project.json"}, set(native.entries))

    def test_native_adapter_fails_closed_before_rename_and_retries_commit_forward(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsMigrationPublishError,
            WindowsProjectCommitApi,
            commit_windows_project,
        )

        source = b'{"format_version":2}\n'
        target = b'{"format_version":3}\n'
        operation_id = "c" * 64
        native = _FakeWindowsNativeApi(source)
        lease = _FakeWindowsLease(native)

        def adapter() -> WindowsProjectCommitApi:
            return WindowsProjectCommitApi(
                lease,
                operation_id=operation_id,
                source_identity=(7, 101),
                source_sha256=__import__("hashlib").sha256(source).hexdigest(),
                source_change_time_ns=1_000,
                target_payload=target,
            )

        native.fail_rename_once = True
        with self.assertRaisesRegex(WindowsMigrationPublishError, "publication failed"):
            commit_windows_project(adapter())

        retained_name = f".project.json.migration.{operation_id}.exchange"
        staged_name = f".project.json.migration.{operation_id}.target"
        self.assertEqual(source, native.entries["project.json"].payload)
        self.assertEqual(source, native.entries[retained_name].payload)
        self.assertEqual(target, native.entries[staged_name].payload)
        self.assertTrue(
            any(
                event.startswith(f"open:{staged_name}:")
                and "delete=True" in event
                and "write=True" in event
                for event in native.events
            )
        )

        recovered = adapter()
        commit_windows_project(recovered, retain_seal=True)
        self.assertEqual(target, native.entries["project.json"].payload)
        recovered.delete_durable_source_retention()
        recovered.release_source_seal()
        self.assertEqual({"project.json"}, set(native.entries))

    def test_unsupported_or_unsafe_source_fails_before_namespace_mutation(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsMigrationCapabilityError,
            WindowsMigrationStateError,
            commit_windows_project,
        )

        unsupported = _FakeCommitApi(
            capability_error=WindowsMigrationCapabilityError("local_ntfs_required")
        )
        with self.assertRaisesRegex(WindowsMigrationCapabilityError, "local_ntfs_required"):
            commit_windows_project(unsupported)
        self.assertEqual(["preflight"], unsupported.events)

        for detail in ("source is a reparse point", "source has multiple hard links"):
            with self.subTest(detail=detail):
                unsafe = _FakeCommitApi(seal_error=WindowsMigrationStateError(detail))
                with self.assertRaisesRegex(WindowsMigrationStateError, detail):
                    commit_windows_project(unsafe)
                self.assertEqual(
                    [
                        "preflight",
                        "observe",
                        "seal:share-read-only",
                        "release-seal",
                    ],
                    unsafe.events,
                )

    def test_flush_failure_stops_before_retention_or_publish(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsMigrationStateError,
            commit_windows_project,
        )

        api = _FakeCommitApi(stage_error=WindowsMigrationStateError("directory flush failed"))
        with self.assertRaisesRegex(WindowsMigrationStateError, "directory flush failed"):
            commit_windows_project(api)
        self.assertNotIn("retain-source", api.events)
        self.assertNotIn("publish-target", api.events)
        self.assertEqual("source_only", api.state)

    def test_crash_after_retention_and_after_rename_recovers_commit_forward(self) -> None:
        from worldforge.windows_project_migration import commit_windows_project

        for crash_event, expected_state in (
            ("after_windows_retention_link", "retained"),
            ("after_windows_rename", "committed"),
        ):
            with self.subTest(crash_event=crash_event):
                api = _FakeCommitApi()

                def interrupt(event: str, expected_event: str = crash_event) -> None:
                    if event == expected_event:
                        raise _InjectedCrash(event)

                with self.assertRaisesRegex(_InjectedCrash, crash_event):
                    commit_windows_project(api, transition_hook=interrupt)
                self.assertEqual(expected_state, api.state)
                publish_count = api.events.count("publish-target")

                commit_windows_project(api)

                self.assertEqual("committed", api.state)
                expected_publishes = 1 if crash_event == "after_windows_rename" else 1
                self.assertEqual(expected_publishes, api.events.count("publish-target"))
                if crash_event == "after_windows_rename":
                    self.assertEqual(publish_count, api.events.count("publish-target"))

    def test_post_rename_ambiguity_never_guesses_rollback(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsMigrationOutcomeIndeterminate,
            WindowsMigrationPublishError,
            commit_windows_project,
        )

        unchanged = _FakeCommitApi(
            state="retained",
            publish_error=WindowsMigrationPublishError("rename failed"),
        )
        with self.assertRaises(WindowsMigrationPublishError):
            commit_windows_project(unchanged)
        self.assertEqual("retained", unchanged.state)

        ambiguous = _FakeCommitApi(
            state="retained",
            publish_error=WindowsMigrationPublishError("rename result unavailable"),
            ambiguous_after_publish=True,
        )
        with self.assertRaises(WindowsMigrationOutcomeIndeterminate):
            commit_windows_project(ambiguous)
        self.assertEqual("ambiguous", ambiguous.state)

    def test_successful_rename_hook_is_not_emitted_for_a_failed_attempt(self) -> None:
        from worldforge.windows_project_migration import (
            WindowsMigrationPublishError,
            commit_windows_project,
        )

        api = _FakeCommitApi(
            state="retained",
            publish_error=WindowsMigrationPublishError("rename failed"),
        )
        observed_events: list[str] = []

        with self.assertRaises(WindowsMigrationPublishError):
            commit_windows_project(api, transition_hook=observed_events.append)

        self.assertIn("after_windows_rename_attempt", observed_events)
        self.assertNotIn("after_windows_rename", observed_events)

    def test_windows_information_class_contract_requires_server_2022_build(self) -> None:
        from worldforge.asset_io import _windows_migration_ex_contract_supported

        self.assertFalse(
            _windows_migration_ex_contract_supported(
                SimpleNamespace(major=10, build=20_347),
            )
        )
        self.assertTrue(
            _windows_migration_ex_contract_supported(
                SimpleNamespace(major=10, build=20_348),
            )
        )


if __name__ == "__main__":
    unittest.main()
