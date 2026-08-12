"""Deterministic generic game packaging and transactional safe extraction."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from gamepack_runtime.distribution import (
    GAME_LOCK_PATH,
    GAME_MANIFEST_PATH,
    StandaloneDistributionError,
    canonical_contract_bytes,
    capture_standalone_tree_with_directories,
    decode_json_object,
)
from gamepack_runtime.game_package import (
    GAME_PACKAGE_FORMAT,
    MAX_GAME_PACKAGE_ARCHIVE_BYTES,
    MAX_GAME_PACKAGE_MANIFEST_BYTES,
    GamePackageError,
    VerifiedGamePackage,
    build_game_package_from_files,
    validate_game_package_document,
    verify_game_package_bytes,
    verify_game_package_file,
)
from gamepack_runtime.persistence_io import (
    PersistenceIOError,
    held_persistence_lock,
    publish_bytes_noreplace,
)
from worldforge.asset_io import AssetContractError, open_verified_output_parent
from worldforge.directory_publish import (
    DirectoryIdentity,
    DirectoryPublishError,
    DirectoryPublishIndeterminateError,
    DirectoryPublishRecoveryRequiredError,
    append_append_only_journal,
    create_append_only_journal,
    create_retained_stage,
    fsync_directory,
    publish_directory_noreplace,
    quarantine_and_remove_verified_directory,
    read_append_only_journal_history_state,
    remove_append_only_journal,
    retained_journal_evidence_path,
    retained_recovery_evidence,
)
from worldforge.file_stat import (
    file_identity,
    is_link_or_reparse,
    path_file_stat,
)
from worldforge.repository_boundary import FORGE_ROOT, repository_kind
from worldforge.standalone_game import (
    StandaloneGameError,
    VerifiedStandaloneGame,
    verify_standalone_game,
)

_JOURNAL_FORMAT = "world-forge.game_package_extraction_journal"
_JOURNAL_VERSION = 1
_JOURNAL_STATES = ("intent", "copying", "ready")
_JOURNAL_RECORD_BYTES = MAX_GAME_PACKAGE_MANIFEST_BYTES
_JOURNAL_FILE_BYTES = _JOURNAL_RECORD_BYTES * 4
_JOURNAL_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "operation_id",
        "state",
        "stage_name",
        "destination_name",
        "destination_path_hash",
        "parent_identity",
        "stage_identity",
        "archive_sha256",
        "package_manifest",
        "standalone_game_hash",
        "payload_lock_hash",
        "payload_tree_hash",
        "manifest_sha256",
        "manifest_size_bytes",
        "lock_sha256",
        "lock_size_bytes",
    }
)
_PublicationHook = Callable[[str, Path | None], None]
_AuthorityHook = Callable[[str, Mapping[str, object]], None]


class WorldForgeGamePackageError(ValueError):
    """Raised when package publication or extraction cannot prove ownership."""

    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        recovery_evidence: Mapping[str, object] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.detail = detail
        self.recovery_evidence = copy.deepcopy(dict(recovery_evidence or {}))
        super().__init__(f"{reason_code}: {detail}")


def _fail(
    reason_code: str,
    detail: str,
    *,
    recovery_evidence: Mapping[str, object] | None = None,
) -> None:
    raise WorldForgeGamePackageError(
        reason_code,
        detail,
        recovery_evidence=recovery_evidence,
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    left_name = os.path.normcase(os.path.realpath(os.fspath(left)))
    right_name = os.path.normcase(os.path.realpath(os.fspath(right)))
    try:
        common = os.path.commonpath((left_name, right_name))
    except ValueError:
        return False
    return common in {left_name, right_name}


def _optional_directory_identity(path: Path) -> DirectoryIdentity | None:
    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("game_package_destination_invalid", str(exc))
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        _fail("game_package_destination_invalid", f"{path} is unsafe")
    return file_identity(info)


def _require_external_destination(destination: Path) -> DirectoryIdentity:
    parent_identity = _optional_directory_identity(destination.parent)
    if parent_identity is None:
        _fail(
            "game_package_parent_missing",
            "destination parent must already exist",
        )
    if destination == FORGE_ROOT or FORGE_ROOT in destination.parents:
        _fail(
            "game_package_destination_invalid",
            "destination must be external to the Forge repository",
        )
    for ancestor in (destination.parent, *destination.parent.parents):
        kind = repository_kind(ancestor)
        if kind is not None:
            _fail(
                "game_package_destination_invalid",
                f"destination cannot be nested inside a {kind} repository",
            )
    return parent_identity


def _optional_file_identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("game_package_file_invalid", str(exc))
    if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        _fail("game_package_file_invalid", f"{path} is not a regular file")
    if info.st_nlink != 1:
        _fail(
            "game_package_publication_conflict",
            f"{path} is already bound into another publication state",
        )
    return file_identity(info)


def _publish_archive(
    destination: Path,
    payload: bytes,
    *,
    parent_identity: DirectoryIdentity,
    publication_hook: _PublicationHook | None = None,
) -> tuple[int, int]:
    def validate_written_archive(written: bytes) -> None:
        verified = verify_game_package_bytes(written)
        try:
            if verified.archive_bytes != payload:
                _fail(
                    "game_package_publication_indeterminate",
                    "temporary archive differs from its exact source bytes",
                )
        finally:
            verified.close()

    try:
        return publish_bytes_noreplace(
            destination.parent,
            destination.name,
            payload,
            expected_parent_identity=parent_identity,
            limit=MAX_GAME_PACKAGE_ARCHIVE_BYTES,
            validate=validate_written_archive,
            publication_hook=publication_hook,
            mode=0o644,
        )
    except WorldForgeGamePackageError:
        raise
    except FileExistsError as exc:
        _fail("game_package_destination_exists", str(exc))
    except PersistenceIOError as exc:
        _fail("game_package_publication_indeterminate", str(exc))
    except OSError as exc:
        _fail("game_package_publication_failed", str(exc))
    _fail("game_package_publication_failed", "archive was not published")


def package_game(
    standalone_game: str | Path,
    destination: str | Path,
    *,
    expected_source_identity: DirectoryIdentity | None = None,
    expected_parent_identity: DirectoryIdentity | None = None,
    _verified_source: VerifiedStandaloneGame | None = None,
    _publication_hook: _PublicationHook | None = None,
) -> VerifiedGamePackage:
    if not ((sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt"):
        _fail(
            "game_package_platform_unsupported",
            "generic game packaging supports only Linux and Windows",
        )
    source_path = Path(os.path.abspath(os.fspath(standalone_game)))
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    parent_identity = _require_external_destination(destination_path)
    if expected_parent_identity is not None and parent_identity != expected_parent_identity:
        _fail(
            "game_package_destination_invalid",
            "archive parent differs from the expected identity",
        )
    source = _verified_source
    close_source = False
    if source is None:
        source = verify_standalone_game(
            source_path,
            expected_root_identity=expected_source_identity,
        )
        close_source = True
    try:
        if source.root != source_path or (
            expected_source_identity is not None
            and source.root_identity != expected_source_identity
        ):
            _fail(
                "game_package_standalone_identity_mismatch",
                "standalone source differs from the expected retained identity",
            )
        try:
            built = build_game_package_from_files(source.files)
        except GamePackageError as exc:
            _fail(exc.reason_code, exc.detail)
    finally:
        if close_source:
            source.close()
    if _paths_overlap(source_path, destination_path):
        _fail(
            "game_package_path_overlap",
            "standalone source and archive destination must be disjoint",
        )
    if _optional_file_identity(destination_path) is not None:
        _fail("game_package_destination_exists", "archive destination already exists")
    _publish_archive(
        destination_path,
        built.archive_bytes,
        parent_identity=parent_identity,
        publication_hook=_publication_hook,
    )
    try:
        visible = verify_game_package_file(destination_path)
    except GamePackageError as exc:
        _fail("game_package_publication_indeterminate", str(exc))
    if (
        visible.archive_sha256 != built.archive_sha256
        or visible.manifest["content_hash"] != built.manifest["content_hash"]
    ):
        _fail(
            "game_package_publication_indeterminate",
            "published archive differs from its exact verified source bytes",
        )
    return visible


def publish_verified_game_package(
    standalone: VerifiedStandaloneGame,
    package: VerifiedGamePackage,
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity,
    expected_archive_sha256: str,
    expected_size_bytes: int,
    _publication_hook: _PublicationHook | None = None,
) -> tuple[VerifiedGamePackage, tuple[int, int]]:
    """Publish exact prebuilt package bytes under retained standalone authority.

    This entry point is intentionally object-based for Studio's private grant
    boundary.  The path-oriented :func:`package_game` API and v1 archive bytes
    remain unchanged.  The standalone tree is recaptured before and after the
    exclusive publication so one archive can never be committed against a
    different source generation.
    """

    if not isinstance(standalone, VerifiedStandaloneGame) or not isinstance(
        package, VerifiedGamePackage
    ):
        _fail(
            "game_package_publication_invalid",
            "verified standalone and package objects are required",
        )
    if not ((sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt"):
        _fail(
            "game_package_platform_unsupported",
            "generic game packaging supports only Linux and Windows",
        )
    if (
        not isinstance(expected_archive_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_archive_sha256) is None
        or isinstance(expected_size_bytes, bool)
        or not isinstance(expected_size_bytes, int)
        or not 1 <= expected_size_bytes <= MAX_GAME_PACKAGE_ARCHIVE_BYTES
    ):
        _fail(
            "game_package_publication_invalid",
            "expected archive identity is invalid",
        )
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    parent_identity = _require_external_destination(destination_path)
    if parent_identity != expected_parent_identity:
        _fail(
            "game_package_destination_invalid",
            "archive parent differs from the retained identity",
        )
    if _paths_overlap(standalone.root, destination_path):
        _fail(
            "game_package_path_overlap",
            "standalone source and archive destination must be disjoint",
        )
    if _optional_file_identity(destination_path) is not None:
        _fail("game_package_destination_exists", "archive destination already exists")

    def recapture() -> VerifiedStandaloneGame:
        try:
            return verify_standalone_game(
                standalone.root,
                expected_root_identity=standalone.root_identity,
            )
        except StandaloneGameError as exc:
            _fail(
                "game_package_standalone_identity_mismatch",
                str(exc),
            )

    refreshed = recapture()
    try:
        if (
            refreshed.manifest != standalone.manifest
            or refreshed.lock != standalone.lock
            or dict(refreshed.files) != dict(standalone.files)
        ):
            _fail(
                "game_package_standalone_identity_mismatch",
                "standalone source differs from its retained snapshot",
            )
        try:
            rebuilt = build_game_package_from_files(refreshed.files)
        except GamePackageError as exc:
            _fail(exc.reason_code, exc.detail)
        if (
            rebuilt.archive_bytes != package.archive_bytes
            or rebuilt.manifest != package.manifest
            or package.archive_sha256 != expected_archive_sha256
            or len(package.archive_bytes) != expected_size_bytes
        ):
            _fail(
                "game_package_publication_invalid",
                "prebuilt archive differs from the exact standalone source",
            )
    finally:
        refreshed.close()

    published_identity = _publish_archive(
        destination_path,
        package.archive_bytes,
        parent_identity=parent_identity,
        publication_hook=_publication_hook,
    )
    try:
        visible = verify_game_package_file(destination_path)
    except GamePackageError as exc:
        _fail("game_package_publication_indeterminate", str(exc))
    if (
        visible.archive_bytes != package.archive_bytes
        or visible.archive_sha256 != expected_archive_sha256
        or visible.manifest != package.manifest
    ):
        visible.close()
        _fail(
            "game_package_publication_indeterminate",
            "published archive differs from its exact private bytes",
        )
    confirmed = recapture()
    try:
        if (
            confirmed.manifest != standalone.manifest
            or confirmed.lock != standalone.lock
            or dict(confirmed.files) != dict(standalone.files)
        ):
            visible.close()
            _fail(
                "game_package_publication_indeterminate",
                "standalone source changed around archive publication",
            )
    finally:
        confirmed.close()
    return visible, published_identity


def verify_game_package(
    path: str | Path,
    *,
    expected_file_identity: DirectoryIdentity | None = None,
) -> VerifiedGamePackage:
    source = Path(os.path.abspath(os.fspath(path)))
    try:
        before = path_file_stat(source)
        if (
            is_link_or_reparse(before)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or (
                expected_file_identity is not None
                and file_identity(before) != expected_file_identity
            )
        ):
            _fail(
                "game_package_file_invalid",
                "package source differs from the expected retained identity",
            )
        verified = verify_game_package_file(source)
        after = path_file_stat(source)
        if (
            is_link_or_reparse(after)
            or not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or file_identity(after) != file_identity(before)
        ):
            _fail(
                "game_package_file_invalid",
                "package source identity changed around retained verification",
            )
        return verified
    except GamePackageError as exc:
        _fail(exc.reason_code, exc.detail)
    except OSError as exc:
        _fail("game_package_file_invalid", str(exc))


def _journal_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.game-package-extraction.journal.json"


def _lock_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.game-package-extraction.lock"


@contextmanager
def _destination_lock(
    destination: Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> Iterator[None]:
    body_error: BaseException | None = None
    try:
        if expected_parent_identity is None:
            with held_persistence_lock(_lock_path(destination)):
                try:
                    yield
                except BaseException as exc:
                    body_error = exc
                    raise
        else:
            with open_verified_output_parent(destination.parent) as parent:
                if parent.identities[-1] != expected_parent_identity:
                    _fail(
                        "game_package_destination_invalid",
                        "destination parent differs from the expected retained identity",
                    )
                with held_persistence_lock(_lock_path(destination)):
                    try:
                        yield
                    except BaseException as exc:
                        body_error = exc
                        raise
    except AssetContractError as exc:
        if body_error is not None:
            body_error.add_note(f"Game package retained parent cleanup failed: {exc}")
            raise body_error from None
        _fail("game_package_extraction_lock_failed", str(exc))
    except PersistenceIOError as exc:
        if body_error is exc:
            raise
        if body_error is not None:
            body_error.add_note(f"Game package extraction lock cleanup failed: {exc}")
            raise body_error from None
        reason = (
            "game_package_extraction_busy"
            if "lock" in str(exc).casefold()
            else "game_package_extraction_lock_failed"
        )
        _fail(reason, str(exc))


def _identity_document(identity: DirectoryIdentity) -> dict[str, int]:
    return {"device": identity[0], "inode": identity[1]}


def _identity_from_document(
    value: object,
    *,
    context: str = "stage",
) -> DirectoryIdentity:
    if (
        type(value) is not dict
        or set(value) != {"device", "inode"}
        or type(value.get("device")) is not int
        or type(value.get("inode")) is not int
        or value["device"] < 0
        or value["inode"] < 0
    ):
        _fail("game_package_journal_invalid", f"{context} identity is invalid")
    return value["device"], value["inode"]


def _destination_path_hash(destination: Path) -> str:
    normalized = os.path.normcase(os.path.normpath(os.path.abspath(destination)))
    return hashlib.sha256(os.fsencode(normalized)).hexdigest()


def _journal_document(
    *,
    operation_id: str,
    state: str,
    stage: Path,
    destination: Path,
    parent_identity: DirectoryIdentity,
    stage_identity: DirectoryIdentity | None,
    archive_sha256: str,
    package_manifest: Mapping[str, Any],
    files: Mapping[str, bytes],
) -> dict[str, object]:
    manifest_payload = files[GAME_MANIFEST_PATH]
    lock_payload = files[GAME_LOCK_PATH]
    return {
        "format": _JOURNAL_FORMAT,
        "format_version": _JOURNAL_VERSION,
        "operation_id": operation_id,
        "state": state,
        "stage_name": stage.name,
        "destination_name": destination.name,
        "destination_path_hash": _destination_path_hash(destination),
        "parent_identity": _identity_document(parent_identity),
        "stage_identity": (None if stage_identity is None else _identity_document(stage_identity)),
        "archive_sha256": archive_sha256,
        "package_manifest": copy.deepcopy(dict(package_manifest)),
        "standalone_game_hash": package_manifest["standalone_game"]["content_hash"],
        "payload_lock_hash": package_manifest["payload_lock"]["content_hash"],
        "payload_tree_hash": package_manifest["payload_lock"]["tree_hash"],
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "manifest_size_bytes": len(manifest_payload),
        "lock_sha256": hashlib.sha256(lock_payload).hexdigest(),
        "lock_size_bytes": len(lock_payload),
    }


def _validate_journal(value: object, destination: Path) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _JOURNAL_FIELDS:
        _fail("game_package_journal_invalid", "journal fields are not closed")
    document = copy.deepcopy(value)
    if (
        document.get("format") != _JOURNAL_FORMAT
        or document.get("format_version") != _JOURNAL_VERSION
        or document.get("destination_name") != destination.name
        or document.get("destination_path_hash") != _destination_path_hash(destination)
    ):
        _fail("game_package_journal_invalid", "journal identity is invalid")
    parent_identity = _identity_from_document(
        document.get("parent_identity"),
        context="parent",
    )
    if _optional_directory_identity(destination.parent) != parent_identity:
        _fail(
            "game_package_journal_invalid",
            "journal parent identity differs from its destination",
        )
    operation_id = document.get("operation_id")
    if type(operation_id) is not str or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        _fail("game_package_journal_invalid", "journal operation ID is invalid")
    state_value = document.get("state")
    if state_value not in _JOURNAL_STATES:
        _fail("game_package_journal_invalid", "journal state is invalid")
    stage_name = document.get("stage_name")
    if (
        type(stage_name) is not str
        or stage_name != f".{destination.name}.game-package-stage-{operation_id}"
    ):
        _fail("game_package_journal_invalid", "journal stage name is invalid")
    if state_value == "intent":
        if document.get("stage_identity") is not None:
            _fail("game_package_journal_invalid", "intent cannot bind a stage")
    else:
        _identity_from_document(document.get("stage_identity"))
    for field in (
        "archive_sha256",
        "standalone_game_hash",
        "payload_lock_hash",
        "payload_tree_hash",
        "manifest_sha256",
        "lock_sha256",
    ):
        value_hash = document.get(field)
        if type(value_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", value_hash) is None:
            _fail("game_package_journal_invalid", f"journal {field} is invalid")
    for field in ("manifest_size_bytes", "lock_size_bytes"):
        size = document.get(field)
        if type(size) is not int or not 1 <= size <= 4 * 1024 * 1024:
            _fail("game_package_journal_invalid", f"journal {field} is invalid")
    try:
        package_manifest = validate_game_package_document(document.get("package_manifest"))
    except GamePackageError as exc:
        _fail("game_package_journal_invalid", str(exc))
    if (
        document["standalone_game_hash"] != package_manifest["standalone_game"]["content_hash"]
        or document["payload_lock_hash"] != package_manifest["payload_lock"]["content_hash"]
        or document["payload_tree_hash"] != package_manifest["payload_lock"]["tree_hash"]
    ):
        _fail(
            "game_package_journal_invalid",
            "journal lineage differs from its package manifest",
        )
    document["package_manifest"] = package_manifest
    return document


def _expected_journal_history(
    terminal: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    intent = {**terminal, "state": "intent", "stage_identity": None}
    if terminal["state"] == "intent":
        return (intent,)
    copying = {**terminal, "state": "copying"}
    if terminal["state"] == "copying":
        return intent, copying
    return intent, copying, {**terminal, "state": "ready"}


def _history_payloads(document: Mapping[str, Any]) -> tuple[bytes, ...]:
    return tuple(canonical_contract_bytes(item) for item in _expected_journal_history(document))


def _journal_payload_sha256(
    document: Mapping[str, Any],
    *,
    state: str,
) -> str:
    if state not in _JOURNAL_STATES:
        _fail("game_package_journal_invalid", "journal authority state is invalid")
    candidate = {**document, "state": state}
    if state == "intent":
        candidate["stage_identity"] = None
    elif candidate.get("stage_identity") is None:
        _fail("game_package_journal_invalid", "journal stage authority is unavailable")
    return hashlib.sha256(canonical_contract_bytes(candidate)).hexdigest()


def _require_journal_payload_authority(
    journal: Mapping[str, Any],
    *,
    expected_sha256: str | None,
    expected_state: str | None,
    stage_allocated: bool,
    reason_code: str,
) -> None:
    if (expected_sha256 is None) != (expected_state is None):
        _fail(reason_code, "game package extraction journal authority is incomplete")
    if expected_sha256 is None or expected_state is None:
        return
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        _fail(reason_code, "game package extraction journal authority is invalid")
    actual_state = str(journal["state"])
    allowed = actual_state == expected_state or (
        expected_state == "copying" and actual_state == "ready"
    )
    if stage_allocated and expected_state == "intent" and actual_state == "copying":
        allowed = True
    if (
        not allowed
        or _journal_payload_sha256(
            journal,
            state=expected_state,
        )
        != expected_sha256
    ):
        _fail(reason_code, "game package extraction journal authority changed")


def _require_reset_stage_absent(
    destination: Path,
    operation_id: str,
    *,
    reason_code: str,
) -> None:
    if re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        _fail(reason_code, "game package extraction reset operation authority is invalid")
    stage = destination.parent / (f".{destination.name}.game-package-stage-{operation_id}")
    try:
        identity = _optional_directory_identity(stage)
    except WorldForgeGamePackageError:
        _fail(reason_code, "game package extraction reset stage reappeared unsafely")
    if identity is not None:
        _fail(reason_code, "game package extraction reset stage reappeared")


def _read_journal(
    destination: Path,
) -> tuple[dict[str, Any], DirectoryIdentity, bytes, bool] | None:
    path = _journal_path(destination)
    try:
        loaded = read_append_only_journal_history_state(
            path,
            max_record_bytes=_JOURNAL_RECORD_BYTES,
            max_file_bytes=_JOURNAL_FILE_BYTES,
        )
    except DirectoryPublishError as exc:
        _fail("game_package_journal_invalid", str(exc))
    if loaded is None:
        return None
    payloads, identity, partial_tail = loaded
    documents: list[dict[str, Any]] = []
    for payload in payloads:
        try:
            document = _validate_journal(
                decode_json_object(payload, str(path)),
                destination,
            )
        except StandaloneDistributionError as exc:
            _fail("game_package_journal_invalid", str(exc))
        if canonical_contract_bytes(document) != payload:
            _fail("game_package_journal_invalid", "journal is not canonical")
        documents.append(document)
    if not documents or tuple(documents) != _expected_journal_history(documents[-1]):
        _fail("game_package_journal_invalid", "journal history is not an exact prefix")
    return documents[-1], identity, payloads[-1], partial_tail


def _write_journal(
    destination: Path,
    document: dict[str, Any],
    *,
    create: bool,
    expected_document: Mapping[str, Any] | None = None,
    expected_identity: DirectoryIdentity | None = None,
) -> DirectoryIdentity:
    path = _journal_path(destination)
    payload = canonical_contract_bytes(document)
    try:
        if create:
            try:
                identity = create_append_only_journal(
                    path,
                    payload,
                    max_record_bytes=_JOURNAL_RECORD_BYTES,
                )
            except FileExistsError:
                _fail(
                    "game_package_recovery_required",
                    "an incomplete package extraction journal exists",
                    recovery_evidence=retained_recovery_evidence(journal_path=path),
                )
            fsync_directory(path.parent, context="game package extraction journal parent")
            return identity
        if expected_document is None or expected_identity is None:
            _fail("game_package_journal_invalid", "journal transition is unbound")
        loaded = _read_journal(destination)
        expected_payload = canonical_contract_bytes(expected_document)
        if (
            loaded is None
            or loaded[0] != expected_document
            or loaded[1] != expected_identity
            or loaded[2] != expected_payload
        ):
            _fail("game_package_journal_changed", "journal changed before append")
        return append_append_only_journal(
            path,
            expected_identity=expected_identity,
            expected_payload=expected_payload,
            expected_history=_history_payloads(expected_document),
            updated_payload=payload,
            max_record_bytes=_JOURNAL_RECORD_BYTES,
            max_file_bytes=_JOURNAL_FILE_BYTES,
            repair_partial_tail=True,
        )
    except WorldForgeGamePackageError:
        raise
    except DirectoryPublishError as exc:
        _fail("game_package_journal_failed", str(exc))


def _remove_journal(
    destination: Path,
    document: Mapping[str, Any],
    identity: DirectoryIdentity,
) -> None:
    journal_path = _journal_path(destination)
    try:
        retained_journal = remove_append_only_journal(
            journal_path,
            expected_identity=identity,
            expected_payload=canonical_contract_bytes(document),
            expected_history=_history_payloads(document),
            max_record_bytes=_JOURNAL_RECORD_BYTES,
            max_file_bytes=_JOURNAL_FILE_BYTES,
        )
        if sys.platform.startswith("linux") and os.name == "posix":
            if retained_journal != retained_journal_evidence_path(journal_path, identity):
                _fail(
                    "game_package_journal_indeterminate",
                    "terminal journal evidence locator changed",
                )
        elif retained_journal is not None:
            _fail(
                "game_package_journal_indeterminate",
                "unexpected terminal journal evidence was returned",
            )
    except DirectoryPublishIndeterminateError as exc:
        _fail("game_package_journal_indeterminate", str(exc))
    except DirectoryPublishError as exc:
        _fail("game_package_journal_failed", str(exc))


def _file_inventory(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    return [
        {
            "path": path,
            "sha256": hashlib.sha256(files[path]).hexdigest(),
            "size_bytes": len(files[path]),
        }
        for path in sorted(files, key=lambda item: item.encode("utf-8"))
    ]


def _journal_matches_game(
    journal: Mapping[str, Any],
    verified: VerifiedStandaloneGame,
    destination: Path,
) -> None:
    files = verified.files
    expected = _journal_document(
        operation_id=journal["operation_id"],
        state=journal["state"],
        stage=destination.parent / journal["stage_name"],
        destination=destination,
        parent_identity=_identity_from_document(
            journal["parent_identity"],
            context="parent",
        ),
        stage_identity=(
            None
            if journal["stage_identity"] is None
            else _identity_from_document(journal["stage_identity"])
        ),
        archive_sha256=journal["archive_sha256"],
        package_manifest=journal["package_manifest"],
        files=files,
    )
    if expected != journal or _file_inventory(files) != journal["package_manifest"]["files"]:
        _fail(
            "game_package_recovery_mismatch",
            "journal differs from the exact extracted standalone game",
        )


def _recover_locked(
    destination: Path,
    *,
    expected_root_identity: DirectoryIdentity | None = None,
    expected_journal_identity: DirectoryIdentity | None = None,
    expected_operation_id: str | None = None,
    expected_content_hash: str | None = None,
    expected_tree_hash: str | None = None,
    expected_stage_identity: DirectoryIdentity | None = None,
    expected_journal_payload_sha256: str | None = None,
    expected_journal_payload_state: str | None = None,
    allow_missing_expected_journal: bool = False,
    require_journal_for_visible: bool = False,
    require_intent_journal: bool = False,
    stage_allocated: bool = False,
    reset_pending: bool = False,
    reject_unbound_journal: bool = False,
    authority_hook: _AuthorityHook | None = None,
) -> VerifiedStandaloneGame | None:
    loaded = _read_journal(destination)
    if loaded is None:
        if reset_pending:
            if (
                not allow_missing_expected_journal
                or expected_journal_identity is None
                or expected_operation_id is None
                or _optional_directory_identity(destination) is not None
            ):
                _fail(
                    "game_package_recovery_ambiguous",
                    "game package extraction reset authority is incomplete",
                )
            _require_reset_stage_absent(
                destination,
                expected_operation_id,
                reason_code="game_package_recovery_ambiguous",
            )
            if authority_hook is not None:
                authority_hook(
                    "publication_reset",
                    {
                        "journal_identity": list(expected_journal_identity),
                        "operation_id": expected_operation_id,
                    },
                )
            return None
        if (
            expected_journal_identity is not None or expected_operation_id is not None
        ) and not allow_missing_expected_journal:
            _fail(
                "game_package_recovery_ambiguous",
                "retained game package extraction journal disappeared",
            )
        if _optional_directory_identity(destination) is None:
            return None
        if require_journal_for_visible:
            _fail(
                "game_package_recovery_ambiguous",
                "visible extracted game has no retained recovery journal",
            )
        if (
            expected_root_identity is None
            and expected_content_hash is None
            and expected_tree_hash is None
            and not allow_missing_expected_journal
        ):
            return None
        return verify_standalone_game(
            destination,
            expected_root_identity=expected_root_identity,
        )
    journal, journal_identity, _payload, partial_tail = loaded
    if reject_unbound_journal and (
        expected_journal_identity is None or expected_operation_id is None
    ):
        _fail(
            "game_package_recovery_ambiguous",
            "game package extraction journal is not bound to trusted authority",
        )
    if expected_journal_identity is not None and journal_identity != expected_journal_identity:
        _fail(
            "game_package_recovery_ambiguous",
            "game package extraction journal identity changed",
        )
    if expected_operation_id is not None and journal["operation_id"] != expected_operation_id:
        _fail(
            "game_package_recovery_ambiguous",
            "game package extraction operation identity changed",
        )
    _require_journal_payload_authority(
        journal,
        expected_sha256=expected_journal_payload_sha256,
        expected_state=expected_journal_payload_state,
        stage_allocated=stage_allocated,
        reason_code="game_package_recovery_ambiguous",
    )
    if expected_content_hash is not None and (
        journal["standalone_game_hash"] != expected_content_hash
    ):
        _fail(
            "game_package_recovery_ambiguous",
            "game package extraction content authority changed",
        )
    if expected_tree_hash is not None and journal["payload_tree_hash"] != expected_tree_hash:
        _fail(
            "game_package_recovery_ambiguous",
            "game package extraction tree authority changed",
        )
    if require_intent_journal and journal["state"] != "intent":
        _fail(
            "game_package_recovery_ambiguous",
            "game package extraction advanced before stage authority was retained",
        )
    if (
        journal["state"] != "intent"
        and expected_stage_identity is not None
        and _identity_from_document(journal["stage_identity"]) != expected_stage_identity
    ):
        _fail(
            "game_package_recovery_ambiguous",
            "game package extraction stage authority changed",
        )
    if reset_pending and journal["state"] != "intent":
        stage = destination.parent / journal["stage_name"]
        _fail(
            "game_package_recovery_required",
            "a stage-bound extraction rollback must complete before resume",
            recovery_evidence=retained_recovery_evidence(
                stage_path=stage,
                stage_identity=expected_stage_identity,
                journal_path=_journal_path(destination),
                journal_identity=journal_identity,
            ),
        )
    if partial_tail and journal["state"] != "copying":
        _fail(
            "game_package_journal_invalid",
            "journal has a torn non-recoverable transition",
        )
    stage = destination.parent / journal["stage_name"]
    if stage_allocated:
        if (
            expected_stage_identity is None
            or _optional_directory_identity(stage) != expected_stage_identity
            or _optional_directory_identity(destination) is not None
            or journal["state"] not in {"intent", "copying"}
        ):
            _fail(
                "game_package_recovery_ambiguous",
                "allocated game package extraction stage authority changed",
            )
        if journal["state"] == "intent":
            copying = {
                **journal,
                "state": "copying",
                "stage_identity": _identity_document(expected_stage_identity),
            }
            journal_identity = _write_journal(
                destination,
                copying,
                create=False,
                expected_document=journal,
                expected_identity=journal_identity,
            )
            journal = copying
        if authority_hook is not None:
            authority_hook(
                "publication_staged",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                    "stage_identity": list(expected_stage_identity),
                    "journal_payload_sha256": _journal_payload_sha256(
                        journal,
                        state="copying",
                    ),
                    "journal_payload_state": "copying",
                },
            )
    if journal["state"] == "intent":
        if (
            _optional_directory_identity(stage) is not None
            or _optional_directory_identity(destination) is not None
        ):
            _fail("game_package_recovery_ambiguous", "intent has an unbound tree")
        if authority_hook is not None:
            authority_hook(
                "publication_resetting",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                },
            )
        _remove_journal(destination, journal, journal_identity)
        if authority_hook is not None:
            authority_hook(
                "publication_reset",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                },
            )
        return None
    expected_identity = _identity_from_document(journal["stage_identity"])
    stage_identity = _optional_directory_identity(stage)
    destination_identity = _optional_directory_identity(destination)
    if stage_identity == expected_identity and destination_identity is None:
        source = stage
    elif destination_identity == expected_identity and stage_identity is None:
        source = destination
    else:
        _fail(
            "game_package_recovery_ambiguous",
            "stage/destination identity is missing, changed, or conflicting",
        )
    try:
        verified = verify_standalone_game(
            source,
            expected_content_hash=journal["standalone_game_hash"],
        )
    except StandaloneGameError as exc:
        if journal["state"] == "copying" and source == stage:
            try:
                _verify_owned_stage_subset(stage, journal)
            except WorldForgeGamePackageError:
                pass
            else:
                _fail(
                    "game_package_recovery_required",
                    "the exact incomplete extraction stage and journal were retained "
                    "for explicit recovery",
                    recovery_evidence=retained_recovery_evidence(
                        stage_path=stage,
                        stage_identity=expected_identity,
                        journal_path=_journal_path(destination),
                        journal_identity=journal_identity,
                    ),
                )
        _fail("game_package_recovery_ambiguous", str(exc))
    try:
        _journal_matches_game(journal, verified, destination)
    finally:
        verified.close()
    if journal["state"] == "copying":
        ready = {**journal, "state": "ready"}
        journal_identity = _write_journal(
            destination,
            ready,
            create=False,
            expected_document=journal,
            expected_identity=journal_identity,
        )
        journal = ready
    if source == stage:
        if _require_external_destination(destination) != _identity_from_document(
            journal["parent_identity"],
            context="parent",
        ):
            _fail(
                "game_package_recovery_ambiguous",
                "destination parent changed before recovered publication",
            )
        try:
            with publish_directory_noreplace(
                stage,
                destination,
                expected_source_identity=expected_identity,
                expected_parent_identity=_identity_from_document(
                    journal["parent_identity"],
                    context="parent",
                ),
            ) as published_identity:
                if published_identity != expected_identity:
                    _fail(
                        "game_package_publication_identity_mismatch",
                        "recovered root identity changed",
                    )
                fsync_directory(
                    destination.parent,
                    context="recovered package extraction parent",
                )
        except (DirectoryPublishError, FileExistsError) as exc:
            _fail("game_package_recovery_failed", str(exc))
    try:
        verified = verify_standalone_game(
            destination,
            expected_content_hash=journal["standalone_game_hash"],
        )
    except StandaloneGameError as exc:
        _fail("game_package_recovery_failed", str(exc))
    try:
        if verified.root_identity != expected_identity:
            _fail(
                "game_package_publication_indeterminate",
                "visible game differs from journal identity",
            )
        _journal_matches_game(journal, verified, destination)
        if _require_external_destination(destination) != _identity_from_document(
            journal["parent_identity"],
            context="parent",
        ):
            _fail(
                "game_package_recovery_ambiguous",
                "destination boundary changed during recovery",
            )
        if authority_hook is not None:
            authority_hook(
                "publication_verified",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                    "stage_identity": list(expected_identity),
                    "published_identity": list(verified.root_identity),
                    "journal_payload_sha256": _journal_payload_sha256(
                        journal,
                        state="ready",
                    ),
                    "journal_payload_state": "ready",
                },
            )
        _remove_journal(destination, journal, journal_identity)
        final = verify_standalone_game(
            destination,
            expected_content_hash=journal["standalone_game_hash"],
        )
    finally:
        verified.close()
    if _read_journal(destination) is not None:
        final.close()
        _fail(
            "game_package_publication_indeterminate",
            "journal reappeared after finalization",
        )
    return final


def recover_game_package_extraction(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
    expected_root_identity: DirectoryIdentity | None = None,
    expected_journal_identity: DirectoryIdentity | None = None,
    expected_operation_id: str | None = None,
    expected_content_hash: str | None = None,
    expected_tree_hash: str | None = None,
    expected_stage_identity: DirectoryIdentity | None = None,
    expected_journal_payload_sha256: str | None = None,
    expected_journal_payload_state: str | None = None,
    allow_missing_expected_journal: bool = False,
    require_journal_for_visible: bool = False,
    require_intent_journal: bool = False,
    stage_allocated: bool = False,
    reset_pending: bool = False,
    reject_unbound_journal: bool = False,
    _authority_hook: _AuthorityHook | None = None,
) -> VerifiedStandaloneGame | None:
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    parent_identity = _require_external_destination(destination_path)
    if expected_parent_identity is not None and parent_identity != expected_parent_identity:
        _fail(
            "game_package_destination_invalid",
            "destination parent differs from the expected identity",
        )
    with _destination_lock(
        destination_path,
        expected_parent_identity=parent_identity,
    ):
        if _require_external_destination(destination_path) != parent_identity:
            _fail(
                "game_package_destination_invalid",
                "destination parent changed before recovery",
            )
        verified = _recover_locked(
            destination_path,
            expected_root_identity=expected_root_identity,
            expected_journal_identity=expected_journal_identity,
            expected_operation_id=expected_operation_id,
            expected_content_hash=expected_content_hash,
            expected_tree_hash=expected_tree_hash,
            expected_stage_identity=expected_stage_identity,
            expected_journal_payload_sha256=expected_journal_payload_sha256,
            expected_journal_payload_state=expected_journal_payload_state,
            allow_missing_expected_journal=allow_missing_expected_journal,
            require_journal_for_visible=require_journal_for_visible,
            require_intent_journal=require_intent_journal,
            stage_allocated=stage_allocated,
            reset_pending=reset_pending,
            reject_unbound_journal=reject_unbound_journal,
            authority_hook=_authority_hook,
        )
        if (
            verified is not None
            and expected_root_identity is not None
            and verified.root_identity != expected_root_identity
        ):
            verified.close()
            _fail(
                "game_package_root_identity_mismatch",
                "recovered extracted game differs from retained identity",
            )
        return verified


def _expected_directory_closure(paths: Mapping[str, bytes]) -> frozenset[str]:
    return frozenset(
        "/".join(parts[:depth])
        for path in paths
        for parts in (Path(path).parts,)
        for depth in range(1, len(parts))
    )


def _verify_owned_stage_subset(
    stage: Path,
    journal: Mapping[str, Any],
) -> None:
    try:
        captured, directories = capture_standalone_tree_with_directories(stage)
    except StandaloneDistributionError as exc:
        _fail("game_package_rollback_ambiguous", str(exc))
    files = dict(captured)
    if directories != _expected_directory_closure(files):
        _fail(
            "game_package_rollback_ambiguous",
            "stage directory closure differs from its present owned files",
        )
    records = {item["path"]: item for item in journal["package_manifest"]["files"]}
    if not set(files).issubset(records):
        _fail("game_package_rollback_ambiguous", "stage contains foreign files")
    for path, payload in files.items():
        record = records[path]
        if (
            len(payload) != record["size_bytes"]
            or hashlib.sha256(payload).hexdigest() != record["sha256"]
        ):
            _fail(
                "game_package_rollback_ambiguous",
                f"stage file differs from its exact package inventory: {path}",
            )


def rollback_game_package_extraction(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
    expected_journal_identity: DirectoryIdentity | None = None,
    expected_operation_id: str | None = None,
    expected_content_hash: str | None = None,
    expected_tree_hash: str | None = None,
    expected_stage_identity: DirectoryIdentity | None = None,
    expected_journal_payload_sha256: str | None = None,
    expected_journal_payload_state: str | None = None,
    allow_missing_expected_journal: bool = False,
    require_intent_journal: bool = False,
    stage_allocated: bool = False,
    reset_pending: bool = False,
    reject_unbound_journal: bool = False,
    _authority_hook: _AuthorityHook | None = None,
) -> dict[str, object]:
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    parent_identity = _require_external_destination(destination_path)
    if expected_parent_identity is not None and parent_identity != expected_parent_identity:
        _fail(
            "game_package_destination_invalid",
            "destination parent differs from the expected identity",
        )
    with _destination_lock(
        destination_path,
        expected_parent_identity=parent_identity,
    ):
        if _require_external_destination(destination_path) != parent_identity:
            _fail(
                "game_package_destination_invalid",
                "destination parent changed before rollback",
            )
        loaded = _read_journal(destination_path)
        if loaded is None:
            if reset_pending:
                if (
                    not allow_missing_expected_journal
                    or expected_journal_identity is None
                    or expected_operation_id is None
                    or _optional_directory_identity(destination_path) is not None
                ):
                    _fail(
                        "game_package_rollback_ambiguous",
                        "game package extraction rollback reset authority is incomplete",
                    )
                _require_reset_stage_absent(
                    destination_path,
                    expected_operation_id,
                    reason_code="game_package_rollback_ambiguous",
                )
                if _authority_hook is not None:
                    _authority_hook(
                        "publication_reset",
                        {
                            "journal_identity": list(expected_journal_identity),
                            "operation_id": expected_operation_id,
                        },
                    )
                return {
                    "status": "rolled_back",
                    "operation_id": expected_operation_id,
                }
            if (
                expected_journal_identity is not None or expected_operation_id is not None
            ) and not allow_missing_expected_journal:
                _fail(
                    "game_package_rollback_ambiguous",
                    "retained game package extraction rollback journal disappeared",
                )
            return {"status": "no_operation"}
        journal, journal_identity, _payload, partial_tail = loaded
        if reject_unbound_journal and (
            expected_journal_identity is None or expected_operation_id is None
        ):
            _fail(
                "game_package_rollback_ambiguous",
                "game package extraction rollback journal is not bound to trusted authority",
            )
        if expected_journal_identity is not None and journal_identity != expected_journal_identity:
            _fail(
                "game_package_rollback_ambiguous",
                "game package extraction rollback journal identity changed",
            )
        if expected_operation_id is not None and journal["operation_id"] != expected_operation_id:
            _fail(
                "game_package_rollback_ambiguous",
                "game package extraction rollback operation identity changed",
            )
        _require_journal_payload_authority(
            journal,
            expected_sha256=expected_journal_payload_sha256,
            expected_state=expected_journal_payload_state,
            stage_allocated=stage_allocated,
            reason_code="game_package_rollback_ambiguous",
        )
        if expected_content_hash is not None and (
            journal["standalone_game_hash"] != expected_content_hash
        ):
            _fail(
                "game_package_rollback_ambiguous",
                "game package extraction rollback content authority changed",
            )
        if expected_tree_hash is not None and journal["payload_tree_hash"] != expected_tree_hash:
            _fail(
                "game_package_rollback_ambiguous",
                "game package extraction rollback tree authority changed",
            )
        if require_intent_journal and journal["state"] != "intent":
            _fail(
                "game_package_rollback_ambiguous",
                "game package extraction rollback advanced before stage authority was retained",
            )
        if (
            journal["state"] != "intent"
            and expected_stage_identity is not None
            and _identity_from_document(journal["stage_identity"]) != expected_stage_identity
        ):
            _fail(
                "game_package_rollback_ambiguous",
                "game package extraction rollback stage authority changed",
            )
        if partial_tail:
            _fail("game_package_rollback_ambiguous", "journal has a torn tail")
        if _optional_directory_identity(destination_path) is not None:
            _fail(
                "game_package_rollback_committed",
                "rollback never removes a visible standalone game",
            )
        stage = destination_path.parent / journal["stage_name"]
        owned_stage_identity: DirectoryIdentity | None = None
        if journal["state"] == "intent":
            actual_stage_identity = _optional_directory_identity(stage)
            if stage_allocated:
                if actual_stage_identity is None and reset_pending:
                    pass
                elif (
                    expected_stage_identity is None
                    or actual_stage_identity != expected_stage_identity
                ):
                    _fail(
                        "game_package_rollback_ambiguous",
                        "allocated extraction rollback stage identity changed",
                    )
                else:
                    _verify_owned_stage_subset(stage, journal)
                    owned_stage_identity = expected_stage_identity
            elif actual_stage_identity is not None:
                _fail("game_package_rollback_ambiguous", "intent has a stage")
        else:
            expected_identity = _identity_from_document(journal["stage_identity"])
            if _require_external_destination(destination_path) != _identity_from_document(
                journal["parent_identity"],
                context="parent",
            ):
                _fail(
                    "game_package_rollback_ambiguous",
                    "destination parent changed before rollback cleanup",
                )
            actual_stage_identity = _optional_directory_identity(stage)
            if actual_stage_identity is None and reset_pending:
                pass
            elif actual_stage_identity != expected_identity:
                _fail("game_package_rollback_ambiguous", "stage identity changed")
            else:
                _verify_owned_stage_subset(stage, journal)
                owned_stage_identity = expected_identity
        if _authority_hook is not None:
            _authority_hook(
                "publication_resetting",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                },
            )
        if owned_stage_identity is not None:
            try:
                quarantine_and_remove_verified_directory(
                    stage,
                    owned_stage_identity,
                    verify=lambda path: _verify_owned_stage_subset(path, journal),
                )
            except DirectoryPublishRecoveryRequiredError as exc:
                _fail(
                    "game_package_rollback_recovery_required",
                    "automatic rollback cleanup is unavailable; the exact owned stage "
                    f"and extraction journal were retained: {exc}",
                    recovery_evidence=retained_recovery_evidence(
                        stage_path=stage,
                        stage_identity=owned_stage_identity,
                        journal_path=_journal_path(destination_path),
                        journal_identity=journal_identity,
                    ),
                )
            except DirectoryPublishIndeterminateError as exc:
                _fail("game_package_rollback_indeterminate", str(exc))
            except (DirectoryPublishError, OSError) as exc:
                _fail("game_package_rollback_failed", str(exc))
        _remove_journal(destination_path, journal, journal_identity)
        if _authority_hook is not None:
            _authority_hook(
                "publication_reset",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                },
            )
        return {
            "status": "rolled_back",
            "operation_id": journal["operation_id"],
            "content_hash": journal["package_manifest"]["content_hash"],
        }


def extract_game_package(
    package: str | Path,
    destination: str | Path,
    *,
    expected_source_identity: DirectoryIdentity | None = None,
    expected_parent_identity: DirectoryIdentity | None = None,
    _verified_package: VerifiedGamePackage | None = None,
    _publication_hook: _PublicationHook | None = None,
    _authority_hook: _AuthorityHook | None = None,
) -> VerifiedStandaloneGame:
    if not ((sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt"):
        _fail(
            "game_package_platform_unsupported",
            "generic game extraction supports only Linux and Windows",
        )
    package_path = Path(os.path.abspath(os.fspath(package)))
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    verified_package = _verified_package
    if verified_package is None:
        verified_package = verify_game_package(
            package_path,
            expected_file_identity=expected_source_identity,
        )
    package_manifest = verified_package.manifest
    files = dict(verified_package.files)
    archive_sha256 = verified_package.archive_sha256
    parent_identity = _require_external_destination(destination_path)
    if expected_parent_identity is not None and parent_identity != expected_parent_identity:
        _fail(
            "game_package_destination_invalid",
            "destination parent differs from the expected identity",
        )
    if _paths_overlap(package_path, destination_path):
        _fail(
            "game_package_path_overlap",
            "archive and extraction destination must be disjoint",
        )
    with _destination_lock(
        destination_path,
        expected_parent_identity=parent_identity,
    ):
        if _require_external_destination(destination_path) != parent_identity:
            _fail(
                "game_package_destination_invalid",
                "destination parent changed before extraction",
            )
        if (
            _read_journal(destination_path) is None
            and _optional_directory_identity(destination_path) is not None
        ):
            _fail(
                "game_package_destination_exists",
                "destination already contains a standalone game",
            )
        recovered = _recover_locked(destination_path)
        if recovered is not None:
            if (
                recovered.manifest["content_hash"]
                == package_manifest["standalone_game"]["content_hash"]
            ):
                return recovered
            recovered.close()
            _fail(
                "game_package_destination_exists",
                "destination contains a different immutable standalone game",
            )
        if destination_path.exists() or destination_path.is_symlink():
            _fail("game_package_destination_exists", "destination already exists")
        operation_id = uuid.uuid4().hex
        stage = destination_path.parent / (
            f".{destination_path.name}.game-package-stage-{operation_id}"
        )
        intent = _journal_document(
            operation_id=operation_id,
            state="intent",
            stage=stage,
            destination=destination_path,
            parent_identity=parent_identity,
            stage_identity=None,
            archive_sha256=archive_sha256,
            package_manifest=package_manifest,
            files=files,
        )
        journal = intent
        journal_identity = _write_journal(destination_path, intent, create=True)
        if _authority_hook is not None:
            _authority_hook(
                "publication_started",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": operation_id,
                    "journal_payload_sha256": _journal_payload_sha256(
                        journal,
                        state="intent",
                    ),
                    "journal_payload_state": "intent",
                },
            )
        if _publication_hook is not None:
            _publication_hook("after_intent_journal_written", None)
        try:
            with create_retained_stage(
                stage,
                expected_parent_identity=parent_identity,
            ) as writer:
                stage_identity = writer.identity
                if _authority_hook is not None:
                    _authority_hook(
                        "publication_stage_allocated",
                        {
                            "journal_identity": list(journal_identity),
                            "operation_id": operation_id,
                            "stage_identity": list(stage_identity),
                            "journal_payload_sha256": _journal_payload_sha256(
                                journal,
                                state="intent",
                            ),
                            "journal_payload_state": "intent",
                        },
                    )
                copying = _journal_document(
                    operation_id=operation_id,
                    state="copying",
                    stage=stage,
                    destination=destination_path,
                    parent_identity=parent_identity,
                    stage_identity=stage_identity,
                    archive_sha256=archive_sha256,
                    package_manifest=package_manifest,
                    files=files,
                )
                journal_identity = _write_journal(
                    destination_path,
                    copying,
                    create=False,
                    expected_document=journal,
                    expected_identity=journal_identity,
                )
                journal = copying
                if _authority_hook is not None:
                    _authority_hook(
                        "publication_staged",
                        {
                            "journal_identity": list(journal_identity),
                            "operation_id": operation_id,
                            "stage_identity": list(stage_identity),
                            "journal_payload_sha256": _journal_payload_sha256(
                                journal,
                                state="copying",
                            ),
                            "journal_payload_state": "copying",
                        },
                    )
                if _publication_hook is not None:
                    _publication_hook("after_copying_journal_written", stage)
                ordered_files = (
                    GAME_MANIFEST_PATH,
                    GAME_LOCK_PATH,
                    *sorted(
                        (
                            relative
                            for relative in files
                            if relative not in {GAME_MANIFEST_PATH, GAME_LOCK_PATH}
                        ),
                        key=lambda item: item.encode("utf-8"),
                    ),
                )
                for relative in ordered_files:
                    writer.write_file(relative, files[relative])
                    if _publication_hook is not None:
                        _publication_hook("after_file_written", stage / relative)
                writer.fsync()
                try:
                    verified_stage = verify_standalone_game(
                        stage,
                        expected_content_hash=package_manifest["standalone_game"]["content_hash"],
                    )
                except StandaloneGameError as exc:
                    _fail("game_package_extraction_failed", str(exc))
                try:
                    _journal_matches_game(journal, verified_stage, destination_path)
                finally:
                    verified_stage.close()
                ready = {**journal, "state": "ready"}
                journal_identity = _write_journal(
                    destination_path,
                    ready,
                    create=False,
                    expected_document=journal,
                    expected_identity=journal_identity,
                )
                journal = ready
                if _publication_hook is not None:
                    _publication_hook("after_ready_journal_written", stage)
                writer.require_binding()
            if _publication_hook is not None:
                _publication_hook("before_destination_publish", stage)
            if _require_external_destination(destination_path) != parent_identity:
                _fail(
                    "game_package_publication_indeterminate",
                    "destination boundary changed before publication",
                )
            with publish_directory_noreplace(
                stage,
                destination_path,
                expected_source_identity=stage_identity,
                expected_parent_identity=parent_identity,
            ) as published_identity:
                if published_identity != stage_identity:
                    _fail(
                        "game_package_publication_identity_mismatch",
                        "published root identity changed",
                    )
                fsync_directory(
                    destination_path.parent,
                    context="published package extraction parent",
                )
                try:
                    verified = verify_standalone_game(
                        destination_path,
                        expected_content_hash=package_manifest["standalone_game"]["content_hash"],
                    )
                except StandaloneGameError as exc:
                    _fail("game_package_publication_indeterminate", str(exc))
                try:
                    if verified.root_identity != stage_identity:
                        _fail(
                            "game_package_publication_indeterminate",
                            "visible destination differs from the retained stage",
                        )
                    _journal_matches_game(journal, verified, destination_path)
                except BaseException:
                    verified.close()
                    raise
                if _require_external_destination(destination_path) != parent_identity:
                    verified.close()
                    _fail(
                        "game_package_publication_indeterminate",
                        "destination boundary changed during publication",
                    )
                if _publication_hook is not None:
                    _publication_hook("before_journal_remove", destination_path)
                if _authority_hook is not None:
                    _authority_hook(
                        "publication_verified",
                        {
                            "journal_identity": list(journal_identity),
                            "operation_id": operation_id,
                            "stage_identity": list(stage_identity),
                            "published_identity": list(verified.root_identity),
                            "journal_payload_sha256": _journal_payload_sha256(
                                journal,
                                state="ready",
                            ),
                            "journal_payload_state": "ready",
                        },
                    )
                _remove_journal(destination_path, journal, journal_identity)
                if _publication_hook is not None:
                    _publication_hook("after_journal_remove", destination_path)
                if _read_journal(destination_path) is not None:
                    verified.close()
                    _fail(
                        "game_package_publication_indeterminate",
                        "journal reappeared after finalization",
                    )
                final = verify_standalone_game(
                    destination_path,
                    expected_content_hash=package_manifest["standalone_game"]["content_hash"],
                )
                verified.close()
                if final.root_identity != stage_identity:
                    final.close()
                    _fail(
                        "game_package_publication_indeterminate",
                        "destination identity changed after journal removal",
                    )
                return final
        except WorldForgeGamePackageError:
            raise
        except FileExistsError as exc:
            _fail("game_package_destination_exists", str(exc))
        except DirectoryPublishError as exc:
            _fail(
                "game_package_publication_failed",
                f"{exc}; retained stage: {stage}",
            )


__all__ = [
    "GAME_PACKAGE_FORMAT",
    "WorldForgeGamePackageError",
    "extract_game_package",
    "package_game",
    "recover_game_package_extraction",
    "rollback_game_package_extraction",
    "verify_game_package",
]
