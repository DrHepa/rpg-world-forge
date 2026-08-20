from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from worldforge.asset_io import AssetContractError, BoundFileBytes


class WindowsMigrationError(ValueError):
    """Base failure for the bounded native Windows migration commit protocol."""


class WindowsMigrationCapabilityError(WindowsMigrationError):
    """The current volume or operating system cannot satisfy the commit contract."""


class WindowsMigrationStateError(WindowsMigrationError):
    """A retained file or durable transition failed before publication."""


class WindowsMigrationPublishError(WindowsMigrationError):
    """Publication failed with an exact, unchanged pre-commit observation."""


class WindowsMigrationOutcomeIndeterminate(WindowsMigrationError):
    """The observed namespace cannot prove a safe pre- or post-commit state."""


@dataclass(frozen=True, slots=True)
class WindowsMigrationCapabilities:
    platform: str
    filesystem: str
    local_fixed_volume: bool
    file_id_128: bool
    hard_links: bool
    posix_unlink_rename: bool
    flushable_directories: bool
    rename_info_ex: bool
    disposition_info_ex: bool


def windows_migration_support_reason(
    capabilities: WindowsMigrationCapabilities,
) -> str | None:
    if capabilities.platform != "nt":
        return "windows_platform_required"
    if capabilities.filesystem.casefold() != "ntfs" or not capabilities.local_fixed_volume:
        return "local_ntfs_required"
    if not capabilities.file_id_128:
        return "file_id_128_unavailable"
    if not capabilities.hard_links:
        return "hard_links_unavailable"
    if not capabilities.posix_unlink_rename:
        return "posix_unlink_rename_unavailable"
    if not capabilities.flushable_directories:
        return "directory_flush_unavailable"
    if not capabilities.rename_info_ex:
        return "rename_info_ex_unavailable"
    if not capabilities.disposition_info_ex:
        return "disposition_info_ex_unavailable"
    return None


_Role = Literal["source", "target", "other"]


@dataclass(frozen=True, slots=True)
class WindowsCommitObservation:
    visible_role: _Role
    retained_role: _Role | None
    staged_role: _Role | None
    visible_link_count: int | None
    retained_link_count: int | None
    staged_link_count: int | None


def classify_windows_commit_observation(
    observation: WindowsCommitObservation,
) -> Literal["stage_target", "retain_source", "publish_target", "committed"]:
    state = (
        observation.visible_role,
        observation.retained_role,
        observation.staged_role,
        observation.visible_link_count,
        observation.retained_link_count,
        observation.staged_link_count,
    )
    actions = {
        ("source", None, None, 1, None, None): "stage_target",
        ("source", None, "target", 1, None, 1): "retain_source",
        ("source", "source", "target", 2, 2, 1): "publish_target",
        ("target", "source", None, 1, 1, None): "committed",
    }
    action = actions.get(state)
    if action is None:
        raise WindowsMigrationOutcomeIndeterminate(
            "world_project_migration_outcome_indeterminate: "
            "Windows project publication observation is not an exact commit state"
        )
    return action


class WindowsCommitApi(Protocol):
    def preflight(self) -> None: ...

    def observe(self) -> WindowsCommitObservation: ...

    def seal_source_share_read_only(self) -> None: ...

    def release_source_seal(self) -> None: ...

    def create_durable_target_stage(self) -> None: ...

    def create_durable_source_retention(self) -> None: ...

    def publish_target_stage(self) -> None: ...

    def verify_and_flush_committed(self) -> None: ...


class _WindowsNativeApi(Protocol):
    def migration_volume_capabilities(
        self,
        root_handle: int,
        root_path: Path,
    ) -> WindowsMigrationCapabilities: ...

    def open_existing_file_strict(
        self,
        parent: int,
        name: str,
        *,
        sealed: bool = False,
        delete: bool = False,
        share_delete: bool = False,
        write: bool = False,
    ) -> int: ...

    def read_strict_bound_bytes(
        self,
        handle: int,
        *,
        limit: int,
        context: str,
    ) -> tuple[BoundFileBytes, int]: ...

    def create_file(self, parent: int, name: str) -> int: ...

    def write_strict_bytes(self, handle: int, payload: bytes, *, context: str) -> None: ...

    def append_strict_journal_frame(
        self,
        handle: int,
        *,
        expected_size: int,
        truncate_to: int | None,
        frame: bytes,
        context: str,
    ) -> None: ...

    def flush_handle(self, handle: int, *, context: str) -> None: ...

    def create_source_hard_link(self, destination: Path, source: Path) -> None: ...

    def rename_ex(self, handle: int, parent_handle: int, destination_name: str) -> None: ...

    def dispose_ex(self, handle: int) -> None: ...

    def close(self, handle: int) -> None: ...


class _WindowsLease(Protocol):
    root: Path
    control_path: Path
    root_handle: int
    control_handle: int
    api: _WindowsNativeApi

    def assert_current(self) -> None: ...

    def flush_control(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _WindowsBoundEntry:
    captured: BoundFileBytes
    link_count: int


def _read_windows_entry(
    api: _WindowsNativeApi,
    parent_handle: int,
    name: str,
    *,
    limit: int,
    optional: bool,
) -> _WindowsBoundEntry | None:
    handle: int | None = None
    try:
        try:
            # Observation handles are short-lived and intentionally share delete;
            # only retained sealed handles enforce the replacement share contract.
            handle = api.open_existing_file_strict(
                parent_handle,
                name,
                share_delete=True,
            )
        except FileNotFoundError:
            if optional:
                return None
            raise
        captured, link_count = api.read_strict_bound_bytes(
            handle,
            limit=limit,
            context=f"Windows migration entry {name}",
        )
        return _WindowsBoundEntry(captured, link_count)
    finally:
        if handle is not None:
            api.close(handle)


def read_windows_bound_bytes(
    api: _WindowsNativeApi,
    parent_handle: int,
    name: str,
    *,
    limit: int,
    allowed_link_counts: frozenset[int] = frozenset({1}),
) -> BoundFileBytes:
    """Read one strict Windows file through a retained parent handle."""

    try:
        loaded = _read_windows_entry(
            api,
            parent_handle,
            name,
            limit=limit,
            optional=False,
        )
    except (AssetContractError, OSError) as exc:
        raise WindowsMigrationStateError(
            f"Could not read strict Windows migration entry {name}: {exc}"
        ) from exc
    assert loaded is not None
    if loaded.link_count not in allowed_link_counts:
        raise WindowsMigrationStateError(
            f"Windows migration entry {name} has an unsafe hard-link count"
        )
    return loaded.captured


def read_optional_windows_bound_bytes(
    api: _WindowsNativeApi,
    parent_handle: int,
    name: str,
    *,
    limit: int,
    allowed_link_counts: frozenset[int] = frozenset({1}),
) -> BoundFileBytes | None:
    """Read one optional strict Windows file without following reparse points."""

    try:
        loaded = _read_windows_entry(
            api,
            parent_handle,
            name,
            limit=limit,
            optional=True,
        )
    except (AssetContractError, OSError) as exc:
        raise WindowsMigrationStateError(
            f"Could not inspect strict Windows migration entry {name}: {exc}"
        ) from exc
    if loaded is None:
        return None
    if loaded.link_count not in allowed_link_counts:
        raise WindowsMigrationStateError(
            f"Windows migration entry {name} has an unsafe hard-link count"
        )
    return loaded.captured


class WindowsProjectCommitApi:
    """Local-NTFS implementation of the identity-sealed commit-forward protocol."""

    def __init__(
        self,
        lease: _WindowsLease,
        *,
        operation_id: str,
        source_identity: tuple[int, int],
        source_sha256: str,
        source_change_time_ns: int,
        target_payload: bytes,
        byte_limit: int = 4 * 1024 * 1024,
    ) -> None:
        self.lease = lease
        self.api = lease.api
        self.operation_id = operation_id
        self.source_identity = source_identity
        self.source_sha256 = source_sha256
        self.source_change_time_ns = source_change_time_ns
        self.target_payload = target_payload
        self.target_sha256 = hashlib.sha256(target_payload).hexdigest()
        self.byte_limit = max(byte_limit, len(target_payload))
        self.target_stage_name = f".project.json.migration.{operation_id}.target"
        self.source_retention_name = f".project.json.migration.{operation_id}.exchange"
        self.source_seal_handle: int | None = None
        self.target_seal_handle: int | None = None
        self.target_identity: tuple[int, int] | None = None

    @staticmethod
    def _digest(captured: BoundFileBytes) -> str:
        return hashlib.sha256(captured.payload).hexdigest()

    def _source_role(self, loaded: _WindowsBoundEntry) -> bool:
        captured = loaded.captured
        return not (
            captured.identity != self.source_identity
            or self._digest(captured) != self.source_sha256
            or captured.size_bytes != len(captured.payload)
        ) and loaded.link_count in {1, 2}

    def _target_role(self, loaded: _WindowsBoundEntry) -> bool:
        return (
            loaded.captured.payload == self.target_payload
            and self._digest(loaded.captured) == self.target_sha256
            and loaded.captured.size_bytes == len(self.target_payload)
            and loaded.link_count == 1
        )

    def _read(self, name: str) -> _WindowsBoundEntry | None:
        return _read_windows_entry(
            self.api,
            self.lease.control_handle,
            name,
            limit=self.byte_limit,
            optional=True,
        )

    def _observed_entries(
        self,
    ) -> tuple[
        WindowsCommitObservation,
        _WindowsBoundEntry | None,
        _WindowsBoundEntry | None,
        _WindowsBoundEntry | None,
    ]:
        self.lease.assert_current()
        visible = self._read("project.json")
        retained = self._read(self.source_retention_name)
        staged = self._read(self.target_stage_name)
        self.lease.assert_current()
        visible_role: _Role = "other"
        retained_role: _Role | None = None
        staged_role: _Role | None = None
        if visible is not None:
            if self._source_role(visible):
                visible_role = "source"
            elif self._target_role(visible):
                visible_role = "target"
        if retained is not None:
            retained_role = "source" if self._source_role(retained) else "other"
        if staged is not None:
            staged_role = "target" if self._target_role(staged) else "other"
        if (
            visible_role == "source"
            and retained_role == "source"
            and visible is not None
            and retained is not None
            and visible.captured.change_time_ns != retained.captured.change_time_ns
        ):
            retained_role = "other"
        return (
            WindowsCommitObservation(
                visible_role=visible_role,
                retained_role=retained_role,
                staged_role=staged_role,
                visible_link_count=None if visible is None else visible.link_count,
                retained_link_count=None if retained is None else retained.link_count,
                staged_link_count=None if staged is None else staged.link_count,
            ),
            visible,
            retained,
            staged,
        )

    def preflight(self) -> None:
        try:
            self.lease.assert_current()
            capabilities = self.api.migration_volume_capabilities(
                self.lease.root_handle,
                self.lease.root,
            )
        except (AssetContractError, OSError) as exc:
            raise WindowsMigrationCapabilityError(
                f"world_project_migration_capability_unavailable: {exc}"
            ) from exc
        reason = windows_migration_support_reason(capabilities)
        if reason is not None:
            raise WindowsMigrationCapabilityError(
                f"world_project_migration_capability_unavailable: {reason}"
            )
        observation, visible, _retained, _staged = self._observed_entries()
        if (
            observation.visible_role == "source"
            and observation.visible_link_count == 1
            and visible is not None
            and visible.captured.change_time_ns != self.source_change_time_ns
        ):
            raise WindowsMigrationStateError(
                "Windows migration source change time diverged before retention"
            )

    def observe(self) -> WindowsCommitObservation:
        try:
            return self._observed_entries()[0]
        except (AssetContractError, OSError) as exc:
            raise WindowsMigrationOutcomeIndeterminate(
                "world_project_migration_outcome_indeterminate: "
                f"could not observe strict Windows migration state: {exc}"
            ) from exc

    def _open_seal(
        self,
        name: str,
        *,
        delete: bool,
        share_delete: bool = False,
        expected_change_time_ns: int | None = None,
    ) -> int:
        handle = self.api.open_existing_file_strict(
            self.lease.control_handle,
            name,
            sealed=True,
            delete=delete,
            share_delete=share_delete,
            write=True,
        )
        try:
            loaded, links = self.api.read_strict_bound_bytes(
                handle,
                limit=self.byte_limit,
                context=f"sealed Windows migration entry {name}",
            )
            entry = _WindowsBoundEntry(loaded, links)
            expected = self._source_role(entry) if delete else self._target_role(entry)
            if not expected:
                raise WindowsMigrationStateError(f"sealed Windows migration entry {name} diverged")
            if (
                expected_change_time_ns is not None
                and loaded.change_time_ns != expected_change_time_ns
            ):
                raise WindowsMigrationStateError(
                    f"sealed Windows migration entry {name} change time diverged"
                )
            return handle
        except BaseException:
            self.api.close(handle)
            raise

    def seal_source_share_read_only(self) -> None:
        if self.source_seal_handle is not None:
            return
        observation = self.observe()
        opened: list[int] = []
        try:
            if observation.visible_role == "source":
                self.source_seal_handle = self._open_seal(
                    "project.json",
                    delete=True,
                    share_delete=True,
                    expected_change_time_ns=(
                        self.source_change_time_ns
                        if observation.visible_link_count == 1 and observation.retained_role is None
                        else None
                    ),
                )
                opened.append(self.source_seal_handle)
            elif observation.visible_role == "target":
                self.target_seal_handle = self._open_seal("project.json", delete=False)
                opened.append(self.target_seal_handle)
                self.source_seal_handle = self._open_seal(
                    self.source_retention_name,
                    delete=True,
                )
                opened.append(self.source_seal_handle)
            else:
                raise WindowsMigrationStateError(
                    "Windows migration source cannot be sealed from this state"
                )
            classify_windows_commit_observation(self.observe())
        except BaseException as primary:
            self.source_seal_handle = None
            self.target_seal_handle = None
            for handle in reversed(opened):
                try:
                    self.api.close(handle)
                except BaseException as cleanup_error:
                    primary.add_note(
                        f"Windows migration partial-seal cleanup failed: {cleanup_error}"
                    )
            if isinstance(primary, WindowsMigrationError):
                raise
            if isinstance(primary, (AssetContractError, OSError)):
                raise WindowsMigrationStateError(
                    f"Windows migration source seal is unavailable: {primary}"
                ) from primary
            raise

    def release_source_seal(self) -> None:
        errors: list[BaseException] = []
        handles = (self.source_seal_handle, self.target_seal_handle)
        self.source_seal_handle = None
        self.target_seal_handle = None
        for handle in handles:
            if handle is None:
                continue
            try:
                self.api.close(handle)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise WindowsMigrationStateError(
                f"Could not release Windows migration seals: {errors[0]}"
            ) from errors[0]

    def create_durable_target_stage(self) -> None:
        handle: int | None = None
        try:
            handle = self.api.create_file(
                self.lease.control_handle,
                self.target_stage_name,
            )
            self.api.write_strict_bytes(
                handle,
                self.target_payload,
                context="Windows migration target stage",
            )
            self.lease.flush_control()
            captured, links = self.api.read_strict_bound_bytes(
                handle,
                limit=self.byte_limit,
                context="Windows migration target stage",
            )
            if not self._target_role(_WindowsBoundEntry(captured, links)):
                raise WindowsMigrationStateError("Windows migration target stage diverged")
        except (AssetContractError, OSError) as exc:
            raise WindowsMigrationStateError(
                f"Could not durably stage the Windows migration target: {exc}"
            ) from exc
        finally:
            if handle is not None:
                self.api.close(handle)

    def create_durable_source_retention(self) -> None:
        stage_handle: int | None = None
        try:
            stage_handle = self.api.open_existing_file_strict(
                self.lease.control_handle,
                self.target_stage_name,
                share_delete=True,
                write=True,
            )
            staged, links = self.api.read_strict_bound_bytes(
                stage_handle,
                limit=self.byte_limit,
                context="Windows migration target stage",
            )
            if not self._target_role(_WindowsBoundEntry(staged, links)):
                raise WindowsMigrationStateError("Windows migration target stage diverged")
            self.api.flush_handle(
                stage_handle,
                context="Windows migration target stage",
            )
            self.lease.flush_control()
            self.api.create_source_hard_link(
                self.lease.control_path / self.source_retention_name,
                self.lease.control_path / "project.json",
            )
            self.lease.flush_control()
            if classify_windows_commit_observation(self.observe()) != "publish_target":
                raise WindowsMigrationStateError(
                    "Windows migration source retention did not become durable"
                )
        except (AssetContractError, OSError) as exc:
            raise WindowsMigrationStateError(
                f"Could not durably retain the Windows migration source: {exc}"
            ) from exc
        finally:
            if stage_handle is not None:
                self.api.close(stage_handle)

    def publish_target_stage(self) -> None:
        handle: int | None = None
        try:
            handle = self.api.open_existing_file_strict(
                self.lease.control_handle,
                self.target_stage_name,
                delete=True,
                write=True,
            )
            staged, links = self.api.read_strict_bound_bytes(
                handle,
                limit=self.byte_limit,
                context="Windows migration target stage",
            )
            if not self._target_role(_WindowsBoundEntry(staged, links)):
                raise WindowsMigrationPublishError(
                    "Windows migration target stage diverged before publication"
                )
            self.api.flush_handle(handle, context="Windows migration target stage")
            self.lease.flush_control()
            self.lease.assert_current()
            self.api.rename_ex(handle, self.lease.control_handle, "project.json")
        except WindowsMigrationPublishError:
            raise
        except (AssetContractError, OSError) as exc:
            raise WindowsMigrationPublishError(
                f"Windows migration target publication failed: {exc}"
            ) from exc
        finally:
            if handle is not None:
                self.api.close(handle)

    def verify_and_flush_committed(self) -> None:
        try:
            if self.source_seal_handle is None:
                self.seal_source_share_read_only()
            if self.target_seal_handle is None:
                self.target_seal_handle = self._open_seal("project.json", delete=False)
            observation, visible, _retained, _staged = self._observed_entries()
            if classify_windows_commit_observation(observation) != "committed":
                raise WindowsMigrationOutcomeIndeterminate(
                    "Windows migration did not reach the exact committed state"
                )
            assert visible is not None
            self.target_identity = visible.captured.identity
            self.api.flush_handle(
                self.target_seal_handle,
                context="Windows migration committed target",
            )
            assert self.source_seal_handle is not None
            self.api.flush_handle(
                self.source_seal_handle,
                context="Windows migration retained source",
            )
            self.lease.flush_control()
            if classify_windows_commit_observation(self.observe()) != "committed":
                raise WindowsMigrationOutcomeIndeterminate(
                    "Windows migration committed state changed after durable flush"
                )
        except WindowsMigrationOutcomeIndeterminate:
            raise
        except (AssetContractError, OSError) as exc:
            raise WindowsMigrationOutcomeIndeterminate(
                "world_project_migration_outcome_indeterminate: "
                f"could not verify durable Windows commit: {exc}"
            ) from exc

    def delete_durable_source_retention(self) -> None:
        """Remove only the exact retained source after durable cleanup authorization."""

        handle = self.source_seal_handle
        if handle is None:
            raise WindowsMigrationOutcomeIndeterminate(
                "Windows migration source retention is not sealed for cleanup"
            )
        mutated = False
        try:
            if classify_windows_commit_observation(self.observe()) != "committed":
                raise WindowsMigrationOutcomeIndeterminate(
                    "Windows migration state changed before source-retention cleanup"
                )
            self.api.close(handle)
            self.source_seal_handle = None
            handle = self._open_seal(
                self.source_retention_name,
                delete=True,
            )
            self.source_seal_handle = handle
            if classify_windows_commit_observation(self.observe()) != "committed":
                raise WindowsMigrationOutcomeIndeterminate(
                    "Windows migration state changed after retaining the cleanup link"
                )
            self.api.dispose_ex(handle)
            mutated = True
            self.api.close(handle)
            self.source_seal_handle = None
            self.lease.flush_control()
            retained = self._read(self.source_retention_name)
            visible = self._read("project.json")
            if retained is not None or visible is None or not self._target_role(visible):
                raise WindowsMigrationOutcomeIndeterminate(
                    "Windows migration source-retention cleanup is indeterminate"
                )
        except WindowsMigrationOutcomeIndeterminate:
            raise
        except (AssetContractError, OSError, RuntimeError, ValueError) as exc:
            detail = "after disposition" if mutated else "before disposition"
            raise WindowsMigrationOutcomeIndeterminate(
                "world_project_migration_outcome_indeterminate: "
                f"Windows source-retention cleanup failed {detail}: {exc}"
            ) from exc


def commit_windows_project(
    api: WindowsCommitApi,
    *,
    transition_hook: Callable[[str], None] | None = None,
    retain_seal: bool = False,
) -> None:
    """Commit forward through only exact, identity-bound Windows observations."""

    hook = transition_hook or (lambda _event: None)
    api.preflight()
    initial_action = classify_windows_commit_observation(api.observe())
    succeeded = False
    sealing_started = False
    try:
        sealing_started = True
        api.seal_source_share_read_only()
        if initial_action == "committed":
            api.verify_and_flush_committed()
            succeeded = True
            return
        while True:
            action = classify_windows_commit_observation(api.observe())
            if action == "stage_target":
                api.create_durable_target_stage()
                hook("after_windows_target_staged")
                continue
            if action == "retain_source":
                api.create_durable_source_retention()
                hook("after_windows_retention_link")
                continue
            if action == "committed":
                api.verify_and_flush_committed()
                succeeded = True
                return

            publish_error: WindowsMigrationPublishError | None = None
            hook("before_windows_rename")
            try:
                api.publish_target_stage()
            except WindowsMigrationPublishError as exc:
                publish_error = exc
            hook("after_windows_rename_attempt")
            try:
                observed_action = classify_windows_commit_observation(api.observe())
            except WindowsMigrationOutcomeIndeterminate:
                raise
            if observed_action == "committed":
                hook("after_windows_rename")
                api.verify_and_flush_committed()
                succeeded = True
                return
            if publish_error is not None and observed_action == "publish_target":
                raise publish_error
            raise WindowsMigrationOutcomeIndeterminate(
                "world_project_migration_outcome_indeterminate: "
                "Windows publication did not reach an exact committed observation"
            )
    finally:
        if sealing_started and not (retain_seal and succeeded):
            primary = sys.exception()
            try:
                api.release_source_seal()
            except BaseException as cleanup_error:
                if primary is None:
                    raise
                primary.add_note(f"Windows migration seal cleanup failed: {cleanup_error}")


__all__ = [
    "WindowsCommitApi",
    "WindowsCommitObservation",
    "WindowsMigrationCapabilities",
    "WindowsMigrationCapabilityError",
    "WindowsMigrationError",
    "WindowsMigrationOutcomeIndeterminate",
    "WindowsMigrationPublishError",
    "WindowsMigrationStateError",
    "WindowsProjectCommitApi",
    "classify_windows_commit_observation",
    "commit_windows_project",
    "read_optional_windows_bound_bytes",
    "read_windows_bound_bytes",
    "windows_migration_support_reason",
]
