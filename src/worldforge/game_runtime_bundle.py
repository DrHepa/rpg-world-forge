from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
import sys
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from isoworld.content.portability import is_portable_path_component
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
    read_creation_object,
)
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
from worldforge.gamepack import (
    GamepackError,
    load_gamepack,
    serialize_gamepack,
    validate_gamepack_document,
)
from worldforge.generic_asset_production import (
    GenericAssetProductionError,
    inspect_runtime_asset_bytes,
)
from worldforge.generic_assetpack import (
    GENERIC_ASSETPACK_MANIFEST,
    GenericAssetpackError,
    serialize_generic_assetpack,
    validate_generic_assetpack_document,
    verify_generic_assetpack,
)
from worldforge.generic_assets import (
    GenericAssetError,
    validate_asset_inventory_document,
)
from worldforge.generic_runtime import (
    RUNTIME_ADAPTER_FORMAT,
    RUNTIME_ADAPTER_REGISTRY_FORMAT,
    RUNTIME_COMPOSITION_FORMAT,
    RUNTIME_SNAPSHOT_FORMAT,
    RUNTIME_SUPPORT_REPORT_FORMAT,
    RuntimeContractError,
    _capture_runtime_files,
    build_runtime_support_report,
    capture_trusted_runtime_snapshot_files,
    resolve_runtime_adapter,
    serialize_game_runtime_composition,
    serialize_runtime_adapter_registry,
    serialize_runtime_snapshot,
    serialize_runtime_support_report,
    validate_game_runtime_composition,
    validate_game_runtime_composition_document,
    validate_runtime_adapter_document,
    validate_runtime_adapter_registry_document,
    validate_runtime_snapshot_document,
    validate_runtime_support_report,
    validate_runtime_support_report_document,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.repository_boundary import (
    RepositoryBoundaryError,
    assert_new_repository_target,
)

GAME_RUNTIME_BUNDLE_FORMAT = "world-forge.game_runtime_bundle"
GAME_RUNTIME_BUNDLE_VERSION = 1
GAME_RUNTIME_BUNDLE_MANIFEST = "game-runtime-bundle.json"
GAME_RUNTIME_BUNDLE_JOURNAL_FORMAT = "world-forge.game_runtime_bundle_publication_journal"
GAME_RUNTIME_BUNDLE_JOURNAL_VERSION = 1

MAX_GAME_RUNTIME_BUNDLE_FILES = 256
MAX_GAME_RUNTIME_BUNDLE_DIRECTORIES = 256
MAX_GAME_RUNTIME_BUNDLE_DEPTH = 32
MAX_GAME_RUNTIME_BUNDLE_FILE_BYTES = 4 * 1024 * 1024
MAX_GAME_RUNTIME_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_GAME_RUNTIME_BUNDLE_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_GAME_RUNTIME_BUNDLE_JOURNAL_BYTES = 16 * MAX_GAME_RUNTIME_BUNDLE_MANIFEST_BYTES

_CODE_LICENSE_SHA256 = "2e55c53ff294650e049d844f2544fec947c3516440aeffca4b2334cf94b13eeb"
_CODE_LICENSE_SIZE = 1063
_CODE_LICENSE_PATH = "licenses/world-forge-mit.txt"

_MANIFEST_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "bundle_id",
        "state",
        "contracts",
        "assetpack",
        "runtime_snapshot_tree",
        "bindings",
        "legal",
        "files",
        "tree_hash",
        "content_hash",
    }
)
_CONTRACTS_FIELDS = frozenset(
    {
        "gamepack",
        "runtime_snapshot",
        "runtime_adapter",
        "runtime_adapter_registry",
        "runtime_composition",
        "runtime_support_report",
    }
)
_IDENTITY_FIELDS = frozenset({"path", "format", "format_version", "id", "content_hash"})
_ADAPTER_IDENTITY_FIELDS = frozenset(
    {
        "path",
        "format",
        "format_version",
        "id",
        "adapter_version",
        "content_hash",
    }
)
_ASSETPACK_FIELDS = frozenset({"root", "manifest", "root_hash", "inventory_hash"})
_RUNTIME_TREE_FIELDS = frozenset({"root", "runtime_api", "tree_hash", "file_count", "total_bytes"})
_RUNTIME_API_FIELDS = frozenset({"id", "version"})
_BINDING_FIELDS = frozenset(
    {
        "binding_id",
        "asset_id",
        "role",
        "media_type",
        "runtime_path",
        "bundle_path",
        "sha256",
        "size_bytes",
    }
)
_LEGAL_FIELDS = frozenset({"bundle_license", "asset_notices"})
_FILE_FIELDS = frozenset({"path", "sha256", "size_bytes"})
_JOURNAL_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "operation_id",
        "state",
        "stage_name",
        "destination_name",
        "stage_identity",
        "bundle_id",
        "content_hash",
        "tree_hash",
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
        "manifest_sha256",
        "manifest_size_bytes",
    }
)

_CONTRACT_PATHS = {
    "gamepack": "contracts/gamepack.json",
    "runtime_snapshot": "contracts/runtime-snapshot.json",
    "runtime_adapter_registry": "contracts/runtime-adapter-registry.json",
    "runtime_composition": "contracts/runtime-composition.json",
    "runtime_support_report": "status/runtime-support-report.json",
}
_CONTRACT_FORMATS = {
    "gamepack": "world-forge.gamepack",
    "runtime_snapshot": RUNTIME_SNAPSHOT_FORMAT,
    "runtime_adapter_registry": RUNTIME_ADAPTER_REGISTRY_FORMAT,
    "runtime_composition": RUNTIME_COMPOSITION_FORMAT,
    "runtime_support_report": RUNTIME_SUPPORT_REPORT_FORMAT,
}

_TreeState = tuple[int, int, int, int, int, int, int]
_VerificationHook = Callable[[str, str | None], None]
_PublicationHook = Callable[[str, str | None], None]


class GameRuntimeBundleError(ValueError):
    """Raised when a generic runtime bundle fails its closed contract."""

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
    raise GameRuntimeBundleError(
        reason_code,
        detail,
        recovery_evidence=recovery_evidence,
    )


def _hash(document: Mapping[str, object]) -> str:
    try:
        return canonical_creation_hash(document)
    except CreationContractError as exc:
        _fail("game_runtime_bundle_contract_invalid", str(exc))


def _identity(
    document: Mapping[str, object],
    *,
    id_field: str,
    path: str,
) -> dict[str, object]:
    identifier = document["game"]["id"] if id_field == "game" else document[id_field]
    return {
        "path": path,
        "format": document["format"],
        "format_version": document["format_version"],
        "id": identifier,
        "content_hash": document["content_hash"],
    }


def _lineage_identity(
    document: Mapping[str, object],
    *,
    id_field: str,
) -> dict[str, object]:
    identity = _identity(
        document,
        id_field=id_field,
        path="",
    )
    identity.pop("path")
    return identity


def _bundle_seed(document: Mapping[str, object]) -> dict[str, object]:
    return {
        key: copy.deepcopy(value)
        for key, value in document.items()
        if key not in {"bundle_id", "content_hash"}
    }


def _derived_bundle_id(document: Mapping[str, object]) -> str:
    return f"game_runtime_bundle_{_hash(_bundle_seed(document))[:48]}"


def _code_license_bytes() -> bytes:
    try:
        payload = (
            resources.files("worldforge")
            .joinpath("templates", "pyray_game", "LICENSE.tmpl")
            .read_bytes()
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        _fail(
            "game_runtime_bundle_license_unavailable",
            f"could not load the code-owned MIT license: {exc}",
        )
    if (
        len(payload) != _CODE_LICENSE_SIZE
        or hashlib.sha256(payload).hexdigest() != _CODE_LICENSE_SHA256
    ):
        _fail(
            "game_runtime_bundle_license_untrusted",
            "the installed code-owned MIT license does not match its audited identity",
        )
    return payload


def _validate_path_tree(paths: Sequence[str], context: str) -> None:
    if len(paths) != len(set(paths)):
        _fail("game_runtime_bundle_path_collision", f"{context} contains duplicate paths")
    folded: dict[str, str] = {}
    path_set = set(paths)
    for path in paths:
        try:
            checked = _portable_relative_path(path, context)
        except CreationContractError as exc:
            _fail("game_runtime_bundle_path_invalid", str(exc))
        if checked != unicodedata.normalize("NFC", checked):
            _fail(
                "game_runtime_bundle_path_collision",
                f"{context} contains a non-NFC path: {checked}",
            )
        key = checked.casefold()
        previous = folded.get(key)
        if previous is not None and previous != checked:
            _fail(
                "game_runtime_bundle_path_collision",
                f"{previous} and {checked} collide under casefold",
            )
        folded[key] = checked
        for parent in PurePosixPath(checked).parents:
            parent_text = parent.as_posix()
            if parent_text != "." and parent_text in path_set:
                _fail(
                    "game_runtime_bundle_path_collision",
                    f"{parent_text} is both a file and a path prefix",
                )


def _file_inventory(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    paths = sorted(files, key=lambda item: item.encode("utf-8"))
    if not paths or len(paths) > MAX_GAME_RUNTIME_BUNDLE_FILES:
        _fail(
            "game_runtime_bundle_limit",
            "runtime bundle file count is empty or exceeds its limit",
        )
    _validate_path_tree(paths, "runtime bundle files")
    total = sum(len(files[path]) for path in paths)
    if total > MAX_GAME_RUNTIME_BUNDLE_BYTES:
        _fail("game_runtime_bundle_limit", "runtime bundle bytes exceed the limit")
    entries: list[dict[str, object]] = []
    for path in paths:
        payload = files[path]
        if len(payload) > MAX_GAME_RUNTIME_BUNDLE_FILE_BYTES:
            _fail(
                "game_runtime_bundle_limit",
                f"runtime bundle file exceeds its limit: {path}",
            )
        entries.append(
            {
                "path": path,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        )
    return entries


def _expected_directories(paths: Sequence[str]) -> set[str]:
    return {
        parent.as_posix()
        for path in paths
        for parent in PurePosixPath(path).parents
        if parent.as_posix() != "."
    }


def _validate_file_entry(value: object, context: str) -> dict[str, Any]:
    entry = _object(value, context)
    _exact_keys(entry, _FILE_FIELDS, context)
    _portable_relative_path(entry.get("path"), f"{context}.path")
    _sha256(entry.get("sha256"), f"{context}.sha256")
    size = _integer(entry.get("size_bytes"), f"{context}.size_bytes", minimum=0)
    if size > MAX_GAME_RUNTIME_BUNDLE_FILE_BYTES:
        _fail("game_runtime_bundle_limit", f"{context}.size_bytes exceeds its limit")
    return entry


def _validate_contract_identity(
    value: object,
    context: str,
    *,
    expected_path: str,
    expected_format: str,
) -> dict[str, Any]:
    identity = _object(value, context)
    _exact_keys(identity, _IDENTITY_FIELDS, context)
    if identity.get("path") != expected_path:
        _fail(
            "game_runtime_bundle_contract_binding_mismatch",
            f"{context}.path is not the fixed bundle path",
        )
    if identity.get("format") != expected_format or identity.get("format_version") != 1:
        _fail(
            "game_runtime_bundle_contract_binding_mismatch",
            f"{context} has an unsupported format identity",
        )
    _identifier(identity.get("id"), f"{context}.id")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    return identity


def validate_game_runtime_bundle_document(value: object) -> dict[str, Any]:
    """Validate only manifest structure and hashes, not directory integrity."""

    try:
        _validate_json_structure(value, context="game runtime bundle")
        document = _object(value, "game runtime bundle")
        _exact_keys(document, _MANIFEST_FIELDS, "game runtime bundle")
        if document.get("format") != GAME_RUNTIME_BUNDLE_FORMAT:
            _fail(
                "game_runtime_bundle_format_invalid",
                f"format must be {GAME_RUNTIME_BUNDLE_FORMAT}",
            )
        if document.get("format_version") != GAME_RUNTIME_BUNDLE_VERSION:
            _fail("game_runtime_bundle_version_invalid", "format_version must be 1")
        if document.get("state") != "pre_execution":
            _fail(
                "game_runtime_bundle_state_overclaim",
                "bundle state must remain pre_execution",
            )
        bundle_id = document.get("bundle_id")
        if (
            not isinstance(bundle_id, str)
            or re.fullmatch(r"game_runtime_bundle_[0-9a-f]{48}", bundle_id) is None
        ):
            _fail(
                "game_runtime_bundle_id_mismatch",
                "bundle_id must be the deterministic 48-hex bundle identity",
            )

        contracts = _object(document.get("contracts"), "game runtime bundle.contracts")
        _exact_keys(contracts, _CONTRACTS_FIELDS, "game runtime bundle.contracts")
        for field, path in _CONTRACT_PATHS.items():
            _validate_contract_identity(
                contracts.get(field),
                f"game runtime bundle.contracts.{field}",
                expected_path=path,
                expected_format=_CONTRACT_FORMATS[field],
            )
        adapter = _object(
            contracts.get("runtime_adapter"),
            "game runtime bundle.contracts.runtime_adapter",
        )
        _exact_keys(
            adapter,
            _ADAPTER_IDENTITY_FIELDS,
            "game runtime bundle.contracts.runtime_adapter",
        )
        if adapter.get("format") != RUNTIME_ADAPTER_FORMAT or adapter.get("format_version") != 1:
            _fail(
                "game_runtime_bundle_contract_binding_mismatch",
                "runtime adapter identity is unsupported",
            )
        adapter_id = _identifier(
            adapter.get("id"),
            "game runtime bundle.contracts.runtime_adapter.id",
        )
        adapter_version = adapter.get("adapter_version")
        if (
            not isinstance(adapter_version, str)
            or re.fullmatch(
                r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",
                adapter_version,
            )
            is None
        ):
            _fail(
                "game_runtime_bundle_contract_binding_mismatch",
                "runtime adapter version is invalid",
            )
        expected_adapter_path = (
            f"runtime/snapshot-tree/descriptors/{adapter_id}@{adapter_version}.json"
        )
        if adapter.get("path") != expected_adapter_path:
            _fail(
                "game_runtime_bundle_contract_binding_mismatch",
                "runtime adapter descriptor path is not deterministic",
            )
        _sha256(
            adapter.get("content_hash"),
            "game runtime bundle.contracts.runtime_adapter.content_hash",
        )

        assetpack = _object(document.get("assetpack"), "game runtime bundle.assetpack")
        _exact_keys(assetpack, _ASSETPACK_FIELDS, "game runtime bundle.assetpack")
        if assetpack.get("root") != "assetpack":
            _fail(
                "game_runtime_bundle_assetpack_mismatch",
                "assetpack root must be assetpack",
            )
        _validate_contract_identity(
            assetpack.get("manifest"),
            "game runtime bundle.assetpack.manifest",
            expected_path="assetpack/assetpack.json",
            expected_format="world-forge.assetpack",
        )
        _sha256(assetpack.get("root_hash"), "game runtime bundle.assetpack.root_hash")
        _sha256(
            assetpack.get("inventory_hash"),
            "game runtime bundle.assetpack.inventory_hash",
        )

        runtime_tree = _object(
            document.get("runtime_snapshot_tree"),
            "game runtime bundle.runtime_snapshot_tree",
        )
        _exact_keys(
            runtime_tree,
            _RUNTIME_TREE_FIELDS,
            "game runtime bundle.runtime_snapshot_tree",
        )
        if runtime_tree.get("root") != "runtime/snapshot-tree":
            _fail(
                "game_runtime_bundle_runtime_tree_mismatch",
                "runtime snapshot tree root is not fixed",
            )
        runtime_api = _object(
            runtime_tree.get("runtime_api"),
            "game runtime bundle.runtime_snapshot_tree.runtime_api",
        )
        _exact_keys(
            runtime_api,
            _RUNTIME_API_FIELDS,
            "game runtime bundle.runtime_snapshot_tree.runtime_api",
        )
        if runtime_api != {"id": "gamepack_runtime", "version": "1.0.0"}:
            _fail(
                "game_runtime_bundle_runtime_tree_mismatch",
                "runtime API identity is unsupported",
            )
        _sha256(
            runtime_tree.get("tree_hash"),
            "game runtime bundle.runtime_snapshot_tree.tree_hash",
        )
        runtime_file_count = _integer(
            runtime_tree.get("file_count"),
            "game runtime bundle.runtime_snapshot_tree.file_count",
            minimum=1,
        )
        runtime_total_bytes = _integer(
            runtime_tree.get("total_bytes"),
            "game runtime bundle.runtime_snapshot_tree.total_bytes",
            minimum=1,
        )

        raw_bindings = document.get("bindings")
        if not isinstance(raw_bindings, list) or not raw_bindings or len(raw_bindings) > 256:
            _fail(
                "game_runtime_bundle_binding_mismatch",
                "bindings must be a non-empty bounded array",
            )
        bindings: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_bindings):
            context = f"game runtime bundle.bindings/{index}"
            binding = _object(raw, context)
            _exact_keys(binding, _BINDING_FIELDS, context)
            _identifier(binding.get("binding_id"), f"{context}.binding_id")
            _identifier(binding.get("asset_id"), f"{context}.asset_id")
            _identifier(binding.get("role"), f"{context}.role")
            media_type = binding.get("media_type")
            if not isinstance(media_type, str) or not media_type:
                _fail("game_runtime_bundle_binding_mismatch", f"{context}.media_type is invalid")
            runtime_path = _portable_relative_path(
                binding.get("runtime_path"),
                f"{context}.runtime_path",
            )
            if binding.get("bundle_path") != f"assetpack/{runtime_path}":
                _fail(
                    "game_runtime_bundle_binding_mismatch",
                    f"{context}.bundle_path is not derived from runtime_path",
                )
            _sha256(binding.get("sha256"), f"{context}.sha256")
            _integer(binding.get("size_bytes"), f"{context}.size_bytes", minimum=1)
            bindings.append(binding)
        binding_ids = [binding["binding_id"] for binding in bindings]
        if binding_ids != sorted(binding_ids, key=lambda item: item.encode("utf-8")):
            _fail(
                "game_runtime_bundle_noncanonical",
                "bindings are not UTF-8 identifier sorted",
            )
        if len({item.casefold() for item in binding_ids}) != len(binding_ids):
            _fail("game_runtime_bundle_binding_mismatch", "binding IDs collide")

        legal = _object(document.get("legal"), "game runtime bundle.legal")
        _exact_keys(legal, _LEGAL_FIELDS, "game runtime bundle.legal")
        license_entry = _validate_file_entry(
            legal.get("bundle_license"),
            "game runtime bundle.legal.bundle_license",
        )
        if (
            license_entry["path"] != _CODE_LICENSE_PATH
            or license_entry["sha256"] != _CODE_LICENSE_SHA256
            or license_entry["size_bytes"] != _CODE_LICENSE_SIZE
        ):
            _fail(
                "game_runtime_bundle_license_untrusted",
                "bundle license identity is not the audited code-owned MIT license",
            )
        raw_notices = legal.get("asset_notices")
        if not isinstance(raw_notices, list) or len(raw_notices) > 1024:
            _fail(
                "game_runtime_bundle_notice_mismatch",
                "asset notices must be a bounded array",
            )
        notices = [
            _validate_file_entry(
                notice,
                f"game runtime bundle.legal.asset_notices/{index}",
            )
            for index, notice in enumerate(raw_notices)
        ]
        notice_paths = [notice["path"] for notice in notices]
        if notice_paths != sorted(notice_paths, key=lambda item: item.encode("utf-8")):
            _fail("game_runtime_bundle_noncanonical", "asset notices are not path sorted")
        if len(set(notice_paths)) != len(notice_paths):
            _fail(
                "game_runtime_bundle_notice_mismatch",
                "asset notice paths must be unique",
            )

        raw_files = document.get("files")
        if (
            not isinstance(raw_files, list)
            or not raw_files
            or len(raw_files) > MAX_GAME_RUNTIME_BUNDLE_FILES
        ):
            _fail(
                "game_runtime_bundle_limit",
                "files must be a non-empty bounded array",
            )
        files = [
            _validate_file_entry(entry, f"game runtime bundle.files/{index}")
            for index, entry in enumerate(raw_files)
        ]
        paths = [entry["path"] for entry in files]
        if paths != sorted(paths, key=lambda item: item.encode("utf-8")):
            _fail("game_runtime_bundle_noncanonical", "files are not UTF-8 path sorted")
        if GAME_RUNTIME_BUNDLE_MANIFEST in paths:
            _fail(
                "game_runtime_bundle_tree_mismatch",
                "root manifest must not appear in its own file inventory",
            )
        _validate_path_tree(paths, "game runtime bundle file inventory")
        total_bytes = sum(entry["size_bytes"] for entry in files)
        if total_bytes > MAX_GAME_RUNTIME_BUNDLE_BYTES:
            _fail("game_runtime_bundle_limit", "file bytes exceed the bundle limit")
        files_by_path = {entry["path"]: entry for entry in files}
        for binding in bindings:
            record = files_by_path.get(binding["bundle_path"])
            if (
                record is None
                or record["sha256"] != binding["sha256"]
                or record["size_bytes"] != binding["size_bytes"]
            ):
                _fail(
                    "game_runtime_bundle_binding_mismatch",
                    f"binding {binding['binding_id']} does not match its bundled file",
                )
        for notice in notices:
            record = files_by_path.get(notice["path"])
            if record != notice:
                _fail(
                    "game_runtime_bundle_notice_mismatch",
                    f"asset notice {notice['path']} does not match the file inventory",
                )
        runtime_entries = [
            entry for entry in files if entry["path"].startswith("runtime/snapshot-tree/")
        ]
        if runtime_file_count != len(runtime_entries) or runtime_total_bytes != sum(
            entry["size_bytes"] for entry in runtime_entries
        ):
            _fail(
                "game_runtime_bundle_runtime_tree_mismatch",
                "runtime snapshot tree counts are not exact",
            )
        runtime_records = [
            {
                "path": entry["path"].removeprefix("runtime/snapshot-tree/"),
                "sha256": entry["sha256"],
                "size_bytes": entry["size_bytes"],
            }
            for entry in runtime_entries
        ]
        if runtime_tree["tree_hash"] != _hash({"files": runtime_records}):
            _fail(
                "game_runtime_bundle_runtime_tree_mismatch",
                "runtime snapshot tree hash is not byte-inventory derived",
            )
        _sha256(document.get("tree_hash"), "game runtime bundle.tree_hash")
        if document["tree_hash"] != _hash({"files": files}):
            _fail(
                "game_runtime_bundle_tree_hash_mismatch",
                "tree_hash is not derived from the canonical file inventory",
            )
        if document["bundle_id"] != _derived_bundle_id(document):
            _fail(
                "game_runtime_bundle_id_mismatch",
                "bundle_id is not deterministically derived",
            )
        _sha256(document.get("content_hash"), "game runtime bundle.content_hash")
        if document["content_hash"] != _hash(document):
            _fail(
                "game_runtime_bundle_content_hash_mismatch",
                "content_hash is not canonical",
            )
        return copy.deepcopy(document)
    except GameRuntimeBundleError:
        raise
    except (CreationContractError, TypeError, ValueError, RecursionError) as exc:
        _fail("game_runtime_bundle_contract_invalid", str(exc))


def serialize_game_runtime_bundle(value: object) -> bytes:
    return canonical_json_bytes(validate_game_runtime_bundle_document(value))


def _adapter_from_registry(
    registry: Mapping[str, Any],
    adapter_identity: Mapping[str, Any],
) -> dict[str, Any]:
    matches = [
        adapter
        for adapter in registry["adapters"]
        if adapter["adapter_id"] == adapter_identity["id"]
        and adapter["content_hash"] == adapter_identity["content_hash"]
    ]
    if len(matches) != 1:
        _fail(
            "game_runtime_bundle_adapter_mismatch",
            "registry does not contain the exact selected adapter",
        )
    return copy.deepcopy(matches[0])


def _assetpack_root_hash(files: Mapping[str, bytes]) -> str:
    entries = [
        {
            "path": path,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
        for path, payload in sorted(
            files.items(),
            key=lambda item: item[0].encode("utf-8"),
        )
    ]
    return _hash({"files": entries})


def _derive_transfer_bindings(
    gamepack: Mapping[str, Any],
    assetpack: Mapping[str, Any],
    adapter: Mapping[str, Any],
) -> list[dict[str, Any]]:
    required = {item["binding_id"] for item in gamepack["asset_requirements"] if item["required"]}
    rules = {item["binding_id"]: item for item in adapter["asset_bindings"]}
    if set(rules) != required:
        _fail(
            "game_runtime_bundle_binding_mismatch",
            "adapter rules do not exactly cover required gamepack bindings",
        )
    assets = {item["asset"]["asset_id"]: item for item in assetpack["assets"]}
    consumed: set[tuple[str, str, str, str]] = set()
    bindings: list[dict[str, Any]] = []
    for binding_id in sorted(required, key=lambda item: item.encode("utf-8")):
        rule = rules[binding_id]
        asset = assets.get(rule["asset_id"])
        outputs = (
            []
            if asset is None
            else [
                output
                for output in asset["outputs"]
                if output["role"] == rule["role"]
                and output["media_type"] == rule["media_type"]
                and output["runtime_path"] == rule["runtime_path"]
            ]
        )
        if len(outputs) != 1:
            _fail(
                "game_runtime_bundle_binding_mismatch",
                f"binding {binding_id} has no exact sealed output",
            )
        output = outputs[0]
        consumed.add(
            (
                rule["asset_id"],
                output["role"],
                output["media_type"],
                output["runtime_path"],
            )
        )
        bindings.append(
            {
                "binding_id": binding_id,
                "asset_id": rule["asset_id"],
                "role": output["role"],
                "media_type": output["media_type"],
                "runtime_path": output["runtime_path"],
                "bundle_path": f"assetpack/{output['runtime_path']}",
                "sha256": output["sha256"],
                "size_bytes": output["size_bytes"],
            }
        )
    outputs = {
        (
            asset_id,
            output["role"],
            output["media_type"],
            output["runtime_path"],
        )
        for asset_id, asset in assets.items()
        for output in asset["outputs"]
    }
    if consumed != outputs:
        _fail(
            "game_runtime_bundle_binding_mismatch",
            "sealed assetpack contains unbound runtime outputs",
        )
    return bindings


def _build_manifest(
    *,
    gamepack: Mapping[str, Any],
    assetpack: Mapping[str, Any],
    assetpack_files: Mapping[str, bytes],
    snapshot: Mapping[str, Any],
    runtime_files: Mapping[str, bytes],
    adapter: Mapping[str, Any],
    registry: Mapping[str, Any],
    composition: Mapping[str, Any],
    support: Mapping[str, Any],
    files: Mapping[str, bytes],
) -> dict[str, Any]:
    snapshot_tree_records = [
        {
            "path": path,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
        for path, payload in sorted(
            runtime_files.items(),
            key=lambda item: item[0].encode("utf-8"),
        )
    ]
    bindings = _derive_transfer_bindings(gamepack, assetpack, adapter)
    notices = sorted(
        {
            (
                f"assetpack/{output['runtime_notice']['path']}",
                output["runtime_notice"]["sha256"],
                output["runtime_notice"]["size_bytes"],
            )
            for asset in assetpack["assets"]
            for output in asset["outputs"]
        },
        key=lambda item: item[0].encode("utf-8"),
    )
    inventory = _file_inventory(files)
    adapter_path = (
        "runtime/snapshot-tree/descriptors/"
        f"{adapter['adapter_id']}@{adapter['adapter_version']}.json"
    )
    document: dict[str, Any] = {
        "format": GAME_RUNTIME_BUNDLE_FORMAT,
        "format_version": GAME_RUNTIME_BUNDLE_VERSION,
        "bundle_id": "",
        "state": "pre_execution",
        "contracts": {
            "gamepack": _identity(
                gamepack,
                id_field="game",
                path=_CONTRACT_PATHS["gamepack"],
            ),
            "runtime_snapshot": _identity(
                snapshot,
                id_field="snapshot_id",
                path=_CONTRACT_PATHS["runtime_snapshot"],
            ),
            "runtime_adapter": {
                **_identity(
                    adapter,
                    id_field="adapter_id",
                    path=adapter_path,
                ),
                "adapter_version": adapter["adapter_version"],
            },
            "runtime_adapter_registry": _identity(
                registry,
                id_field="registry_id",
                path=_CONTRACT_PATHS["runtime_adapter_registry"],
            ),
            "runtime_composition": _identity(
                composition,
                id_field="composition_id",
                path=_CONTRACT_PATHS["runtime_composition"],
            ),
            "runtime_support_report": _identity(
                support,
                id_field="report_id",
                path=_CONTRACT_PATHS["runtime_support_report"],
            ),
        },
        "assetpack": {
            "root": "assetpack",
            "manifest": _identity(
                assetpack,
                id_field="assetpack_id",
                path="assetpack/assetpack.json",
            ),
            "root_hash": _assetpack_root_hash(assetpack_files),
            "inventory_hash": assetpack["inventory"]["content_hash"],
        },
        "runtime_snapshot_tree": {
            "root": "runtime/snapshot-tree",
            "runtime_api": copy.deepcopy(snapshot["runtime_api"]),
            "tree_hash": _hash({"files": snapshot_tree_records}),
            "file_count": len(snapshot_tree_records),
            "total_bytes": sum(item["size_bytes"] for item in snapshot_tree_records),
        },
        "bindings": bindings,
        "legal": {
            "bundle_license": {
                "path": _CODE_LICENSE_PATH,
                "sha256": _CODE_LICENSE_SHA256,
                "size_bytes": _CODE_LICENSE_SIZE,
            },
            "asset_notices": [
                {"path": path, "sha256": sha256, "size_bytes": size}
                for path, sha256, size in notices
            ],
        },
        "files": inventory,
        "tree_hash": _hash({"files": inventory}),
        "content_hash": "",
    }
    document["bundle_id"] = _derived_bundle_id(document)
    document["content_hash"] = _hash(document)
    return validate_game_runtime_bundle_document(document)


def _load_bundle_inputs(
    *,
    gamepack_path: str | Path,
    inventory_path: str | Path,
    assetpack_root: str | Path,
    snapshot_path: str | Path,
    registry_path: str | Path,
    composition_path: str | Path,
    support_report_path: str | Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, bytes],
    dict[str, Any],
    dict[str, bytes],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    try:
        gamepack = load_gamepack(gamepack_path)
        inventory = validate_asset_inventory_document(read_creation_object(inventory_path))
        snapshot = validate_runtime_snapshot_document(read_creation_object(snapshot_path))
        verified_assetpack = verify_generic_assetpack(assetpack_root)
        try:
            assetpack = verified_assetpack.manifest
        finally:
            verified_assetpack.close()
        return _validate_bundle_object_inputs(
            gamepack=gamepack,
            inventory=inventory,
            assetpack=assetpack,
            assetpack_root=assetpack_root,
            snapshot=snapshot,
            registry=read_creation_object(registry_path),
            composition=read_creation_object(composition_path),
            support_report=read_creation_object(support_report_path),
        )
    except GameRuntimeBundleError:
        raise
    except (
        CreationContractError,
        GamepackError,
        GenericAssetError,
        GenericAssetpackError,
        RuntimeContractError,
        OSError,
    ) as exc:
        _fail("game_runtime_bundle_source_invalid", str(exc))


def _validate_bundle_object_inputs(
    *,
    gamepack: object,
    inventory: object,
    assetpack: object,
    assetpack_root: str | Path,
    snapshot: object,
    registry: object,
    composition: object,
    support_report: object,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, bytes],
    dict[str, Any],
    dict[str, bytes],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    """Validate immutable object inputs and bind them to one verified assetpack tree."""

    try:
        checked_gamepack = validate_gamepack_document(gamepack)
        checked_inventory = validate_asset_inventory_document(inventory)
        checked_assetpack = validate_generic_assetpack_document(assetpack)
        checked_snapshot = validate_runtime_snapshot_document(snapshot)
        checked_registry = validate_runtime_adapter_registry_document(
            registry,
            snapshot=checked_snapshot,
        )
        checked_composition = validate_game_runtime_composition(
            validate_game_runtime_composition_document(composition),
            gamepack=checked_gamepack,
            inventory=checked_inventory,
            assetpack_root=assetpack_root,
            registry=checked_registry,
            snapshot=checked_snapshot,
        )
        checked_support = validate_runtime_support_report(
            validate_runtime_support_report_document(support_report),
            composition=checked_composition,
            gamepack=checked_gamepack,
            registry=checked_registry,
            snapshot=checked_snapshot,
            evidence=[],
        )
        expected_support = build_runtime_support_report(
            checked_composition,
            gamepack=checked_gamepack,
            registry=checked_registry,
            snapshot=checked_snapshot,
            evidence=[],
        )
        if checked_support != expected_support:
            _fail(
                "game_runtime_bundle_support_overclaim",
                "support report is not the exact evidence-free blocked report",
            )
        if (
            checked_support["evidence"]
            or checked_support["dimensions"]["adapter"] != "declared"
            or checked_support["dimensions"]["packaging"] != "unverified"
            or checked_support["dimensions"]["release"] != "blocked"
            or checked_support["supported"] is not False
            or any(
                item["status"] != "untested" or item["evidence_ids"]
                for item in checked_support["dimensions"]["execution"]
            )
        ):
            _fail(
                "game_runtime_bundle_support_overclaim",
                "pre-execution bundle support must remain evidence-free and blocked",
            )
        adapter = resolve_runtime_adapter(
            checked_gamepack,
            registry=checked_registry,
            snapshot=checked_snapshot,
        )
        if adapter["state"] != "declared":
            _fail(
                "game_runtime_bundle_support_overclaim",
                "pre-execution bundle requires a declared adapter",
            )
        runtime_files = dict(
            capture_trusted_runtime_snapshot_files(
                snapshot=checked_snapshot,
                registry=checked_registry,
            )
        )
        verified_assetpack = verify_generic_assetpack(assetpack_root)
        try:
            retained_assetpack = verified_assetpack.manifest
            assetpack_files = dict(verified_assetpack.files)
        finally:
            verified_assetpack.close()
        if retained_assetpack != checked_assetpack:
            _fail(
                "game_runtime_bundle_assetpack_mismatch",
                "assetpack object does not match the exact retained D3 bytes",
            )
        expected_assetpack = {
            **_lineage_identity(checked_assetpack, id_field="assetpack_id"),
            "root_hash": _assetpack_root_hash(assetpack_files),
            "inventory_hash": checked_assetpack["inventory"]["content_hash"],
        }
        if (
            checked_composition["assetpack"] != expected_assetpack
            or checked_composition["asset_inventory"] != checked_assetpack["asset_inventory"]
            or checked_assetpack["gamepack"] != _lineage_identity(checked_gamepack, id_field="game")
        ):
            _fail(
                "game_runtime_bundle_assetpack_mismatch",
                "composition does not bind the exact retained D3 assetpack",
            )
        return (
            checked_gamepack,
            checked_assetpack,
            assetpack_files,
            checked_snapshot,
            runtime_files,
            adapter,
            checked_registry,
            checked_composition,
            checked_support,
        )
    except GameRuntimeBundleError:
        raise
    except (
        CreationContractError,
        GamepackError,
        GenericAssetError,
        GenericAssetpackError,
        RuntimeContractError,
        OSError,
    ) as exc:
        _fail("game_runtime_bundle_source_invalid", str(exc))


def _build_game_runtime_bundle_manifest_from_validated(
    validated: tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, bytes],
        dict[str, Any],
        dict[str, bytes],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ],
) -> tuple[dict[str, Any], Mapping[str, bytes]]:
    (
        gamepack,
        assetpack,
        assetpack_files,
        snapshot,
        runtime_files,
        adapter,
        registry,
        composition,
        support,
    ) = validated
    files: dict[str, bytes] = {
        _CONTRACT_PATHS["gamepack"]: serialize_gamepack(gamepack),
        _CONTRACT_PATHS["runtime_snapshot"]: serialize_runtime_snapshot(snapshot),
        _CONTRACT_PATHS["runtime_adapter_registry"]: (serialize_runtime_adapter_registry(registry)),
        _CONTRACT_PATHS["runtime_composition"]: (serialize_game_runtime_composition(composition)),
        _CONTRACT_PATHS["runtime_support_report"]: (serialize_runtime_support_report(support)),
        _CODE_LICENSE_PATH: _code_license_bytes(),
    }
    files.update({f"assetpack/{path}": payload for path, payload in assetpack_files.items()})
    files.update(
        {f"runtime/snapshot-tree/{path}": payload for path, payload in runtime_files.items()}
    )
    manifest = _build_manifest(
        gamepack=gamepack,
        assetpack=assetpack,
        assetpack_files=assetpack_files,
        snapshot=snapshot,
        runtime_files=runtime_files,
        adapter=adapter,
        registry=registry,
        composition=composition,
        support=support,
        files=files,
    )
    return manifest, MappingProxyType(dict(files))


def build_game_runtime_bundle_manifest_from_objects(
    *,
    gamepack: object,
    inventory: object,
    assetpack: object,
    assetpack_root: str | Path,
    snapshot: object,
    registry: object,
    composition: object,
    support_report: object,
) -> tuple[dict[str, Any], Mapping[str, bytes]]:
    """Build deterministic runtime-only bytes from immutable validated objects."""

    return _build_game_runtime_bundle_manifest_from_validated(
        _validate_bundle_object_inputs(
            gamepack=gamepack,
            inventory=inventory,
            assetpack=assetpack,
            assetpack_root=assetpack_root,
            snapshot=snapshot,
            registry=registry,
            composition=composition,
            support_report=support_report,
        )
    )


def build_game_runtime_bundle_manifest(
    *,
    gamepack_path: str | Path,
    inventory_path: str | Path,
    assetpack_root: str | Path,
    snapshot_path: str | Path,
    registry_path: str | Path,
    composition_path: str | Path,
    support_report_path: str | Path,
) -> tuple[dict[str, Any], Mapping[str, bytes]]:
    """Build one deterministic pre-execution manifest plus its exact runtime files."""

    return _build_game_runtime_bundle_manifest_from_validated(
        _load_bundle_inputs(
            gamepack_path=gamepack_path,
            inventory_path=inventory_path,
            assetpack_root=assetpack_root,
            snapshot_path=snapshot_path,
            registry_path=registry_path,
            composition_path=composition_path,
            support_report_path=support_report_path,
        )
    )


@dataclass(frozen=True, slots=True)
class _PhysicalTree:
    root_state: _TreeState
    files: frozenset[str]
    directories: frozenset[str]


def _tree_state(info: FileStat) -> _TreeState:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _physical_tree(root: Path) -> _PhysicalTree:
    try:
        root_info = path_file_stat(root)
    except OSError as exc:
        _fail("game_runtime_bundle_directory_invalid", str(exc))
    if is_link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        _fail(
            "game_runtime_bundle_directory_invalid",
            "runtime bundle root must be a real directory",
        )
    files: set[str] = set()
    directories: set[str] = set()
    pending = [Path()]
    try:
        while pending:
            relative_directory = pending.pop()
            current = root / relative_directory
            with os.scandir(current) as iterator:
                entries = sorted(iterator, key=lambda item: os.fsencode(item.name))
            sibling_keys: set[str] = set()
            for entry in entries:
                name = entry.name
                if (
                    type(name) is not str
                    or not is_portable_path_component(name)
                    or unicodedata.normalize("NFC", name) != name
                ):
                    _fail(
                        "game_runtime_bundle_tree_unsafe",
                        f"runtime bundle has an unsafe path component: {name!r}",
                    )
                key = name.casefold()
                if key in sibling_keys:
                    _fail(
                        "game_runtime_bundle_path_collision",
                        f"runtime bundle directory has a casefold collision: {current}",
                    )
                sibling_keys.add(key)
                relative_path = relative_directory / name
                relative = relative_path.as_posix()
                if (
                    name == "__pycache__"
                    or name.endswith((".pyc", ".pyo"))
                    or len(PurePosixPath(relative).parts) > MAX_GAME_RUNTIME_BUNDLE_DEPTH
                ):
                    _fail(
                        "game_runtime_bundle_tree_unsafe",
                        f"runtime bundle contains a forbidden path: {relative}",
                    )
                info = path_file_stat(root / relative_path)
                if is_link_or_reparse(info):
                    _fail(
                        "game_runtime_bundle_tree_unsafe",
                        f"runtime bundle entry is linked or reparse-backed: {relative}",
                    )
                if stat.S_ISDIR(info.st_mode):
                    directories.add(relative)
                    pending.append(relative_path)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    files.add(relative)
                    if info.st_size > MAX_GAME_RUNTIME_BUNDLE_FILE_BYTES:
                        _fail(
                            "game_runtime_bundle_limit",
                            f"runtime bundle file exceeds its limit: {relative}",
                        )
                else:
                    _fail(
                        "game_runtime_bundle_tree_unsafe",
                        f"runtime bundle entry is special or hard-linked: {relative}",
                    )
                if (
                    len(files) > MAX_GAME_RUNTIME_BUNDLE_FILES + 1
                    or len(directories) > MAX_GAME_RUNTIME_BUNDLE_DIRECTORIES
                ):
                    _fail(
                        "game_runtime_bundle_limit",
                        "runtime bundle tree exceeds its node limits",
                    )
    except GameRuntimeBundleError:
        raise
    except OSError as exc:
        _fail("game_runtime_bundle_directory_invalid", str(exc))
    return _PhysicalTree(
        root_state=_tree_state(root_info),
        files=frozenset(files),
        directories=frozenset(directories),
    )


def _physical_tree_from_fd(root_fd: int) -> _PhysicalTree:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        root_info = descriptor_file_stat(root_fd)
    except OSError as exc:
        _fail("game_runtime_bundle_directory_invalid", str(exc))
    if is_link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        _fail(
            "game_runtime_bundle_directory_invalid",
            "retained runtime bundle root must be a real directory",
        )

    files: set[str] = set()
    directories: set[str] = set()
    try:
        pending: list[tuple[Path, int]] = [(Path(), os.open(".", flags, dir_fd=root_fd))]
    except OSError as exc:
        _fail("game_runtime_bundle_directory_invalid", str(exc))
    try:
        while pending:
            relative_directory, current_fd = pending.pop()
            try:
                names = sorted(os.listdir(current_fd), key=os.fsencode)
                sibling_keys: set[str] = set()
                for name in names:
                    if (
                        type(name) is not str
                        or not is_portable_path_component(name)
                        or unicodedata.normalize("NFC", name) != name
                    ):
                        _fail(
                            "game_runtime_bundle_tree_unsafe",
                            f"runtime bundle has an unsafe path component: {name!r}",
                        )
                    key = name.casefold()
                    if key in sibling_keys:
                        _fail(
                            "game_runtime_bundle_path_collision",
                            "retained runtime bundle directory has a casefold collision",
                        )
                    sibling_keys.add(key)
                    relative_path = relative_directory / name
                    relative = relative_path.as_posix()
                    if (
                        name == "__pycache__"
                        or name.endswith((".pyc", ".pyo"))
                        or len(PurePosixPath(relative).parts) > MAX_GAME_RUNTIME_BUNDLE_DEPTH
                    ):
                        _fail(
                            "game_runtime_bundle_tree_unsafe",
                            f"runtime bundle contains a forbidden path: {relative}",
                        )
                    info = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
                    if is_link_or_reparse(info):
                        _fail(
                            "game_runtime_bundle_tree_unsafe",
                            f"runtime bundle entry is linked or reparse-backed: {relative}",
                        )
                    if stat.S_ISDIR(info.st_mode):
                        child_fd = os.open(name, flags, dir_fd=current_fd)
                        try:
                            opened = descriptor_file_stat(child_fd)
                            if _tree_state(opened) != _tree_state(info):
                                _fail(
                                    "game_runtime_bundle_tree_changed",
                                    f"retained runtime bundle directory changed: {relative}",
                                )
                        except BaseException:
                            try:
                                os.close(child_fd)
                            except OSError as cleanup_error:
                                if sys.exception() is not None:
                                    sys.exception().add_note(
                                        "retained bundle child descriptor cleanup failed: "
                                        f"{cleanup_error}"
                                    )
                            raise
                        directories.add(relative)
                        pending.append((relative_path, child_fd))
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        files.add(relative)
                        if info.st_size > MAX_GAME_RUNTIME_BUNDLE_FILE_BYTES:
                            _fail(
                                "game_runtime_bundle_limit",
                                f"runtime bundle file exceeds its limit: {relative}",
                            )
                    else:
                        _fail(
                            "game_runtime_bundle_tree_unsafe",
                            f"runtime bundle entry is special or hard-linked: {relative}",
                        )
                    if (
                        len(files) > MAX_GAME_RUNTIME_BUNDLE_FILES + 1
                        or len(directories) > MAX_GAME_RUNTIME_BUNDLE_DIRECTORIES
                    ):
                        _fail(
                            "game_runtime_bundle_limit",
                            "runtime bundle tree exceeds its node limits",
                        )
            finally:
                os.close(current_fd)
    except BaseException:
        primary = sys.exception()
        for _relative, descriptor in pending:
            try:
                os.close(descriptor)
            except OSError as cleanup_error:
                if primary is not None:
                    primary.add_note(
                        f"retained bundle pending descriptor cleanup failed: {cleanup_error}"
                    )
        raise
    return _PhysicalTree(
        root_state=_tree_state(root_info),
        files=frozenset(files),
        directories=frozenset(directories),
    )


def _capture_bundle_tree(
    root: Path,
    *,
    hook: _VerificationHook | None,
    retained_root_fd: int | None = None,
) -> tuple[dict[str, bytes], _PhysicalTree]:
    before = (
        _physical_tree(root)
        if retained_root_fd is None
        else _physical_tree_from_fd(retained_root_fd)
    )
    if hook is not None:
        hook("after_tree_snapshot", None)
    try:
        captured_prefixed = _capture_runtime_files(
            root,
            _verification_hook=hook,
            _retained_root_fd=retained_root_fd,
        )
    except RuntimeContractError as exc:
        _fail("game_runtime_bundle_tree_changed", str(exc))
    prefix = "gamepack_runtime/"
    if any(not path.startswith(prefix) for path in captured_prefixed):
        _fail(
            "game_runtime_bundle_tree_changed",
            "retained tree capture returned an invalid path",
        )
    captured = {path.removeprefix(prefix): payload for path, payload in captured_prefixed.items()}
    after = (
        _physical_tree(root)
        if retained_root_fd is None
        else _physical_tree_from_fd(retained_root_fd)
    )
    if before != after or set(captured) != set(before.files):
        _fail(
            "game_runtime_bundle_tree_changed",
            "runtime bundle tree changed during retained capture",
        )
    return captured, before


def _decode_canonical(
    files: Mapping[str, bytes],
    path: str,
    validator: Callable[[object], dict[str, Any]],
    serializer: Callable[[object], bytes],
) -> dict[str, Any]:
    payload = files.get(path)
    if payload is None:
        _fail("game_runtime_bundle_file_missing", f"runtime bundle is missing {path}")
    try:
        document = validator(_decode_creation_object(payload, Path(path)))
    except (
        CreationContractError,
        GamepackError,
        GenericAssetpackError,
        RuntimeContractError,
    ) as exc:
        _fail("game_runtime_bundle_contract_invalid", f"{path}: {exc}")
    if serializer(document) != payload:
        _fail(
            "game_runtime_bundle_contract_noncanonical",
            f"{path} is not the exact canonical serialization",
        )
    return document


def _verify_assetpack_bytes(
    files: Mapping[str, bytes],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    prefix = "assetpack/"
    assetpack_files = {
        path.removeprefix(prefix): payload
        for path, payload in files.items()
        if path.startswith(prefix)
    }
    manifest = _decode_canonical(
        assetpack_files,
        GENERIC_ASSETPACK_MANIFEST,
        validate_generic_assetpack_document,
        serialize_generic_assetpack,
    )
    inventory = {entry["path"]: entry for entry in manifest["inventory"]["files"]}
    if set(assetpack_files) != {GENERIC_ASSETPACK_MANIFEST, *inventory}:
        _fail(
            "game_runtime_bundle_assetpack_mismatch",
            "nested D3 assetpack files are not exact",
        )
    outputs = {
        output["runtime_path"]: output
        for asset in manifest["assets"]
        for output in asset["outputs"]
    }
    notices = {
        output["runtime_notice"]["path"]
        for asset in manifest["assets"]
        for output in asset["outputs"]
    }
    for path, record in inventory.items():
        payload = assetpack_files[path]
        if (
            len(payload) != record["size_bytes"]
            or hashlib.sha256(payload).hexdigest() != record["sha256"]
        ):
            _fail(
                "game_runtime_bundle_assetpack_mismatch",
                f"nested D3 file identity changed: {path}",
            )
        output = outputs.get(path)
        if output is not None:
            try:
                metadata = inspect_runtime_asset_bytes(
                    payload,
                    role=output["role"],
                    media_type=output["media_type"],
                    expectations=output["constraints"],
                )
            except GenericAssetProductionError as exc:
                _fail(
                    "game_runtime_bundle_assetpack_mismatch",
                    f"{path}: {exc}",
                )
            if metadata != output["metadata"]:
                _fail(
                    "game_runtime_bundle_assetpack_mismatch",
                    f"{path} media metadata differs from D3",
                )
        elif path in notices:
            try:
                payload.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                _fail(
                    "game_runtime_bundle_notice_mismatch",
                    f"{path} is not valid UTF-8: {exc}",
                )
        else:
            _fail(
                "game_runtime_bundle_assetpack_mismatch",
                f"{path} is not a runtime output or legal notice",
            )
    return manifest, assetpack_files


class VerifiedGameRuntimeBundle:
    """Immutable retained-byte snapshot from one integral bundle verification."""

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
        self._evidence = MappingProxyType(
            {
                "integrity": "valid",
                "state": "pre_execution",
                "release": "blocked",
                "supported": False,
                "bundle_id": manifest["bundle_id"],
                "content_hash": manifest["content_hash"],
            }
        )
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            _fail(
                "game_runtime_bundle_snapshot_closed",
                "verified runtime bundle snapshot is already closed",
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

    def read_bytes(self, relative_path: str) -> bytes:
        self._require_open()
        try:
            return self._files[relative_path]
        except KeyError:
            _fail(
                "game_runtime_bundle_file_missing",
                f"verified snapshot has no file {relative_path!r}",
            )

    def close(self) -> None:
        self._files.clear()
        self._closed = True

    def __enter__(self) -> VerifiedGameRuntimeBundle:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self.close()


def verify_game_runtime_bundle(
    root: str | Path,
    *,
    expected_content_hash: str | None = None,
    _verification_hook: _VerificationHook | None = None,
) -> VerifiedGameRuntimeBundle:
    """Integrally verify an exact runtime-only pre-execution bundle tree."""

    root_path = Path(os.path.abspath(os.fspath(root)))
    try:
        files, tree = _capture_bundle_tree(
            root_path,
            hook=_verification_hook,
        )
        manifest = _decode_canonical(
            files,
            GAME_RUNTIME_BUNDLE_MANIFEST,
            validate_game_runtime_bundle_document,
            serialize_game_runtime_bundle,
        )
        if expected_content_hash is not None:
            try:
                checked_expected = _sha256(
                    expected_content_hash,
                    "expected game runtime bundle hash",
                )
            except CreationContractError as exc:
                _fail("game_runtime_bundle_expected_hash_invalid", str(exc))
            if manifest["content_hash"] != checked_expected:
                _fail(
                    "game_runtime_bundle_expected_hash_mismatch",
                    "runtime bundle does not match the requested immutable hash",
                )
        expected_files = {
            GAME_RUNTIME_BUNDLE_MANIFEST,
            *(entry["path"] for entry in manifest["files"]),
        }
        if set(files) != expected_files:
            _fail(
                "game_runtime_bundle_tree_mismatch",
                "runtime bundle has missing or extra files",
            )
        expected_directories = _expected_directories(tuple(expected_files))
        if set(tree.directories) != expected_directories:
            _fail(
                "game_runtime_bundle_tree_mismatch",
                "runtime bundle has missing, extra, or empty directories",
            )
        for record in manifest["files"]:
            payload = files[record["path"]]
            if (
                len(payload) != record["size_bytes"]
                or hashlib.sha256(payload).hexdigest() != record["sha256"]
            ):
                _fail(
                    "game_runtime_bundle_file_hash_mismatch",
                    f"runtime bundle file identity changed: {record['path']}",
                )

        gamepack = _decode_canonical(
            files,
            _CONTRACT_PATHS["gamepack"],
            validate_gamepack_document,
            serialize_gamepack,
        )
        snapshot = _decode_canonical(
            files,
            _CONTRACT_PATHS["runtime_snapshot"],
            validate_runtime_snapshot_document,
            serialize_runtime_snapshot,
        )
        registry = _decode_canonical(
            files,
            _CONTRACT_PATHS["runtime_adapter_registry"],
            lambda value: validate_runtime_adapter_registry_document(
                value,
                snapshot=snapshot,
            ),
            serialize_runtime_adapter_registry,
        )
        trusted_runtime_files = dict(
            capture_trusted_runtime_snapshot_files(
                snapshot=snapshot,
                registry=registry,
            )
        )
        bundled_runtime_files = {
            path.removeprefix("runtime/snapshot-tree/"): payload
            for path, payload in files.items()
            if path.startswith("runtime/snapshot-tree/")
        }
        if bundled_runtime_files != trusted_runtime_files:
            _fail(
                "game_runtime_bundle_runtime_tree_mismatch",
                "bundled runtime snapshot bytes are not the trusted installed kernel",
            )
        adapter_identity = manifest["contracts"]["runtime_adapter"]
        adapter = _adapter_from_registry(registry, adapter_identity)
        descriptor_path = adapter_identity["path"]
        descriptor = _decode_canonical(
            files,
            descriptor_path,
            validate_runtime_adapter_document,
            lambda value: canonical_json_bytes(validate_runtime_adapter_document(value)),
        )
        if descriptor != adapter:
            _fail(
                "game_runtime_bundle_adapter_mismatch",
                "selected descriptor differs from its registry source of truth",
            )
        assetpack, assetpack_files = _verify_assetpack_bytes(files)
        allowed_payload_files = {
            *_CONTRACT_PATHS.values(),
            _CODE_LICENSE_PATH,
            *(f"assetpack/{path}" for path in assetpack_files),
            *(f"runtime/snapshot-tree/{path}" for path in trusted_runtime_files),
        }
        manifest_payload_files = {entry["path"] for entry in manifest["files"]}
        if manifest_payload_files != allowed_payload_files or set(files) != {
            GAME_RUNTIME_BUNDLE_MANIFEST,
            *allowed_payload_files,
        }:
            _fail(
                "game_runtime_bundle_tree_mismatch",
                "runtime bundle is not the exact contract, D3, trusted runtime, and legal closure",
            )
        composition = _decode_canonical(
            files,
            _CONTRACT_PATHS["runtime_composition"],
            validate_game_runtime_composition_document,
            serialize_game_runtime_composition,
        )
        support = _decode_canonical(
            files,
            _CONTRACT_PATHS["runtime_support_report"],
            validate_runtime_support_report_document,
            serialize_runtime_support_report,
        )
        expected_bindings = _derive_transfer_bindings(
            gamepack,
            assetpack,
            adapter,
        )
        composition_bindings = [
            {key: binding[key] for key in binding if key != "bundle_path"}
            for binding in expected_bindings
        ]
        if composition["bindings"] != composition_bindings:
            _fail(
                "game_runtime_bundle_composition_mismatch",
                "composition bindings do not rebuild from exact runtime inputs",
            )
        identity_checks = (
            (
                manifest["contracts"]["gamepack"],
                _identity(
                    gamepack,
                    id_field="game",
                    path=_CONTRACT_PATHS["gamepack"],
                ),
            ),
            (
                manifest["contracts"]["runtime_snapshot"],
                _identity(
                    snapshot,
                    id_field="snapshot_id",
                    path=_CONTRACT_PATHS["runtime_snapshot"],
                ),
            ),
            (
                manifest["contracts"]["runtime_adapter_registry"],
                _identity(
                    registry,
                    id_field="registry_id",
                    path=_CONTRACT_PATHS["runtime_adapter_registry"],
                ),
            ),
            (
                manifest["contracts"]["runtime_composition"],
                _identity(
                    composition,
                    id_field="composition_id",
                    path=_CONTRACT_PATHS["runtime_composition"],
                ),
            ),
            (
                manifest["contracts"]["runtime_support_report"],
                _identity(
                    support,
                    id_field="report_id",
                    path=_CONTRACT_PATHS["runtime_support_report"],
                ),
            ),
        )
        for actual, expected in identity_checks:
            if actual != expected:
                _fail(
                    "game_runtime_bundle_contract_binding_mismatch",
                    "manifest contract identity does not match bundled bytes",
                )
        expected_assetpack_lineage = {
            **_lineage_identity(assetpack, id_field="assetpack_id"),
            "root_hash": _assetpack_root_hash(assetpack_files),
            "inventory_hash": assetpack["inventory"]["content_hash"],
        }
        if (
            composition["gamepack"] != _lineage_identity(gamepack, id_field="game")
            or assetpack["gamepack"] != _lineage_identity(gamepack, id_field="game")
            or composition["asset_inventory"] != assetpack["asset_inventory"]
            or composition["assetpack"] != expected_assetpack_lineage
            or composition["adapter"] != _lineage_identity(adapter, id_field="adapter_id")
            or composition["registry"]
            != _lineage_identity(
                registry,
                id_field="registry_id",
            )
            or composition["runtime_snapshot"]
            != _lineage_identity(snapshot, id_field="snapshot_id")
        ):
            _fail(
                "game_runtime_bundle_composition_mismatch",
                "composition does not bind the exact bundled lineage",
            )
        expected_support = build_runtime_support_report(
            composition,
            gamepack=gamepack,
            registry=registry,
            snapshot=snapshot,
            evidence=[],
        )
        if support != expected_support:
            _fail(
                "game_runtime_bundle_support_overclaim",
                "support report is not the exact evidence-free blocked report",
            )
        if (
            support["dimensions"]["release"] != "blocked"
            or support["dimensions"]["packaging"] != "unverified"
            or support["supported"] is not False
            or support["evidence"]
        ):
            _fail(
                "game_runtime_bundle_support_overclaim",
                "runtime bundle support overclaims pre-execution evidence",
            )
        if manifest["bindings"] != expected_bindings:
            _fail(
                "game_runtime_bundle_binding_mismatch",
                "manifest bindings do not rebuild from exact runtime inputs",
            )
        if (
            manifest["assetpack"]["manifest"]["content_hash"] != assetpack["content_hash"]
            or manifest["assetpack"]["root_hash"] != _assetpack_root_hash(assetpack_files)
            or manifest["assetpack"]["inventory_hash"] != assetpack["inventory"]["content_hash"]
        ):
            _fail(
                "game_runtime_bundle_assetpack_mismatch",
                "manifest D3 identity does not match nested retained bytes",
            )
        if files[_CODE_LICENSE_PATH] != _code_license_bytes():
            _fail(
                "game_runtime_bundle_license_untrusted",
                "bundle license bytes are not the exact code-owned MIT license",
            )
        expected_notices = sorted(
            (
                {
                    "path": f"assetpack/{output['runtime_notice']['path']}",
                    "sha256": output["runtime_notice"]["sha256"],
                    "size_bytes": output["runtime_notice"]["size_bytes"],
                }
                for asset in assetpack["assets"]
                for output in asset["outputs"]
            ),
            key=lambda item: item["path"].encode("utf-8"),
        )
        if manifest["legal"]["asset_notices"] != expected_notices:
            _fail(
                "game_runtime_bundle_notice_mismatch",
                "bundle legal notice closure is not exact",
            )
        expected_notice_paths = {item["path"] for item in expected_notices}
        if expected_notice_paths != {
            f"assetpack/{output['runtime_notice']['path']}"
            for asset in assetpack["assets"]
            for output in asset["outputs"]
        }:
            _fail(
                "game_runtime_bundle_notice_mismatch",
                "bundle legal notice paths are not unique",
            )
        return VerifiedGameRuntimeBundle(
            root_path,
            manifest,
            files,
            (tree.root_state[0], tree.root_state[1]),
        )
    except GameRuntimeBundleError:
        raise
    except (
        CreationContractError,
        DirectoryPublishError,
        GamepackError,
        GenericAssetProductionError,
        GenericAssetpackError,
        RuntimeContractError,
        OSError,
    ) as exc:
        _fail("game_runtime_bundle_verification_failed", str(exc))


def _journal_path(destination: Path) -> Path:
    return destination.parent / (f".{destination.name}.game-runtime-bundle.journal.json")


def _lock_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.game-runtime-bundle.lock"


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
                "game_runtime_bundle_lock_changed",
                "runtime bundle publication lock binding changed",
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
                "game_runtime_bundle_lock_unsafe",
                "runtime bundle publication lock is unsafe",
            )
        if opened.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
            fsync_directory(path.parent, context="runtime bundle lock parent")
        elif opened.st_size != 1:
            _fail(
                "game_runtime_bundle_lock_unsafe",
                "runtime bundle publication lock contents are invalid",
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, 1) != b"\0":
            _fail(
                "game_runtime_bundle_lock_unsafe",
                "runtime bundle publication lock contents are invalid",
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
                "game_runtime_bundle_publication_busy",
                f"another runtime bundle publication is in progress: {exc}",
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
    except GameRuntimeBundleError:
        raise
    except (DirectoryPublishError, OSError) as exc:
        _fail("game_runtime_bundle_lock_failed", str(exc))
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _identity_document(identity: DirectoryIdentity) -> dict[str, int | str]:
    try:
        return encode_publication_identity(identity, windows=os.name == "nt")
    except PublicationIdentityCodecError as exc:
        _fail("game_runtime_bundle_journal_invalid", str(exc))


def _identity_from_document(value: object) -> DirectoryIdentity:
    context = "runtime bundle publication journal.stage_identity"
    try:
        return decode_publication_identity(value, context=context)
    except PublicationIdentityCodecError as exc:
        _fail("game_runtime_bundle_journal_invalid", str(exc))


def _journal_document(
    *,
    operation_id: str,
    state: str,
    stage: Path,
    destination: Path,
    stage_identity: DirectoryIdentity | None,
    manifest: Mapping[str, Any],
    manifest_payload: bytes,
) -> dict[str, object]:
    contracts = manifest["contracts"]
    assetpack = manifest["assetpack"]
    runtime_tree = manifest["runtime_snapshot_tree"]
    return {
        "format": GAME_RUNTIME_BUNDLE_JOURNAL_FORMAT,
        "format_version": GAME_RUNTIME_BUNDLE_JOURNAL_VERSION,
        "operation_id": operation_id,
        "state": state,
        "stage_name": stage.name,
        "destination_name": destination.name,
        "stage_identity": (None if stage_identity is None else _identity_document(stage_identity)),
        "bundle_id": manifest["bundle_id"],
        "content_hash": manifest["content_hash"],
        "tree_hash": manifest["tree_hash"],
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
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "manifest_size_bytes": len(manifest_payload),
    }


def _validate_journal(value: object, destination: Path) -> dict[str, Any]:
    try:
        journal = _object(value, "runtime bundle publication journal")
        _exact_keys(journal, _JOURNAL_FIELDS, "runtime bundle publication journal")
        if (
            journal.get("format") != GAME_RUNTIME_BUNDLE_JOURNAL_FORMAT
            or journal.get("format_version") != GAME_RUNTIME_BUNDLE_JOURNAL_VERSION
        ):
            _fail("game_runtime_bundle_journal_invalid", "unknown journal format")
        operation_id = journal.get("operation_id")
        if not isinstance(operation_id, str) or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
            _fail("game_runtime_bundle_journal_invalid", "operation_id is invalid")
        state = journal.get("state")
        if state not in {"intent", "copying", "ready"}:
            _fail("game_runtime_bundle_journal_invalid", "journal state is invalid")
        if journal.get("destination_name") != destination.name:
            _fail(
                "game_runtime_bundle_journal_invalid",
                "journal destination name is invalid",
            )
        stage_name = journal.get("stage_name")
        if (
            not isinstance(stage_name, str)
            or not is_portable_path_component(stage_name)
            or (
                stage_name != destination.name
                and not stage_name.startswith(f".{destination.name}.game-runtime-bundle-")
            )
        ):
            _fail("game_runtime_bundle_journal_invalid", "journal stage name is invalid")
        if state == "intent":
            if journal.get("stage_identity") is not None:
                _fail(
                    "game_runtime_bundle_journal_invalid",
                    "intent journal cannot claim a stage identity",
                )
        else:
            _identity_from_document(journal.get("stage_identity"))
        bundle_id = journal.get("bundle_id")
        if (
            not isinstance(bundle_id, str)
            or re.fullmatch(r"game_runtime_bundle_[0-9a-f]{48}", bundle_id) is None
        ):
            _fail(
                "game_runtime_bundle_journal_invalid",
                "journal bundle_id is invalid",
            )
        for field in (
            "content_hash",
            "tree_hash",
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
            "manifest_sha256",
        ):
            _sha256(journal.get(field), f"journal.{field}")
        size = _integer(
            journal.get("manifest_size_bytes"),
            "journal.manifest_size_bytes",
            minimum=1,
        )
        if size > MAX_GAME_RUNTIME_BUNDLE_MANIFEST_BYTES:
            _fail(
                "game_runtime_bundle_journal_invalid",
                "journal manifest size exceeds its limit",
            )
        return journal
    except GameRuntimeBundleError:
        raise
    except CreationContractError as exc:
        _fail("game_runtime_bundle_journal_invalid", str(exc))


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
            max_record_bytes=MAX_GAME_RUNTIME_BUNDLE_MANIFEST_BYTES,
            max_file_bytes=MAX_GAME_RUNTIME_BUNDLE_JOURNAL_BYTES,
        )
    except DirectoryPublishError as exc:
        _fail("game_runtime_bundle_journal_invalid", str(exc))
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
            _fail("game_runtime_bundle_journal_invalid", str(exc))
        if canonical_json_bytes(document) != payload:
            _fail(
                "game_runtime_bundle_journal_invalid",
                "journal record is not canonical",
            )
        documents.append(document)
    if tuple(documents) != _expected_journal_history(documents[-1]):
        _fail(
            "game_runtime_bundle_journal_invalid",
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
                    max_record_bytes=MAX_GAME_RUNTIME_BUNDLE_MANIFEST_BYTES,
                )
            except FileExistsError:
                _fail(
                    "game_runtime_bundle_recovery_required",
                    "an incomplete runtime bundle publication journal exists",
                    recovery_evidence=retained_recovery_evidence(journal_path=path),
                )
            fsync_directory(path.parent, context="runtime bundle journal parent")
            lock.require_binding()
            return identity
        if expected_document is None or expected_identity is None:
            _fail(
                "game_runtime_bundle_journal_invalid",
                "journal transition lacks its exact prior identity",
            )
        loaded = _read_journal_state(path, path.parent / document["destination_name"])
        expected_payload = canonical_json_bytes(expected_document)
        if (
            loaded is None
            or loaded[0] != expected_document
            or loaded[1] != expected_identity
            or loaded[2] != expected_payload
        ):
            _fail(
                "game_runtime_bundle_journal_changed",
                "journal changed before its append-only transition",
            )
        identity = append_append_only_journal(
            path,
            expected_identity=expected_identity,
            expected_payload=expected_payload,
            expected_history=_history_payloads(expected_document),
            updated_payload=payload,
            max_record_bytes=MAX_GAME_RUNTIME_BUNDLE_MANIFEST_BYTES,
            max_file_bytes=MAX_GAME_RUNTIME_BUNDLE_JOURNAL_BYTES,
            repair_partial_tail=True,
        )
        lock.require_binding()
        return identity
    except GameRuntimeBundleError:
        raise
    except DirectoryPublishError as exc:
        _fail("game_runtime_bundle_journal_failed", str(exc))


def _remove_journal(
    path: Path,
    document: dict[str, Any],
    identity: DirectoryIdentity,
    *,
    lock: _DestinationLock,
) -> None:
    try:
        lock.require_binding()
        retained_journal = remove_append_only_journal(
            path,
            expected_identity=identity,
            expected_payload=canonical_json_bytes(document),
            expected_history=_history_payloads(document),
            max_record_bytes=MAX_GAME_RUNTIME_BUNDLE_MANIFEST_BYTES,
            max_file_bytes=MAX_GAME_RUNTIME_BUNDLE_JOURNAL_BYTES,
        )
        if sys.platform.startswith("linux") and os.name == "posix":
            expected_retained = retained_journal_evidence_path(path, identity)
            if retained_journal != expected_retained:
                _fail(
                    "game_runtime_bundle_journal_indeterminate",
                    "terminal journal evidence locator changed",
                )
        elif retained_journal is not None:
            _fail(
                "game_runtime_bundle_journal_indeterminate",
                "unexpected terminal journal evidence was returned",
            )
        lock.require_binding()
    except DirectoryPublishIndeterminateError as exc:
        _fail("game_runtime_bundle_journal_indeterminate", str(exc))
    except DirectoryPublishError as exc:
        _fail("game_runtime_bundle_journal_failed", str(exc))


def _optional_directory_identity(path: Path) -> DirectoryIdentity | None:
    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("game_runtime_bundle_directory_invalid", str(exc))
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        _fail(
            "game_runtime_bundle_directory_invalid",
            f"{path} must be a real directory",
        )
    return file_identity(info)


def _journal_matches_verified(
    journal: Mapping[str, object],
    verified: VerifiedGameRuntimeBundle,
) -> None:
    _journal_matches_manifest(
        journal,
        verified.manifest,
        verified.read_bytes(GAME_RUNTIME_BUNDLE_MANIFEST),
    )


def _journal_matches_manifest(
    journal: Mapping[str, object],
    manifest: Mapping[str, object],
    manifest_payload: bytes,
) -> None:
    expected = _journal_document(
        operation_id=str(journal["operation_id"]),
        state=str(journal["state"]),
        stage=Path(str(journal["stage_name"])),
        destination=Path(str(journal["destination_name"])),
        stage_identity=(
            None
            if journal["stage_identity"] is None
            else _identity_from_document(journal["stage_identity"])
        ),
        manifest=manifest,
        manifest_payload=manifest_payload,
    )
    for field in _JOURNAL_FIELDS:
        if journal[field] != expected[field]:
            _fail(
                "game_runtime_bundle_recovery_mismatch",
                f"journal {field} does not match the exact runtime bundle",
            )


def _require_visible_publication(
    destination: Path,
    stage: Path,
    *,
    expected_identity: DirectoryIdentity,
    expected_content_hash: str,
    journal_path: Path,
    journal: Mapping[str, object],
    journal_identity: DirectoryIdentity,
    lock: _DestinationLock,
    journal_present: bool,
) -> VerifiedGameRuntimeBundle:
    lock.require_binding()
    if _optional_directory_identity(stage) is not None:
        _fail(
            "game_runtime_bundle_publication_indeterminate",
            "private stage name reappeared after publication",
        )
    if _optional_directory_identity(destination) != expected_identity:
        _fail(
            "game_runtime_bundle_publication_indeterminate",
            "visible destination no longer resolves to the retained published root",
        )
    loaded = _read_journal_state(journal_path, destination)
    if journal_present:
        expected_payload = canonical_json_bytes(journal)
        if (
            loaded is None
            or loaded[0] != journal
            or loaded[1] != journal_identity
            or loaded[2] != expected_payload
            or loaded[3]
        ):
            _fail(
                "game_runtime_bundle_publication_indeterminate",
                "publication journal identity or history changed before finalization",
            )
    elif loaded is not None:
        _fail(
            "game_runtime_bundle_publication_indeterminate",
            "publication journal reappeared after identity-bound deletion",
        )
    verified = verify_game_runtime_bundle(
        destination,
        expected_content_hash=expected_content_hash,
    )
    try:
        if verified.root_identity != expected_identity:
            _fail(
                "game_runtime_bundle_publication_indeterminate",
                "verified destination differs from the retained published root",
            )
        if journal_present:
            _journal_matches_verified(journal, verified)
        lock.require_binding()
        if (
            _optional_directory_identity(stage) is not None
            or _optional_directory_identity(destination) != expected_identity
        ):
            _fail(
                "game_runtime_bundle_publication_indeterminate",
                "publication namespace changed during final integral verification",
            )
    except BaseException:
        verified.close()
        raise
    return verified


def _verify_owned_stage_subset(
    stage: Path,
    journal: Mapping[str, object],
    *,
    retained_root_fd: int | None = None,
) -> None:
    files, tree = _capture_bundle_tree(
        stage,
        hook=None,
        retained_root_fd=retained_root_fd,
    )
    expected_identity = _identity_from_document(journal["stage_identity"])
    if tree.root_state[:2] != expected_identity:
        _fail(
            "game_runtime_bundle_rollback_ambiguous",
            "owned stage identity changed during subset verification",
        )
    manifest_payload = files.get(GAME_RUNTIME_BUNDLE_MANIFEST)
    if manifest_payload is None:
        _fail(
            "game_runtime_bundle_rollback_ambiguous",
            "owned stage has no manifest binding for its retained files",
        )
    manifest = _decode_canonical(
        files,
        GAME_RUNTIME_BUNDLE_MANIFEST,
        validate_game_runtime_bundle_document,
        serialize_game_runtime_bundle,
    )
    _journal_matches_manifest(journal, manifest, manifest_payload)
    inventory = {entry["path"]: entry for entry in manifest["files"]}
    allowed_files = {GAME_RUNTIME_BUNDLE_MANIFEST, *inventory}
    if not set(files).issubset(allowed_files):
        _fail(
            "game_runtime_bundle_rollback_ambiguous",
            "owned stage contains a foreign file",
        )
    if not set(tree.directories).issubset(_expected_directories(tuple(allowed_files))):
        _fail(
            "game_runtime_bundle_rollback_ambiguous",
            "owned stage contains a foreign directory",
        )
    for path, payload in files.items():
        if path == GAME_RUNTIME_BUNDLE_MANIFEST:
            continue
        record = inventory[path]
        if (
            len(payload) != record["size_bytes"]
            or hashlib.sha256(payload).hexdigest() != record["sha256"]
        ):
            _fail(
                "game_runtime_bundle_rollback_ambiguous",
                f"owned stage file differs from its manifest: {path}",
            )


def _remove_empty_owned_stage(
    stage: Path,
    expected_identity: DirectoryIdentity,
    journal_path: Path,
    journal_identity: DirectoryIdentity,
) -> None:
    try:
        remove_verified_empty_directory(stage, expected_identity)
    except DirectoryPublishRecoveryRequiredError as exc:
        _fail(
            "game_runtime_bundle_recovery_required",
            "automatic cleanup is unavailable; the exact owned stage and publication "
            f"journal were retained for explicit recovery: {exc}",
            recovery_evidence=retained_recovery_evidence(
                stage_path=stage,
                stage_identity=expected_identity,
                journal_path=journal_path,
                journal_identity=journal_identity,
            ),
        )
    except DirectoryPublishIndeterminateError as exc:
        _fail("game_runtime_bundle_recovery_indeterminate", str(exc))
    except DirectoryPublishError as exc:
        _fail("game_runtime_bundle_recovery_failed", str(exc))


def _recover_locked(
    destination: Path,
    lock: _DestinationLock,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> VerifiedGameRuntimeBundle | None:
    _require_expected_parent_identity(destination.parent, expected_parent_identity)
    journal_path = _journal_path(destination)
    loaded = _read_journal_state(journal_path, destination)
    if loaded is None:
        if _optional_directory_identity(destination) is None:
            return None
        return verify_game_runtime_bundle(destination)
    journal, journal_identity, _payload, partial_tail = loaded
    if partial_tail and journal["state"] != "copying":
        _fail(
            "game_runtime_bundle_journal_invalid",
            "journal has a torn tail outside the recoverable copying transition",
        )
    stage = destination.parent / journal["stage_name"]
    if journal["state"] == "intent":
        if (
            _optional_directory_identity(stage) is not None
            or _optional_directory_identity(destination) is not None
        ):
            _fail(
                "game_runtime_bundle_recovery_ambiguous",
                "intent journal has an unbound stage or destination",
            )
        _remove_journal(journal_path, journal, journal_identity, lock=lock)
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
            "game_runtime_bundle_recovery_ambiguous",
            "stage/destination identities are missing, changed, or conflicting",
        )
    if journal["state"] == "copying" and source == stage:
        stage_tree = _physical_tree(stage)
        if not stage_tree.files and not stage_tree.directories:
            if partial_tail:
                _fail(
                    "game_runtime_bundle_recovery_ambiguous",
                    "copying journal tail cannot be repaired without a complete stage",
                )
            lock.require_binding()
            _remove_empty_owned_stage(
                stage,
                expected_identity,
                journal_path,
                journal_identity,
            )
            lock.require_binding()
            _remove_journal(
                journal_path,
                journal,
                journal_identity,
                lock=lock,
            )
            return None
        if GAME_RUNTIME_BUNDLE_MANIFEST not in stage_tree.files:
            _fail(
                "game_runtime_bundle_recovery_ambiguous",
                "copying stage contains unbound entries without a bundle manifest",
            )
    verified = verify_game_runtime_bundle(
        source,
        expected_content_hash=journal["content_hash"],
    )
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
        recovered: VerifiedGameRuntimeBundle | None = None
        try:
            with publish_directory_noreplace(
                stage,
                destination,
                expected_source_identity=expected_identity,
                expected_parent_identity=expected_parent_identity,
            ) as published_identity:
                if published_identity != expected_identity:
                    _fail(
                        "game_runtime_bundle_publication_identity_mismatch",
                        "recovered publication identity changed",
                    )
                initially_verified = verify_game_runtime_bundle(
                    destination,
                    expected_content_hash=journal["content_hash"],
                )
                try:
                    _journal_matches_verified(journal, initially_verified)
                finally:
                    initially_verified.close()
                checked_before_removal = _require_visible_publication(
                    destination,
                    stage,
                    expected_identity=expected_identity,
                    expected_content_hash=journal["content_hash"],
                    journal_path=journal_path,
                    journal=journal,
                    journal_identity=journal_identity,
                    lock=lock,
                    journal_present=True,
                )
                checked_before_removal.close()
                _remove_journal(
                    journal_path,
                    journal,
                    journal_identity,
                    lock=lock,
                )
                recovered = _require_visible_publication(
                    destination,
                    stage,
                    expected_identity=expected_identity,
                    expected_content_hash=journal["content_hash"],
                    journal_path=journal_path,
                    journal=journal,
                    journal_identity=journal_identity,
                    lock=lock,
                    journal_present=False,
                )
        except DirectoryPublishIndeterminateError as exc:
            if recovered is not None:
                recovered.close()
            _fail("game_runtime_bundle_publication_indeterminate", str(exc))
        except (DirectoryPublishError, FileExistsError) as exc:
            if recovered is not None:
                recovered.close()
            _fail("game_runtime_bundle_recovery_failed", str(exc))
    else:
        checked_before_removal = _require_visible_publication(
            destination,
            stage,
            expected_identity=expected_identity,
            expected_content_hash=journal["content_hash"],
            journal_path=journal_path,
            journal=journal,
            journal_identity=journal_identity,
            lock=lock,
            journal_present=True,
        )
        checked_before_removal.close()
        _remove_journal(journal_path, journal, journal_identity, lock=lock)
        recovered = _require_visible_publication(
            destination,
            stage,
            expected_identity=expected_identity,
            expected_content_hash=journal["content_hash"],
            journal_path=journal_path,
            journal=journal,
            journal_identity=journal_identity,
            lock=lock,
            journal_present=False,
        )
    if recovered is None:
        _fail(
            "game_runtime_bundle_recovery_indeterminate",
            "recovery finalization produced no retained verified snapshot",
        )
    return recovered


def recover_game_runtime_bundle(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> VerifiedGameRuntimeBundle | None:
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    _require_expected_parent_identity(destination_path.parent, expected_parent_identity)
    with _destination_lock(destination_path) as lock:
        return _recover_locked(
            destination_path,
            lock,
            expected_parent_identity=expected_parent_identity,
        )


def rollback_game_runtime_bundle(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> dict[str, object]:
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    _require_expected_parent_identity(destination_path.parent, expected_parent_identity)
    with _destination_lock(destination_path) as lock:
        _require_expected_parent_identity(destination_path.parent, expected_parent_identity)
        journal_path = _journal_path(destination_path)
        loaded = _read_journal_state(journal_path, destination_path)
        if loaded is None:
            return {"status": "no_operation"}
        journal, journal_identity, _payload, partial_tail = loaded
        if partial_tail:
            _fail(
                "game_runtime_bundle_rollback_ambiguous",
                "rollback preserves a journal with a torn transition",
            )
        stage = destination_path.parent / journal["stage_name"]
        if _optional_directory_identity(destination_path) is not None:
            _fail(
                "game_runtime_bundle_rollback_committed",
                "rollback never removes a visible destination",
            )
        if journal["state"] == "intent":
            if _optional_directory_identity(stage) is not None:
                _fail(
                    "game_runtime_bundle_rollback_ambiguous",
                    "intent journal has an unbound stage",
                )
        else:
            expected_identity = _identity_from_document(journal["stage_identity"])
            if _optional_directory_identity(stage) != expected_identity:
                _fail(
                    "game_runtime_bundle_rollback_ambiguous",
                    "rollback stage identity changed",
                )

            stage_tree = _physical_tree(stage)
            if (
                journal["state"] == "copying"
                and not stage_tree.files
                and not stage_tree.directories
            ):
                try:
                    remove_verified_empty_directory(stage, expected_identity)
                except DirectoryPublishRecoveryRequiredError as exc:
                    _fail(
                        "game_runtime_bundle_rollback_recovery_required",
                        "automatic rollback cleanup is unavailable; the exact owned "
                        f"stage and publication journal were retained: {exc}",
                        recovery_evidence=retained_recovery_evidence(
                            stage_path=stage,
                            stage_identity=expected_identity,
                            journal_path=journal_path,
                            journal_identity=journal_identity,
                        ),
                    )
                except DirectoryPublishIndeterminateError as exc:
                    _fail("game_runtime_bundle_rollback_indeterminate", str(exc))
                except DirectoryPublishError as exc:
                    _fail("game_runtime_bundle_rollback_failed", str(exc))
            else:
                if (
                    journal["state"] == "copying"
                    and GAME_RUNTIME_BUNDLE_MANIFEST not in stage_tree.files
                ):
                    _fail(
                        "game_runtime_bundle_rollback_ambiguous",
                        "copying stage contains unbound entries without a bundle manifest",
                    )

                def verify_owned(
                    stage_path: Path,
                    retained_root_fd: int | None,
                ) -> None:
                    _verify_owned_stage_subset(
                        stage_path,
                        journal,
                        retained_root_fd=retained_root_fd,
                    )

                try:
                    quarantine_and_remove_verified_directory(
                        stage,
                        expected_identity,
                        verify_retained=verify_owned,
                    )
                except DirectoryPublishRecoveryRequiredError as exc:
                    _fail(
                        "game_runtime_bundle_rollback_recovery_required",
                        "automatic rollback cleanup is unavailable; the exact owned "
                        f"stage and publication journal were retained: {exc}",
                        recovery_evidence=retained_recovery_evidence(
                            stage_path=stage,
                            stage_identity=expected_identity,
                            journal_path=journal_path,
                            journal_identity=journal_identity,
                        ),
                    )
                except DirectoryPublishIndeterminateError as exc:
                    _fail("game_runtime_bundle_rollback_indeterminate", str(exc))
                except DirectoryPublishError as exc:
                    _fail("game_runtime_bundle_rollback_failed", str(exc))
        _remove_journal(journal_path, journal, journal_identity, lock=lock)
        return {
            "status": "rolled_back",
            "operation_id": journal["operation_id"],
            "content_hash": journal["content_hash"],
        }


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
            "game_runtime_bundle_parent_identity_invalid",
            "expected runtime bundle parent identity is invalid",
        )
    return value


def _require_expected_parent_identity(
    parent: Path,
    expected: DirectoryIdentity | None,
) -> None:
    checked = _checked_parent_identity(expected)
    if checked is None:
        return
    try:
        info = path_file_stat(parent)
    except OSError as exc:
        _fail(
            "game_runtime_bundle_parent_identity_mismatch",
            f"runtime bundle parent authority is unavailable: {exc}",
        )
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode) or file_identity(info) != checked:
        _fail(
            "game_runtime_bundle_parent_identity_mismatch",
            "runtime bundle parent identity differs from its retained authority",
        )


def _validate_destination(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> Path:
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    if (
        not is_portable_path_component(destination_path.name)
        or destination_path.name.startswith(".")
        or len(destination_path.name.encode("utf-8")) > 160
    ):
        _fail(
            "game_runtime_bundle_destination_invalid",
            "destination name is not portable",
        )
    if not destination_path.parent.exists():
        _fail(
            "game_runtime_bundle_destination_invalid",
            "destination parent must already exist",
        )
    lexical_issues = validate_lexical_directory_root(destination_path.parent)
    if lexical_issues:
        _fail(
            "game_runtime_bundle_destination_invalid",
            f"destination parent is unsafe: {', '.join(lexical_issues)}",
        )
    _require_expected_parent_identity(destination_path.parent, expected_parent_identity)
    return destination_path


def _publish_game_runtime_bundle(
    destination: str | Path,
    *,
    manifest: Mapping[str, Any],
    payload_files: Mapping[str, bytes],
    expected_parent_identity: DirectoryIdentity | None = None,
    _publication_hook: _PublicationHook | None = None,
) -> VerifiedGameRuntimeBundle:
    """Stage, verify and exclusively publish one prepared pre-execution bundle."""

    if not ((sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt"):
        _fail(
            "game_runtime_bundle_platform_unsupported",
            "runtime bundle publication supports only Linux and Windows",
        )
    checked_parent = _checked_parent_identity(expected_parent_identity)
    manifest = validate_game_runtime_bundle_document(manifest)
    files = {GAME_RUNTIME_BUNDLE_MANIFEST: serialize_game_runtime_bundle(manifest)}
    files.update(payload_files)
    destination_path = _validate_destination(
        destination,
        expected_parent_identity=checked_parent,
    )
    with _destination_lock(destination_path) as lock:
        _require_expected_parent_identity(destination_path.parent, checked_parent)
        if _publication_hook is not None:
            _publication_hook("after_lock_acquired", None)
        _require_expected_parent_identity(destination_path.parent, checked_parent)
        recovered = _recover_locked(
            destination_path,
            lock,
            expected_parent_identity=checked_parent,
        )
        if recovered is not None:
            if recovered.manifest["content_hash"] == manifest["content_hash"]:
                return recovered
            recovered.close()
            _fail(
                "game_runtime_bundle_destination_exists",
                "destination contains a different immutable runtime bundle",
            )
        try:
            destination_path = assert_new_repository_target(
                destination_path,
                repository_type="generic game runtime bundle",
            )
        except RepositoryBoundaryError as exc:
            if destination_path.exists() or destination_path.is_symlink():
                _fail("game_runtime_bundle_destination_exists", str(exc))
            _fail("game_runtime_bundle_destination_invalid", str(exc))

        operation_id = uuid.uuid4().hex
        stage = destination_path.parent / (
            f".{destination_path.name}.game-runtime-bundle-{operation_id}"
        )
        journal_path = _journal_path(destination_path)
        journal_identity: DirectoryIdentity | None = None
        journal: dict[str, Any] | None = None
        stage_identity: DirectoryIdentity | None = None
        published: VerifiedGameRuntimeBundle | None = None
        try:
            intent = _journal_document(
                operation_id=operation_id,
                state="intent",
                stage=stage,
                destination=destination_path,
                stage_identity=None,
                manifest=manifest,
                manifest_payload=files[GAME_RUNTIME_BUNDLE_MANIFEST],
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
                require_guard=lock.require_binding,
                hook=_publication_hook,
            ) as writer:
                stage_identity = writer.identity
                copying = _journal_document(
                    operation_id=operation_id,
                    state="copying",
                    stage=stage,
                    destination=destination_path,
                    stage_identity=stage_identity,
                    manifest=manifest,
                    manifest_payload=files[GAME_RUNTIME_BUNDLE_MANIFEST],
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
                    GAME_RUNTIME_BUNDLE_MANIFEST,
                    *sorted(
                        (
                            relative
                            for relative in files
                            if relative != GAME_RUNTIME_BUNDLE_MANIFEST
                        ),
                        key=lambda item: item.encode("utf-8"),
                    ),
                )
                for relative in ordered_files:
                    writer.write_file(relative, files[relative])
                writer.fsync()
                verified_stage = verify_game_runtime_bundle(
                    stage,
                    expected_content_hash=manifest["content_hash"],
                )
                try:
                    _journal_matches_verified(journal, verified_stage)
                    writer.require_binding()
                finally:
                    verified_stage.close()
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
            _require_expected_parent_identity(destination_path.parent, checked_parent)
            finalized: VerifiedGameRuntimeBundle | None = None
            try:
                with publish_directory_noreplace(
                    stage,
                    destination_path,
                    expected_source_identity=stage_identity,
                    expected_parent_identity=checked_parent,
                ) as published_identity:
                    if published_identity != stage_identity:
                        _fail(
                            "game_runtime_bundle_publication_identity_mismatch",
                            "published directory identity changed",
                        )
                    fsync_directory(
                        destination_path.parent,
                        context="published runtime bundle parent",
                    )
                    _require_expected_parent_identity(destination_path.parent, checked_parent)
                    published = verify_game_runtime_bundle(
                        destination_path,
                        expected_content_hash=manifest["content_hash"],
                    )
                    try:
                        _journal_matches_verified(journal, published)
                    finally:
                        published.close()
                        published = None
                    if _publication_hook is not None:
                        _publication_hook("before_journal_remove", None)
                    checked_before_removal = _require_visible_publication(
                        destination_path,
                        stage,
                        expected_identity=stage_identity,
                        expected_content_hash=manifest["content_hash"],
                        journal_path=journal_path,
                        journal=journal,
                        journal_identity=journal_identity,
                        lock=lock,
                        journal_present=True,
                    )
                    checked_before_removal.close()
                    _remove_journal(
                        journal_path,
                        journal,
                        journal_identity,
                        lock=lock,
                    )
                    if _publication_hook is not None:
                        _publication_hook("after_journal_remove", None)
                    finalized = _require_visible_publication(
                        destination_path,
                        stage,
                        expected_identity=stage_identity,
                        expected_content_hash=manifest["content_hash"],
                        journal_path=journal_path,
                        journal=journal,
                        journal_identity=journal_identity,
                        lock=lock,
                        journal_present=False,
                    )
            except DirectoryPublishIndeterminateError as exc:
                if finalized is not None:
                    finalized.close()
                _fail("game_runtime_bundle_publication_indeterminate", str(exc))
            except (DirectoryPublishError, FileExistsError) as exc:
                if finalized is not None:
                    finalized.close()
                _fail("game_runtime_bundle_publication_failed", str(exc))
            if finalized is None:
                _fail(
                    "game_runtime_bundle_publication_indeterminate",
                    "publication finalization produced no retained verified snapshot",
                )
            return finalized
        except BaseException as exc:
            if isinstance(exc, GameRuntimeBundleError):
                raise
            raise


def build_game_runtime_bundle_from_objects(
    destination: str | Path,
    *,
    gamepack: object,
    inventory: object,
    assetpack: object,
    assetpack_root: str | Path,
    snapshot: object,
    registry: object,
    composition: object,
    support_report: object,
    expected_parent_identity: DirectoryIdentity | None = None,
    _publication_hook: _PublicationHook | None = None,
) -> VerifiedGameRuntimeBundle:
    """Build and publish one runtime-only bundle from immutable object inputs."""

    manifest, payload_files = build_game_runtime_bundle_manifest_from_objects(
        gamepack=gamepack,
        inventory=inventory,
        assetpack=assetpack,
        assetpack_root=assetpack_root,
        snapshot=snapshot,
        registry=registry,
        composition=composition,
        support_report=support_report,
    )
    return _publish_game_runtime_bundle(
        destination,
        manifest=manifest,
        payload_files=payload_files,
        expected_parent_identity=expected_parent_identity,
        _publication_hook=_publication_hook,
    )


def build_game_runtime_bundle(
    destination: str | Path,
    *,
    gamepack_path: str | Path,
    inventory_path: str | Path,
    assetpack_root: str | Path,
    snapshot_path: str | Path,
    registry_path: str | Path,
    composition_path: str | Path,
    support_report_path: str | Path,
    expected_parent_identity: DirectoryIdentity | None = None,
    _publication_hook: _PublicationHook | None = None,
) -> VerifiedGameRuntimeBundle:
    """Build, stage, verify and exclusively publish one pre-execution bundle."""

    manifest, payload_files = build_game_runtime_bundle_manifest(
        gamepack_path=gamepack_path,
        inventory_path=inventory_path,
        assetpack_root=assetpack_root,
        snapshot_path=snapshot_path,
        registry_path=registry_path,
        composition_path=composition_path,
        support_report_path=support_report_path,
    )
    return _publish_game_runtime_bundle(
        destination,
        manifest=manifest,
        payload_files=payload_files,
        expected_parent_identity=expected_parent_identity,
        _publication_hook=_publication_hook,
    )
