"""Forge orchestration and immutable evidence for generic headless execution."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
import sys
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from gamepack_runtime import GameLogicError
from gamepack_runtime.headless import (
    GAME_EXECUTION_SCRIPT_FORMAT,
    HEADLESS_EXECUTION_RECEIPT_FORMAT,
    MAX_GAME_EXECUTION_SCRIPT_BYTES,
    MAX_HEADLESS_EXECUTION_RECEIPT_BYTES,
    HeadlessExecutionResult,
    execute_game_execution_script,
    serialize_game_execution_script,
    validate_game_execution_script,
    validate_headless_execution_receipt,
)
from gamepack_runtime.persistence import (
    load_game_replay_bytes,
    load_game_save_bytes,
)
from gamepack_runtime.persistence_io import (
    decode_json_object,
    held_persistence_lock,
)
from worldforge.creation_contracts import canonical_creation_hash
from worldforge.directory_publish import (
    DirectoryIdentity,
    DirectoryPublishError,
    DirectoryPublishIndeterminateError,
    append_append_only_journal,
    create_append_only_journal,
    create_retained_stage,
    directory_identity,
    fsync_directory,
    publish_directory_noreplace,
    read_append_only_journal_history_state,
    remove_append_only_journal,
)
from worldforge.file_stat import file_identity, is_link_or_reparse, path_file_stat
from worldforge.game_persistence import persistence_context_from_bundle
from worldforge.game_runtime_bundle import (
    GameRuntimeBundleError,
    VerifiedGameRuntimeBundle,
    verify_game_runtime_bundle,
)
from worldforge.generic_runtime import (
    RuntimeContractError,
    _capture_runtime_files,
    build_builtin_runtime_adapters,
    build_runtime_evidence,
    build_runtime_support_report,
    serialize_runtime_evidence,
    serialize_runtime_support_report,
    validate_runtime_evidence_document,
    validate_runtime_support_report_document,
)
from worldforge.integrity import canonical_json_bytes

HEADLESS_EVIDENCE_SET_FORMAT = "world-forge.headless_evidence_set"
HEADLESS_EVIDENCE_SET_VERSION = 1
HEADLESS_EVIDENCE_SET_MANIFEST = "headless-evidence-set.json"
HEADLESS_EVIDENCE_COMMIT = "HEADLESS-EVIDENCE-COMMIT.json"
HEADLESS_AUTHORITY_RESULT_FIELDS = (
    "content_hash",
    "evidence_set_id",
    "execution_status",
    "integrity",
    "path",
    "release",
    "supported",
)
HEADLESS_AUTHORITY_RESULT_POLICY = MappingProxyType(
    {
        "version": 1,
        "fields": HEADLESS_AUTHORITY_RESULT_FIELDS,
        "execution_status": "headless_verified",
        "integrity": "valid",
        "release": "blocked",
        "supported": False,
    }
)
MAX_HEADLESS_EVIDENCE_FILE_BYTES = 4 * 1024 * 1024
MAX_HEADLESS_EVIDENCE_TOTAL_BYTES = 64 * 1024 * 1024
MAX_HEADLESS_EVIDENCE_FILES = 70
MAX_HEADLESS_EVIDENCE_DIRECTORIES = 16
MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES = 64 * 1024

_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_SET_ID_RE = re.compile(r"^headless_evidence_set_[0-9a-f]{40}$")
_OPERATION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_EVIDENCE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "evidence_set_id",
        "state",
        "runtime_bundle",
        "execution_script",
        "headless_receipt",
        "runtime_evidence",
        "support",
        "files",
        "tree_hash",
        "file_count",
        "total_bytes",
        "content_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_FILE_FIELDS = frozenset({"path", "sha256", "size_bytes"})
_RUNTIME_EVIDENCE_REFERENCE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "content_hash",
        "execution_status",
        "platform",
    }
)
_SUPPORT_REFERENCE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "content_hash",
        "compatibility_status",
        "release",
        "supported",
    }
)
_COMMIT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "evidence_set",
        "tree_hash",
    }
)
_JOURNAL_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "operation_id",
        "state",
        "stage_name",
        "destination_name",
        "evidence_set_hash",
        "stage_identity",
    }
)
_PublicationHook = Callable[[str, str | None], None]


class GenericHeadlessError(ValueError):
    """Raised when headless execution or external evidence fails closed."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise GenericHeadlessError(reason_code, detail)


@dataclass(frozen=True, slots=True)
class _PublicationLockGuard:
    path: Path
    identity: DirectoryIdentity
    state: tuple[int, int, int, int, int, int, int]

    @classmethod
    def capture(cls, path: Path) -> _PublicationLockGuard:
        info = path_file_stat(path)
        if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail(
                "evidence_publication_failed",
                "headless evidence publication lock has an unsafe identity",
            )
        return cls(
            path=path,
            identity=file_identity(info),
            state=(
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            ),
        )

    def require_binding(self) -> None:
        info = path_file_stat(self.path)
        state = (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_nlink,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        if (
            is_link_or_reparse(info)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or file_identity(info) != self.identity
            or state != self.state
        ):
            _fail(
                "evidence_publication_failed",
                "headless evidence publication lock binding changed",
            )


def _identity(document: Mapping[str, object], *, id_field: str) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[id_field],
        "content_hash": document["content_hash"],
    }


def _object(value: object, context: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("evidence_invalid", f"{context} must be an exact object")
    return value


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], context: str) -> None:
    if frozenset(value) != expected:
        missing = sorted(expected - set(value), key=lambda item: item.encode("utf-8"))
        extra = sorted(set(value) - expected, key=lambda item: item.encode("utf-8"))
        _fail(
            "evidence_invalid",
            f"{context} fields differ; missing={missing!r} extra={extra!r}",
        )


def _sha256(value: object, context: str) -> str:
    if type(value) is not str or _HEX_RE.fullmatch(value) is None:
        _fail("evidence_invalid", f"{context} must be lowercase SHA-256")
    return value


def _validate_identity(
    value: object,
    context: str,
    *,
    expected_format: str | None = None,
) -> dict[str, Any]:
    identity = _object(value, context)
    _exact_keys(identity, _IDENTITY_FIELDS, context)
    if expected_format is not None and identity.get("format") != expected_format:
        _fail("binding_mismatch", f"{context}.format differs")
    if identity.get("format_version") != 1:
        _fail("binding_mismatch", f"{context}.format_version differs")
    if type(identity.get("id")) is not str or not identity["id"]:
        _fail("evidence_invalid", f"{context}.id is invalid")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    return identity


def _path(value: object, context: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > 1024
        or "\\" in value
        or unicodedata.normalize("NFC", value) != value
    ):
        _fail("evidence_invalid", f"{context} is not a portable path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        _fail("evidence_invalid", f"{context} is not a portable relative path")
    return value


def _file_inventory(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    records = [
        {
            "path": path,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
        for path, payload in sorted(files.items(), key=lambda item: item[0].encode("utf-8"))
    ]
    if (
        len(records) > MAX_HEADLESS_EVIDENCE_FILES
        or any(record["size_bytes"] > MAX_HEADLESS_EVIDENCE_FILE_BYTES for record in records)
        or sum(record["size_bytes"] for record in records) > MAX_HEADLESS_EVIDENCE_TOTAL_BYTES
    ):
        _fail("evidence_limit", "headless evidence payload exceeds its bounds")
    return records


def _manifest_seed(document: Mapping[str, object]) -> dict[str, object]:
    return {
        key: document[key]
        for key in (
            "state",
            "runtime_bundle",
            "execution_script",
            "headless_receipt",
            "runtime_evidence",
            "support",
            "files",
            "tree_hash",
            "file_count",
            "total_bytes",
        )
    }


def _seal_manifest(document: dict[str, object]) -> dict[str, object]:
    document["evidence_set_id"] = (
        "headless_evidence_set_" + canonical_creation_hash(_manifest_seed(document))[:40]
    )
    document["content_hash"] = canonical_creation_hash(document)
    return document


def validate_headless_evidence_set_document(value: object) -> dict[str, Any]:
    try:
        document = _object(copy.deepcopy(value), "headless evidence set")
        _exact_keys(document, _EVIDENCE_FIELDS, "headless evidence set")
        if document.get("format") != HEADLESS_EVIDENCE_SET_FORMAT:
            _fail("evidence_invalid", f"format must be {HEADLESS_EVIDENCE_SET_FORMAT}")
        if document.get("format_version") != HEADLESS_EVIDENCE_SET_VERSION:
            _fail("evidence_invalid", "headless evidence set format_version must be 1")
        if (
            type(document.get("evidence_set_id")) is not str
            or _SET_ID_RE.fullmatch(document["evidence_set_id"]) is None
        ):
            _fail("evidence_invalid", "headless evidence_set_id is invalid")
        if document.get("state") != "committed":
            _fail("evidence_invalid", "headless evidence set state must be committed")
        _validate_identity(
            document.get("runtime_bundle"),
            "headless evidence set.runtime_bundle",
            expected_format="world-forge.game_runtime_bundle",
        )
        _validate_identity(
            document.get("execution_script"),
            "headless evidence set.execution_script",
            expected_format=GAME_EXECUTION_SCRIPT_FORMAT,
        )
        _validate_identity(
            document.get("headless_receipt"),
            "headless evidence set.headless_receipt",
            expected_format=HEADLESS_EXECUTION_RECEIPT_FORMAT,
        )
        evidence = _object(
            document.get("runtime_evidence"),
            "headless evidence set.runtime_evidence",
        )
        _exact_keys(
            evidence,
            _RUNTIME_EVIDENCE_REFERENCE_FIELDS,
            "headless evidence set.runtime_evidence",
        )
        if (
            evidence.get("format") != "world-forge.runtime_evidence"
            or evidence.get("format_version") != 1
            or evidence.get("execution_status") != "headless_verified"
        ):
            _fail("evidence_overclaim", "runtime evidence reference is not headless_verified v1")
        _sha256(evidence.get("content_hash"), "runtime evidence reference.content_hash")
        platform_value = _object(evidence.get("platform"), "runtime evidence reference.platform")
        if platform_value.get("platform_id") not in {
            "platform:linux_x86_64",
            "platform:windows_x86_64",
        }:
            _fail("platform_unsupported", "runtime evidence reference platform is unsupported")
        support = _object(document.get("support"), "headless evidence set.support")
        _exact_keys(support, _SUPPORT_REFERENCE_FIELDS, "headless evidence set.support")
        if (
            support.get("format") != "world-forge.runtime_support_report"
            or support.get("format_version") != 1
            or support.get("compatibility_status") != "partially_supported"
            or support.get("release") != "blocked"
            or support.get("supported") is not False
        ):
            _fail("evidence_overclaim", "headless support reference overclaims release readiness")
        _sha256(support.get("content_hash"), "support reference.content_hash")
        files = document.get("files")
        if type(files) is not list or not files or len(files) > MAX_HEADLESS_EVIDENCE_FILES:
            _fail("evidence_limit", "headless evidence files must be bounded")
        paths: list[str] = []
        total = 0
        for index, raw in enumerate(files):
            record = _object(raw, f"headless evidence set.files/{index}")
            _exact_keys(record, _FILE_FIELDS, f"headless evidence set.files/{index}")
            paths.append(_path(record.get("path"), f"headless evidence set.files/{index}.path"))
            _sha256(record.get("sha256"), f"headless evidence set.files/{index}.sha256")
            size = record.get("size_bytes")
            if (
                type(size) is not int
                or isinstance(size, bool)
                or not 1 <= size <= MAX_HEADLESS_EVIDENCE_FILE_BYTES
            ):
                _fail("evidence_limit", "headless evidence file size is invalid")
            total += size
        if paths != sorted(paths, key=lambda item: item.encode("utf-8")):
            _fail("evidence_invalid", "headless evidence file paths are not canonical")
        if len({path.casefold() for path in paths}) != len(paths):
            _fail("evidence_invalid", "headless evidence file paths collide")
        expected_tree_hash = canonical_creation_hash({"files": files})
        if document.get("tree_hash") != expected_tree_hash:
            _fail("evidence_hash_mismatch", "headless evidence tree hash differs")
        if document.get("file_count") != len(files) or document.get("total_bytes") != total:
            _fail("evidence_hash_mismatch", "headless evidence inventory totals differ")
        expected_id = (
            "headless_evidence_set_" + canonical_creation_hash(_manifest_seed(document))[:40]
        )
        if document["evidence_set_id"] != expected_id:
            _fail("evidence_hash_mismatch", "headless evidence set ID differs")
        if document.get("content_hash") != canonical_creation_hash(document):
            _fail("evidence_hash_mismatch", "headless evidence set content hash differs")
        return document
    except GenericHeadlessError:
        raise
    except (TypeError, ValueError, RecursionError) as exc:
        _fail("evidence_invalid", str(exc))


def serialize_headless_evidence_set(value: object) -> bytes:
    return canonical_json_bytes(validate_headless_evidence_set_document(value))


def _commit_document(manifest: Mapping[str, object]) -> dict[str, object]:
    return {
        "format": "world-forge.headless_evidence_commit",
        "format_version": 1,
        "evidence_set": _identity(manifest, id_field="evidence_set_id"),
        "tree_hash": manifest["tree_hash"],
    }


def _validate_commit(value: object, manifest: Mapping[str, object]) -> dict[str, object]:
    document = _object(value, "headless evidence commit")
    _exact_keys(document, _COMMIT_FIELDS, "headless evidence commit")
    if document != _commit_document(manifest):
        _fail("evidence_commit_mismatch", "headless evidence commit marker differs")
    return document


def _bundle_inputs(bundle: VerifiedGameRuntimeBundle) -> dict[str, dict[str, Any]]:
    manifest = bundle.manifest

    def document(relative: str) -> dict[str, Any]:
        return decode_json_object(
            bundle.read_bytes(relative),
            source=f"{bundle.root}/{relative}",
            limit=MAX_GAME_EXECUTION_SCRIPT_BYTES,
        )

    adapter_path = manifest["contracts"]["runtime_adapter"]["path"]
    return {
        "gamepack": document("contracts/gamepack.json"),
        "composition": document("contracts/runtime-composition.json"),
        "adapter": document(adapter_path),
        "runtime_snapshot": document("contracts/runtime-snapshot.json"),
        "registry": document(manifest["contracts"]["runtime_adapter_registry"]["path"]),
    }


def _executor_key(adapter: Mapping[str, object]) -> str:
    exact = (
        adapter.get("adapter_id"),
        adapter.get("adapter_version"),
        adapter.get("content_hash"),
    )
    registry = {
        (
            item["adapter_id"],
            item["adapter_version"],
            item["content_hash"],
        ): "gamepack_runtime.headless.v1"
        for item in build_builtin_runtime_adapters()
    }
    try:
        return registry[exact]
    except KeyError:
        _fail("executor_absent", "no exact code-owned headless executor matches the adapter")


def _runtime_evidence_from_result(
    result: HeadlessExecutionResult,
    *,
    composition: Mapping[str, object],
) -> dict[str, Any]:
    receipt = validate_headless_execution_receipt(result.receipt)
    if receipt["executor"]["key"] != "gamepack_runtime.headless.v1":
        _fail("executor_absent", "headless receipt executor is not code-owned")
    checks = [
        {
            "check_id": check["check_id"],
            "kind": check["kind"],
            "status": check["status"],
            "evidence_id": check["evidence_id"],
            "content_hash": check["content_hash"],
        }
        for check in receipt["checks"]
    ]
    return build_runtime_evidence(
        composition,
        platform_id=receipt["host"]["platform_id"],
        execution_status="headless_verified",
        packaging_status="unverified",
        checks=checks,
    )


def _support_reference(document: Mapping[str, Any]) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document["report_id"],
        "content_hash": document["content_hash"],
        "compatibility_status": document["compatibility_status"],
        "release": document["dimensions"]["release"],
        "supported": document["supported"],
    }


def _runtime_evidence_reference(document: Mapping[str, Any]) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document["evidence_id"],
        "content_hash": document["content_hash"],
        "execution_status": document["execution_status"],
        "platform": copy.deepcopy(document["platform"]),
    }


def _build_payload(
    bundle: VerifiedGameRuntimeBundle,
    script: object,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    inputs = _bundle_inputs(bundle)
    _executor_key(inputs["adapter"])
    checked_script = validate_game_execution_script(
        bundle.manifest,
        script,
        **{key: inputs[key] for key in ("gamepack", "composition", "adapter", "runtime_snapshot")},
    )
    result = execute_game_execution_script(
        bundle.manifest,
        checked_script,
        **{key: inputs[key] for key in ("gamepack", "composition", "adapter", "runtime_snapshot")},
    )
    runtime_evidence = _runtime_evidence_from_result(
        result,
        composition=inputs["composition"],
    )
    support = build_runtime_support_report(
        inputs["composition"],
        gamepack=inputs["gamepack"],
        registry=inputs["registry"],
        snapshot=inputs["runtime_snapshot"],
        evidence=[runtime_evidence],
    )
    if (
        support["compatibility_status"] != "partially_supported"
        or support["dimensions"]["release"] != "blocked"
        or support["supported"]
        or support["dimensions"]["adapter"] != "declared"
        or support["dimensions"]["packaging"] != "unverified"
    ):
        _fail("evidence_overclaim", "headless support report overclaims adapter or release state")
    files: dict[str, bytes] = {
        "execution/script.json": serialize_game_execution_script(
            checked_script,
            bundle.manifest,
            **{
                key: inputs[key]
                for key in ("gamepack", "composition", "adapter", "runtime_snapshot")
            },
        ),
        "receipts/headless.json": result.receipt_bytes,
        "runtime/evidence.json": serialize_runtime_evidence(runtime_evidence),
        "runtime/support-report.json": serialize_runtime_support_report(support),
    }
    for scenario_id in sorted(result.saves, key=lambda item: item.encode("utf-8")):
        files[f"persistence/{scenario_id}.save.json"] = result.save_bytes[scenario_id]
        files[f"persistence/{scenario_id}.replay.json"] = result.replay_bytes[scenario_id]
    inventory = _file_inventory(files)
    manifest: dict[str, Any] = {
        "format": HEADLESS_EVIDENCE_SET_FORMAT,
        "format_version": HEADLESS_EVIDENCE_SET_VERSION,
        "evidence_set_id": "",
        "state": "committed",
        "runtime_bundle": _identity(bundle.manifest, id_field="bundle_id"),
        "execution_script": _identity(checked_script, id_field="script_id"),
        "headless_receipt": _identity(result.receipt, id_field="receipt_id"),
        "runtime_evidence": _runtime_evidence_reference(runtime_evidence),
        "support": _support_reference(support),
        "files": inventory,
        "tree_hash": canonical_creation_hash({"files": inventory}),
        "file_count": len(inventory),
        "total_bytes": sum(record["size_bytes"] for record in inventory),
        "content_hash": "",
    }
    return validate_headless_evidence_set_document(_seal_manifest(manifest)), files


@dataclass(frozen=True, slots=True)
class _TreeSnapshot:
    root_identity: DirectoryIdentity
    files: Mapping[str, bytes]
    directories: frozenset[str]


def _capture_tree(root: Path) -> _TreeSnapshot:
    identity: DirectoryIdentity | None = None

    def capture_root_identity(event: str, _relative: str | None) -> None:
        nonlocal identity
        if event != "before_final_verification":
            return
        try:
            identity = directory_identity(root, context="headless evidence root")
        except DirectoryPublishError as exc:
            _fail("evidence_tree_unsafe", str(exc))

    try:
        prefixed = _capture_runtime_files(
            root,
            _verification_hook=capture_root_identity,
        )
    except (RuntimeContractError, DirectoryPublishError) as exc:
        _fail("evidence_tree_unsafe", str(exc))
    if identity is None:
        _fail(
            "evidence_tree_unsafe",
            "retained evidence capture did not bind the root identity",
        )
    prefix = "gamepack_runtime/"
    if any(not path.startswith(prefix) for path in prefixed):
        _fail("evidence_tree_unsafe", "retained evidence capture returned invalid paths")
    files = {path.removeprefix(prefix): payload for path, payload in prefixed.items()}
    directories: set[str] = set()
    for path in files:
        parent = PurePosixPath(path).parent
        while parent.as_posix() != ".":
            directories.add(parent.as_posix())
            parent = parent.parent
    if (
        len(files) > MAX_HEADLESS_EVIDENCE_FILES + 2
        or len(directories) > MAX_HEADLESS_EVIDENCE_DIRECTORIES
    ):
        _fail("evidence_limit", "headless evidence tree exceeds node limits")
    return _TreeSnapshot(identity, MappingProxyType(files), frozenset(directories))


class VerifiedHeadlessEvidenceSet:
    """Retained immutable byte snapshot of one external evidence set."""

    __slots__ = ("_closed", "_evidence", "_files", "_manifest", "root", "root_identity")

    def __init__(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        files: Mapping[str, bytes],
        root_identity: DirectoryIdentity,
    ) -> None:
        self.root = root
        self.root_identity = root_identity
        self._manifest = copy.deepcopy(manifest)
        self._files = dict(files)
        self._evidence = MappingProxyType(
            {
                "execution_status": "headless_verified",
                "integrity": "valid",
                "release": "blocked",
                "supported": False,
                "evidence_set_id": manifest["evidence_set_id"],
                "content_hash": manifest["content_hash"],
            }
        )
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            _fail("evidence_snapshot_closed", "verified evidence snapshot is closed")

    @property
    def manifest(self) -> dict[str, Any]:
        self._require_open()
        return copy.deepcopy(self._manifest)

    @property
    def files(self) -> Mapping[str, bytes]:
        self._require_open()
        return MappingProxyType(dict(self._files))

    @property
    def evidence(self) -> Mapping[str, object]:
        self._require_open()
        return self._evidence

    def read_bytes(self, relative: str) -> bytes:
        self._require_open()
        try:
            return self._files[relative]
        except KeyError:
            _fail("evidence_file_missing", f"verified evidence has no file {relative!r}")

    def close(self) -> None:
        self._files.clear()
        self._closed = True

    def __enter__(self) -> VerifiedHeadlessEvidenceSet:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self.close()


def headless_authority_result_policy_document() -> dict[str, object]:
    """Return the exact JSON policy generated into Studio."""

    return {
        "version": HEADLESS_AUTHORITY_RESULT_POLICY["version"],
        "fields": list(HEADLESS_AUTHORITY_RESULT_FIELDS),
        "execution_status": HEADLESS_AUTHORITY_RESULT_POLICY["execution_status"],
        "integrity": HEADLESS_AUTHORITY_RESULT_POLICY["integrity"],
        "release": HEADLESS_AUTHORITY_RESULT_POLICY["release"],
        "supported": HEADLESS_AUTHORITY_RESULT_POLICY["supported"],
    }


def build_headless_authority_result(
    verified: VerifiedHeadlessEvidenceSet,
    *,
    path: str | Path,
) -> dict[str, object]:
    """Build the exact successful Python-to-Studio authority result."""

    if type(verified) is not VerifiedHeadlessEvidenceSet:
        _fail("authority_result_invalid", "verified evidence must be an exact snapshot")
    evidence = dict(verified.evidence)
    expected_evidence_fields = set(HEADLESS_AUTHORITY_RESULT_FIELDS) - {"path"}
    if set(evidence) != expected_evidence_fields:
        _fail("authority_result_invalid", "verified evidence fields differ")
    absolute = str(Path(os.path.abspath(os.fspath(path))))
    if unicodedata.normalize("NFC", absolute) != absolute:
        _fail("authority_result_invalid", "authority result path must be NFC")
    result = {**evidence, "path": absolute}
    if (
        tuple(sorted(result, key=lambda item: item.encode("utf-8")))
        != HEADLESS_AUTHORITY_RESULT_FIELDS
        or result["execution_status"] != HEADLESS_AUTHORITY_RESULT_POLICY["execution_status"]
        or result["integrity"] != HEADLESS_AUTHORITY_RESULT_POLICY["integrity"]
        or result["release"] != HEADLESS_AUTHORITY_RESULT_POLICY["release"]
        or result["supported"] is not HEADLESS_AUTHORITY_RESULT_POLICY["supported"]
        or _SET_ID_RE.fullmatch(result["evidence_set_id"]) is None  # type: ignore[arg-type]
        or _HEX_RE.fullmatch(result["content_hash"]) is None  # type: ignore[arg-type]
    ):
        _fail("authority_result_invalid", "authority result differs from code-owned policy")
    return result


def _decode_canonical(
    files: Mapping[str, bytes],
    relative: str,
    *,
    limit: int,
) -> dict[str, Any]:
    payload = files.get(relative)
    if payload is None:
        _fail("evidence_file_missing", f"headless evidence is missing {relative}")
    try:
        document = decode_json_object(payload, source=relative, limit=limit)
    except GameLogicError as exc:
        _fail("evidence_invalid", f"{relative}: {exc.reason_code}: {exc.detail}")
    return document


def verify_headless_evidence_set(
    root: str | Path,
    *,
    bundle_root: str | Path,
    expected_content_hash: str | None = None,
) -> VerifiedHeadlessEvidenceSet:
    root_path = Path(os.path.abspath(os.fspath(root)))
    bundle = verify_game_runtime_bundle(bundle_root)
    try:
        tree = _capture_tree(root_path)
        files = tree.files
        manifest = validate_headless_evidence_set_document(
            _decode_canonical(
                files,
                HEADLESS_EVIDENCE_SET_MANIFEST,
                limit=MAX_HEADLESS_EXECUTION_RECEIPT_BYTES,
            )
        )
        if expected_content_hash is not None and manifest["content_hash"] != expected_content_hash:
            _fail("binding_mismatch", "headless evidence set hash differs from expected hash")
        expected_files = {
            HEADLESS_EVIDENCE_SET_MANIFEST,
            HEADLESS_EVIDENCE_COMMIT,
            *(record["path"] for record in manifest["files"]),
        }
        if set(files) != expected_files:
            _fail("evidence_tree_unsafe", "headless evidence exact file closure differs")
        expected_directories = {
            parent.as_posix()
            for relative in expected_files
            for parent in [PurePosixPath(relative).parent]
            if parent.as_posix() != "."
        }
        if tree.directories != expected_directories:
            _fail("evidence_tree_unsafe", "headless evidence exact directory closure differs")
        for record in manifest["files"]:
            payload = files[record["path"]]
            if (
                len(payload) != record["size_bytes"]
                or hashlib.sha256(payload).hexdigest() != record["sha256"]
            ):
                _fail("evidence_hash_mismatch", f"evidence file differs: {record['path']}")
        if files[HEADLESS_EVIDENCE_SET_MANIFEST] != serialize_headless_evidence_set(manifest):
            _fail("evidence_invalid", "headless evidence manifest is noncanonical")
        commit = _decode_canonical(
            files,
            HEADLESS_EVIDENCE_COMMIT,
            limit=64 * 1024,
        )
        _validate_commit(commit, manifest)
        inputs = _bundle_inputs(bundle)
        context = persistence_context_from_bundle(bundle)
        script = validate_game_execution_script(
            bundle.manifest,
            _decode_canonical(
                files,
                "execution/script.json",
                limit=MAX_GAME_EXECUTION_SCRIPT_BYTES,
            ),
            **{
                key: inputs[key]
                for key in ("gamepack", "composition", "adapter", "runtime_snapshot")
            },
        )
        if manifest["runtime_bundle"] != _identity(bundle.manifest, id_field="bundle_id"):
            _fail("binding_mismatch", "headless evidence binds a different runtime bundle")
        if manifest["execution_script"] != _identity(script, id_field="script_id"):
            _fail("binding_mismatch", "headless evidence binds a different script")
        expected_result = execute_game_execution_script(
            bundle.manifest,
            script,
            **{
                key: inputs[key]
                for key in ("gamepack", "composition", "adapter", "runtime_snapshot")
            },
        )
        receipt = validate_headless_execution_receipt(
            _decode_canonical(
                files,
                "receipts/headless.json",
                limit=MAX_HEADLESS_EXECUTION_RECEIPT_BYTES,
            )
        )
        if (
            files["receipts/headless.json"] != expected_result.receipt_bytes
            or receipt != expected_result.receipt
            or manifest["headless_receipt"] != _identity(receipt, id_field="receipt_id")
        ):
            _fail("evidence_receipt_mismatch", "headless receipt differs from re-execution")
        for scenario_id in sorted(expected_result.saves, key=lambda item: item.encode("utf-8")):
            save_path = f"persistence/{scenario_id}.save.json"
            replay_path = f"persistence/{scenario_id}.replay.json"
            if (
                files[save_path] != expected_result.save_bytes[scenario_id]
                or files[replay_path] != expected_result.replay_bytes[scenario_id]
            ):
                _fail("evidence_persistence_mismatch", f"scenario={scenario_id} bytes differ")
            load_game_save_bytes(files[save_path], context, source=save_path)
            load_game_replay_bytes(files[replay_path], context, source=replay_path)
        runtime_evidence = validate_runtime_evidence_document(
            _decode_canonical(
                files,
                "runtime/evidence.json",
                limit=MAX_HEADLESS_EXECUTION_RECEIPT_BYTES,
            ),
            composition=inputs["composition"],
        )
        expected_runtime_evidence = _runtime_evidence_from_result(
            expected_result,
            composition=inputs["composition"],
        )
        if (
            runtime_evidence != expected_runtime_evidence
            or files["runtime/evidence.json"]
            != serialize_runtime_evidence(expected_runtime_evidence)
            or manifest["runtime_evidence"] != _runtime_evidence_reference(runtime_evidence)
        ):
            _fail("evidence_runtime_mismatch", "runtime evidence differs from exact receipt")
        support = validate_runtime_support_report_document(
            _decode_canonical(
                files,
                "runtime/support-report.json",
                limit=MAX_HEADLESS_EXECUTION_RECEIPT_BYTES,
            )
        )
        expected_support = build_runtime_support_report(
            inputs["composition"],
            gamepack=inputs["gamepack"],
            registry=inputs["registry"],
            snapshot=inputs["runtime_snapshot"],
            evidence=[runtime_evidence],
        )
        if (
            support != expected_support
            or files["runtime/support-report.json"]
            != serialize_runtime_support_report(expected_support)
            or manifest["support"] != _support_reference(support)
        ):
            _fail("evidence_support_mismatch", "support report differs from exact evidence")
        return VerifiedHeadlessEvidenceSet(
            root_path,
            manifest,
            files,
            tree.root_identity,
        )
    except GenericHeadlessError:
        raise
    except (GameRuntimeBundleError, GameLogicError, RuntimeContractError) as exc:
        reason = getattr(exc, "reason_code", "evidence_invalid")
        detail = getattr(exc, "detail", str(exc))
        _fail(reason, detail)
    finally:
        bundle.close()


def build_headless_evidence_tree(
    destination: str | Path,
    *,
    bundle_root: str | Path,
    script_bytes: bytes,
    expected_bundle_hash: str | None = None,
) -> VerifiedHeadlessEvidenceSet:
    """Build and verify one exact evidence tree without publishing it externally.

    This is the private-worker primitive.  The caller supplies retained script
    bytes rather than a pathname, and the destination must be an absent leaf in
    an already trusted private stage.  External publication remains a separate
    authority transition.
    """

    if not ((sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt"):
        _fail("platform_unsupported", "headless evidence supports Linux and Windows")
    if type(script_bytes) is not bytes or len(script_bytes) > MAX_GAME_EXECUTION_SCRIPT_BYTES:
        _fail("script_invalid", "retained headless script bytes are invalid")
    destination_path = _validate_destination(destination)
    try:
        if destination_path.exists() or destination_path.is_symlink():
            _fail("evidence_publication_failed", "private evidence destination already exists")
    except OSError as exc:
        _fail("evidence_publication_failed", str(exc))
    bundle = verify_game_runtime_bundle(
        bundle_root,
        expected_content_hash=expected_bundle_hash,
    )
    try:
        script = decode_json_object(
            script_bytes,
            source="retained game execution script",
            limit=MAX_GAME_EXECUTION_SCRIPT_BYTES,
        )
        manifest, payload_files = _build_payload(bundle, script)
    except GenericHeadlessError:
        raise
    except (GameLogicError, GameRuntimeBundleError, RuntimeContractError) as exc:
        _fail(
            getattr(exc, "reason_code", "script_invalid"),
            getattr(exc, "detail", str(exc)),
        )
    finally:
        bundle.close()
    files = {
        **payload_files,
        HEADLESS_EVIDENCE_SET_MANIFEST: serialize_headless_evidence_set(manifest),
        HEADLESS_EVIDENCE_COMMIT: canonical_json_bytes(_commit_document(manifest)),
    }
    try:
        with create_retained_stage(destination_path) as writer:
            stage_identity = writer.identity
            for relative in sorted(files, key=lambda item: item.encode("utf-8")):
                writer.write_file(relative, files[relative])
            writer.fsync()
            checked = verify_headless_evidence_set(
                destination_path,
                bundle_root=bundle_root,
                expected_content_hash=manifest["content_hash"],
            )
            try:
                if (
                    checked.root_identity != stage_identity
                    or checked.manifest != manifest
                    or dict(checked.files) != files
                ):
                    _fail("evidence_publication_failed", "private evidence tree changed")
            finally:
                checked.close()
        final = verify_headless_evidence_set(
            destination_path,
            bundle_root=bundle_root,
            expected_content_hash=manifest["content_hash"],
        )
        if final.root_identity != stage_identity:
            final.close()
            _fail("evidence_publication_failed", "private evidence identity changed")
        return final
    except GenericHeadlessError:
        raise
    except (DirectoryPublishError, FileExistsError, OSError) as exc:
        _fail("evidence_publication_failed", str(exc))


def publish_headless_evidence_tree(
    source: str | Path,
    destination: str | Path,
    *,
    bundle_root: str | Path,
    expected_content_hash: str,
    expected_tree_hash: str,
    expected_source_identity: DirectoryIdentity,
) -> VerifiedHeadlessEvidenceSet:
    """Journal and exclusively publish one already verified private tree.

    The worker tree is never renamed into caller-visible storage.  Its exact
    verified bytes are copied into a code-owned sibling stage so the final
    no-replace rename remains same-filesystem on Linux and Windows.  The
    existing headless journal is used for crash recovery before and after the
    visible rename.
    """

    source_path = Path(os.path.abspath(os.fspath(source)))
    destination_path = _validate_destination(destination)
    if (
        type(expected_source_identity) is not tuple
        or len(expected_source_identity) != 2
        or any(type(item) is not int for item in expected_source_identity)
    ):
        _fail("evidence_publication_failed", "private evidence identity is invalid")
    if (
        not isinstance(expected_content_hash, str)
        or _HEX_RE.fullmatch(expected_content_hash) is None
    ):
        _fail("evidence_publication_failed", "expected evidence hash is invalid")
    if not isinstance(expected_tree_hash, str) or _HEX_RE.fullmatch(expected_tree_hash) is None:
        _fail("evidence_publication_failed", "expected evidence tree hash is invalid")
    source_verified = verify_headless_evidence_set(
        source_path,
        bundle_root=bundle_root,
        expected_content_hash=expected_content_hash,
    )
    try:
        if (
            source_verified.root_identity != expected_source_identity
            or source_verified.manifest["tree_hash"] != expected_tree_hash
        ):
            _fail("evidence_publication_failed", "private evidence binding changed")
        source_manifest = source_verified.manifest
        source_files = dict(source_verified.files)
    finally:
        source_verified.close()
    try:
        lock_path = _lock_path(destination_path)
        with held_persistence_lock(lock_path):
            lock = _PublicationLockGuard.capture(lock_path)
            recovered = _recover_locked(
                destination_path,
                bundle_root=bundle_root,
                lock=lock,
            )
            if recovered is not None:
                if recovered.manifest == source_manifest and dict(recovered.files) == source_files:
                    return recovered
                recovered.close()
                _fail(
                    "evidence_publication_failed",
                    "destination contains a different immutable evidence set",
                )
            operation_id = uuid.uuid4().hex
            publication_stage = destination_path.parent / (
                f".{destination_path.name}.headless-evidence-{operation_id}"
            )
            intent = _journal_document(
                operation_id=operation_id,
                state="intent",
                stage=publication_stage,
                destination=destination_path,
                evidence_set_hash=expected_content_hash,
                stage_identity=None,
            )
            intent_payload = _journal_payload(intent)
            try:
                journal_identity = create_append_only_journal(
                    _journal_path(destination_path),
                    intent_payload,
                    max_record_bytes=MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES,
                )
            except (DirectoryPublishError, FileExistsError) as exc:
                _fail("evidence_publication_failed", str(exc))
            with create_retained_stage(
                publication_stage,
                require_guard=lock.require_binding,
            ) as writer:
                publication_identity = writer.identity
                for relative in sorted(source_files, key=lambda item: item.encode("utf-8")):
                    writer.write_file(relative, source_files[relative])
                writer.fsync()
                staged = verify_headless_evidence_set(
                    publication_stage,
                    bundle_root=bundle_root,
                    expected_content_hash=expected_content_hash,
                )
                try:
                    if (
                        staged.root_identity != publication_identity
                        or staged.manifest != source_manifest
                        or dict(staged.files) != source_files
                    ):
                        _fail("evidence_publication_failed", "publication stage changed")
                finally:
                    staged.close()
                ready = _journal_document(
                    operation_id=operation_id,
                    state="ready",
                    stage=publication_stage,
                    destination=destination_path,
                    evidence_set_hash=expected_content_hash,
                    stage_identity=publication_identity,
                )
                ready_payload = _journal_payload(ready)
                try:
                    append_append_only_journal(
                        _journal_path(destination_path),
                        expected_identity=journal_identity,
                        expected_payload=intent_payload,
                        expected_history=(intent_payload,),
                        updated_payload=ready_payload,
                        max_record_bytes=MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES,
                        max_file_bytes=MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES * 3,
                    )
                except DirectoryPublishError as exc:
                    _fail("evidence_publication_failed", str(exc))
                lock.require_binding()
                writer.require_binding()
            history = (intent_payload, ready_payload)
            try:
                with publish_directory_noreplace(
                    publication_stage,
                    destination_path,
                    expected_source_identity=publication_identity,
                ) as published_identity:
                    lock.require_binding()
                    if published_identity != publication_identity:
                        _fail(
                            "evidence_publication_failed",
                            "published evidence identity changed",
                        )
                    fsync_directory(
                        destination_path.parent,
                        context="headless evidence publication parent",
                    )
                    checked = verify_headless_evidence_set(
                        destination_path,
                        bundle_root=bundle_root,
                        expected_content_hash=expected_content_hash,
                    )
                    try:
                        if (
                            checked.root_identity != publication_identity
                            or checked.manifest != source_manifest
                            or dict(checked.files) != source_files
                        ):
                            _fail(
                                "evidence_publication_failed",
                                "published evidence tree changed",
                            )
                    finally:
                        checked.close()
                    lock.require_binding()
                    _remove_journal(
                        destination_path,
                        identity=journal_identity,
                        payloads=history,
                    )
                    lock.require_binding()
                    final = verify_headless_evidence_set(
                        destination_path,
                        bundle_root=bundle_root,
                        expected_content_hash=expected_content_hash,
                    )
                    if (
                        final.root_identity != publication_identity
                        or final.manifest != source_manifest
                        or dict(final.files) != source_files
                    ):
                        final.close()
                        _fail("evidence_publication_failed", "final evidence tree changed")
                    lock.require_binding()
                    return final
            except DirectoryPublishIndeterminateError as exc:
                _fail("evidence_publication_recovery_failed", str(exc))
            except (DirectoryPublishError, FileExistsError) as exc:
                _fail("evidence_publication_failed", str(exc))
    except GenericHeadlessError:
        raise
    except (GameLogicError, DirectoryPublishError, OSError) as exc:
        _fail("evidence_publication_failed", str(exc))


def _validate_destination(destination: str | Path) -> Path:
    path = Path(os.path.abspath(os.fspath(destination)))
    if (
        not path.name
        or path.name.startswith(".")
        or "/" in path.name
        or "\\" in path.name
        or unicodedata.normalize("NFC", path.name) != path.name
        or len(path.name.encode("utf-8")) > 160
    ):
        _fail("evidence_publication_failed", "evidence destination name is not portable")
    if not path.parent.exists():
        _fail("evidence_publication_failed", "evidence destination parent must exist")
    return path


def _journal_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.headless-evidence.journal"


def _lock_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.headless-evidence.lock"


def _journal_document(
    *,
    operation_id: str,
    state: str,
    stage: Path,
    destination: Path,
    evidence_set_hash: str,
    stage_identity: DirectoryIdentity | None,
) -> dict[str, object]:
    return {
        "format": "world-forge.headless_evidence_publication_journal",
        "format_version": 1,
        "operation_id": operation_id,
        "state": state,
        "stage_name": stage.name,
        "destination_name": destination.name,
        "evidence_set_hash": evidence_set_hash,
        "stage_identity": (
            None if stage_identity is None else [stage_identity[0], stage_identity[1]]
        ),
    }


def _validate_journal(
    value: object,
    destination: Path,
) -> dict[str, Any]:
    document = _object(value, "headless evidence publication journal")
    _exact_keys(document, _JOURNAL_FIELDS, "headless evidence publication journal")
    if (
        document.get("format") != "world-forge.headless_evidence_publication_journal"
        or document.get("format_version") != 1
        or type(document.get("operation_id")) is not str
        or _OPERATION_ID_RE.fullmatch(document["operation_id"]) is None
        or document.get("state") not in {"intent", "ready"}
        or document.get("destination_name") != destination.name
    ):
        _fail("evidence_recovery_failed", "headless evidence journal metadata differs")
    expected_stage = f".{destination.name}.headless-evidence-{document['operation_id']}"
    if document.get("stage_name") != expected_stage:
        _fail("evidence_recovery_failed", "headless evidence journal stage differs")
    _sha256(document.get("evidence_set_hash"), "headless evidence journal hash")
    identity = document.get("stage_identity")
    if document["state"] == "intent":
        if identity is not None:
            _fail("evidence_recovery_failed", "intent journal has a stage identity")
    elif (
        type(identity) is not list
        or len(identity) != 2
        or any(type(item) is not int or isinstance(item, bool) for item in identity)
    ):
        _fail("evidence_recovery_failed", "ready journal stage identity is invalid")
    return document


def _journal_payload(document: Mapping[str, object]) -> bytes:
    return canonical_json_bytes(document)


def _read_journal(
    destination: Path,
) -> tuple[list[dict[str, Any]], DirectoryIdentity, tuple[bytes, ...], bool] | None:
    try:
        loaded = read_append_only_journal_history_state(
            _journal_path(destination),
            max_record_bytes=MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES,
            max_file_bytes=MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES * 3,
        )
    except DirectoryPublishError as exc:
        _fail("evidence_recovery_failed", str(exc))
    if loaded is None:
        return None
    payloads, identity, partial = loaded
    documents = [
        _validate_journal(
            decode_json_object(
                payload,
                source=str(_journal_path(destination)),
                limit=MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES,
            ),
            destination,
        )
        for payload in payloads
    ]
    if len(documents) not in {1, 2}:
        _fail("evidence_recovery_failed", "headless evidence journal history length differs")
    if len(documents) == 2:
        if (
            documents[0]["state"] != "intent"
            or documents[1]["state"] != "ready"
            or documents[0]["operation_id"] != documents[1]["operation_id"]
            or documents[0]["evidence_set_hash"] != documents[1]["evidence_set_hash"]
        ):
            _fail("evidence_recovery_failed", "headless evidence journal history differs")
    return documents, identity, payloads, partial


def _optional_identity(path: Path) -> DirectoryIdentity | None:
    try:
        return directory_identity(path, context="headless evidence publication")
    except DirectoryPublishError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return None
        _fail("evidence_recovery_failed", str(exc))


def _remove_journal(
    destination: Path,
    *,
    identity: DirectoryIdentity,
    payloads: tuple[bytes, ...],
) -> None:
    try:
        remove_append_only_journal(
            _journal_path(destination),
            expected_identity=identity,
            expected_payload=payloads[-1],
            expected_history=payloads,
            max_record_bytes=MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES,
            max_file_bytes=MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES * 3,
        )
    except (DirectoryPublishError, FileNotFoundError) as exc:
        _fail("evidence_publication_recovery_failed", str(exc))


def _recover_locked(
    destination: Path,
    *,
    bundle_root: str | Path,
    lock: _PublicationLockGuard,
) -> VerifiedHeadlessEvidenceSet | None:
    lock.require_binding()
    loaded = _read_journal(destination)
    if loaded is None:
        identity = _optional_identity(destination)
        if identity is None:
            return None
        return verify_headless_evidence_set(destination, bundle_root=bundle_root)
    documents, journal_identity, payloads, partial = loaded
    current = documents[-1]
    stage = destination.parent / current["stage_name"]
    stage_identity = _optional_identity(stage)
    destination_identity = _optional_identity(destination)
    if current["state"] == "intent":
        if partial or stage_identity is not None or destination_identity is not None:
            _fail(
                "evidence_publication_recovery_failed",
                "intent journal has ambiguous stage or destination state",
            )
        lock.require_binding()
        _remove_journal(
            destination,
            identity=journal_identity,
            payloads=payloads,
        )
        return None
    if partial:
        _fail("evidence_publication_recovery_failed", "ready journal has a torn tail")
    expected_identity = tuple(current["stage_identity"])
    if stage_identity == expected_identity and destination_identity is None:
        try:
            lock.require_binding()
            with publish_directory_noreplace(
                stage,
                destination,
                expected_source_identity=expected_identity,
            ):
                lock.require_binding()
                verified = verify_headless_evidence_set(
                    destination,
                    bundle_root=bundle_root,
                    expected_content_hash=current["evidence_set_hash"],
                )
                try:
                    if verified.root_identity != expected_identity:
                        _fail(
                            "evidence_publication_recovery_failed",
                            "recovered destination identity differs",
                        )
                    lock.require_binding()
                    _remove_journal(
                        destination,
                        identity=journal_identity,
                        payloads=payloads,
                    )
                    final = verify_headless_evidence_set(
                        destination,
                        bundle_root=bundle_root,
                        expected_content_hash=current["evidence_set_hash"],
                    )
                    lock.require_binding()
                finally:
                    verified.close()
                return final
        except DirectoryPublishIndeterminateError as exc:
            _fail("evidence_publication_recovery_failed", str(exc))
        except (DirectoryPublishError, FileExistsError) as exc:
            _fail("evidence_publication_recovery_failed", str(exc))
    if destination_identity == expected_identity and stage_identity is None:
        lock.require_binding()
        verified = verify_headless_evidence_set(
            destination,
            bundle_root=bundle_root,
            expected_content_hash=current["evidence_set_hash"],
        )
        lock.require_binding()
        _remove_journal(
            destination,
            identity=journal_identity,
            payloads=payloads,
        )
        final = verify_headless_evidence_set(
            destination,
            bundle_root=bundle_root,
            expected_content_hash=current["evidence_set_hash"],
        )
        lock.require_binding()
        verified.close()
        return final
    _fail(
        "evidence_publication_recovery_failed",
        "ready journal stage/destination identities are ambiguous",
    )


def recover_headless_evidence_set(
    destination: str | Path,
    *,
    bundle_root: str | Path,
) -> VerifiedHeadlessEvidenceSet | None:
    destination_path = _validate_destination(destination)
    try:
        lock_path = _lock_path(destination_path)
        with held_persistence_lock(lock_path):
            lock = _PublicationLockGuard.capture(lock_path)
            return _recover_locked(
                destination_path,
                bundle_root=bundle_root,
                lock=lock,
            )
    except GenericHeadlessError:
        raise
    except (GameLogicError, DirectoryPublishError, OSError) as exc:
        _fail(
            "evidence_publication_recovery_failed",
            getattr(exc, "detail", str(exc)),
        )


def build_headless_evidence_set(
    destination: str | Path,
    *,
    bundle_root: str | Path,
    script_path: str | Path,
    expected_bundle_hash: str | None = None,
    _publication_hook: _PublicationHook | None = None,
) -> VerifiedHeadlessEvidenceSet:
    """Execute, stage, verify and exclusively publish an external evidence set."""

    if not ((sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt"):
        _fail("platform_unsupported", "headless evidence publication supports Linux and Windows")
    destination_path = _validate_destination(destination)
    bundle = verify_game_runtime_bundle(
        bundle_root,
        expected_content_hash=expected_bundle_hash,
    )
    try:
        script = decode_json_object(
            Path(script_path).read_bytes(),
            source=str(script_path),
            limit=MAX_GAME_EXECUTION_SCRIPT_BYTES,
        )
        manifest, payload_files = _build_payload(bundle, script)
    except GenericHeadlessError:
        raise
    except (OSError, GameLogicError, GameRuntimeBundleError, RuntimeContractError) as exc:
        _fail(
            getattr(exc, "reason_code", "script_invalid"),
            getattr(exc, "detail", str(exc)),
        )
    finally:
        bundle.close()
    files = {
        **payload_files,
        HEADLESS_EVIDENCE_SET_MANIFEST: serialize_headless_evidence_set(manifest),
        HEADLESS_EVIDENCE_COMMIT: canonical_json_bytes(_commit_document(manifest)),
    }
    try:
        lock_path = _lock_path(destination_path)
        with held_persistence_lock(lock_path):
            lock = _PublicationLockGuard.capture(lock_path)
            if _publication_hook is not None:
                _publication_hook("after_lock_acquired", None)
            lock.require_binding()
            recovered = _recover_locked(
                destination_path,
                bundle_root=bundle_root,
                lock=lock,
            )
            if recovered is not None:
                if recovered.manifest["content_hash"] == manifest["content_hash"]:
                    return recovered
                recovered.close()
                _fail(
                    "evidence_publication_failed",
                    "destination contains a different immutable evidence set",
                )
            operation_id = uuid.uuid4().hex
            stage = destination_path.parent / (
                f".{destination_path.name}.headless-evidence-{operation_id}"
            )
            intent = _journal_document(
                operation_id=operation_id,
                state="intent",
                stage=stage,
                destination=destination_path,
                evidence_set_hash=manifest["content_hash"],
                stage_identity=None,
            )
            intent_payload = _journal_payload(intent)
            try:
                journal_identity = create_append_only_journal(
                    _journal_path(destination_path),
                    intent_payload,
                    max_record_bytes=MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES,
                )
            except (DirectoryPublishError, FileExistsError) as exc:
                _fail("evidence_publication_failed", str(exc))
            if _publication_hook is not None:
                _publication_hook("after_intent_journal_written", None)
            with create_retained_stage(
                stage,
                require_guard=lock.require_binding,
                hook=_publication_hook,
            ) as writer:
                stage_identity = writer.identity
                for relative in sorted(files, key=lambda item: item.encode("utf-8")):
                    writer.write_file(relative, files[relative])
                writer.fsync()
                checked_stage = verify_headless_evidence_set(
                    stage,
                    bundle_root=bundle_root,
                    expected_content_hash=manifest["content_hash"],
                )
                try:
                    if checked_stage.root_identity != stage_identity:
                        _fail("evidence_publication_failed", "staged evidence identity differs")
                finally:
                    checked_stage.close()
                ready = _journal_document(
                    operation_id=operation_id,
                    state="ready",
                    stage=stage,
                    destination=destination_path,
                    evidence_set_hash=manifest["content_hash"],
                    stage_identity=stage_identity,
                )
                ready_payload = _journal_payload(ready)
                try:
                    append_append_only_journal(
                        _journal_path(destination_path),
                        expected_identity=journal_identity,
                        expected_payload=intent_payload,
                        expected_history=(intent_payload,),
                        updated_payload=ready_payload,
                        max_record_bytes=MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES,
                        max_file_bytes=MAX_HEADLESS_EVIDENCE_JOURNAL_BYTES * 3,
                    )
                except DirectoryPublishError as exc:
                    _fail("evidence_publication_failed", str(exc))
                if _publication_hook is not None:
                    _publication_hook("after_ready_journal_written", None)
                lock.require_binding()
                writer.require_binding()
            history = (intent_payload, ready_payload)
            try:
                with publish_directory_noreplace(
                    stage,
                    destination_path,
                    expected_source_identity=stage_identity,
                ) as published_identity:
                    lock.require_binding()
                    if published_identity != stage_identity:
                        _fail("evidence_publication_failed", "published identity differs")
                    fsync_directory(
                        destination_path.parent,
                        context="headless evidence publication parent",
                    )
                    verified = verify_headless_evidence_set(
                        destination_path,
                        bundle_root=bundle_root,
                        expected_content_hash=manifest["content_hash"],
                    )
                    try:
                        if verified.root_identity != stage_identity:
                            _fail(
                                "evidence_publication_failed",
                                "published evidence identity differs",
                            )
                    finally:
                        verified.close()
                    if _publication_hook is not None:
                        _publication_hook("before_journal_remove", None)
                    lock.require_binding()
                    _remove_journal(
                        destination_path,
                        identity=journal_identity,
                        payloads=history,
                    )
                    if _publication_hook is not None:
                        _publication_hook("after_journal_remove", None)
                    lock.require_binding()
                    final = verify_headless_evidence_set(
                        destination_path,
                        bundle_root=bundle_root,
                        expected_content_hash=manifest["content_hash"],
                    )
                    if final.root_identity != stage_identity:
                        final.close()
                        _fail(
                            "evidence_publication_failed",
                            "final evidence identity differs",
                        )
                    lock.require_binding()
                    return final
            except DirectoryPublishIndeterminateError as exc:
                _fail("evidence_publication_recovery_failed", str(exc))
            except (DirectoryPublishError, FileExistsError) as exc:
                _fail("evidence_publication_failed", str(exc))
    except GenericHeadlessError:
        raise
    except (GameLogicError, OSError) as exc:
        _fail(
            "evidence_publication_failed",
            getattr(exc, "detail", str(exc)),
        )


__all__ = [
    "HEADLESS_AUTHORITY_RESULT_FIELDS",
    "HEADLESS_AUTHORITY_RESULT_POLICY",
    "HEADLESS_EVIDENCE_COMMIT",
    "HEADLESS_EVIDENCE_SET_FORMAT",
    "HEADLESS_EVIDENCE_SET_MANIFEST",
    "HEADLESS_EVIDENCE_SET_VERSION",
    "GenericHeadlessError",
    "VerifiedHeadlessEvidenceSet",
    "build_headless_authority_result",
    "build_headless_evidence_set",
    "build_headless_evidence_tree",
    "headless_authority_result_policy_document",
    "publish_headless_evidence_tree",
    "recover_headless_evidence_set",
    "serialize_headless_evidence_set",
    "validate_headless_evidence_set_document",
    "verify_headless_evidence_set",
]
