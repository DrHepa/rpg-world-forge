from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from worldforge._publication_identity import (
    PublicationIdentityCodecError,
    decode_publication_identity,
    encode_publication_identity,
)
from worldforge.creation_contracts import (
    CreationContractError,
    _decode_creation_object,
    _exact_keys,
    _identifier,
    _integer,
    _object,
    _portable_relative_path,
    _sha256,
    _validate_json_structure,
    canonical_creation_hash,
)
from worldforge.directory_publish import (
    DirectoryIdentity,
    DirectoryPublishError,
    DirectoryPublishIndeterminateError,
    DirectoryPublishRecoveryRequiredError,
    RetainedStageWriter,
    append_append_only_journal,
    create_append_only_journal,
    create_retained_stage,
    fsync_directory,
    publish_directory_noreplace,
    quarantine_and_remove_verified_directory,
    read_append_only_journal_history_state,
    remove_append_only_journal,
    remove_verified_empty_directory,
    retained_journal_evidence_path,
    retained_recovery_evidence,
)
from worldforge.file_stat import (
    FileStat,
    descriptor_file_stat,
    file_identity,
    is_link_or_reparse,
    path_file_stat,
)
from worldforge.game_boundary_policy import validate_lexical_directory_root
from worldforge.game_runtime_bundle import (
    GAME_RUNTIME_BUNDLE_FORMAT,
    GAME_RUNTIME_BUNDLE_MANIFEST,
    GameRuntimeBundleError,
    _capture_bundle_tree,
    _expected_directories,
    _verify_game_runtime_bundle_with_stage_capability,
    verify_game_runtime_bundle,
)
from worldforge.generic_runtime import (
    RuntimeContractError,
    _create_runtime_stage_read_capability,
    _RuntimeStageReadCapability,
    validate_runtime_adapter_document,
    validate_runtime_snapshot_document,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.runtime_implementation import (
    RUNTIME_IMPLEMENTATION_FORMAT,
    RuntimeImplementationError,
    build_runtime_implementation,
    serialize_runtime_implementation,
    validate_runtime_implementation_document,
)
from worldforge.runtime_platform_lock import (
    RUNTIME_PLATFORM_LOCK_FORMAT,
    RuntimePlatformLockError,
    build_builtin_runtime_platform_locks,
    serialize_runtime_platform_lock,
    validate_runtime_platform_lock_document,
)
from worldforge.standalone_templates import (
    REQUIRED_LAUNCHER_ROLES,
    STANDALONE_TEMPLATE_FILES,
    materialization_policy_bytes,
)

GAME_MATERIALIZATION_BUNDLE_FORMAT = "world-forge.game_materialization_bundle"
GAME_MATERIALIZATION_BUNDLE_VERSION = 1
GAME_MATERIALIZATION_BUNDLE_MANIFEST = "game-materialization-bundle.json"
GAME_MATERIALIZATION_BUNDLE_JOURNAL_FORMAT = (
    "world-forge.game_materialization_bundle_publication_journal"
)
GAME_MATERIALIZATION_BUNDLE_JOURNAL_VERSION = 1
MAX_GAME_MATERIALIZATION_BUNDLE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_GAME_MATERIALIZATION_BUNDLE_JOURNAL_BYTES = 16 * MAX_GAME_MATERIALIZATION_BUNDLE_MANIFEST_BYTES

_RUNTIME_BUNDLE_ROOT = "runtime-bundle"
_IMPLEMENTATION_PATH = "contracts/runtime-implementation.json"
_PLATFORM_LOCK_ROOT = "contracts/platform-locks"
_LAUNCHER_POLICY_PATH = "launchers/materialization-policy.json"
_LICENSE_PATH = "licenses/world-forge-mit.txt"
_LICENSE_SHA256 = "2e55c53ff294650e049d844f2544fec947c3516440aeffca4b2334cf94b13eeb"
_LICENSE_SIZE = 1063
_REQUIRED_LAUNCHER_ROLES = REQUIRED_LAUNCHER_ROLES

_MANIFEST_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "materialization_bundle_id",
        "state",
        "materialization_ready",
        "missing_launcher_roles",
        "runtime_bundle",
        "runtime_implementation",
        "platform_locks",
        "launchers",
        "lineage",
        "legal",
        "files",
        "tree_hash",
        "content_hash",
    }
)
_RUNTIME_BUNDLE_FIELDS = frozenset({"root", "manifest"})
_RUNTIME_BUNDLE_MANIFEST_FIELDS = frozenset(
    {"path", "format", "format_version", "id", "content_hash", "tree_hash"}
)
_IMPLEMENTATION_IDENTITY_FIELDS = frozenset(
    {"path", "format", "format_version", "id", "content_hash"}
)
_PLATFORM_LOCK_SET_FIELDS = frozenset({"root", "set_hash", "locks"})
_PLATFORM_LOCK_IDENTITY_FIELDS = frozenset(
    {
        "path",
        "format",
        "format_version",
        "id",
        "content_hash",
        "os",
        "python_minor",
        "abi",
    }
)
_LAUNCHER_FIELDS = frozenset({"root", "policy_version", "required_roles", "inventory", "tree_hash"})
_LAUNCHER_RECORD_FIELDS = frozenset({"path", "output_path", "role", "sha256", "size_bytes"})
_LINEAGE_FIELDS = frozenset(
    {
        "gamepack_hash",
        "assetpack_hash",
        "assetpack_root_hash",
        "assetpack_inventory_hash",
        "runtime_snapshot_hash",
        "runtime_snapshot_tree_hash",
        "adapter_hash",
        "registry_hash",
        "composition_hash",
        "support_report_hash",
        "runtime_bundle_hash",
        "runtime_bundle_tree_hash",
        "runtime_implementation_hash",
        "platform_lock_set_hash",
    }
)
_LEGAL_FIELDS = frozenset({"bundle_license"})
_FILE_FIELDS = frozenset({"path", "sha256", "size_bytes"})
_JOURNAL_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "operation_id",
        "state",
        "stage_name",
        "destination_name",
        "parent_identity",
        "stage_identity",
        "materialization_bundle_id",
        "content_hash",
        "tree_hash",
        "runtime_bundle_hash",
        "runtime_implementation_hash",
        "platform_lock_set_hash",
        "manifest_sha256",
        "manifest_size_bytes",
    }
)

_TreeState = tuple[int, int, int, int, int, int, int]
_PublicationHook = Callable[[str, str | None], None]


class GameMaterializationBundleError(ValueError):
    """Raised when the materialization envelope is unsafe or incoherent."""

    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        recovery_evidence: Mapping[str, object] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.detail = detail
        self.recovery_evidence = dict(recovery_evidence or {})
        super().__init__(f"{reason_code}: {detail}")


def _fail(
    reason_code: str,
    detail: str,
    *,
    recovery_evidence: Mapping[str, object] | None = None,
) -> None:
    raise GameMaterializationBundleError(
        reason_code,
        detail,
        recovery_evidence=recovery_evidence,
    )


def _hash(document: Mapping[str, object]) -> str:
    try:
        return canonical_creation_hash(document)
    except CreationContractError as exc:
        _fail("game_materialization_bundle_invalid", str(exc))


def _utf8_key(value: str) -> bytes:
    return value.encode("utf-8")


def _file_record(path: str, payload: bytes) -> dict[str, object]:
    return {
        "path": path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _file_inventory(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    return [_file_record(path, files[path]) for path in sorted(files, key=_utf8_key)]


def _validate_file_records(
    value: object,
    context: str,
    *,
    allow_empty: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "" if allow_empty else " non-empty"
        _fail(
            "game_materialization_bundle_invalid",
            f"{context} must be a{qualifier} array",
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item_context = f"{context}/{index}"
        item = _object(raw, item_context)
        _exact_keys(item, _FILE_FIELDS, item_context)
        path = _portable_relative_path(item.get("path"), f"{item_context}.path")
        folded = path.casefold()
        if folded in seen:
            _fail(
                "game_materialization_bundle_path_collision",
                f"{context} contains an NFC/casefold path collision",
            )
        seen.add(folded)
        _sha256(item.get("sha256"), f"{item_context}.sha256")
        _integer(item.get("size_bytes"), f"{item_context}.size_bytes")
        result.append(copy.deepcopy(item))
    if [item["path"] for item in result] != sorted(
        (item["path"] for item in result),
        key=_utf8_key,
    ):
        _fail(
            "game_materialization_bundle_file_order_invalid",
            f"{context} must use UTF-8 path order",
        )
    return result


def _runtime_bundle_identity(
    manifest: Mapping[str, object],
) -> dict[str, object]:
    return {
        "root": _RUNTIME_BUNDLE_ROOT,
        "manifest": {
            "path": f"{_RUNTIME_BUNDLE_ROOT}/{GAME_RUNTIME_BUNDLE_MANIFEST}",
            "format": GAME_RUNTIME_BUNDLE_FORMAT,
            "format_version": 1,
            "id": manifest["bundle_id"],
            "content_hash": manifest["content_hash"],
            "tree_hash": manifest["tree_hash"],
        },
    }


def _implementation_identity(
    implementation: Mapping[str, object],
) -> dict[str, object]:
    return {
        "path": _IMPLEMENTATION_PATH,
        "format": RUNTIME_IMPLEMENTATION_FORMAT,
        "format_version": 1,
        "id": implementation["implementation_id"],
        "content_hash": implementation["content_hash"],
    }


def _lock_identities(
    locks: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    identities = [
        {
            "path": f"{_PLATFORM_LOCK_ROOT}/{lock['lock_id']}.json",
            "format": RUNTIME_PLATFORM_LOCK_FORMAT,
            "format_version": 1,
            "id": lock["lock_id"],
            "content_hash": lock["content_hash"],
            "os": lock["platform"]["os"],  # type: ignore[index]
            "python_minor": lock["python"]["minor"],  # type: ignore[index]
            "abi": lock["python"]["abi"],  # type: ignore[index]
        }
        for lock in locks
    ]
    return sorted(identities, key=lambda item: _utf8_key(str(item["id"])))


def _launcher_policy_bytes(*, ready: bool) -> bytes:
    return materialization_policy_bytes(ready=ready)


def _launcher_payloads(*, ready: bool) -> dict[str, tuple[bytes, str, str]]:
    result = {
        _LAUNCHER_POLICY_PATH: (
            _launcher_policy_bytes(ready=ready),
            "materialization_policy",
            "materialization-policy.json",
        )
    }
    if ready:
        result.update(
            {
                f"launchers/templates/{output_path}": (
                    payload,
                    role,
                    output_path,
                )
                for output_path, (payload, role) in STANDALONE_TEMPLATE_FILES.items()
            }
        )
    return result


def _lineage(
    runtime_manifest: Mapping[str, object],
    implementation: Mapping[str, object],
    lock_set_hash: str,
) -> dict[str, object]:
    contracts = runtime_manifest["contracts"]  # type: ignore[index]
    assetpack = runtime_manifest["assetpack"]  # type: ignore[index]
    runtime_tree = runtime_manifest["runtime_snapshot_tree"]  # type: ignore[index]
    return {
        "gamepack_hash": contracts["gamepack"]["content_hash"],
        "assetpack_hash": assetpack["manifest"]["content_hash"],
        "assetpack_root_hash": assetpack["root_hash"],
        "assetpack_inventory_hash": assetpack["inventory_hash"],
        "runtime_snapshot_hash": contracts["runtime_snapshot"]["content_hash"],
        "runtime_snapshot_tree_hash": runtime_tree["tree_hash"],
        "adapter_hash": contracts["runtime_adapter"]["content_hash"],
        "registry_hash": contracts["runtime_adapter_registry"]["content_hash"],
        "composition_hash": contracts["runtime_composition"]["content_hash"],
        "support_report_hash": contracts["runtime_support_report"]["content_hash"],
        "runtime_bundle_hash": runtime_manifest["content_hash"],
        "runtime_bundle_tree_hash": runtime_manifest["tree_hash"],
        "runtime_implementation_hash": implementation["content_hash"],
        "platform_lock_set_hash": lock_set_hash,
    }


def _nested_runtime_documents(
    runtime_manifest: Mapping[str, object],
    files: Mapping[str, bytes],
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot_path = runtime_manifest["contracts"]["runtime_snapshot"]["path"]  # type: ignore[index]
    adapter_path = runtime_manifest["contracts"]["runtime_adapter"]["path"]  # type: ignore[index]
    try:
        snapshot = validate_runtime_snapshot_document(
            _decode_creation_object(
                files[snapshot_path],
                f"runtime bundle {snapshot_path}",
            )
        )
        adapter = validate_runtime_adapter_document(
            _decode_creation_object(
                files[adapter_path],
                f"runtime bundle {adapter_path}",
            )
        )
        return snapshot, adapter
    except KeyError as exc:
        _fail(
            "game_materialization_bundle_nested_contract_missing",
            f"runtime bundle is missing {exc.args[0]}",
        )
    except (CreationContractError, RuntimeContractError) as exc:
        _fail("game_materialization_bundle_nested_contract_invalid", str(exc))


def _validated_implementation(
    value: object,
    *,
    runtime_manifest: Mapping[str, object],
    runtime_files: Mapping[str, bytes],
    locks: Sequence[object],
) -> dict[str, Any]:
    snapshot, adapter = _nested_runtime_documents(runtime_manifest, runtime_files)
    try:
        implementation = validate_runtime_implementation_document(
            value,
            adapter=adapter,
            snapshot=snapshot,
            platform_locks=locks,
        )
    except RuntimeImplementationError as exc:
        if exc.reason_code == "runtime_implementation_adapter_mismatch":
            _fail("runtime_implementation_adapter_mismatch", exc.detail)
        _fail("game_materialization_bundle_runtime_implementation_invalid", str(exc))
    runtime_adapter = runtime_manifest["contracts"]["runtime_adapter"]  # type: ignore[index]
    if implementation["adapter"] != {
        "adapter_id": runtime_adapter["id"],
        "adapter_version": runtime_adapter["adapter_version"],
        "content_hash": runtime_adapter["content_hash"],
    }:
        _fail(
            "runtime_implementation_adapter_mismatch",
            "runtime implementation does not identify the nested active adapter",
        )
    runtime_snapshot = runtime_manifest["contracts"]["runtime_snapshot"]  # type: ignore[index]
    if (
        implementation["snapshot"]["snapshot_id"] != runtime_snapshot["id"]
        or implementation["snapshot"]["content_hash"] != runtime_snapshot["content_hash"]
        or implementation["snapshot"]["tree_hash"]
        != runtime_manifest["runtime_snapshot_tree"]["tree_hash"]  # type: ignore[index]
    ):
        _fail(
            "runtime_implementation_snapshot_mismatch",
            "runtime implementation does not identify the nested snapshot",
        )
    return implementation


def _build_manifest(
    *,
    runtime_manifest: Mapping[str, object],
    implementation: Mapping[str, object],
    locks: Sequence[Mapping[str, object]],
    payload_files: Mapping[str, bytes],
    materialization_ready: bool,
) -> dict[str, Any]:
    lock_identities = _lock_identities(locks)
    lock_set_hash = _hash({"locks": lock_identities})
    launcher_payloads = _launcher_payloads(ready=materialization_ready)
    launcher_inventory = [
        {
            **_file_record(path, payload_files[path]),
            "output_path": output_path,
            "role": role,
        }
        for path, (_payload, role, output_path) in sorted(
            launcher_payloads.items(),
            key=lambda item: _utf8_key(item[0]),
        )
    ]
    missing_roles = [
        role
        for role in _REQUIRED_LAUNCHER_ROLES
        if role not in {item["role"] for item in launcher_inventory}
    ]
    files = _file_inventory(payload_files)
    seed: dict[str, Any] = {
        "format": GAME_MATERIALIZATION_BUNDLE_FORMAT,
        "format_version": GAME_MATERIALIZATION_BUNDLE_VERSION,
        "state": ("materialization_ready" if materialization_ready else "contract_only"),
        "materialization_ready": materialization_ready,
        "missing_launcher_roles": missing_roles,
        "runtime_bundle": _runtime_bundle_identity(runtime_manifest),
        "runtime_implementation": _implementation_identity(implementation),
        "platform_locks": {
            "root": _PLATFORM_LOCK_ROOT,
            "set_hash": lock_set_hash,
            "locks": lock_identities,
        },
        "launchers": {
            "root": "launchers",
            "policy_version": 1,
            "required_roles": list(_REQUIRED_LAUNCHER_ROLES),
            "inventory": launcher_inventory,
            "tree_hash": _hash(
                {
                    "files": [
                        {
                            "path": item["path"],
                            "sha256": item["sha256"],
                            "size_bytes": item["size_bytes"],
                        }
                        for item in launcher_inventory
                    ]
                }
            ),
        },
        "lineage": _lineage(runtime_manifest, implementation, lock_set_hash),
        "legal": {
            "bundle_license": _file_record(
                _LICENSE_PATH,
                payload_files[_LICENSE_PATH],
            )
        },
        "files": files,
        "tree_hash": _hash({"files": files}),
    }
    manifest = {
        **seed,
        "materialization_bundle_id": ("game_materialization_bundle_" + _hash(seed)[:36]),
        "content_hash": "",
    }
    manifest["content_hash"] = _hash(manifest)
    return validate_game_materialization_bundle_document(manifest)


def validate_game_materialization_bundle_document(
    value: object,
) -> dict[str, Any]:
    try:
        _validate_json_structure(value, context="game materialization bundle")
        document = _object(value, "game materialization bundle")
        _exact_keys(document, _MANIFEST_FIELDS, "game materialization bundle")
        if document.get("format") != GAME_MATERIALIZATION_BUNDLE_FORMAT:
            _fail(
                "game_materialization_bundle_format_mismatch",
                f"format must be {GAME_MATERIALIZATION_BUNDLE_FORMAT}",
            )
        if document.get("format_version") != GAME_MATERIALIZATION_BUNDLE_VERSION:
            _fail(
                "game_materialization_bundle_version_mismatch",
                "format_version must be 1",
            )
        bundle_id = _identifier(
            document.get("materialization_bundle_id"),
            "game materialization bundle.materialization_bundle_id",
        )
        state = document.get("state")
        if state not in {"contract_only", "materialization_ready"}:
            _fail(
                "game_materialization_bundle_state_invalid",
                "state must be contract_only or materialization_ready",
            )
        materialization_ready = document.get("materialization_ready")
        if type(materialization_ready) is not bool:
            _fail(
                "game_materialization_bundle_state_invalid",
                "materialization_ready must be an exact boolean",
            )
        missing_launcher_roles = document.get("missing_launcher_roles")
        expected_state = "materialization_ready" if materialization_ready else "contract_only"
        expected_missing = [] if materialization_ready else list(_REQUIRED_LAUNCHER_ROLES)
        if state != expected_state or missing_launcher_roles != expected_missing:
            _fail(
                "game_materialization_bundle_readiness_overclaim",
                "state, readiness, and missing launcher roles disagree",
            )

        runtime_bundle = _object(
            document.get("runtime_bundle"),
            "game materialization bundle.runtime_bundle",
        )
        _exact_keys(
            runtime_bundle,
            _RUNTIME_BUNDLE_FIELDS,
            "game materialization bundle.runtime_bundle",
        )
        if runtime_bundle.get("root") != _RUNTIME_BUNDLE_ROOT:
            _fail(
                "game_materialization_bundle_runtime_root_invalid",
                "runtime bundle root must be runtime-bundle",
            )
        runtime_identity = _object(
            runtime_bundle.get("manifest"),
            "game materialization bundle.runtime_bundle.manifest",
        )
        _exact_keys(
            runtime_identity,
            _RUNTIME_BUNDLE_MANIFEST_FIELDS,
            "game materialization bundle.runtime_bundle.manifest",
        )
        if (
            runtime_identity.get("path") != f"{_RUNTIME_BUNDLE_ROOT}/{GAME_RUNTIME_BUNDLE_MANIFEST}"
            or runtime_identity.get("format") != GAME_RUNTIME_BUNDLE_FORMAT
            or runtime_identity.get("format_version") != 1
        ):
            _fail(
                "game_materialization_bundle_runtime_identity_invalid",
                "nested runtime bundle identity is not closed",
            )
        if (
            not isinstance(runtime_identity.get("id"), str)
            or re.fullmatch(
                r"game_runtime_bundle_[0-9a-f]{48}",
                runtime_identity["id"],
            )
            is None
        ):
            _fail(
                "game_materialization_bundle_runtime_identity_invalid",
                "nested runtime bundle ID is invalid",
            )
        _sha256(
            runtime_identity.get("content_hash"),
            "game materialization bundle.runtime_bundle.manifest.content_hash",
        )
        _sha256(
            runtime_identity.get("tree_hash"),
            "game materialization bundle.runtime_bundle.manifest.tree_hash",
        )

        implementation_identity = _object(
            document.get("runtime_implementation"),
            "game materialization bundle.runtime_implementation",
        )
        _exact_keys(
            implementation_identity,
            _IMPLEMENTATION_IDENTITY_FIELDS,
            "game materialization bundle.runtime_implementation",
        )
        if (
            implementation_identity.get("path") != _IMPLEMENTATION_PATH
            or implementation_identity.get("format") != RUNTIME_IMPLEMENTATION_FORMAT
            or implementation_identity.get("format_version") != 1
        ):
            _fail(
                "game_materialization_bundle_runtime_implementation_identity_invalid",
                "runtime implementation identity is not closed",
            )
        _identifier(
            implementation_identity.get("id"),
            "game materialization bundle.runtime_implementation.id",
        )
        _sha256(
            implementation_identity.get("content_hash"),
            "game materialization bundle.runtime_implementation.content_hash",
        )

        lock_set = _object(
            document.get("platform_locks"),
            "game materialization bundle.platform_locks",
        )
        _exact_keys(
            lock_set,
            _PLATFORM_LOCK_SET_FIELDS,
            "game materialization bundle.platform_locks",
        )
        if lock_set.get("root") != _PLATFORM_LOCK_ROOT:
            _fail(
                "game_materialization_bundle_platform_lock_root_invalid",
                "platform lock root is not closed",
            )
        _sha256(
            lock_set.get("set_hash"),
            "game materialization bundle.platform_locks.set_hash",
        )
        raw_locks = lock_set.get("locks")
        if not isinstance(raw_locks, list) or len(raw_locks) != 4:
            _fail(
                "game_materialization_bundle_platform_locks_incomplete",
                "exactly four platform lock identities are required",
            )
        lock_ids: set[str] = set()
        locks: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_locks):
            context = f"game materialization bundle.platform_locks.locks/{index}"
            item = _object(raw, context)
            _exact_keys(item, _PLATFORM_LOCK_IDENTITY_FIELDS, context)
            lock_id = _identifier(item.get("id"), f"{context}.id")
            if lock_id in lock_ids:
                _fail(
                    "game_materialization_bundle_platform_locks_incomplete",
                    "platform lock IDs must be unique",
                )
            lock_ids.add(lock_id)
            expected_path = f"{_PLATFORM_LOCK_ROOT}/{lock_id}.json"
            if (
                item.get("path") != expected_path
                or item.get("format") != RUNTIME_PLATFORM_LOCK_FORMAT
                or item.get("format_version") != 1
            ):
                _fail(
                    "game_materialization_bundle_platform_lock_identity_invalid",
                    f"{context} is not closed",
                )
            _sha256(item.get("content_hash"), f"{context}.content_hash")
            if item.get("os") not in {"linux", "windows"}:
                _fail(
                    "game_materialization_bundle_platform_lock_identity_invalid",
                    f"{context}.os is invalid",
                )
            if item.get("python_minor") not in {"3.11", "3.12"}:
                _fail(
                    "game_materialization_bundle_platform_lock_identity_invalid",
                    f"{context}.python_minor is invalid",
                )
            if item.get("abi") not in {"cp311", "cp312"}:
                _fail(
                    "game_materialization_bundle_platform_lock_identity_invalid",
                    f"{context}.abi is invalid",
                )
            locks.append(copy.deepcopy(item))
        if [item["id"] for item in locks] != sorted(
            (item["id"] for item in locks),
            key=_utf8_key,
        ):
            _fail(
                "game_materialization_bundle_platform_lock_order_invalid",
                "platform locks must use UTF-8 ID order",
            )
        if lock_set["set_hash"] != _hash({"locks": locks}):
            _fail(
                "game_materialization_bundle_platform_lock_set_hash_mismatch",
                "platform lock set hash is not canonical",
            )

        launchers = _object(
            document.get("launchers"),
            "game materialization bundle.launchers",
        )
        _exact_keys(
            launchers,
            _LAUNCHER_FIELDS,
            "game materialization bundle.launchers",
        )
        if (
            launchers.get("root") != "launchers"
            or launchers.get("policy_version") != 1
            or launchers.get("required_roles") != list(_REQUIRED_LAUNCHER_ROLES)
        ):
            _fail(
                "game_materialization_bundle_launcher_policy_invalid",
                "launcher policy is not the closed v1 policy",
            )
        raw_inventory = launchers.get("inventory")
        expected_launcher_payloads = _launcher_payloads(ready=materialization_ready)
        if not isinstance(raw_inventory, list) or len(raw_inventory) != len(
            expected_launcher_payloads
        ):
            _fail(
                "game_materialization_bundle_launcher_policy_invalid",
                "launcher inventory is incomplete",
            )
        launcher_records: list[dict[str, Any]] = []
        output_paths: set[str] = set()
        roles: set[str] = set()
        for index, raw in enumerate(raw_inventory):
            context = f"game materialization bundle.launchers.inventory/{index}"
            record = _object(raw, context)
            _exact_keys(record, _LAUNCHER_RECORD_FIELDS, context)
            path = _portable_relative_path(record.get("path"), f"{context}.path")
            output_path = _portable_relative_path(
                record.get("output_path"),
                f"{context}.output_path",
            )
            role = _identifier(record.get("role"), f"{context}.role")
            _sha256(record.get("sha256"), f"{context}.sha256")
            _integer(
                record.get("size_bytes"),
                f"{context}.size_bytes",
                minimum=1,
            )
            expected = expected_launcher_payloads.get(path)
            if expected is None or (role, output_path) != (expected[1], expected[2]):
                _fail(
                    "game_materialization_bundle_launcher_policy_invalid",
                    f"{context} is not code-owned",
                )
            output_key = output_path.casefold()
            if output_key in output_paths:
                _fail(
                    "game_materialization_bundle_path_collision",
                    "launcher output paths collide by NFC/casefold",
                )
            output_paths.add(output_key)
            roles.add(role)
            launcher_records.append(copy.deepcopy(record))
        if launcher_records != sorted(
            launcher_records,
            key=lambda item: _utf8_key(item["path"]),
        ):
            _fail(
                "game_materialization_bundle_file_order_invalid",
                "launcher inventory must use UTF-8 path order",
            )
        expected_present_roles = {role for role in _REQUIRED_LAUNCHER_ROLES if role in roles}
        if expected_present_roles != (
            set(_REQUIRED_LAUNCHER_ROLES) if materialization_ready else set()
        ):
            _fail(
                "game_materialization_bundle_readiness_overclaim",
                "required launcher role closure disagrees with readiness",
            )
        launcher_files = [
            {
                "path": record["path"],
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
            }
            for record in launcher_records
        ]
        _sha256(
            launchers.get("tree_hash"),
            "game materialization bundle.launchers.tree_hash",
        )
        if launchers["tree_hash"] != _hash({"files": launcher_files}):
            _fail(
                "game_materialization_bundle_launcher_tree_hash_mismatch",
                "launcher tree hash is not canonical",
            )

        lineage = _object(
            document.get("lineage"),
            "game materialization bundle.lineage",
        )
        _exact_keys(
            lineage,
            _LINEAGE_FIELDS,
            "game materialization bundle.lineage",
        )
        for field in _LINEAGE_FIELDS:
            _sha256(lineage.get(field), f"game materialization bundle.lineage.{field}")
        if (
            lineage["runtime_bundle_hash"] != runtime_identity["content_hash"]
            or lineage["runtime_bundle_tree_hash"] != runtime_identity["tree_hash"]
            or lineage["runtime_implementation_hash"] != implementation_identity["content_hash"]
            or lineage["platform_lock_set_hash"] != lock_set["set_hash"]
        ):
            _fail(
                "game_materialization_bundle_lineage_mismatch",
                "outer lineage does not bind its immediate immutable subjects",
            )

        legal = _object(
            document.get("legal"),
            "game materialization bundle.legal",
        )
        _exact_keys(legal, _LEGAL_FIELDS, "game materialization bundle.legal")
        license_record = _object(
            legal.get("bundle_license"),
            "game materialization bundle.legal.bundle_license",
        )
        _exact_keys(
            license_record,
            _FILE_FIELDS,
            "game materialization bundle.legal.bundle_license",
        )
        if license_record != {
            "path": _LICENSE_PATH,
            "sha256": _LICENSE_SHA256,
            "size_bytes": _LICENSE_SIZE,
        }:
            _fail(
                "game_materialization_bundle_license_mismatch",
                "bundle license is not the exact World Forge MIT license",
            )

        files = _validate_file_records(
            document.get("files"),
            "game materialization bundle.files",
        )
        files_by_path = {item["path"]: item for item in files}
        for launcher_file in launcher_files:
            if files_by_path.get(launcher_file["path"]) != launcher_file:
                _fail(
                    "game_materialization_bundle_launcher_inventory_mismatch",
                    "launcher identity does not match the canonical file inventory",
                )
        if files_by_path.get(_LICENSE_PATH) != license_record:
            _fail(
                "game_materialization_bundle_license_inventory_mismatch",
                "bundle license identity does not match the canonical file inventory",
            )
        paths = {item["path"] for item in files}
        required_paths = {
            f"{_RUNTIME_BUNDLE_ROOT}/{GAME_RUNTIME_BUNDLE_MANIFEST}",
            _IMPLEMENTATION_PATH,
            *(item["path"] for item in locks),
            _LAUNCHER_POLICY_PATH,
            _LICENSE_PATH,
        }
        if not required_paths.issubset(paths):
            _fail(
                "game_materialization_bundle_file_closure_invalid",
                "required materialization contract files are absent",
            )
        allowed_exact = {
            _IMPLEMENTATION_PATH,
            *(item["path"] for item in locks),
            _LICENSE_PATH,
        }
        if any(
            path not in allowed_exact
            and not path.startswith(f"{_RUNTIME_BUNDLE_ROOT}/")
            and not path.startswith("launchers/")
            for path in paths
        ):
            _fail(
                "game_materialization_bundle_file_closure_invalid",
                "outer envelope contains an unrecognized file class",
            )
        if document.get("tree_hash") != _hash({"files": files}):
            _fail(
                "game_materialization_bundle_tree_hash_mismatch",
                "tree_hash is not derived from the canonical file inventory",
            )
        _sha256(
            document.get("content_hash"),
            "game materialization bundle.content_hash",
        )
        seed = {
            key: item
            for key, item in document.items()
            if key not in {"materialization_bundle_id", "content_hash"}
        }
        expected_id = "game_materialization_bundle_" + _hash(seed)[:36]
        if bundle_id != expected_id:
            _fail(
                "game_materialization_bundle_id_mismatch",
                "materialization_bundle_id is not canonical",
            )
        if document["content_hash"] != _hash(document):
            _fail(
                "game_materialization_bundle_content_hash_mismatch",
                "content_hash is not canonical",
            )
        return copy.deepcopy(document)
    except GameMaterializationBundleError:
        raise
    except CreationContractError as exc:
        _fail("game_materialization_bundle_invalid", str(exc))


def serialize_game_materialization_bundle(value: object) -> bytes:
    return canonical_json_bytes(validate_game_materialization_bundle_document(value))


class VerifiedGameMaterializationBundle:
    """Retained immutable bytes from one integral envelope verification."""

    __slots__ = (
        "_closed",
        "_evidence",
        "_files",
        "_manifest",
        "root",
        "root_identity",
    )

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
        ready = manifest["materialization_ready"] is True
        self._evidence = MappingProxyType(
            {
                "integrity": "valid",
                "state": manifest["state"],
                "materialization_ready": ready,
                "release": "blocked",
                "supported": False,
                "bundle_id": manifest["materialization_bundle_id"],
                "content_hash": manifest["content_hash"],
            }
        )
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            _fail(
                "game_materialization_bundle_snapshot_closed",
                "verified materialization bundle snapshot is closed",
            )

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
            _fail(
                "game_materialization_bundle_file_missing",
                f"verified envelope has no file {relative!r}",
            )

    def close(self) -> None:
        self._files.clear()
        self._closed = True

    def __enter__(self) -> VerifiedGameMaterializationBundle:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self.close()


def _decode_canonical(
    files: Mapping[str, bytes],
    path: str,
    validator: Any,
    serializer: Any,
) -> dict[str, Any]:
    try:
        payload = files[path]
    except KeyError:
        _fail(
            "game_materialization_bundle_file_missing",
            f"envelope is missing {path}",
        )
    try:
        document = validator(_decode_creation_object(payload, path))
        if serializer(document) != payload:
            _fail(
                "game_materialization_bundle_noncanonical_json",
                f"{path} is not canonical JSON",
            )
        return document
    except GameMaterializationBundleError:
        raise
    except (
        CreationContractError,
        RuntimeImplementationError,
        RuntimePlatformLockError,
    ) as exc:
        _fail("game_materialization_bundle_contract_invalid", f"{path}: {exc}")


def _materialization_stage_read_capability(
    writer: object,
    *,
    expected_stage: Path,
    capability_root: Path,
) -> _RuntimeStageReadCapability:
    def require_stage_binding() -> None:
        try:
            RetainedStageWriter._require_active_binding(  # noqa: SLF001
                writer,
                expected_stage=expected_stage,
            )
        except DirectoryPublishError as exc:
            _fail("game_materialization_bundle_stage_capability_invalid", str(exc))

    require_stage_binding()
    return _create_runtime_stage_read_capability(
        root=capability_root,
        require_binding=require_stage_binding,
    )


def _verify_nested_runtime_bundle_from_retained_materialization_stage(
    nested_root: str | Path,
    *,
    expected_content_hash: str,
    expected_outer_stage: str | Path,
    _retained_stage_writer: RetainedStageWriter,
) -> object:
    outer_stage = Path(os.path.abspath(os.fspath(expected_outer_stage)))
    nested_root_path = Path(os.path.abspath(os.fspath(nested_root)))
    if nested_root_path != outer_stage / _RUNTIME_BUNDLE_ROOT:
        _fail(
            "game_materialization_bundle_stage_capability_invalid",
            "nested runtime bundle root does not bind the materialization stage",
        )
    stage_capability = _materialization_stage_read_capability(
        _retained_stage_writer,
        expected_stage=outer_stage,
        capability_root=nested_root_path,
    )
    try:
        nested = _verify_game_runtime_bundle_with_stage_capability(
            nested_root_path,
            expected_content_hash=expected_content_hash,
            _stage_capability=stage_capability,
        )
    finally:
        stage_capability.require_binding()
    stage_capability.require_binding()
    return nested


def verify_game_materialization_bundle(
    root: str | Path,
    *,
    expected_content_hash: str | None = None,
    expected_parent_identity: DirectoryIdentity | None = None,
    _retained_stage_writer: RetainedStageWriter | None = None,
) -> VerifiedGameMaterializationBundle:
    root_path = Path(os.path.abspath(os.fspath(root)))
    _require_expected_parent_identity(root_path.parent, expected_parent_identity)
    try:
        stage_capability = None
        if _retained_stage_writer is not None:
            stage_capability = _materialization_stage_read_capability(
                _retained_stage_writer,
                expected_stage=root_path,
                capability_root=root_path,
            )
        files, tree = _capture_bundle_tree(
            root_path,
            hook=None,
            stage_capability=stage_capability,
        )
        if stage_capability is not None:
            stage_capability.require_binding()
    except GameRuntimeBundleError as exc:
        _fail("game_materialization_bundle_tree_invalid", str(exc))
    manifest = _decode_canonical(
        files,
        GAME_MATERIALIZATION_BUNDLE_MANIFEST,
        validate_game_materialization_bundle_document,
        serialize_game_materialization_bundle,
    )
    if expected_content_hash is not None:
        try:
            checked_expected = _sha256(
                expected_content_hash,
                "expected materialization bundle hash",
            )
        except CreationContractError as exc:
            _fail("game_materialization_bundle_expected_hash_invalid", str(exc))
        if manifest["content_hash"] != checked_expected:
            _fail(
                "game_materialization_bundle_expected_hash_mismatch",
                "envelope does not match the expected immutable hash",
            )
    expected_files = {
        GAME_MATERIALIZATION_BUNDLE_MANIFEST,
        *(item["path"] for item in manifest["files"]),
    }
    if set(files) != expected_files:
        _fail(
            "game_materialization_bundle_file_closure_invalid",
            "envelope has missing or additional files",
        )
    if set(tree.directories) != _expected_directories(tuple(expected_files)):
        _fail(
            "game_materialization_bundle_directory_closure_invalid",
            "envelope has missing or additional directories",
        )
    records = _file_inventory(
        {
            path: payload
            for path, payload in files.items()
            if path != GAME_MATERIALIZATION_BUNDLE_MANIFEST
        }
    )
    if records != manifest["files"]:
        _fail(
            "game_materialization_bundle_file_hash_mismatch",
            "envelope physical bytes do not match its inventory",
        )

    nested_root = root_path / _RUNTIME_BUNDLE_ROOT
    try:
        if _retained_stage_writer is not None:
            nested = _verify_nested_runtime_bundle_from_retained_materialization_stage(
                nested_root,
                expected_content_hash=manifest["runtime_bundle"]["manifest"]["content_hash"],
                expected_outer_stage=root_path,
                _retained_stage_writer=_retained_stage_writer,
            )
        else:
            nested = verify_game_runtime_bundle(
                nested_root,
                expected_content_hash=manifest["runtime_bundle"]["manifest"]["content_hash"],
            )
    except GameRuntimeBundleError as exc:
        _fail("game_materialization_bundle_nested_runtime_invalid", str(exc))
    try:
        runtime_manifest = nested.manifest
        runtime_files = nested.files
        if manifest["runtime_bundle"] != _runtime_bundle_identity(runtime_manifest):
            _fail(
                "game_materialization_bundle_nested_runtime_identity_mismatch",
                "nested runtime bundle identity does not match the outer envelope",
            )
        lock_documents = [
            _decode_canonical(
                files,
                item["path"],
                validate_runtime_platform_lock_document,
                serialize_runtime_platform_lock,
            )
            for item in manifest["platform_locks"]["locks"]
        ]
        if manifest["platform_locks"]["locks"] != _lock_identities(lock_documents):
            _fail(
                "game_materialization_bundle_platform_lock_mismatch",
                "platform lock documents do not match their outer identities",
            )
        implementation = _decode_canonical(
            files,
            _IMPLEMENTATION_PATH,
            validate_runtime_implementation_document,
            serialize_runtime_implementation,
        )
        implementation = _validated_implementation(
            implementation,
            runtime_manifest=runtime_manifest,
            runtime_files=runtime_files,
            locks=lock_documents,
        )
        if manifest["runtime_implementation"] != _implementation_identity(implementation):
            _fail(
                "game_materialization_bundle_runtime_implementation_identity_mismatch",
                "runtime implementation document does not match its outer identity",
            )
        lock_set_hash = _hash({"locks": _lock_identities(lock_documents)})
        expected_lineage = _lineage(
            runtime_manifest,
            implementation,
            lock_set_hash,
        )
        if manifest["lineage"] != expected_lineage:
            _fail(
                "game_materialization_bundle_lineage_mismatch",
                "outer lineage is crossed or incomplete",
            )
    finally:
        nested.close()

    for path, (payload, _role, _output_path) in _launcher_payloads(
        ready=manifest["materialization_ready"],
    ).items():
        if files.get(path) != payload:
            _fail(
                "game_materialization_bundle_launcher_policy_mismatch",
                f"launcher/template bytes are not code-owned: {path}",
            )
    if (
        hashlib.sha256(files[_LICENSE_PATH]).hexdigest() != _LICENSE_SHA256
        or len(files[_LICENSE_PATH]) != _LICENSE_SIZE
    ):
        _fail(
            "game_materialization_bundle_license_mismatch",
            "outer license bytes are not the exact World Forge MIT license",
        )
    _require_expected_parent_identity(root_path.parent, expected_parent_identity)
    return VerifiedGameMaterializationBundle(
        root_path,
        manifest,
        files,
        tree.root_state[:2],
    )


def _prepare_game_materialization_bundle(
    *,
    runtime_bundle_root: str | Path,
    runtime_implementation: object | None,
    platform_locks: Sequence[object] | None,
    include_standalone_launchers: bool,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    runtime_bundle_path = Path(os.path.abspath(os.fspath(runtime_bundle_root)))
    try:
        nested = verify_game_runtime_bundle(runtime_bundle_path)
    except GameRuntimeBundleError as exc:
        _fail("game_materialization_bundle_nested_runtime_invalid", str(exc))
    try:
        runtime_manifest = nested.manifest
        runtime_files = nested.files
        if platform_locks is None:
            lock_inputs: Sequence[object] = build_builtin_runtime_platform_locks()
        else:
            lock_inputs = platform_locks
        try:
            locks = tuple(validate_runtime_platform_lock_document(item) for item in lock_inputs)
        except RuntimePlatformLockError as exc:
            _fail("game_materialization_bundle_platform_lock_invalid", str(exc))
        if len(locks) != 4:
            _fail(
                "game_materialization_bundle_platform_locks_incomplete",
                "exactly four audited platform locks are required",
            )
        if runtime_implementation is None:
            snapshot, adapter = _nested_runtime_documents(runtime_manifest, runtime_files)
            try:
                implementation_input = build_runtime_implementation(
                    adapter=adapter,
                    snapshot=snapshot,
                    platform_locks=locks,
                )
            except RuntimeImplementationError as exc:
                _fail(
                    "game_materialization_bundle_runtime_implementation_invalid",
                    str(exc),
                )
        else:
            implementation_input = runtime_implementation
        implementation = _validated_implementation(
            implementation_input,
            runtime_manifest=runtime_manifest,
            runtime_files=runtime_files,
            locks=locks,
        )
        payload_files = {
            f"{_RUNTIME_BUNDLE_ROOT}/{path}": payload for path, payload in runtime_files.items()
        }
        payload_files[_IMPLEMENTATION_PATH] = serialize_runtime_implementation(implementation)
        for lock in locks:
            payload_files[f"{_PLATFORM_LOCK_ROOT}/{lock['lock_id']}.json"] = (
                serialize_runtime_platform_lock(lock)
            )
        if type(include_standalone_launchers) is not bool:
            _fail(
                "game_materialization_bundle_invalid",
                "include_standalone_launchers must be an exact boolean",
            )
        for path, (payload, _role, _output_path) in _launcher_payloads(
            ready=include_standalone_launchers,
        ).items():
            payload_files[path] = payload
        payload_files[_LICENSE_PATH] = runtime_files[_LICENSE_PATH]
        manifest = _build_manifest(
            runtime_manifest=runtime_manifest,
            implementation=implementation,
            locks=locks,
            payload_files=payload_files,
            materialization_ready=include_standalone_launchers,
        )
        return manifest, payload_files
    finally:
        nested.close()


def build_game_materialization_bundle_manifest(
    *,
    runtime_bundle_root: str | Path,
    runtime_implementation: object | None = None,
    platform_locks: Sequence[object] | None = None,
    include_standalone_launchers: bool = True,
) -> tuple[dict[str, Any], Mapping[str, bytes]]:
    """Build deterministic materialization manifest and immutable payload bytes."""

    manifest, payloads = _prepare_game_materialization_bundle(
        runtime_bundle_root=runtime_bundle_root,
        runtime_implementation=runtime_implementation,
        platform_locks=platform_locks,
        include_standalone_launchers=include_standalone_launchers,
    )
    return copy.deepcopy(manifest), MappingProxyType(dict(payloads))


def _tree_state(info: FileStat) -> _TreeState:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_mode),
        int(info.st_nlink),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def _journal_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.game-materialization-bundle.journal.json"


def _lock_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.game-materialization-bundle.lock"


@dataclass(frozen=True, slots=True)
class _DestinationLock:
    path: Path
    descriptor: int
    identity: DirectoryIdentity
    state: _TreeState

    def require_binding(self) -> None:
        opened = descriptor_file_stat(self.descriptor)
        named = path_file_stat(self.path)
        if (
            is_link_or_reparse(opened)
            or is_link_or_reparse(named)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or file_identity(opened) != self.identity
            or file_identity(named) != self.identity
            or _tree_state(opened) != self.state
            or _tree_state(named) != self.state
        ):
            _fail(
                "game_materialization_bundle_lock_changed",
                "materialization bundle publication lock binding changed",
            )


@contextmanager
def _destination_lock(destination: Path) -> Iterator[_DestinationLock]:
    path = _lock_path(destination)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        opened = descriptor_file_stat(descriptor)
        named = path_file_stat(path)
        if (
            is_link_or_reparse(opened)
            or is_link_or_reparse(named)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or file_identity(opened) != file_identity(named)
        ):
            _fail(
                "game_materialization_bundle_lock_unsafe",
                "materialization bundle publication lock is unsafe",
            )
        if opened.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
            fsync_directory(path.parent, context="materialization bundle lock parent")
        elif opened.st_size != 1:
            _fail(
                "game_materialization_bundle_lock_unsafe",
                "materialization bundle publication lock contents are invalid",
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, 1) != b"\0":
            _fail(
                "game_materialization_bundle_lock_unsafe",
                "materialization bundle publication lock contents are invalid",
            )
        try:
            if os.name == "nt":  # pragma: no cover - native Windows CI
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            _fail(
                "game_materialization_bundle_publication_busy",
                f"another materialization bundle publication is in progress: {exc}",
            )
        retained = descriptor_file_stat(descriptor)
        guard = _DestinationLock(
            path=path,
            descriptor=descriptor,
            identity=file_identity(retained),
            state=_tree_state(retained),
        )
        guard.require_binding()
        yield guard
        guard.require_binding()
    except GameMaterializationBundleError:
        raise
    except (DirectoryPublishError, OSError) as exc:
        _fail("game_materialization_bundle_lock_failed", str(exc))
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _identity_document(identity: DirectoryIdentity) -> dict[str, int | str]:
    try:
        return encode_publication_identity(identity, windows=os.name == "nt")
    except PublicationIdentityCodecError as exc:
        _fail("game_materialization_bundle_journal_invalid", str(exc))


def _identity_from_document(value: object, *, context: str) -> DirectoryIdentity:
    try:
        return decode_publication_identity(value, context=context)
    except PublicationIdentityCodecError as exc:
        _fail("game_materialization_bundle_journal_invalid", str(exc))


def _checked_parent_identity(
    value: DirectoryIdentity | None,
) -> DirectoryIdentity | None:
    if value is None:
        return None
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value)
    ):
        _fail(
            "game_materialization_bundle_parent_identity_invalid",
            "expected materialization bundle parent identity is invalid",
        )
    return value


def _require_expected_parent_identity(
    parent: Path,
    expected: DirectoryIdentity | None,
) -> DirectoryIdentity:
    checked = _checked_parent_identity(expected)
    try:
        info = path_file_stat(parent)
    except OSError as exc:
        _fail(
            "game_materialization_bundle_parent_identity_mismatch",
            f"materialization bundle parent authority is unavailable: {exc}",
        )
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        _fail(
            "game_materialization_bundle_parent_identity_mismatch",
            "materialization bundle parent is not a real directory",
        )
    identity = file_identity(info)
    if checked is not None and identity != checked:
        _fail(
            "game_materialization_bundle_parent_identity_mismatch",
            "materialization bundle parent identity differs from retained authority",
        )
    return identity


def _optional_directory_identity(path: Path) -> DirectoryIdentity | None:
    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("game_materialization_bundle_directory_invalid", str(exc))
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        _fail(
            "game_materialization_bundle_directory_invalid",
            f"{path} must be a real directory",
        )
    return file_identity(info)


def _validate_destination(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None,
) -> tuple[Path, DirectoryIdentity]:
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    if (
        not destination_path.name
        or destination_path.name.startswith(".")
        or len(destination_path.name.encode("utf-8")) > 160
        or any(character in destination_path.name for character in "/\\\0")
    ):
        _fail(
            "game_materialization_bundle_destination_invalid",
            "destination name is not portable",
        )
    if not destination_path.parent.exists():
        _fail(
            "game_materialization_bundle_parent_missing",
            "destination parent must already exist",
        )
    lexical_issues = validate_lexical_directory_root(destination_path.parent)
    if lexical_issues:
        _fail(
            "game_materialization_bundle_destination_invalid",
            f"destination parent is unsafe: {', '.join(lexical_issues)}",
        )
    parent_identity = _require_expected_parent_identity(
        destination_path.parent,
        expected_parent_identity,
    )
    return destination_path, parent_identity


def _journal_document(
    *,
    operation_id: str,
    state: str,
    stage: Path,
    destination: Path,
    parent_identity: DirectoryIdentity,
    stage_identity: DirectoryIdentity | None,
    manifest: Mapping[str, Any],
    manifest_payload: bytes,
) -> dict[str, object]:
    return {
        "format": GAME_MATERIALIZATION_BUNDLE_JOURNAL_FORMAT,
        "format_version": GAME_MATERIALIZATION_BUNDLE_JOURNAL_VERSION,
        "operation_id": operation_id,
        "state": state,
        "stage_name": stage.name,
        "destination_name": destination.name,
        "parent_identity": _identity_document(parent_identity),
        "stage_identity": None if stage_identity is None else _identity_document(stage_identity),
        "materialization_bundle_id": manifest["materialization_bundle_id"],
        "content_hash": manifest["content_hash"],
        "tree_hash": manifest["tree_hash"],
        "runtime_bundle_hash": manifest["lineage"]["runtime_bundle_hash"],
        "runtime_implementation_hash": manifest["lineage"]["runtime_implementation_hash"],
        "platform_lock_set_hash": manifest["lineage"]["platform_lock_set_hash"],
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "manifest_size_bytes": len(manifest_payload),
    }


def _validate_journal(value: object, destination: Path) -> dict[str, Any]:
    try:
        journal = _object(value, "materialization bundle publication journal")
        _exact_keys(journal, _JOURNAL_FIELDS, "materialization bundle publication journal")
        if (
            journal.get("format") != GAME_MATERIALIZATION_BUNDLE_JOURNAL_FORMAT
            or journal.get("format_version") != GAME_MATERIALIZATION_BUNDLE_JOURNAL_VERSION
        ):
            _fail("game_materialization_bundle_journal_invalid", "unknown journal format")
        operation_id = journal.get("operation_id")
        if not isinstance(operation_id, str) or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
            _fail("game_materialization_bundle_journal_invalid", "operation_id is invalid")
        state = journal.get("state")
        if state not in {"intent", "copying", "ready"}:
            _fail("game_materialization_bundle_journal_invalid", "journal state is invalid")
        if journal.get("destination_name") != destination.name:
            _fail(
                "game_materialization_bundle_journal_invalid",
                "journal destination name is invalid",
            )
        stage_name = journal.get("stage_name")
        if (
            not isinstance(stage_name, str)
            or not stage_name.startswith(f".{destination.name}.game-materialization-bundle-")
            or "/" in stage_name
            or "\\" in stage_name
        ):
            _fail("game_materialization_bundle_journal_invalid", "journal stage name is invalid")
        _identity_from_document(journal.get("parent_identity"), context="journal.parent_identity")
        if state == "intent":
            if journal.get("stage_identity") is not None:
                _fail(
                    "game_materialization_bundle_journal_invalid",
                    "intent journal cannot claim a stage identity",
                )
        else:
            _identity_from_document(journal.get("stage_identity"), context="journal.stage_identity")
        bundle_id = journal.get("materialization_bundle_id")
        if (
            not isinstance(bundle_id, str)
            or re.fullmatch(r"game_materialization_bundle_[0-9a-f]{36}", bundle_id) is None
        ):
            _fail(
                "game_materialization_bundle_journal_invalid",
                "journal materialization_bundle_id is invalid",
            )
        for field in (
            "content_hash",
            "tree_hash",
            "runtime_bundle_hash",
            "runtime_implementation_hash",
            "platform_lock_set_hash",
            "manifest_sha256",
        ):
            _sha256(journal.get(field), f"journal.{field}")
        size = _integer(
            journal.get("manifest_size_bytes"),
            "journal.manifest_size_bytes",
            minimum=1,
        )
        if size > MAX_GAME_MATERIALIZATION_BUNDLE_MANIFEST_BYTES:
            _fail(
                "game_materialization_bundle_journal_invalid",
                "journal manifest size exceeds its limit",
            )
        return journal
    except GameMaterializationBundleError:
        raise
    except CreationContractError as exc:
        _fail("game_materialization_bundle_journal_invalid", str(exc))


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
    return tuple(canonical_json_bytes(item) for item in _expected_journal_history(document))


def _read_journal_state(
    path: Path,
    destination: Path,
) -> tuple[dict[str, Any], DirectoryIdentity, bytes, bool] | None:
    try:
        loaded = read_append_only_journal_history_state(
            path,
            max_record_bytes=MAX_GAME_MATERIALIZATION_BUNDLE_MANIFEST_BYTES,
            max_file_bytes=MAX_GAME_MATERIALIZATION_BUNDLE_JOURNAL_BYTES,
        )
    except DirectoryPublishError as exc:
        _fail("game_materialization_bundle_journal_invalid", str(exc))
    if loaded is None:
        return None
    payloads, identity, partial_tail = loaded
    documents: list[dict[str, Any]] = []
    for payload in payloads:
        try:
            document = _validate_journal(
                _decode_creation_object(payload, path),
                destination,
            )
        except CreationContractError as exc:
            _fail("game_materialization_bundle_journal_invalid", str(exc))
        if canonical_json_bytes(document) != payload:
            _fail(
                "game_materialization_bundle_journal_invalid",
                "journal record is not canonical",
            )
        documents.append(document)
    if tuple(documents) != _expected_journal_history(documents[-1]):
        _fail(
            "game_materialization_bundle_journal_invalid",
            "journal history is not the exact state prefix",
        )
    return documents[-1], identity, payloads[-1], partial_tail


def _write_journal(
    path: Path,
    document: dict[str, Any],
    *,
    lock: _DestinationLock,
    create: bool,
    expected_document: dict[str, Any] | None = None,
    expected_identity: DirectoryIdentity | None = None,
) -> DirectoryIdentity:
    payload = canonical_json_bytes(document)
    try:
        lock.require_binding()
        if create:
            try:
                identity = create_append_only_journal(
                    path,
                    payload,
                    max_record_bytes=MAX_GAME_MATERIALIZATION_BUNDLE_MANIFEST_BYTES,
                )
            except FileExistsError:
                _fail(
                    "game_materialization_bundle_recovery_required",
                    "an incomplete materialization publication journal exists",
                    recovery_evidence=retained_recovery_evidence(journal_path=path),
                )
            fsync_directory(path.parent, context="materialization bundle journal parent")
            lock.require_binding()
            return identity
        if expected_document is None or expected_identity is None:
            _fail(
                "game_materialization_bundle_journal_invalid",
                "journal transition lacks its exact prior identity",
            )
        loaded = _read_journal_state(path, path.parent / str(document["destination_name"]))
        expected_payload = canonical_json_bytes(expected_document)
        if (
            loaded is None
            or loaded[0] != expected_document
            or loaded[1] != expected_identity
            or loaded[2] != expected_payload
        ):
            _fail(
                "game_materialization_bundle_journal_changed",
                "journal changed before its append-only transition",
            )
        identity = append_append_only_journal(
            path,
            expected_identity=expected_identity,
            expected_payload=expected_payload,
            expected_history=_history_payloads(expected_document),
            updated_payload=payload,
            max_record_bytes=MAX_GAME_MATERIALIZATION_BUNDLE_MANIFEST_BYTES,
            max_file_bytes=MAX_GAME_MATERIALIZATION_BUNDLE_JOURNAL_BYTES,
            repair_partial_tail=True,
        )
        lock.require_binding()
        return identity
    except GameMaterializationBundleError:
        raise
    except DirectoryPublishError as exc:
        _fail("game_materialization_bundle_journal_failed", str(exc))


def _remove_journal(
    path: Path,
    document: dict[str, Any],
    identity: DirectoryIdentity,
    *,
    lock: _DestinationLock,
) -> None:
    try:
        lock.require_binding()
        retained = remove_append_only_journal(
            path,
            expected_identity=identity,
            expected_payload=canonical_json_bytes(document),
            expected_history=_history_payloads(document),
            max_record_bytes=MAX_GAME_MATERIALIZATION_BUNDLE_MANIFEST_BYTES,
            max_file_bytes=MAX_GAME_MATERIALIZATION_BUNDLE_JOURNAL_BYTES,
        )
        if sys.platform.startswith("linux") and os.name == "posix":
            if retained != retained_journal_evidence_path(path, identity):
                _fail(
                    "game_materialization_bundle_journal_indeterminate",
                    "terminal journal evidence locator changed",
                )
        elif retained is not None:
            _fail(
                "game_materialization_bundle_journal_indeterminate",
                "unexpected terminal journal evidence was returned",
            )
        lock.require_binding()
    except DirectoryPublishIndeterminateError as exc:
        _fail("game_materialization_bundle_journal_indeterminate", str(exc))
    except DirectoryPublishError as exc:
        _fail("game_materialization_bundle_journal_failed", str(exc))


def _journal_matches_verified(
    journal: Mapping[str, Any],
    verified: VerifiedGameMaterializationBundle,
) -> None:
    expected = _journal_document(
        operation_id=str(journal["operation_id"]),
        state=str(journal["state"]),
        stage=Path(str(journal["stage_name"])),
        destination=Path(str(journal["destination_name"])),
        parent_identity=_identity_from_document(
            journal["parent_identity"],
            context="journal.parent_identity",
        ),
        stage_identity=(
            None
            if journal["stage_identity"] is None
            else _identity_from_document(
                journal["stage_identity"],
                context="journal.stage_identity",
            )
        ),
        manifest=verified.manifest,
        manifest_payload=verified.read_bytes(GAME_MATERIALIZATION_BUNDLE_MANIFEST),
    )
    if dict(journal) != expected:
        _fail(
            "game_materialization_bundle_recovery_mismatch",
            "journal differs from the exact materialization bundle",
        )


def _verify_owned_stage_subset(
    stage: Path,
    journal: Mapping[str, Any],
    *,
    retained_root_fd: int | None = None,
) -> None:
    try:
        files, tree = _capture_bundle_tree(
            stage,
            hook=None,
            retained_root_fd=retained_root_fd,
        )
    except GameRuntimeBundleError as exc:
        _fail("game_materialization_bundle_rollback_ambiguous", str(exc))
    expected_identity = _identity_from_document(
        journal["stage_identity"],
        context="journal.stage_identity",
    )
    if tree.root_state[:2] != expected_identity:
        _fail(
            "game_materialization_bundle_rollback_ambiguous",
            "retained partial stage identity changed",
        )
    if not files and not tree.directories:
        return
    payload = files.get(GAME_MATERIALIZATION_BUNDLE_MANIFEST)
    if payload is None:
        _fail(
            "game_materialization_bundle_rollback_ambiguous",
            "partial stage contains unbound entries without its manifest",
        )
    try:
        manifest = validate_game_materialization_bundle_document(
            _decode_creation_object(payload, GAME_MATERIALIZATION_BUNDLE_MANIFEST)
        )
    except (CreationContractError, GameMaterializationBundleError) as exc:
        _fail("game_materialization_bundle_rollback_ambiguous", str(exc))
    if (
        hashlib.sha256(payload).hexdigest() != journal["manifest_sha256"]
        or len(payload) != journal["manifest_size_bytes"]
        or manifest["content_hash"] != journal["content_hash"]
        or manifest["tree_hash"] != journal["tree_hash"]
    ):
        _fail(
            "game_materialization_bundle_rollback_ambiguous",
            "partial stage manifest differs from its journal",
        )
    expected_records = {item["path"]: item for item in manifest["files"]}
    expected_records[GAME_MATERIALIZATION_BUNDLE_MANIFEST] = _file_record(
        GAME_MATERIALIZATION_BUNDLE_MANIFEST,
        payload,
    )
    for relative, actual in files.items():
        expected = expected_records.get(relative)
        if (
            expected is None
            or hashlib.sha256(actual).hexdigest() != expected["sha256"]
            or len(actual) != expected["size_bytes"]
        ):
            _fail(
                "game_materialization_bundle_rollback_ambiguous",
                f"partial stage contains foreign or changed bytes at {relative}",
            )
    expected_directories = _expected_directories(tuple(expected_records))
    if not set(tree.directories).issubset(expected_directories):
        _fail(
            "game_materialization_bundle_rollback_ambiguous",
            "partial stage contains foreign directories",
        )


def _recovery_required(
    code: str,
    detail: str,
    *,
    stage: Path,
    stage_identity: DirectoryIdentity | None,
    journal_path: Path,
    journal_identity: DirectoryIdentity,
) -> None:
    _fail(
        code,
        detail,
        recovery_evidence=retained_recovery_evidence(
            stage_path=stage,
            stage_identity=stage_identity,
            journal_path=journal_path,
            journal_identity=journal_identity,
        ),
    )


def _recover_locked(
    destination: Path,
    lock: _DestinationLock,
    *,
    expected_parent_identity: DirectoryIdentity | None,
) -> VerifiedGameMaterializationBundle | None:
    journal_path = _journal_path(destination)
    loaded = _read_journal_state(journal_path, destination)
    if loaded is None:
        if _optional_directory_identity(destination) is None:
            return None
        return verify_game_materialization_bundle(
            destination,
            expected_parent_identity=expected_parent_identity,
        )
    journal, journal_identity, _payload, partial_tail = loaded
    journal_parent = _identity_from_document(
        journal["parent_identity"],
        context="journal.parent_identity",
    )
    current_parent = _require_expected_parent_identity(destination.parent, journal_parent)
    if expected_parent_identity is not None and current_parent != expected_parent_identity:
        _fail(
            "game_materialization_bundle_recovery_ambiguous",
            "journal parent differs from caller authority",
        )
    lock.require_binding()
    stage = destination.parent / str(journal["stage_name"])
    stage_identity = _optional_directory_identity(stage)
    destination_identity = _optional_directory_identity(destination)
    if journal["state"] == "intent":
        if stage_identity is not None or destination_identity is not None:
            _fail(
                "game_materialization_bundle_recovery_ambiguous",
                "intent journal has an unbound tree",
            )
        _remove_journal(journal_path, journal, journal_identity, lock=lock)
        return None
    expected_identity = _identity_from_document(
        journal["stage_identity"],
        context="journal.stage_identity",
    )
    if stage_identity == expected_identity and destination_identity is None:
        source = stage
    elif destination_identity == expected_identity and stage_identity is None:
        source = destination
    else:
        _fail(
            "game_materialization_bundle_recovery_ambiguous",
            "stage/destination identity is missing, changed, or conflicting",
        )
    if partial_tail and journal["state"] != "copying":
        _fail(
            "game_materialization_bundle_journal_invalid",
            "journal has a torn non-recoverable transition",
        )
    try:
        verified = verify_game_materialization_bundle(
            source,
            expected_content_hash=str(journal["content_hash"]),
            expected_parent_identity=journal_parent,
        )
    except GameMaterializationBundleError as exc:
        if source == stage and journal["state"] == "copying":
            try:
                _verify_owned_stage_subset(stage, journal)
            except GameMaterializationBundleError:
                pass
            else:
                _recovery_required(
                    "game_materialization_bundle_recovery_required",
                    "the exact incomplete materialization stage and journal were retained "
                    "for explicit recovery",
                    stage=stage,
                    stage_identity=expected_identity,
                    journal_path=journal_path,
                    journal_identity=journal_identity,
                )
        _fail("game_materialization_bundle_recovery_ambiguous", str(exc))
    try:
        _journal_matches_verified(journal, verified)
    finally:
        verified.close()
    if journal["state"] == "copying":
        ready = {**journal, "state": "ready"}
        journal_identity = _write_journal(
            journal_path,
            ready,
            lock=lock,
            create=False,
            expected_document=journal,
            expected_identity=journal_identity,
        )
        journal = ready
    if source == stage:
        try:
            with publish_directory_noreplace(
                stage,
                destination,
                expected_source_identity=expected_identity,
                expected_parent_identity=journal_parent,
            ) as published_identity:
                if published_identity != expected_identity:
                    _fail(
                        "game_materialization_bundle_publication_identity_mismatch",
                        "recovered root identity changed",
                    )
                fsync_directory(
                    destination.parent,
                    context="recovered materialization bundle parent",
                )
        except DirectoryPublishIndeterminateError as exc:
            _fail("game_materialization_bundle_publication_indeterminate", str(exc))
        except (DirectoryPublishError, FileExistsError) as exc:
            _fail("game_materialization_bundle_recovery_failed", str(exc))
    final = verify_game_materialization_bundle(
        destination,
        expected_content_hash=str(journal["content_hash"]),
        expected_parent_identity=journal_parent,
    )
    try:
        if final.root_identity != expected_identity:
            _fail(
                "game_materialization_bundle_publication_indeterminate",
                "visible bundle differs from journal identity",
            )
        _journal_matches_verified(journal, final)
        lock.require_binding()
        _remove_journal(journal_path, journal, journal_identity, lock=lock)
    except BaseException:
        final.close()
        raise
    if _read_journal_state(journal_path, destination) is not None:
        final.close()
        _fail(
            "game_materialization_bundle_publication_indeterminate",
            "journal reappeared after finalization",
        )
    return final


def recover_game_materialization_bundle(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> VerifiedGameMaterializationBundle | None:
    destination_path, parent_identity = _validate_destination(
        destination,
        expected_parent_identity=expected_parent_identity,
    )
    with _destination_lock(destination_path) as lock:
        return _recover_locked(
            destination_path,
            lock,
            expected_parent_identity=parent_identity,
        )


def rollback_game_materialization_bundle(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> dict[str, object]:
    destination_path, parent_identity = _validate_destination(
        destination,
        expected_parent_identity=expected_parent_identity,
    )
    with _destination_lock(destination_path) as lock:
        journal_path = _journal_path(destination_path)
        loaded = _read_journal_state(journal_path, destination_path)
        if loaded is None:
            return {"status": "no_operation"}
        journal, journal_identity, _payload, partial_tail = loaded
        if partial_tail:
            _fail(
                "game_materialization_bundle_rollback_ambiguous",
                "rollback preserves a journal with a torn transition",
            )
        journal_parent = _identity_from_document(
            journal["parent_identity"],
            context="journal.parent_identity",
        )
        if journal_parent != parent_identity:
            _fail(
                "game_materialization_bundle_rollback_ambiguous",
                "rollback parent authority changed",
            )
        stage = destination_path.parent / str(journal["stage_name"])
        if _optional_directory_identity(destination_path) is not None:
            _fail(
                "game_materialization_bundle_rollback_committed",
                "rollback never removes a visible destination",
            )
        if journal["state"] == "intent":
            if _optional_directory_identity(stage) is not None:
                _fail(
                    "game_materialization_bundle_rollback_ambiguous",
                    "intent journal has an unbound stage",
                )
        else:
            stage_identity = _identity_from_document(
                journal["stage_identity"],
                context="journal.stage_identity",
            )
            if _optional_directory_identity(stage) != stage_identity:
                _fail(
                    "game_materialization_bundle_rollback_ambiguous",
                    "rollback stage identity changed",
                )

            def verify_owned(path: Path, retained_root_fd: int | None) -> None:
                _verify_owned_stage_subset(
                    path,
                    journal,
                    retained_root_fd=retained_root_fd,
                )

            try:
                files, tree = _capture_bundle_tree(stage, hook=None)
                if not files and not tree.directories:
                    remove_verified_empty_directory(stage, stage_identity)
                else:
                    quarantine_and_remove_verified_directory(
                        stage,
                        stage_identity,
                        verify_retained=verify_owned,
                    )
            except DirectoryPublishRecoveryRequiredError as exc:
                _recovery_required(
                    "game_materialization_bundle_rollback_recovery_required",
                    "automatic rollback cleanup is unavailable; the exact owned stage "
                    f"and publication journal were retained: {exc}",
                    stage=stage,
                    stage_identity=stage_identity,
                    journal_path=journal_path,
                    journal_identity=journal_identity,
                )
            except DirectoryPublishIndeterminateError as exc:
                _fail("game_materialization_bundle_rollback_indeterminate", str(exc))
            except (DirectoryPublishError, GameRuntimeBundleError) as exc:
                _fail("game_materialization_bundle_rollback_failed", str(exc))
        _remove_journal(journal_path, journal, journal_identity, lock=lock)
        return {
            "status": "rolled_back",
            "operation_id": journal["operation_id"],
            "content_hash": journal["content_hash"],
        }


def _paths_overlap(left: Path, right: Path) -> bool:
    left_name = os.path.normcase(os.path.realpath(os.fspath(left)))
    right_name = os.path.normcase(os.path.realpath(os.fspath(right)))
    try:
        common = os.path.commonpath((left_name, right_name))
    except ValueError:
        return False
    return common in {left_name, right_name}


def build_game_materialization_bundle(
    destination: str | Path,
    *,
    runtime_bundle_root: str | Path,
    runtime_implementation: object | None = None,
    platform_locks: Sequence[object] | None = None,
    include_standalone_launchers: bool = True,
    expected_parent_identity: DirectoryIdentity | None = None,
    _publication_hook: _PublicationHook | None = None,
) -> VerifiedGameMaterializationBundle:
    if not ((sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt"):
        _fail(
            "game_materialization_bundle_platform_unsupported",
            "materialization bundle publication supports only Linux and Windows",
        )
    destination_path, parent_identity = _validate_destination(
        destination,
        expected_parent_identity=expected_parent_identity,
    )
    runtime_bundle_path = Path(os.path.abspath(os.fspath(runtime_bundle_root)))
    if _paths_overlap(destination_path, runtime_bundle_path):
        _fail(
            "game_materialization_bundle_path_overlap",
            "destination must not overlap the immutable runtime bundle",
        )
    manifest, payload_files = _prepare_game_materialization_bundle(
        runtime_bundle_root=runtime_bundle_path,
        runtime_implementation=runtime_implementation,
        platform_locks=platform_locks,
        include_standalone_launchers=include_standalone_launchers,
    )
    all_files = {
        GAME_MATERIALIZATION_BUNDLE_MANIFEST: serialize_game_materialization_bundle(manifest),
        **payload_files,
    }
    with _destination_lock(destination_path) as lock:
        _require_expected_parent_identity(destination_path.parent, parent_identity)
        if _publication_hook is not None:
            _publication_hook("after_lock_acquired", None)
        recovered = _recover_locked(
            destination_path,
            lock,
            expected_parent_identity=parent_identity,
        )
        if recovered is not None:
            if recovered.manifest["content_hash"] == manifest["content_hash"]:
                return recovered
            recovered.close()
            _fail(
                "game_materialization_bundle_destination_exists",
                "destination contains a different immutable materialization bundle",
            )
        operation_id = uuid.uuid4().hex
        stage = destination_path.parent / (
            f".{destination_path.name}.game-materialization-bundle-{operation_id}"
        )
        if _paths_overlap(stage, runtime_bundle_path):
            _fail(
                "game_materialization_bundle_path_overlap",
                "private stage must not overlap the immutable runtime bundle",
            )
        journal_path = _journal_path(destination_path)
        manifest_payload = all_files[GAME_MATERIALIZATION_BUNDLE_MANIFEST]
        intent = _journal_document(
            operation_id=operation_id,
            state="intent",
            stage=stage,
            destination=destination_path,
            parent_identity=parent_identity,
            stage_identity=None,
            manifest=manifest,
            manifest_payload=manifest_payload,
        )
        journal_identity = _write_journal(
            journal_path,
            intent,
            lock=lock,
            create=True,
        )
        journal = intent
        if _publication_hook is not None:
            _publication_hook("after_intent_journal_written", None)
        with create_retained_stage(
            stage,
            expected_parent_identity=parent_identity,
            require_guard=lock.require_binding,
            hook=_publication_hook,
        ) as writer:
            stage_identity = writer.identity
            copying = _journal_document(
                operation_id=operation_id,
                state="copying",
                stage=stage,
                destination=destination_path,
                parent_identity=parent_identity,
                stage_identity=stage_identity,
                manifest=manifest,
                manifest_payload=manifest_payload,
            )
            journal_identity = _write_journal(
                journal_path,
                copying,
                lock=lock,
                create=False,
                expected_document=journal,
                expected_identity=journal_identity,
            )
            journal = copying
            if _publication_hook is not None:
                _publication_hook("after_copying_journal_written", None)
            ordered_files = (
                GAME_MATERIALIZATION_BUNDLE_MANIFEST,
                *sorted(
                    (
                        relative
                        for relative in all_files
                        if relative != GAME_MATERIALIZATION_BUNDLE_MANIFEST
                    ),
                    key=_utf8_key,
                ),
            )
            for relative in ordered_files:
                writer.write_file(relative, all_files[relative])
            writer.fsync()
            with verify_game_materialization_bundle(
                stage,
                expected_content_hash=str(manifest["content_hash"]),
                expected_parent_identity=parent_identity,
                _retained_stage_writer=writer,
            ) as verified_stage:
                _journal_matches_verified(journal, verified_stage)
                writer.require_binding()
            ready = {**journal, "state": "ready"}
            journal_identity = _write_journal(
                journal_path,
                ready,
                lock=lock,
                create=False,
                expected_document=journal,
                expected_identity=journal_identity,
            )
            journal = ready
            if _publication_hook is not None:
                _publication_hook("after_ready_journal_written", None)
            writer.require_binding()
        if _publication_hook is not None:
            _publication_hook("before_destination_publish", None)
        try:
            with publish_directory_noreplace(
                stage,
                destination_path,
                expected_source_identity=stage_identity,
                expected_parent_identity=parent_identity,
            ) as published_identity:
                if published_identity != stage_identity:
                    _fail(
                        "game_materialization_bundle_publication_identity_mismatch",
                        "published directory identity changed",
                    )
                fsync_directory(
                    destination_path.parent,
                    context="published materialization bundle parent",
                )
                published = verify_game_materialization_bundle(
                    destination_path,
                    expected_content_hash=str(manifest["content_hash"]),
                    expected_parent_identity=parent_identity,
                )
                try:
                    if published.root_identity != stage_identity:
                        _fail(
                            "game_materialization_bundle_publication_indeterminate",
                            "visible destination identity changed",
                        )
                    _journal_matches_verified(journal, published)
                finally:
                    published.close()
                if _publication_hook is not None:
                    _publication_hook("before_journal_remove", None)
                lock.require_binding()
                _remove_journal(journal_path, journal, journal_identity, lock=lock)
                if _publication_hook is not None:
                    _publication_hook("after_journal_remove", None)
                finalized = verify_game_materialization_bundle(
                    destination_path,
                    expected_content_hash=str(manifest["content_hash"]),
                    expected_parent_identity=parent_identity,
                )
                if finalized.root_identity != stage_identity:
                    finalized.close()
                    _fail(
                        "game_materialization_bundle_publication_indeterminate",
                        "final destination identity changed",
                    )
                if _read_journal_state(journal_path, destination_path) is not None:
                    finalized.close()
                    _fail(
                        "game_materialization_bundle_publication_indeterminate",
                        "journal reappeared after finalization",
                    )
                return finalized
        except DirectoryPublishIndeterminateError as exc:
            _fail("game_materialization_bundle_publication_indeterminate", str(exc))
        except FileExistsError as exc:
            _fail("game_materialization_bundle_destination_exists", str(exc))
        except DirectoryPublishError as exc:
            _fail("game_materialization_bundle_publication_failed", str(exc))


def require_game_materialization_bundle(
    root: str | Path,
) -> VerifiedGameMaterializationBundle:
    root_path = Path(os.path.abspath(os.fspath(root)))
    manifest = root_path / GAME_MATERIALIZATION_BUNDLE_MANIFEST
    if manifest.is_file():
        return verify_game_materialization_bundle(root_path)
    if (root_path / GAME_RUNTIME_BUNDLE_MANIFEST).is_file():
        _fail(
            "runtime_implementation_identity_missing",
            "bare game runtime bundle v1 has no executable implementation identity",
        )
    _fail(
        "game_materialization_bundle_manifest_missing",
        f"{GAME_MATERIALIZATION_BUNDLE_MANIFEST} is missing",
    )
