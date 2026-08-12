from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from isoworld.content.publication_journal import (
    PublicationJournalError,
    canonical_journal_record,
    journal_frame,
    recover_validated_journal_history,
)
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.asset_io import (
    AssetContractError,
    BoundFileBytes,
    read_bound_bytes,
    read_bound_bytes_at,
    read_optional_bound_bytes_at,
    remove_bound_file_at,
    write_bytes_identity_atomic_replace_at,
)
from worldforge.directory_publish import (
    DirectoryPublishError,
    DirectoryPublishIndeterminateError,
    append_append_only_journal,
    create_append_only_journal,
    read_append_only_journal_history_state,
    read_append_only_journal_state,
    remove_append_only_journal,
)
from worldforge.windows_project_migration import (
    WindowsMigrationCapabilityError,
    WindowsMigrationOutcomeIndeterminate,
    WindowsMigrationPublishError,
    WindowsMigrationStateError,
    WindowsProjectCommitApi,
    classify_windows_commit_observation,
    commit_windows_project,
    read_optional_windows_bound_bytes,
    read_windows_bound_bytes,
)
from worldforge.workflow import WorkflowError
from worldforge.world_lock import RetainedWorldLifecycle, WindowsRetainedWorldLifecycle

PROJECT_PATH = Path(".worldforge/project.json")
CONTROL_PATHS = (
    PROJECT_PATH,
    Path(".worldforge/status.json"),
    Path("source/manifest.json"),
    Path("source/world.json"),
)
BACKUP_PATH = Path(".worldforge/project-migration.backup.json")
JOURNAL_PATH = Path(".worldforge/project-migration.journal.json")
EVIDENCE_PATH = Path(".worldforge/project-migration-v3.evidence.json")

BACKUP_FORMAT = "world-forge.world_project_migration_backup"
JOURNAL_FORMAT = "world-forge.world_project_migration_journal"
EVIDENCE_FORMAT = "world-forge.world_project_migration_evidence"
MIGRATION_VERSION = 1
MAX_PROJECT_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = 8 * 1024 * 1024
MAX_JOURNAL_BYTES = 32 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_JOURNAL_STATES = ("prepared", "replaced", "verified", "cleanup_authorized")

MigrationHook = Callable[[str], None]


def _migration_transition_hook(_event: str) -> None:
    """Inject bounded interruption points in migration state-machine tests."""


class WorldProjectMigrationError(WorkflowError):
    """A precise, recoverable world-project identity migration failure."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _fail(reason_code: str, detail: str) -> None:
    raise WorldProjectMigrationError(reason_code, detail)


def _validate_recovery_shape(
    *,
    project_version: int,
    journal_state: str | None,
    backup_present: bool,
    staged_role: str | None,
    evidence_present: bool,
    windows_commit_forward: bool = False,
) -> None:
    """Reject artifact combinations no durable migration transition can produce."""

    allowed = False
    if journal_state is None:
        if not backup_present:
            allowed = staged_role is None and (not evidence_present or project_version == 3)
        else:
            # A crash after the durable backup but before the first journal record
            # is recoverable only while the original v2 name remains untouched.
            allowed = project_version == 2 and staged_role is None and not evidence_present
    elif journal_state == "prepared":
        allowed = (
            backup_present
            and not evidence_present
            and (
                (project_version == 2 and staged_role in {None, "target"})
                or (project_version == 3 and staged_role == "source")
            )
        )
    elif journal_state == "replaced":
        allowed = project_version == 3 and backup_present and staged_role in {None, "source"}
    elif journal_state == "verified":
        allowed = (
            project_version == 3
            and backup_present
            and staged_role == ("source" if windows_commit_forward else None)
            and evidence_present
        )
    elif journal_state == "cleanup_authorized":
        allowed = (
            project_version == 3
            and staged_role in ({None, "source"} if windows_commit_forward else {None})
            and evidence_present
        )
    if not allowed:
        _fail(
            "world_project_migration_state_diverged",
            "migration recovery state is transition-impossible",
        )


def _staged_role(
    staged: BoundFileBytes | None,
    *,
    source_identity: tuple[int, int] | None,
    source_hash: str,
    target_hash: str,
) -> str | None:
    if staged is None:
        return None
    digest = _sha256(staged.payload)
    if source_identity is not None and staged.identity == source_identity and digest == source_hash:
        return "source"
    if digest == target_hash:
        return "target"
    return "invalid"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _identity_document(identity: tuple[int, int]) -> dict[str, int | str]:
    if os.name == "nt":
        return {
            "volume_serial": f"{identity[0]:016x}",
            "file_id": f"{identity[1]:032x}",
        }
    return {"device": identity[0], "inode": identity[1]}


def _identity(value: object, *, context: str) -> tuple[int, int]:
    if not isinstance(value, dict):
        _fail("world_project_migration_state_diverged", f"migration {context} diverged")
    if set(value) == {"device", "inode"}:
        if (
            isinstance(value.get("device"), bool)
            or not isinstance(value.get("device"), int)
            or isinstance(value.get("inode"), bool)
            or not isinstance(value.get("inode"), int)
            or value["device"] < 0
            or value["inode"] < 0
        ):
            _fail("world_project_migration_state_diverged", f"migration {context} diverged")
        return value["device"], value["inode"]
    if set(value) == {"volume_serial", "file_id"}:
        volume = value.get("volume_serial")
        file_id = value.get("file_id")
        if (
            not isinstance(volume, str)
            or re.fullmatch(r"[0-9a-f]{16}", volume) is None
            or not isinstance(file_id, str)
            or re.fullmatch(r"[0-9a-f]{32}", file_id) is None
        ):
            _fail("world_project_migration_state_diverged", f"migration {context} diverged")
        return int(volume, 16), int(file_id, 16)
    _fail("world_project_migration_state_diverged", f"migration {context} diverged")


def _canonical_record(value: object) -> bytes:
    return canonical_journal_record(value)


def _decode_canonical(payload: bytes, *, context: str) -> dict[str, Any]:
    try:
        value = decode_json_object(payload, source=context)
    except RuntimeIOError as exc:
        _fail("world_project_migration_state_diverged", f"migration {context} diverged: {exc}")
    if _canonical_record(value) != payload:
        _fail(
            "world_project_migration_state_diverged",
            f"migration {context} diverged: record is not canonical",
        )
    return value


def _read_project(path: Path) -> tuple[BoundFileBytes, dict[str, Any]]:
    try:
        captured = read_bound_bytes(path, limit=MAX_PROJECT_BYTES)
        project = decode_json_object(captured.payload, source=path)
    except (AssetContractError, RuntimeIOError) as exc:
        _fail("world_project_migration_project_invalid", str(exc))
    return captured, project


def _target_project(project: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    target = dict(project)
    target["format_version"] = 3
    target["tool_repository"] = "world-forge"
    payload = (json.dumps(target, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return target, payload


def _backup_document(
    source: BoundFileBytes,
    *,
    operation_id: str,
) -> dict[str, object]:
    return {
        "format": BACKUP_FORMAT,
        "format_version": MIGRATION_VERSION,
        "operation_id": operation_id,
        "project_bytes_base64": base64.b64encode(source.payload).decode("ascii"),
        "source_identity": _identity_document(source.identity),
        "source_sha256": _sha256(source.payload),
        "source_size_bytes": len(source.payload),
        "source_change_time_ns": source.change_time_ns,
    }


def _validate_backup(
    payload: bytes,
    *,
    expected_source_hash: str,
) -> dict[str, Any]:
    value = _decode_canonical(payload, context="backup")
    if (
        set(value)
        != {
            "format",
            "format_version",
            "operation_id",
            "project_bytes_base64",
            "source_identity",
            "source_sha256",
            "source_size_bytes",
            "source_change_time_ns",
        }
        or value.get("format") != BACKUP_FORMAT
        or value.get("format_version") != MIGRATION_VERSION
        or not isinstance(value.get("operation_id"), str)
        or SHA256_PATTERN.fullmatch(value["operation_id"]) is None
        or isinstance(value.get("source_change_time_ns"), bool)
        or not isinstance(value.get("source_change_time_ns"), int)
        or value["source_change_time_ns"] < 0
    ):
        _fail("world_project_migration_state_diverged", "migration backup diverged")
    if value.get("source_sha256") != expected_source_hash:
        _fail("world_project_migration_state_diverged", "migration backup diverged")
    encoded = value.get("project_bytes_base64")
    if not isinstance(encoded, str):
        _fail("world_project_migration_state_diverged", "migration backup diverged")
    try:
        source_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise WorldProjectMigrationError(
            "world_project_migration_state_diverged",
            "migration backup diverged",
        ) from exc
    if (
        not source_bytes
        or len(source_bytes) > MAX_PROJECT_BYTES
        or value.get("source_size_bytes") != len(source_bytes)
        or _sha256(source_bytes) != expected_source_hash
    ):
        _fail("world_project_migration_state_diverged", "migration backup diverged")
    _identity(value.get("source_identity"), context="backup")
    try:
        source_project = decode_json_object(source_bytes, source="migration backup project")
    except RuntimeIOError as exc:
        _fail(
            "world_project_migration_state_diverged",
            f"migration backup diverged: {exc}",
        )
    if (
        source_project.get("format_version") != 2
        or source_project.get("tool_repository") != "rpg-world-forge"
    ):
        _fail("world_project_migration_state_diverged", "migration backup diverged")
    value["project_bytes"] = source_bytes
    value["project_document"] = source_project
    return value


def _new_operation_id() -> str:
    return secrets.token_hex(32)


def _journal_document(
    *,
    state: str,
    operation_id: str,
    source_hash: str,
    target_hash: str,
    backup_identity: tuple[int, int],
    target_identity: tuple[int, int] | None,
    evidence_identity: tuple[int, int] | None,
    evidence_sha256: str | None,
) -> dict[str, object]:
    return {
        "format": JOURNAL_FORMAT,
        "format_version": MIGRATION_VERSION,
        "operation_id": operation_id,
        "state": state,
        "from_format_version": 2,
        "to_format_version": 3,
        "source_sha256": source_hash,
        "target_sha256": target_hash,
        "backup_identity": _identity_document(backup_identity),
        "target_identity": (
            None if target_identity is None else _identity_document(target_identity)
        ),
        "evidence_identity": (
            None if evidence_identity is None else _identity_document(evidence_identity)
        ),
        "evidence_sha256": evidence_sha256,
    }


def _validate_journal_history(
    payloads: tuple[bytes, ...],
    *,
    source_hash: str,
    target_hash: str,
    backup_identity: tuple[int, int],
    operation_id: str,
) -> tuple[tuple[dict[str, Any], ...], tuple[bytes, ...]]:
    if not payloads or len(payloads) > len(_JOURNAL_STATES):
        _fail("world_project_migration_state_diverged", "migration journal diverged")
    records = tuple(_decode_canonical(payload, context="journal") for payload in payloads)
    previous_target: tuple[int, int] | None = None
    previous_evidence: tuple[int, int] | None = None
    previous_evidence_sha: str | None = None
    for index, record in enumerate(records):
        expected_state = _JOURNAL_STATES[index]
        if set(record) != {
            "format",
            "format_version",
            "operation_id",
            "state",
            "from_format_version",
            "to_format_version",
            "source_sha256",
            "target_sha256",
            "backup_identity",
            "target_identity",
            "evidence_identity",
            "evidence_sha256",
        }:
            _fail("world_project_migration_state_diverged", "migration journal diverged")
        if (
            record.get("format") != JOURNAL_FORMAT
            or record.get("format_version") != MIGRATION_VERSION
            or record.get("operation_id") != operation_id
            or record.get("state") != expected_state
            or record.get("from_format_version") != 2
            or record.get("to_format_version") != 3
            or record.get("source_sha256") != source_hash
            or record.get("target_sha256") != target_hash
            or _identity(record.get("backup_identity"), context="journal") != backup_identity
        ):
            _fail("world_project_migration_state_diverged", "migration journal diverged")
        target_value = record.get("target_identity")
        evidence_value = record.get("evidence_identity")
        evidence_sha = record.get("evidence_sha256")
        if expected_state == "prepared":
            if target_value is not None or evidence_value is not None or evidence_sha is not None:
                _fail("world_project_migration_state_diverged", "migration journal diverged")
        elif expected_state == "replaced":
            previous_target = _identity(target_value, context="journal")
            if evidence_value is not None or evidence_sha is not None:
                _fail("world_project_migration_state_diverged", "migration journal diverged")
        else:
            target = _identity(target_value, context="journal")
            evidence = _identity(evidence_value, context="journal")
            if (
                target != previous_target
                or not isinstance(evidence_sha, str)
                or not SHA256_PATTERN.fullmatch(evidence_sha)
            ):
                _fail("world_project_migration_state_diverged", "migration journal diverged")
            if expected_state == "verified":
                previous_evidence = evidence
                previous_evidence_sha = evidence_sha
            elif evidence != previous_evidence or evidence_sha != previous_evidence_sha:
                _fail("world_project_migration_state_diverged", "migration journal diverged")
    return records, payloads


@dataclass(slots=True)
class _WindowsMigrationRecord:
    lease: WindowsRetainedWorldLifecycle
    name: str
    handle: int
    identity: tuple[int, int]
    payload: bytes
    history: tuple[bytes, ...]
    partial_tail: bool
    complete_prefix_size: int
    change_time_ns: int | None
    max_record_bytes: int
    max_file_bytes: int
    close_attempted: bool = False


@dataclass(slots=True)
class _MigrationCleanupState:
    cleanup_mutated: bool = False


def _recover_strict_windows_record(
    payload: bytes,
    *,
    max_record_bytes: int,
    context: str,
) -> tuple[tuple[bytes, ...], int, bool]:
    try:
        history, complete_prefix_size, partial_tail = recover_validated_journal_history(
            payload,
            max_record_bytes=max_record_bytes,
        )
    except PublicationJournalError as exc:
        raise DirectoryPublishError(f"Windows migration {context} framing diverged: {exc}") from exc
    if not history:
        raise DirectoryPublishError(f"Windows migration {context} history is empty")
    return history, complete_prefix_size, partial_tail


def _close_windows_migration_record_once(record: _WindowsMigrationRecord) -> None:
    if record.close_attempted:
        return
    record.close_attempted = True
    record.lease.api.close(record.handle)


def _load_windows_migration_record(
    lease: WindowsRetainedWorldLifecycle,
    *,
    name: str,
    max_record_bytes: int,
    max_file_bytes: int,
    context: str,
    retain: bool,
    share_delete: bool = False,
    write: bool = False,
) -> _WindowsMigrationRecord | None:
    handle: int | None = None
    record: _WindowsMigrationRecord | None = None
    try:
        lease.assert_current()
        try:
            handle = lease.api.open_existing_file_strict(
                lease.control_handle,
                name,
                sealed=True,
                share_delete=share_delete,
                write=write,
            )
        except FileNotFoundError:
            lease.assert_current()
            return None
        captured, link_count = lease.api.read_strict_bound_bytes(
            handle,
            limit=max_file_bytes,
            context=f"Windows migration {context}",
        )
        if link_count != 1 or captured.size_bytes != len(captured.payload):
            raise DirectoryPublishError(
                f"Windows migration {context} is not an exact standalone regular file"
            )
        history, complete_prefix_size, partial_tail = _recover_strict_windows_record(
            captured.payload,
            max_record_bytes=max_record_bytes,
            context=context,
        )
        lease.assert_current()
        repeated, repeated_links = lease.api.read_strict_bound_bytes(
            handle,
            limit=max_file_bytes,
            context=f"Windows migration {context}",
        )
        if (
            repeated_links != 1
            or repeated.identity != captured.identity
            or repeated.size_bytes != captured.size_bytes
            or repeated.change_time_ns != captured.change_time_ns
            or repeated.payload != captured.payload
            or _sha256(repeated.payload) != _sha256(captured.payload)
        ):
            raise DirectoryPublishError(f"Windows migration {context} changed during strict read")
        repeated_history, repeated_prefix_size, repeated_partial_tail = (
            _recover_strict_windows_record(
                repeated.payload,
                max_record_bytes=max_record_bytes,
                context=context,
            )
        )
        if (
            repeated_history != history
            or repeated_prefix_size != complete_prefix_size
            or repeated_partial_tail != partial_tail
        ):
            raise DirectoryPublishError(f"Windows migration {context} history changed")
        lease.assert_current()
        record = _WindowsMigrationRecord(
            lease=lease,
            name=name,
            handle=handle,
            identity=captured.identity,
            payload=captured.payload,
            history=history,
            partial_tail=partial_tail,
            complete_prefix_size=complete_prefix_size,
            change_time_ns=captured.change_time_ns,
            max_record_bytes=max_record_bytes,
            max_file_bytes=max_file_bytes,
        )
        if retain:
            return record
        _close_windows_migration_record_once(record)
        return record
    except DirectoryPublishError:
        raise
    except Exception as exc:
        raise DirectoryPublishError(
            f"Windows migration {context} strict retained read failed: {exc}"
        ) from exc
    finally:
        if handle is not None and (record is None or (not retain and not record.close_attempted)):
            try:
                if record is None:
                    lease.api.close(handle)
                else:
                    _close_windows_migration_record_once(record)
            except Exception as exc:
                primary = sys.exception()
                if primary is not None:
                    primary.add_note(f"Windows migration {context} handle cleanup failed: {exc}")
                else:
                    raise DirectoryPublishError(
                        f"Windows migration {context} handle cleanup failed: {exc}"
                    ) from exc


def _read_windows_migration_record_state(
    lease: WindowsRetainedWorldLifecycle,
    *,
    name: str,
    max_record_bytes: int,
    max_file_bytes: int,
    context: str,
    history: bool,
) -> tuple[tuple[bytes, ...], tuple[int, int], bool] | None:
    loaded = _load_windows_migration_record(
        lease,
        name=name,
        max_record_bytes=max_record_bytes,
        max_file_bytes=max_file_bytes,
        context=context,
        retain=False,
    )
    if loaded is None:
        return None
    payloads = loaded.history if history else (loaded.history[-1],)
    return payloads, loaded.identity, loaded.partial_tail


def _retain_windows_evidence(
    lease: WindowsRetainedWorldLifecycle,
    *,
    name: str,
    expected_identity: tuple[int, int],
    expected_payload: bytes,
) -> _WindowsMigrationRecord:
    try:
        record = _load_windows_migration_record(
            lease,
            name=name,
            max_record_bytes=MAX_RECORD_BYTES,
            max_file_bytes=MAX_RECORD_BYTES,
            context="evidence anchor",
            retain=True,
        )
        if (
            record is None
            or record.identity != expected_identity
            or record.payload != expected_payload
            or record.history != (expected_payload,)
            or record.partial_tail
            or record.complete_prefix_size != len(expected_payload)
            or _sha256(record.payload) != _sha256(expected_payload)
        ):
            raise DirectoryPublishError("Windows migration evidence anchor diverged")
        return record
    except Exception as exc:
        if "record" in locals() and record is not None:
            try:
                _close_windows_migration_record_once(record)
            except Exception as close_exc:
                exc.add_note(f"Windows migration evidence anchor cleanup failed: {close_exc}")
        if isinstance(exc, WorldProjectMigrationError):
            raise
        _fail(
            "world_project_migration_state_diverged",
            f"migration evidence anchor diverged: {exc}",
        )


def _evidence_failure_code(state: _MigrationCleanupState) -> str:
    return (
        "world_project_migration_outcome_indeterminate"
        if state.cleanup_mutated
        else "world_project_migration_state_diverged"
    )


def _revalidate_windows_evidence(
    record: _WindowsMigrationRecord,
    state: _MigrationCleanupState,
    *,
    context: str,
) -> None:
    try:
        record.lease.assert_current()
        retained, retained_links = record.lease.api.read_strict_bound_bytes(
            record.handle,
            limit=record.max_file_bytes,
            context=f"Windows migration retained evidence {context}",
        )
        history, complete_prefix_size, partial_tail = _recover_strict_windows_record(
            retained.payload,
            max_record_bytes=record.max_record_bytes,
            context=f"retained evidence {context}",
        )
        if (
            retained_links != 1
            or retained.identity != record.identity
            or retained.size_bytes != len(record.payload)
            or retained.change_time_ns != record.change_time_ns
            or retained.payload != record.payload
            or _sha256(retained.payload) != _sha256(record.payload)
            or history != record.history
            or complete_prefix_size != record.complete_prefix_size
            or partial_tail != record.partial_tail
        ):
            raise DirectoryPublishError("Windows migration retained evidence changed")
        named = _load_windows_migration_record(
            record.lease,
            name=record.name,
            max_record_bytes=record.max_record_bytes,
            max_file_bytes=record.max_file_bytes,
            context=f"named evidence {context}",
            retain=False,
            share_delete=True,
        )
        if (
            named is None
            or named.identity != record.identity
            or named.payload != record.payload
            or named.history != record.history
            or named.partial_tail != record.partial_tail
            or named.complete_prefix_size != record.complete_prefix_size
            or named.change_time_ns != record.change_time_ns
        ):
            raise DirectoryPublishError("Windows migration evidence name binding changed")
        record.lease.assert_current()
    except Exception as exc:
        if isinstance(exc, WorldProjectMigrationError):
            raise
        _fail(
            _evidence_failure_code(state),
            f"migration evidence {context} diverged: {exc}",
        )


def _close_windows_evidence(
    record: _WindowsMigrationRecord,
    state: _MigrationCleanupState,
    *,
    context: str,
) -> None:
    try:
        _close_windows_migration_record_once(record)
    except Exception as exc:
        _fail(
            _evidence_failure_code(state),
            f"migration evidence {context} close failed: {exc}",
        )


def _read_backup(
    path: Path,
    *,
    expected_source_hash: str,
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle | None = None,
) -> tuple[dict[str, Any], tuple[int, int], bytes] | None:
    try:
        if isinstance(lease, WindowsRetainedWorldLifecycle):
            strict = _read_windows_migration_record_state(
                lease,
                name=path.name,
                max_record_bytes=MAX_RECORD_BYTES,
                max_file_bytes=MAX_RECORD_BYTES,
                context="backup authorization",
                history=False,
            )
            loaded = None if strict is None else (strict[0][0], strict[1], strict[2])
        else:
            loaded = read_append_only_journal_state(
                path,
                max_record_bytes=MAX_RECORD_BYTES,
                max_file_bytes=MAX_RECORD_BYTES,
            )
    except DirectoryPublishError as exc:
        _fail("world_project_migration_state_diverged", f"migration backup diverged: {exc}")
    if loaded is None:
        return None
    payload, identity, partial_tail = loaded
    if partial_tail:
        _fail("world_project_migration_state_diverged", "migration backup diverged")
    return _validate_backup(payload, expected_source_hash=expected_source_hash), identity, payload


def _read_journal(
    path: Path,
    *,
    source_hash: str,
    target_hash: str,
    backup_identity: tuple[int, int],
    operation_id: str,
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle | None = None,
) -> tuple[tuple[dict[str, Any], ...], tuple[bytes, ...], tuple[int, int], bool] | None:
    try:
        if isinstance(lease, WindowsRetainedWorldLifecycle):
            loaded = _read_windows_migration_record_state(
                lease,
                name=path.name,
                max_record_bytes=MAX_RECORD_BYTES,
                max_file_bytes=MAX_JOURNAL_BYTES,
                context="journal authorization",
                history=True,
            )
        else:
            loaded = read_append_only_journal_history_state(
                path,
                max_record_bytes=MAX_RECORD_BYTES,
                max_file_bytes=MAX_JOURNAL_BYTES,
            )
    except DirectoryPublishError as exc:
        _fail("world_project_migration_state_diverged", f"migration journal diverged: {exc}")
    if loaded is None:
        return None
    payloads, identity, partial_tail = loaded
    records, history = _validate_journal_history(
        payloads,
        source_hash=source_hash,
        target_hash=target_hash,
        backup_identity=backup_identity,
        operation_id=operation_id,
    )
    return records, history, identity, partial_tail


def _read_journal_after_backup_cleanup(
    path: Path,
    *,
    expected_source_hash: str,
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle | None = None,
) -> (
    tuple[
        tuple[dict[str, Any], ...],
        tuple[bytes, ...],
        tuple[int, int],
        bool,
        tuple[int, int],
        str,
        str,
    ]
    | None
):
    try:
        if isinstance(lease, WindowsRetainedWorldLifecycle):
            loaded = _read_windows_migration_record_state(
                lease,
                name=path.name,
                max_record_bytes=MAX_RECORD_BYTES,
                max_file_bytes=MAX_JOURNAL_BYTES,
                context="cleanup journal authorization",
                history=True,
            )
        else:
            loaded = read_append_only_journal_history_state(
                path,
                max_record_bytes=MAX_RECORD_BYTES,
                max_file_bytes=MAX_JOURNAL_BYTES,
            )
    except DirectoryPublishError as exc:
        _fail("world_project_migration_state_diverged", f"migration journal diverged: {exc}")
    if loaded is None:
        return None
    payloads, identity, partial_tail = loaded
    if not payloads:
        _fail("world_project_migration_state_diverged", "migration journal diverged")
    first = _decode_canonical(payloads[0], context="journal")
    if first.get("source_sha256") != expected_source_hash:
        _fail("world_project_migration_state_diverged", "migration journal diverged")
    target_hash = first.get("target_sha256")
    if not isinstance(target_hash, str) or SHA256_PATTERN.fullmatch(target_hash) is None:
        _fail("world_project_migration_state_diverged", "migration journal diverged")
    backup_identity = _identity(first.get("backup_identity"), context="journal")
    operation_id = first.get("operation_id")
    if not isinstance(operation_id, str) or SHA256_PATTERN.fullmatch(operation_id) is None:
        _fail("world_project_migration_state_diverged", "migration journal diverged")
    records, history = _validate_journal_history(
        payloads,
        source_hash=expected_source_hash,
        target_hash=target_hash,
        backup_identity=backup_identity,
        operation_id=operation_id,
    )
    if records[-1]["state"] != "cleanup_authorized":
        _fail(
            "world_project_migration_state_diverged",
            "migration backup is absent before cleanup authorization",
        )
    return (
        records,
        history,
        identity,
        partial_tail,
        backup_identity,
        target_hash,
        operation_id,
    )


def _evidence_document(
    *,
    operation_id: str,
    source_hash: str,
    target_hash: str,
    target_identity: tuple[int, int],
) -> dict[str, object]:
    return {
        "format": EVIDENCE_FORMAT,
        "format_version": MIGRATION_VERSION,
        "operation_id": operation_id,
        "from_format_version": 2,
        "to_format_version": 3,
        "source_sha256": source_hash,
        "target_sha256": target_hash,
        "target_identity": _identity_document(target_identity),
        "status": "verified",
    }


def _read_evidence(
    path: Path,
    *,
    source_hash: str,
    target_hash: str,
    target_identity: tuple[int, int],
    operation_id: str,
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle | None = None,
) -> tuple[dict[str, Any], tuple[int, int], bytes] | None:
    try:
        if isinstance(lease, WindowsRetainedWorldLifecycle):
            strict = _read_windows_migration_record_state(
                lease,
                name=path.name,
                max_record_bytes=MAX_RECORD_BYTES,
                max_file_bytes=MAX_RECORD_BYTES,
                context="evidence authorization",
                history=False,
            )
            loaded = None if strict is None else (strict[0][0], strict[1], strict[2])
        else:
            loaded = read_append_only_journal_state(
                path,
                max_record_bytes=MAX_RECORD_BYTES,
                max_file_bytes=MAX_RECORD_BYTES,
            )
    except DirectoryPublishError as exc:
        _fail("world_project_migration_state_diverged", f"migration evidence diverged: {exc}")
    if loaded is None:
        return None
    payload, identity, partial_tail = loaded
    if partial_tail:
        _fail("world_project_migration_state_diverged", "migration evidence diverged")
    value = _decode_canonical(payload, context="evidence")
    expected = _evidence_document(
        operation_id=operation_id,
        source_hash=source_hash,
        target_hash=target_hash,
        target_identity=target_identity,
    )
    if value != expected:
        _fail("world_project_migration_state_diverged", "migration evidence diverged")
    return value, identity, payload


def _lease_flush_control(
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle,
) -> None:
    if isinstance(lease, WindowsRetainedWorldLifecycle):
        try:
            lease.flush_control()
        except (AssetContractError, OSError) as exc:
            _fail(
                "world_project_migration_state_diverged",
                f"migration control-directory durability diverged: {exc}",
            )
        return
    try:
        os.fsync(lease.control_fd)
    except OSError as exc:
        _fail(
            "world_project_migration_state_diverged",
            f"migration control-directory durability diverged: {exc}",
        )


def _lease_read_optional_control(
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle,
    name: str,
    *,
    limit: int,
    allowed_windows_links: frozenset[int] = frozenset({1}),
) -> BoundFileBytes | None:
    if isinstance(lease, WindowsRetainedWorldLifecycle):
        try:
            return read_optional_windows_bound_bytes(
                lease.api,
                lease.control_handle,
                name,
                limit=limit,
                allowed_link_counts=allowed_windows_links,
            )
        except WindowsMigrationStateError as exc:
            raise AssetContractError(str(exc)) from exc
    return read_optional_bound_bytes_at(lease.control_fd, name, limit=limit)


def _windows_recovery_staged_role(
    *,
    project_version: int,
    target_stage: BoundFileBytes | None,
    source_retention: BoundFileBytes | None,
    source_identity: tuple[int, int] | None,
    source_hash: str,
    target_hash: str,
) -> str | None:
    target_role = _staged_role(
        target_stage,
        source_identity=None,
        source_hash=source_hash,
        target_hash=target_hash,
    )
    retained_role = _staged_role(
        source_retention,
        source_identity=source_identity,
        source_hash=source_hash,
        target_hash=target_hash,
    )
    if project_version == 2:
        if target_stage is None and source_retention is None:
            return None
        if target_role == "target" and retained_role in {None, "source"}:
            return "target"
        return "invalid"
    if target_stage is not None:
        return "invalid"
    if source_retention is None:
        return None
    return "source" if retained_role == "source" else "invalid"


def _raise_windows_migration_error(exc: Exception) -> None:
    if isinstance(exc, WindowsMigrationCapabilityError):
        reason = "world_project_migration_capability_unavailable"
    elif isinstance(exc, WindowsMigrationOutcomeIndeterminate):
        reason = "world_project_migration_outcome_indeterminate"
    elif isinstance(exc, WindowsMigrationPublishError):
        reason = "world_project_migration_publish_failed"
    else:
        reason = "world_project_migration_state_diverged"
    raise WorldProjectMigrationError(reason, str(exc)) from exc


def _append_windows_migration_journal(
    lease: WindowsRetainedWorldLifecycle,
    *,
    name: str,
    expected_identity: tuple[int, int],
    expected_history: tuple[bytes, ...],
    updated_payload: bytes,
    repair_partial_tail: bool,
) -> None:
    """Append one migration transition through one strict retained Windows handle."""

    record: _WindowsMigrationRecord | None = None
    try:
        if not expected_history:
            raise DirectoryPublishError("Expected migration journal history is empty")
        expected_raw, expected_records = _expected_migration_record_bytes(
            expected_history[-1],
            expected_history,
        )
        try:
            frame = journal_frame(updated_payload)
        except PublicationJournalError as exc:
            raise DirectoryPublishError(
                f"Windows migration journal transition is invalid: {exc}"
            ) from exc
        expected_after = expected_raw + frame
        expected_after_records = (*expected_records, updated_payload)
        if len(expected_after) > MAX_JOURNAL_BYTES:
            raise DirectoryPublishError("Windows migration journal exceeds its byte limit")

        record = _load_windows_migration_record(
            lease,
            name=name,
            max_record_bytes=MAX_RECORD_BYTES,
            max_file_bytes=MAX_JOURNAL_BYTES,
            context="journal transition authorization",
            retain=True,
            write=True,
        )
        if record is None:
            raise DirectoryPublishError("Windows migration journal disappeared")
        if (
            record.identity != expected_identity
            or record.history != expected_records
            or record.complete_prefix_size != len(expected_raw)
            or record.payload[: record.complete_prefix_size] != expected_raw
            or record.partial_tail != repair_partial_tail
        ):
            raise DirectoryPublishError(
                "Windows migration journal identity or complete history changed"
            )

        truncate_to: int | None = None
        if record.partial_tail:
            partial_frame = record.payload[record.complete_prefix_size :]
            if not frame.startswith(partial_frame):
                raise DirectoryPublishError(
                    "Windows migration journal partial tail does not match the transition"
                )
            truncate_to = record.complete_prefix_size
        elif record.payload != expected_raw:
            raise DirectoryPublishError("Windows migration journal bytes changed")

        lease.assert_current()
        lease.api.append_strict_journal_frame(
            record.handle,
            expected_size=len(record.payload),
            truncate_to=truncate_to,
            frame=frame,
            context="Windows migration journal transition",
        )
        lease.assert_current()
        captured, link_count = lease.api.read_strict_bound_bytes(
            record.handle,
            limit=MAX_JOURNAL_BYTES,
            context="Windows migration journal transition verification",
        )
        _require_exact_migration_record(
            captured,
            link_count,
            expected_identity=expected_identity,
            expected_raw=expected_after,
            expected_records=expected_after_records,
            max_record_bytes=MAX_RECORD_BYTES,
            context="journal transition",
        )
        _close_windows_migration_record_once(record)
        lease.flush_control()
        named = _load_windows_migration_record(
            lease,
            name=name,
            max_record_bytes=MAX_RECORD_BYTES,
            max_file_bytes=MAX_JOURNAL_BYTES,
            context="journal transition name binding",
            retain=False,
        )
        if (
            named is None
            or named.identity != expected_identity
            or named.payload != expected_after
            or named.history != expected_after_records
            or named.partial_tail
            or named.complete_prefix_size != len(expected_after)
        ):
            raise DirectoryPublishError(
                "Windows migration journal name binding changed after transition"
            )
        lease.assert_current()
    except DirectoryPublishError:
        raise
    except Exception as exc:
        raise DirectoryPublishError(
            f"Windows migration journal strict retained append failed: {exc}"
        ) from exc
    finally:
        if record is not None and not record.close_attempted:
            try:
                _close_windows_migration_record_once(record)
            except Exception as exc:
                primary = sys.exception()
                if primary is not None:
                    primary.add_note(
                        f"Windows migration journal transition handle cleanup failed: {exc}"
                    )
                else:
                    raise DirectoryPublishError(
                        f"Windows migration journal transition handle cleanup failed: {exc}"
                    ) from exc


def _append_journal(
    path: Path,
    *,
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle,
    identity: tuple[int, int],
    history: tuple[bytes, ...],
    updated: dict[str, object],
    repair_partial_tail: bool,
) -> tuple[bytes, ...]:
    updated_payload = _canonical_record(updated)
    try:
        if isinstance(lease, WindowsRetainedWorldLifecycle):
            _append_windows_migration_journal(
                lease,
                name=path.name,
                expected_identity=identity,
                expected_history=history,
                updated_payload=updated_payload,
                repair_partial_tail=repair_partial_tail,
            )
        else:
            append_append_only_journal(
                path,
                expected_identity=identity,
                expected_payload=history[-1],
                expected_history=history,
                updated_payload=updated_payload,
                max_record_bytes=MAX_RECORD_BYTES,
                max_file_bytes=MAX_JOURNAL_BYTES,
                repair_partial_tail=repair_partial_tail,
                retained_parent_fd=lease.control_fd,
            )
            _lease_flush_control(lease)
    except (DirectoryPublishError, OSError) as exc:
        _fail("world_project_migration_state_diverged", f"migration journal diverged: {exc}")
    return (*history, updated_payload)


def _result(
    *,
    status: str,
    mode: str,
    source_hash: str,
    target_hash: str,
    evidence_sha256: str | None,
    from_format_version: int = 2,
    to_format_version: int = 3,
    apply_supported: bool | None = None,
    apply_capability_reason: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "status": status,
        "mode": mode,
        "format": "rpg-world-forge.project",
        "from_format_version": from_format_version,
        "to_format_version": to_format_version,
        "source_sha256": source_hash,
        "target_sha256": target_hash,
        "evidence_sha256": evidence_sha256,
    }
    if apply_supported is not None:
        result["apply_supported"] = apply_supported
        result["apply_capability_reason"] = apply_capability_reason
    return result


def _validate_expected_hash(value: object) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        _fail(
            "world_project_migration_expected_hash_invalid",
            "expected_source_hash must be a 64-character lowercase SHA-256 digest",
        )
    return value


def _read_control_snapshot(
    root: Path,
    *,
    lease: RetainedWorldLifecycle | None = None,
) -> dict[Path, BoundFileBytes]:
    try:
        if lease is not None:
            lease.assert_current()
            if isinstance(lease, WindowsRetainedWorldLifecycle):
                captured = {
                    PROJECT_PATH: read_windows_bound_bytes(
                        lease.api,
                        lease.control_handle,
                        "project.json",
                        limit=MAX_PROJECT_BYTES,
                        allowed_link_counts=frozenset({1, 2}),
                    ),
                    Path(".worldforge/status.json"): read_windows_bound_bytes(
                        lease.api,
                        lease.control_handle,
                        "status.json",
                        limit=MAX_PROJECT_BYTES,
                    ),
                    Path("source/manifest.json"): read_windows_bound_bytes(
                        lease.api,
                        lease.source_handle,
                        "manifest.json",
                        limit=MAX_PROJECT_BYTES,
                    ),
                    Path("source/world.json"): read_windows_bound_bytes(
                        lease.api,
                        lease.source_handle,
                        "world.json",
                        limit=MAX_PROJECT_BYTES,
                    ),
                }
            else:
                captured = {
                    PROJECT_PATH: read_bound_bytes_at(
                        lease.control_fd,
                        "project.json",
                        limit=MAX_PROJECT_BYTES,
                    ),
                    Path(".worldforge/status.json"): read_bound_bytes_at(
                        lease.control_fd,
                        "status.json",
                        limit=MAX_PROJECT_BYTES,
                    ),
                    Path("source/manifest.json"): read_bound_bytes_at(
                        lease.source_fd,
                        "manifest.json",
                        limit=MAX_PROJECT_BYTES,
                    ),
                    Path("source/world.json"): read_bound_bytes_at(
                        lease.source_fd,
                        "world.json",
                        limit=MAX_PROJECT_BYTES,
                    ),
                }
            lease.assert_current()
            return captured
        return {
            relative: read_bound_bytes(root / relative, limit=MAX_PROJECT_BYTES)
            for relative in CONTROL_PATHS
        }
    except (AssetContractError, WindowsMigrationStateError) as exc:
        _fail("world_project_migration_project_invalid", str(exc))


def _validated_snapshot(
    root: Path,
    *,
    allow_legacy: bool,
    lease: RetainedWorldLifecycle | None = None,
) -> tuple[BoundFileBytes, dict[str, Any]]:
    from worldforge.world_lifecycle import (
        _validate_source_manifest,
        inspect_world_project_snapshot,
    )

    before = _read_control_snapshot(root, lease=lease)
    values: dict[Path, dict[str, Any]] = {}
    try:
        for relative, captured in before.items():
            values[relative] = decode_json_object(captured.payload, source=root / relative)
    except RuntimeIOError as exc:
        _fail("world_project_migration_project_invalid", str(exc))
    project = values[PROJECT_PATH]
    try:
        _validate_source_manifest(root / "source", values[Path("source/manifest.json")])
        inspect_world_project_snapshot(
            root,
            project,
            values[Path("source/world.json")],
            values[Path(".worldforge/status.json")],
            allow_legacy=allow_legacy,
            error_type=WorkflowError,
        )
    except WorkflowError as exc:
        _fail("world_project_migration_project_invalid", str(exc))
    after = _read_control_snapshot(root, lease=lease)
    if before != after:
        _fail(
            "world_project_migration_project_changed",
            "World-project controls changed during strict validation",
        )
    return before[PROJECT_PATH], project


def _dry_run(root: Path, expected_source_hash: str) -> dict[str, object]:
    from worldforge.world_lock import world_project_migration_apply_capability

    preflight, preflight_project = _read_project(root / PROJECT_PATH)
    if preflight_project.get("format_version") == 1:
        _fail(
            "world_project_migration_upgrade_required",
            "Legacy world-project v1 must use worldforge upgrade-world before identity migration",
        )
    source, project = _validated_snapshot(root, allow_legacy=True)
    if source != preflight or project != preflight_project:
        _fail(
            "world_project_migration_project_changed",
            "World-project project.json changed during strict validation",
        )
    source_hash = _sha256(source.payload)
    if source_hash != expected_source_hash:
        _fail(
            "world_project_migration_expected_hash_mismatch",
            "World-project expected source hash "
            f"{expected_source_hash} does not match {source_hash}",
        )
    version = project.get("format_version")
    if version != 2:
        _fail(
            "world_project_migration_version_invalid",
            "Dry-run supports only a coherent world-project v2 source",
        )
    _target, target_payload = _target_project(project)
    _validate_dry_run_migration_artifacts(
        root,
        source=source,
        expected_source_hash=expected_source_hash,
        target_hash=_sha256(target_payload),
    )
    apply_supported, capability_reason = world_project_migration_apply_capability(root)
    return _result(
        status="would_migrate",
        mode="dry-run",
        source_hash=source_hash,
        target_hash=_sha256(target_payload),
        evidence_sha256=None,
        apply_supported=apply_supported,
        apply_capability_reason=capability_reason,
    )


def _entry_exists_no_follow(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        _fail(
            "world_project_migration_state_diverged",
            f"could not inspect migration artifact {path.name}: {exc}",
        )
    return True


def _migration_stage_names(control_root: Path) -> frozenset[str]:
    prefix = ".project.json.migration."
    try:
        with os.scandir(control_root) as entries:
            return frozenset(entry.name for entry in entries if entry.name.startswith(prefix))
    except OSError as exc:
        _fail(
            "world_project_migration_state_diverged",
            f"could not inspect migration staging entries: {exc}",
        )


def _validate_dry_run_migration_artifacts(
    root: Path,
    *,
    source: BoundFileBytes,
    expected_source_hash: str,
    target_hash: str,
) -> None:
    """Refuse to advertise readiness while durable recovery state is present."""

    control_root = root / ".worldforge"
    backup_path = control_root / BACKUP_PATH.name
    journal_path = control_root / JOURNAL_PATH.name
    evidence_path = control_root / EVIDENCE_PATH.name
    backup_present = _entry_exists_no_follow(backup_path)
    journal_present = _entry_exists_no_follow(journal_path)
    evidence_present = _entry_exists_no_follow(evidence_path)
    stage_names = _migration_stage_names(control_root)

    if not backup_present:
        if journal_present or stage_names:
            _fail(
                "world_project_migration_state_diverged",
                "migration recovery artifacts exist without a durable backup",
            )
        if evidence_present:
            _fail(
                "world_project_migration_state_diverged",
                "migration evidence exists for a legacy v2 project",
            )
        return

    backup_loaded = _read_backup(
        backup_path,
        expected_source_hash=expected_source_hash,
    )
    if backup_loaded is None:
        _fail("world_project_migration_state_diverged", "migration backup diverged")
    backup, backup_identity, _backup_payload = backup_loaded
    source_identity = _identity(backup.get("source_identity"), context="backup")
    if backup.get("project_bytes") != source.payload or source_identity != source.identity:
        _fail("world_project_migration_state_diverged", "migration backup diverged")
    operation_id = backup["operation_id"]
    allowed_stages = frozenset(
        {
            f".project.json.migration.{operation_id}.exchange",
            f".project.json.migration.{operation_id}.target",
        }
    )
    if not stage_names.issubset(allowed_stages):
        _fail("world_project_migration_state_diverged", "migration staging state diverged")
    if evidence_present:
        _fail(
            "world_project_migration_state_diverged",
            "migration evidence exists before a v2 project was replaced",
        )
    if not journal_present:
        if stage_names:
            _fail(
                "world_project_migration_state_diverged",
                "migration staging exists before the prepared journal",
            )
    else:
        journal_loaded = _read_journal(
            journal_path,
            source_hash=expected_source_hash,
            target_hash=target_hash,
            backup_identity=backup_identity,
            operation_id=operation_id,
        )
        if journal_loaded is None or journal_loaded[0][-1].get("state") != "prepared":
            _fail(
                "world_project_migration_state_diverged",
                "legacy v2 project has an impossible migration journal state",
            )

    _fail(
        "world_project_migration_recovery_required",
        "durable migration recovery state exists; run apply to recover it",
    )


def _remove_record(
    path: Path,
    *,
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle,
    identity: tuple[int, int],
    payload: bytes,
    history: tuple[bytes, ...] | None = None,
    context: str,
) -> None:
    try:
        if isinstance(lease, WindowsRetainedWorldLifecycle):
            _remove_windows_migration_record(
                lease,
                name=path.name,
                expected_identity=identity,
                expected_payload=payload,
                expected_history=history,
                max_record_bytes=MAX_RECORD_BYTES,
                max_file_bytes=MAX_JOURNAL_BYTES,
                context=context,
            )
        else:
            remove_append_only_journal(
                path,
                expected_identity=identity,
                expected_payload=payload,
                expected_history=history,
                max_record_bytes=MAX_RECORD_BYTES,
                max_file_bytes=MAX_JOURNAL_BYTES,
                retained_parent_fd=lease.control_fd,
            )
            _lease_flush_control(lease)
    except DirectoryPublishIndeterminateError as exc:
        _fail(
            "world_project_migration_outcome_indeterminate",
            f"migration {context} cleanup outcome is indeterminate: {exc}",
        )
    except (DirectoryPublishError, FileNotFoundError, OSError) as exc:
        _fail("world_project_migration_state_diverged", f"migration {context} diverged: {exc}")


def _expected_migration_record_bytes(
    expected_payload: bytes,
    expected_history: tuple[bytes, ...] | None,
) -> tuple[bytes, tuple[bytes, ...]]:
    records = (expected_payload,) if expected_history is None else expected_history
    if not records or records[-1] != expected_payload:
        raise DirectoryPublishError("Expected migration record history is invalid")
    try:
        raw = records[0] + b"".join(journal_frame(record) for record in records[1:])
    except PublicationJournalError as exc:
        raise DirectoryPublishError(f"Expected migration record history is invalid: {exc}") from exc
    return raw, records


def _require_exact_migration_record(
    captured: BoundFileBytes,
    link_count: int,
    *,
    expected_identity: tuple[int, int],
    expected_raw: bytes,
    expected_records: tuple[bytes, ...],
    max_record_bytes: int,
    context: str,
) -> None:
    if (
        captured.identity != expected_identity
        or link_count != 1
        or captured.size_bytes != len(expected_raw)
        or captured.payload != expected_raw
        or _sha256(captured.payload) != _sha256(expected_raw)
    ):
        raise DirectoryPublishError(f"Migration {context} identity or content changed")
    try:
        records, complete_prefix_size, partial_tail = recover_validated_journal_history(
            captured.payload,
            max_record_bytes=max_record_bytes,
        )
    except PublicationJournalError as exc:
        raise DirectoryPublishError(f"Migration {context} framing changed: {exc}") from exc
    if records != expected_records or partial_tail or complete_prefix_size != len(captured.payload):
        raise DirectoryPublishError(f"Migration {context} history changed")


def _remove_windows_migration_record(
    lease: WindowsRetainedWorldLifecycle,
    *,
    name: str,
    expected_identity: tuple[int, int],
    expected_payload: bytes,
    expected_history: tuple[bytes, ...] | None,
    max_record_bytes: int,
    max_file_bytes: int,
    context: str,
) -> None:
    """Delete one exact migration record through strict retained Windows handles."""

    expected_raw, expected_records = _expected_migration_record_bytes(
        expected_payload,
        expected_history,
    )
    handle: int | None = None
    disposition_attempted = False
    primary: BaseException | None = None
    close_error: BaseException | None = None
    try:
        lease.assert_current()
        handle = lease.api.open_existing_file_strict(
            lease.control_handle,
            name,
            sealed=True,
            delete=True,
        )
        for _validation_pass in range(2):
            captured, link_count = lease.api.read_strict_bound_bytes(
                handle,
                limit=max_file_bytes,
                context=f"Windows migration {context} cleanup record",
            )
            _require_exact_migration_record(
                captured,
                link_count,
                expected_identity=expected_identity,
                expected_raw=expected_raw,
                expected_records=expected_records,
                max_record_bytes=max_record_bytes,
                context=context,
            )
            lease.assert_current()
        disposition_attempted = True
        lease.api.dispose_ex(handle)
    except BaseException as exc:
        primary = exc

    if handle is not None:
        try:
            lease.api.close(handle)
        except BaseException as exc:
            close_error = exc

    if disposition_attempted and (primary is not None or close_error is not None):
        cause = primary if primary is not None else close_error
        assert cause is not None
        if primary is not None and close_error is not None:
            primary.add_note(f"Windows migration record handle cleanup failed: {close_error}")
        raise DirectoryPublishIndeterminateError(
            f"Windows migration {context} cleanup is indeterminate after disposition"
        ) from cause
    if primary is not None:
        if close_error is not None:
            primary.add_note(f"Windows migration record handle cleanup failed: {close_error}")
        if isinstance(primary, DirectoryPublishError):
            raise primary
        if isinstance(primary, (AssetContractError, FileNotFoundError, OSError)):
            raise DirectoryPublishError(
                f"Windows migration {context} cleanup validation failed: {primary}"
            ) from primary
        if isinstance(primary, Exception):
            raise DirectoryPublishError(
                f"Windows migration {context} cleanup validation failed: {primary}"
            ) from primary
        raise primary
    if close_error is not None:
        raise DirectoryPublishError(
            f"Windows migration {context} record handle cleanup failed: {close_error}"
        ) from close_error

    try:
        lease.flush_control()
    except Exception as exc:
        raise DirectoryPublishIndeterminateError(
            f"Windows migration {context} cleanup directory flush failed"
        ) from exc

    verification_handle: int | None = None
    try:
        try:
            verification_handle = lease.api.open_existing_file_strict(
                lease.control_handle,
                name,
                share_delete=True,
            )
        except FileNotFoundError:
            try:
                lease.assert_current()
            except Exception as exc:
                raise DirectoryPublishIndeterminateError(
                    f"Windows migration {context} cleanup ancestry verification failed"
                ) from exc
            return
        except Exception as exc:
            raise DirectoryPublishIndeterminateError(
                f"Windows migration {context} cleanup absence check failed"
            ) from exc
        raise DirectoryPublishIndeterminateError(
            f"Windows migration {context} cleanup name remained or reappeared"
        )
    finally:
        if verification_handle is not None:
            try:
                lease.api.close(verification_handle)
            except Exception as exc:
                raise DirectoryPublishIndeterminateError(
                    f"Windows migration {context} cleanup verification handle failed"
                ) from exc


def _require_post_cleanup_ancestry(
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle,
    *,
    context: str,
) -> None:
    try:
        lease.assert_current()
    except Exception as exc:
        _fail(
            "world_project_migration_outcome_indeterminate",
            f"migration {context} cleanup ancestry outcome is indeterminate: {exc}",
        )


def _cleanup_authorized_records(
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle,
    *,
    expected_source_hash: str,
    backup_was_present: bool,
    backup_identity: tuple[int, int],
    backup_payload: bytes,
    journal_identity: tuple[int, int],
    journal_history: tuple[bytes, ...],
    windows_commit: WindowsProjectCommitApi | None,
    staged_source: BoundFileBytes | None,
    evidence_anchor: _WindowsMigrationRecord | None,
    cleanup_state: _MigrationCleanupState,
) -> None:
    def revalidate(context: str) -> None:
        if evidence_anchor is not None:
            _revalidate_windows_evidence(
                evidence_anchor,
                cleanup_state,
                context=context,
            )

    def remember_indeterminate(exc: WorldProjectMigrationError) -> None:
        if exc.reason_code == "world_project_migration_outcome_indeterminate":
            cleanup_state.cleanup_mutated = True

    try:
        if isinstance(lease, WindowsRetainedWorldLifecycle) and staged_source is not None:
            if windows_commit is None:
                _fail(
                    "world_project_migration_state_diverged",
                    "Windows migration source retention cannot be cleaned safely",
                )
            try:
                windows_commit.delete_durable_source_retention()
            except (
                WindowsMigrationCapabilityError,
                WindowsMigrationOutcomeIndeterminate,
                WindowsMigrationPublishError,
                WindowsMigrationStateError,
            ) as exc:
                try:
                    _raise_windows_migration_error(exc)
                except WorldProjectMigrationError as mapped:
                    remember_indeterminate(mapped)
                    raise
            cleanup_state.cleanup_mutated = True
            _migration_transition_hook("after_windows_retention_removed")
            revalidate("after retained source cleanup")

        if backup_was_present:
            current_backup = _read_backup(
                lease.control_path / BACKUP_PATH.name,
                expected_source_hash=expected_source_hash,
                lease=lease,
            )
            if current_backup is None:
                _fail(
                    "world_project_migration_state_diverged",
                    "migration backup is absent after cleanup authorization",
                )
            _backup, current_backup_identity, current_backup_payload = current_backup
            if (
                current_backup_identity != backup_identity
                or current_backup_payload != backup_payload
            ):
                _fail(
                    "world_project_migration_state_diverged",
                    "migration backup diverged before cleanup",
                )
            try:
                _remove_record(
                    lease.control_path / BACKUP_PATH.name,
                    lease=lease,
                    identity=backup_identity,
                    payload=current_backup_payload,
                    context="backup",
                )
            except WorldProjectMigrationError as exc:
                remember_indeterminate(exc)
                raise
            cleanup_state.cleanup_mutated = True
            _migration_transition_hook("after_backup_removed")
            revalidate("between backup and journal cleanup")

        try:
            _remove_record(
                lease.control_path / JOURNAL_PATH.name,
                lease=lease,
                identity=journal_identity,
                payload=journal_history[-1],
                history=journal_history,
                context="journal",
            )
        except WorldProjectMigrationError as exc:
            remember_indeterminate(exc)
            raise
        cleanup_state.cleanup_mutated = True
        revalidate("after journal cleanup")
        _require_post_cleanup_ancestry(lease, context="journal")
        revalidate("before migration success")
    finally:
        if evidence_anchor is not None:
            _close_windows_evidence(
                evidence_anchor,
                cleanup_state,
                context="cleanup anchor",
            )


def _apply_locked_transaction(
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle,
    expected_source_hash: str,
    cleanup: ExitStack,
) -> dict[str, object]:
    from worldforge.world_lifecycle import PROJECT_REPOSITORIES

    root = lease.root
    backup_path = lease.control_path / BACKUP_PATH.name
    journal_path = lease.control_path / JOURNAL_PATH.name
    evidence_path = lease.control_path / EVIDENCE_PATH.name
    lease.assert_current()
    try:
        if isinstance(lease, WindowsRetainedWorldLifecycle):
            preflight = read_windows_bound_bytes(
                lease.api,
                lease.control_handle,
                "project.json",
                limit=MAX_PROJECT_BYTES,
                allowed_link_counts=frozenset({1, 2}),
            )
        else:
            preflight = read_bound_bytes_at(
                lease.control_fd,
                "project.json",
                limit=MAX_PROJECT_BYTES,
            )
        preflight_project = decode_json_object(
            preflight.payload,
            source=root / PROJECT_PATH,
        )
    except (AssetContractError, RuntimeIOError, WindowsMigrationStateError) as exc:
        _fail("world_project_migration_project_invalid", str(exc))
    if preflight_project.get("format_version") == 1:
        _fail(
            "world_project_migration_upgrade_required",
            "Legacy world-project v1 must use worldforge upgrade-world before identity migration",
        )
    current, project = _validated_snapshot(
        root,
        allow_legacy=False,
        lease=lease,
    )
    version = project.get("format_version")
    if version not in PROJECT_REPOSITORIES:
        _fail(
            "world_project_migration_version_invalid",
            "Only coherent world-project versions 2 and 3 can be migrated",
        )

    backup_loaded = _read_backup(
        backup_path,
        expected_source_hash=expected_source_hash,
        lease=lease,
    )
    journal_loaded: (
        tuple[
            tuple[dict[str, Any], ...],
            tuple[bytes, ...],
            tuple[int, int],
            bool,
        ]
        | None
    ) = None
    backup_payload: bytes | None = None
    backup_was_present = backup_loaded is not None
    backup: dict[str, Any] | None = None
    source_identity: tuple[int, int] | None = None
    windows_commit: WindowsProjectCommitApi | None = None

    if version == 3 and backup_loaded is None:
        cleaned = _read_journal_after_backup_cleanup(
            journal_path,
            expected_source_hash=expected_source_hash,
            lease=lease,
        )
        if cleaned is None:
            current_hash = _sha256(current.payload)
            return _result(
                status="already_current",
                mode="apply",
                source_hash=current_hash,
                target_hash=current_hash,
                evidence_sha256=None,
                from_format_version=3,
                to_format_version=3,
            )
        (
            records,
            history,
            journal_identity,
            partial_tail,
            backup_identity,
            target_hash,
            operation_id,
        ) = cleaned
        if _sha256(current.payload) != target_hash:
            _fail("world_project_migration_state_diverged", "migration target diverged")
        target_payload = current.payload
        journal_loaded = records, history, journal_identity, partial_tail
    else:
        if backup_loaded is None:
            current_hash = _sha256(current.payload)
            if version != 2 or current_hash != expected_source_hash:
                _fail(
                    "world_project_migration_expected_hash_mismatch",
                    "World-project expected source hash "
                    f"{expected_source_hash} does not match {current_hash}",
                )
            operation_id = _new_operation_id()
            if isinstance(lease, WindowsRetainedWorldLifecycle):
                if current.change_time_ns is None:
                    _fail(
                        "world_project_migration_state_diverged",
                        "Windows migration source change time is unavailable",
                    )
                _candidate, candidate_target = _target_project(project)
                initial_windows = WindowsProjectCommitApi(
                    lease,
                    operation_id=operation_id,
                    source_identity=current.identity,
                    source_sha256=expected_source_hash,
                    source_change_time_ns=current.change_time_ns,
                    target_payload=candidate_target,
                    byte_limit=MAX_PROJECT_BYTES,
                )
                try:
                    initial_windows.preflight()
                    if (
                        classify_windows_commit_observation(initial_windows.observe())
                        != "stage_target"
                    ):
                        _fail(
                            "world_project_migration_state_diverged",
                            "Windows migration source is not an exact standalone file",
                        )
                except (
                    WindowsMigrationCapabilityError,
                    WindowsMigrationOutcomeIndeterminate,
                    WindowsMigrationPublishError,
                    WindowsMigrationStateError,
                ) as exc:
                    _raise_windows_migration_error(exc)
            backup_payload = _canonical_record(_backup_document(current, operation_id=operation_id))
            try:
                backup_identity = create_append_only_journal(
                    backup_path,
                    backup_payload,
                    max_record_bytes=MAX_RECORD_BYTES,
                )
                _lease_flush_control(lease)
            except FileExistsError:
                backup_loaded = _read_backup(
                    backup_path,
                    expected_source_hash=expected_source_hash,
                    lease=lease,
                )
                if backup_loaded is None:
                    _fail(
                        "world_project_migration_state_diverged",
                        "migration backup diverged",
                    )
                backup, backup_identity, backup_payload = backup_loaded
                backup_was_present = True
                operation_id = backup["operation_id"]
            except DirectoryPublishError as exc:
                _fail(
                    "world_project_migration_state_diverged",
                    f"migration backup diverged: {exc}",
                )
            else:
                if isinstance(lease, WindowsRetainedWorldLifecycle):
                    backup_loaded = _read_backup(
                        backup_path,
                        expected_source_hash=expected_source_hash,
                        lease=lease,
                    )
                    if backup_loaded is None:
                        _fail(
                            "world_project_migration_state_diverged",
                            "migration backup diverged after creation",
                        )
                    backup, backup_identity, backup_payload = backup_loaded
                else:
                    backup = _validate_backup(
                        backup_payload,
                        expected_source_hash=expected_source_hash,
                    )
                backup_was_present = True
            _migration_transition_hook("after_backup_created")
        else:
            backup, backup_identity, backup_payload = backup_loaded
            operation_id = backup["operation_id"]

        if backup is None:
            _fail("world_project_migration_state_diverged", "migration backup diverged")
        source_identity = _identity(backup.get("source_identity"), context="backup")
        source_project = backup["project_document"]
        _target, target_payload = _target_project(source_project)
        target_hash = _sha256(target_payload)
        journal_loaded = _read_journal(
            journal_path,
            source_hash=expected_source_hash,
            target_hash=target_hash,
            backup_identity=backup_identity,
            operation_id=operation_id,
            lease=lease,
        )

        if isinstance(lease, WindowsRetainedWorldLifecycle):
            source_change_time_ns = backup.get("source_change_time_ns")
            if not isinstance(source_change_time_ns, int):
                _fail(
                    "world_project_migration_state_diverged",
                    "Windows migration source change time diverged",
                )
            windows_commit = WindowsProjectCommitApi(
                lease,
                operation_id=operation_id,
                source_identity=source_identity,
                source_sha256=expected_source_hash,
                source_change_time_ns=source_change_time_ns,
                target_payload=target_payload,
                byte_limit=MAX_PROJECT_BYTES,
            )
            cleanup.callback(windows_commit.release_source_seal)

    staging_name = f".project.json.migration.{operation_id}.exchange"
    try:
        if isinstance(lease, WindowsRetainedWorldLifecycle):
            shape_target_stage = _lease_read_optional_control(
                lease,
                f".project.json.migration.{operation_id}.target",
                limit=max(MAX_PROJECT_BYTES, len(target_payload)),
            )
            shape_source_retention = _lease_read_optional_control(
                lease,
                staging_name,
                limit=MAX_PROJECT_BYTES,
                allowed_windows_links=frozenset({1, 2}),
            )
            shape_staged_role = _windows_recovery_staged_role(
                project_version=version,
                target_stage=shape_target_stage,
                source_retention=shape_source_retention,
                source_identity=source_identity,
                source_hash=expected_source_hash,
                target_hash=target_hash,
            )
        else:
            shape_staged = _lease_read_optional_control(
                lease,
                staging_name,
                limit=max(MAX_PROJECT_BYTES, len(target_payload)),
            )
            shape_staged_role = _staged_role(
                shape_staged,
                source_identity=source_identity,
                source_hash=expected_source_hash,
                target_hash=target_hash,
            )
        shape_evidence = _lease_read_optional_control(
            lease,
            EVIDENCE_PATH.name,
            limit=MAX_RECORD_BYTES,
        )
    except AssetContractError as exc:
        _fail(
            "world_project_migration_state_diverged",
            f"migration recovery state diverged: {exc}",
        )
    journal_state = None if journal_loaded is None else journal_loaded[0][-1]["state"]
    if journal_state in {"verified", "cleanup_authorized"} and shape_evidence is None:
        _fail(
            "world_project_migration_state_diverged",
            "migration evidence is absent after verification",
        )
    _validate_recovery_shape(
        project_version=version,
        journal_state=journal_state,
        backup_present=backup_was_present,
        staged_role=shape_staged_role,
        evidence_present=shape_evidence is not None,
        windows_commit_forward=isinstance(lease, WindowsRetainedWorldLifecycle),
    )

    if journal_loaded is None:
        prepared = _journal_document(
            state="prepared",
            operation_id=operation_id,
            source_hash=expected_source_hash,
            target_hash=target_hash,
            backup_identity=backup_identity,
            target_identity=None,
            evidence_identity=None,
            evidence_sha256=None,
        )
        prepared_payload = _canonical_record(prepared)
        try:
            journal_identity = create_append_only_journal(
                journal_path,
                prepared_payload,
                max_record_bytes=MAX_RECORD_BYTES,
            )
            _lease_flush_control(lease)
        except FileExistsError:
            journal_loaded = _read_journal(
                journal_path,
                source_hash=expected_source_hash,
                target_hash=target_hash,
                backup_identity=backup_identity,
                operation_id=operation_id,
                lease=lease,
            )
            if journal_loaded is None:
                _fail(
                    "world_project_migration_state_diverged",
                    "migration journal diverged",
                )
            records, history, journal_identity, partial_tail = journal_loaded
        except DirectoryPublishError as exc:
            _fail(
                "world_project_migration_state_diverged",
                f"migration journal diverged: {exc}",
            )
        else:
            if isinstance(lease, WindowsRetainedWorldLifecycle):
                journal_loaded = _read_journal(
                    journal_path,
                    source_hash=expected_source_hash,
                    target_hash=target_hash,
                    backup_identity=backup_identity,
                    operation_id=operation_id,
                    lease=lease,
                )
                if journal_loaded is None:
                    _fail(
                        "world_project_migration_state_diverged",
                        "migration journal diverged after creation",
                    )
                records, history, journal_identity, partial_tail = journal_loaded
            else:
                records = (prepared,)
                history = (prepared_payload,)
                partial_tail = False
        _migration_transition_hook("after_journal_prepared")
    else:
        records, history, journal_identity, partial_tail = journal_loaded

    state = records[-1]["state"]
    if state == "prepared":
        if source_identity is None:
            _fail(
                "world_project_migration_state_diverged",
                "migration source identity diverged",
            )
        if isinstance(lease, WindowsRetainedWorldLifecycle):
            if windows_commit is None:
                _fail(
                    "world_project_migration_state_diverged",
                    "Windows migration commit adapter is unavailable",
                )
            renamed_this_attempt = False

            def windows_transition(event: str) -> None:
                nonlocal renamed_this_attempt
                if event == "before_windows_rename":
                    _migration_transition_hook("before_replacement")
                    _migration_transition_hook("before_identity_exchange")
                    lease.assert_current()
                elif event == "after_windows_rename":
                    renamed_this_attempt = True
                    _migration_transition_hook("after_identity_exchange")
                _migration_transition_hook(event)

            try:
                commit_windows_project(
                    windows_commit,
                    transition_hook=windows_transition,
                    retain_seal=True,
                )
            except (
                WindowsMigrationCapabilityError,
                WindowsMigrationOutcomeIndeterminate,
                WindowsMigrationPublishError,
                WindowsMigrationStateError,
            ) as exc:
                _raise_windows_migration_error(exc)
            if windows_commit.target_identity is None:
                _fail(
                    "world_project_migration_outcome_indeterminate",
                    "Windows migration target identity is unavailable after commit",
                )
            target_identity = windows_commit.target_identity
            lease.assert_current()
            if renamed_this_attempt:
                _migration_transition_hook("after_replacement")
        else:
            try:
                staged = read_optional_bound_bytes_at(
                    lease.control_fd,
                    staging_name,
                    limit=max(MAX_PROJECT_BYTES, len(target_payload)),
                )
            except AssetContractError as exc:
                _fail(
                    "world_project_migration_state_diverged",
                    f"migration exchange staging diverged: {exc}",
                )
            current, current_project = _validated_snapshot(
                root,
                allow_legacy=False,
                lease=lease,
            )
            current_hash = _sha256(current.payload)
            if current_hash == expected_source_hash:
                if (
                    current.identity != source_identity
                    or current_project.get("format_version") != 2
                ):
                    _fail(
                        "world_project_migration_state_diverged",
                        "migration source identity diverged",
                    )
                if staged is not None:
                    if staged.payload != target_payload:
                        _fail(
                            "world_project_migration_state_diverged",
                            "migration exchange staging diverged",
                        )
                    try:
                        remove_bound_file_at(
                            lease.control_fd,
                            staging_name,
                            expected_identity=staged.identity,
                            expected_sha256=target_hash,
                            limit=max(MAX_PROJECT_BYTES, len(target_payload)),
                        )
                        os.fsync(lease.control_fd)
                    except (AssetContractError, OSError) as exc:
                        _fail(
                            "world_project_migration_state_diverged",
                            f"migration exchange staging diverged: {exc}",
                        )
                _migration_transition_hook("before_replacement")

                def before_exchange() -> None:
                    _migration_transition_hook("before_identity_exchange")
                    lease.assert_current()

                try:
                    target_identity = write_bytes_identity_atomic_replace_at(
                        lease.control_fd,
                        "project.json",
                        target_payload,
                        expected_sha256=expected_source_hash,
                        expected_identity=source_identity,
                        staging_name=staging_name,
                        before_exchange=before_exchange,
                        after_exchange=lambda: _migration_transition_hook(
                            "after_identity_exchange"
                        ),
                    )
                except AssetContractError as exc:
                    detail = str(exc)
                    normalized_detail = detail.casefold()
                    if "unavailable" in normalized_detail:
                        raise WorldProjectMigrationError(
                            "world_project_migration_capability_unavailable",
                            detail,
                        ) from exc
                    if "identity" in normalized_detail:
                        detail = f"migration source identity diverged: {detail}"
                    elif "bytes diverged" in normalized_detail:
                        detail = "World-project control changed before replacement"
                    raise WorldProjectMigrationError(
                        "world_project_migration_cas_mismatch",
                        detail,
                    ) from exc
                lease.assert_current()
                _migration_transition_hook("after_replacement")
            elif current_hash == target_hash and current_project.get("format_version") == 3:
                if (
                    staged is None
                    or staged.identity != source_identity
                    or _sha256(staged.payload) != expected_source_hash
                ):
                    _fail(
                        "world_project_migration_state_diverged",
                        "migration exchange recovery diverged",
                    )
                target_identity = current.identity
            else:
                _fail("world_project_migration_state_diverged", "migration source diverged")
        replaced = _journal_document(
            state="replaced",
            operation_id=operation_id,
            source_hash=expected_source_hash,
            target_hash=target_hash,
            backup_identity=backup_identity,
            target_identity=target_identity,
            evidence_identity=None,
            evidence_sha256=None,
        )
        history = _append_journal(
            journal_path,
            lease=lease,
            identity=journal_identity,
            history=history,
            updated=replaced,
            repair_partial_tail=partial_tail,
        )
        records = (*records, replaced)
        partial_tail = False
        _migration_transition_hook("after_journal_replaced")

    if isinstance(lease, WindowsRetainedWorldLifecycle):
        try:
            staged_source = _lease_read_optional_control(
                lease,
                staging_name,
                limit=MAX_PROJECT_BYTES,
                allowed_windows_links=frozenset({1}),
            )
        except AssetContractError as exc:
            _fail(
                "world_project_migration_state_diverged",
                f"migration Windows source retention diverged: {exc}",
            )
        if staged_source is not None:
            if (
                source_identity is None
                or staged_source.identity != source_identity
                or _sha256(staged_source.payload) != expected_source_hash
                or windows_commit is None
            ):
                _fail(
                    "world_project_migration_state_diverged",
                    "migration Windows source retention diverged",
                )
            if windows_commit.target_identity is None:
                try:
                    commit_windows_project(windows_commit, retain_seal=True)
                except (
                    WindowsMigrationCapabilityError,
                    WindowsMigrationOutcomeIndeterminate,
                    WindowsMigrationPublishError,
                    WindowsMigrationStateError,
                ) as exc:
                    _raise_windows_migration_error(exc)
        elif records[-1]["state"] != "cleanup_authorized":
            _fail(
                "world_project_migration_state_diverged",
                "Windows migration source retention disappeared before cleanup authorization",
            )
    else:
        try:
            staged_source = read_optional_bound_bytes_at(
                lease.control_fd,
                staging_name,
                limit=MAX_PROJECT_BYTES,
            )
        except AssetContractError as exc:
            _fail(
                "world_project_migration_state_diverged",
                f"migration exchange staging diverged: {exc}",
            )
        if staged_source is not None:
            if (
                source_identity is None
                or staged_source.identity != source_identity
                or _sha256(staged_source.payload) != expected_source_hash
            ):
                _fail(
                    "world_project_migration_state_diverged",
                    "migration exchange source diverged",
                )
            try:
                remove_bound_file_at(
                    lease.control_fd,
                    staging_name,
                    expected_identity=source_identity,
                    expected_sha256=expected_source_hash,
                    limit=MAX_PROJECT_BYTES,
                )
                os.fsync(lease.control_fd)
            except (AssetContractError, OSError) as exc:
                _fail(
                    "world_project_migration_state_diverged",
                    f"migration exchange source cleanup diverged: {exc}",
                )

    target_identity = _identity(records[-1].get("target_identity"), context="journal")
    if (
        windows_commit is not None
        and windows_commit.target_identity is not None
        and windows_commit.target_identity != target_identity
    ):
        _fail(
            "world_project_migration_state_diverged",
            "Windows migration target identity diverged from durable journal",
        )
    current, current_project = _validated_snapshot(
        root,
        allow_legacy=False,
        lease=lease,
    )
    if (
        current.identity != target_identity
        or _sha256(current.payload) != target_hash
        or current_project.get("format_version") != 3
        or current_project.get("tool_repository") != "world-forge"
    ):
        _fail("world_project_migration_state_diverged", "migration target diverged")

    evidence_loaded = _read_evidence(
        evidence_path,
        source_hash=expected_source_hash,
        target_hash=target_hash,
        target_identity=target_identity,
        operation_id=operation_id,
        lease=lease,
    )
    if evidence_loaded is None:
        if records[-1]["state"] != "replaced":
            _fail(
                "world_project_migration_state_diverged",
                "migration evidence is absent after verification",
            )
        evidence_payload = _canonical_record(
            _evidence_document(
                operation_id=operation_id,
                source_hash=expected_source_hash,
                target_hash=target_hash,
                target_identity=target_identity,
            )
        )
        try:
            evidence_identity = create_append_only_journal(
                evidence_path,
                evidence_payload,
                max_record_bytes=MAX_RECORD_BYTES,
            )
            _lease_flush_control(lease)
        except FileExistsError:
            evidence_loaded = _read_evidence(
                evidence_path,
                source_hash=expected_source_hash,
                target_hash=target_hash,
                target_identity=target_identity,
                operation_id=operation_id,
                lease=lease,
            )
            if evidence_loaded is None:
                _fail(
                    "world_project_migration_state_diverged",
                    "migration evidence diverged",
                )
            _evidence, evidence_identity, evidence_payload = evidence_loaded
        except DirectoryPublishError as exc:
            _fail(
                "world_project_migration_state_diverged",
                f"migration evidence diverged: {exc}",
            )
        else:
            if isinstance(lease, WindowsRetainedWorldLifecycle):
                evidence_loaded = _read_evidence(
                    evidence_path,
                    source_hash=expected_source_hash,
                    target_hash=target_hash,
                    target_identity=target_identity,
                    operation_id=operation_id,
                    lease=lease,
                )
                if evidence_loaded is None:
                    _fail(
                        "world_project_migration_state_diverged",
                        "migration evidence diverged after creation",
                    )
                _evidence, evidence_identity, evidence_payload = evidence_loaded
        _migration_transition_hook("after_evidence_created")
    else:
        _evidence, evidence_identity, evidence_payload = evidence_loaded
    evidence_sha256 = _sha256(evidence_payload)
    cleanup_state = _MigrationCleanupState()
    evidence_anchor: _WindowsMigrationRecord | None = None
    if isinstance(lease, WindowsRetainedWorldLifecycle):
        evidence_anchor = _retain_windows_evidence(
            lease,
            name=EVIDENCE_PATH.name,
            expected_identity=evidence_identity,
            expected_payload=evidence_payload,
        )
        cleanup.callback(
            _close_windows_evidence,
            evidence_anchor,
            cleanup_state,
            context="migration unwind",
        )

    if records[-1]["state"] == "replaced":
        verified = _journal_document(
            state="verified",
            operation_id=operation_id,
            source_hash=expected_source_hash,
            target_hash=target_hash,
            backup_identity=backup_identity,
            target_identity=target_identity,
            evidence_identity=evidence_identity,
            evidence_sha256=evidence_sha256,
        )
        history = _append_journal(
            journal_path,
            lease=lease,
            identity=journal_identity,
            history=history,
            updated=verified,
            repair_partial_tail=partial_tail,
        )
        records = (*records, verified)
        partial_tail = False
        _migration_transition_hook("after_journal_verified")
    else:
        recorded_evidence_identity = _identity(
            records[-1].get("evidence_identity"),
            context="journal",
        )
        if (
            recorded_evidence_identity != evidence_identity
            or records[-1].get("evidence_sha256") != evidence_sha256
        ):
            _fail("world_project_migration_state_diverged", "migration evidence diverged")

    if evidence_anchor is not None:
        _revalidate_windows_evidence(
            evidence_anchor,
            cleanup_state,
            context="before cleanup authorization",
        )

    if records[-1]["state"] == "verified":
        cleanup_authorized = _journal_document(
            state="cleanup_authorized",
            operation_id=operation_id,
            source_hash=expected_source_hash,
            target_hash=target_hash,
            backup_identity=backup_identity,
            target_identity=target_identity,
            evidence_identity=evidence_identity,
            evidence_sha256=evidence_sha256,
        )
        history = _append_journal(
            journal_path,
            lease=lease,
            identity=journal_identity,
            history=history,
            updated=cleanup_authorized,
            repair_partial_tail=partial_tail,
        )
        records = (*records, cleanup_authorized)
        _migration_transition_hook("after_cleanup_authorized")
        if evidence_anchor is not None:
            _revalidate_windows_evidence(
                evidence_anchor,
                cleanup_state,
                context="after cleanup authorization",
            )

    _cleanup_authorized_records(
        lease,
        expected_source_hash=expected_source_hash,
        backup_was_present=backup_was_present,
        backup_identity=backup_identity,
        backup_payload=backup_payload,
        journal_identity=journal_identity,
        journal_history=history,
        windows_commit=windows_commit,
        staged_source=staged_source,
        evidence_anchor=evidence_anchor,
        cleanup_state=cleanup_state,
    )
    return _result(
        status="migrated",
        mode="apply",
        source_hash=expected_source_hash,
        target_hash=target_hash,
        evidence_sha256=evidence_sha256,
    )


def _apply_locked(
    lease: RetainedWorldLifecycle | WindowsRetainedWorldLifecycle,
    expected_source_hash: str,
) -> dict[str, object]:
    with ExitStack() as cleanup:
        return _apply_locked_transaction(lease, expected_source_hash, cleanup)


def migrate_world_project(
    project_root: str | Path,
    *,
    expected_source_hash: str,
    mode: str,
) -> dict[str, object]:
    """Dry-run or durably migrate one retained world-project v2 identity to v3."""

    expected = _validate_expected_hash(expected_source_hash)
    if mode not in {"dry-run", "apply"}:
        _fail("world_project_migration_mode_invalid", "mode must be dry-run or apply")
    root = Path(project_root)
    if mode == "dry-run":
        return _dry_run(root, expected)

    from worldforge.world_lock import exclusive_retained_world_lifecycle

    try:
        with exclusive_retained_world_lifecycle(
            root,
            error_type=WorkflowError,
        ) as lease:
            return _apply_locked(lease, expected)
    except WorldProjectMigrationError:
        raise
    except WorkflowError as exc:
        detail = str(exc)
        normalized = detail.casefold()
        if "primitives are unavailable" in normalized:
            reason_code = "world_project_migration_capability_unavailable"
        elif "changed" in normalized:
            reason_code = "world_project_migration_project_changed"
        elif "lock" in normalized or "already in progress" in normalized:
            reason_code = "world_project_migration_lock_unavailable"
        else:
            reason_code = "world_project_migration_project_invalid"
        raise WorldProjectMigrationError(reason_code, detail) from exc
    except OSError as exc:
        raise WorldProjectMigrationError(
            "world_project_migration_io_failed",
            f"World-project migration I/O failed: {exc}",
        ) from exc
