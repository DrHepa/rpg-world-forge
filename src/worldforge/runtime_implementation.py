from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from worldforge.creation_contracts import (
    CreationContractError,
    _decode_creation_object,
    _exact_keys,
    _identifier,
    _integer,
    _object,
    _portable_relative_path,
    _semver,
    _sha256,
    _validate_json_structure,
    canonical_creation_hash,
    read_creation_object,
)
from worldforge.generic_runtime import (
    RuntimeContractError,
    validate_runtime_adapter_document,
    validate_runtime_snapshot_document,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.runtime_implementation_policy import (
    TRUSTED_ADAPTER_HASHES as _TRUSTED_ADAPTER_HASHES,
)
from worldforge.runtime_implementation_policy import (
    TRUSTED_PACKAGE_TREE_HASHES as _TRUSTED_PACKAGE_TREE_HASHES,
)
from worldforge.runtime_implementation_policy import (
    TRUSTED_SNAPSHOT_IDENTITY as _TRUSTED_SNAPSHOT_IDENTITY,
)
from worldforge.runtime_platform_lock import (
    RuntimePlatformLockError,
    build_builtin_runtime_platform_locks,
    validate_runtime_platform_lock_document,
)

RUNTIME_IMPLEMENTATION_FORMAT = "world-forge.runtime_implementation"
RUNTIME_IMPLEMENTATION_VERSION = 1

_IMPLEMENTATION_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "implementation_id",
        "adapter",
        "snapshot",
        "runtime_api",
        "packages",
        "entry_points",
        "platform_locks",
        "materialization_policy",
        "content_hash",
    }
)
_ADAPTER_FIELDS = frozenset({"adapter_id", "adapter_version", "content_hash"})
_SNAPSHOT_FIELDS = frozenset({"snapshot_id", "content_hash", "tree_hash"})
_RUNTIME_API_FIELDS = frozenset({"id", "version"})
_PACKAGE_FIELDS = frozenset(
    {
        "package",
        "source_prefix",
        "destination_root",
        "role",
        "classification",
        "files",
        "tree_hash",
    }
)
_FILE_FIELDS = frozenset({"path", "sha256", "size_bytes"})
_ENTRY_POINT_FIELDS = frozenset({"role", "module", "symbol"})
_LOCK_REFERENCE_FIELDS = frozenset({"lock_id", "content_hash", "os", "python_minor", "abi"})
_MATERIALIZATION_POLICY_FIELDS = frozenset(
    {
        "version",
        "standalone_source_root",
        "immutable_runtime",
        "runtime_ai",
    }
)

_PACKAGE_POLICY = (
    (
        "gamepack_raylib_2d",
        "src/gamepack_raylib_2d",
        "raylib_2d_adapter",
    ),
    (
        "gamepack_runtime",
        "src/gamepack_runtime",
        "deterministic_kernel",
    ),
)
_ENTRY_POINT_POLICY = {
    "gamepack_raylib_2d_puzzle": (
        (
            "application_factory",
            "gamepack_raylib_2d.app",
            "RuntimeApp.from_bundle",
        ),
        (
            "backend_factory",
            "gamepack_raylib_2d.backend",
            "PyrayBackend",
        ),
        (
            "bundle_loader",
            "gamepack_raylib_2d.resources",
            "load_runtime_bundle",
        ),
        (
            "native_smoke",
            "gamepack_raylib_2d.native_smoke",
            "native_smoke",
        ),
    ),
    "gamepack_raylib_2d_text": (
        (
            "application_factory",
            "gamepack_raylib_2d.app",
            "RuntimeApp.from_bundle",
        ),
        (
            "backend_factory",
            "gamepack_raylib_2d.backend",
            "PyrayBackend",
        ),
        (
            "bundle_loader",
            "gamepack_raylib_2d.resources",
            "load_runtime_bundle",
        ),
        (
            "native_smoke",
            "gamepack_raylib_2d.native_smoke",
            "native_smoke",
        ),
    ),
}


class RuntimeImplementationError(ValueError):
    """Raised when executable runtime identity is incomplete or crossed."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise RuntimeImplementationError(reason_code, detail)


def _hash(document: Mapping[str, object]) -> str:
    try:
        return canonical_creation_hash(document)
    except CreationContractError as exc:
        _fail("runtime_implementation_invalid", str(exc))


def _utf8_key(value: str) -> bytes:
    return value.encode("utf-8")


def _validate_file_records(
    value: object,
    context: str,
    *,
    allow_empty: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "" if allow_empty else " non-empty"
        _fail("runtime_implementation_invalid", f"{context} must be a{qualifier} array")
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
                "runtime_implementation_package_collision",
                f"{context} contains an NFC/casefold path collision",
            )
        seen.add(folded)
        _sha256(item.get("sha256"), f"{item_context}.sha256")
        _integer(item.get("size_bytes"), f"{item_context}.size_bytes")
        result.append(copy.deepcopy(item))
    expected = sorted((item["path"] for item in result), key=_utf8_key)
    if [item["path"] for item in result] != expected:
        _fail(
            "runtime_implementation_package_order_invalid",
            f"{context} must use UTF-8 path order",
        )
    return result


def _snapshot_package_records(
    snapshot: Mapping[str, object],
    source_prefix: str,
) -> list[dict[str, Any]]:
    prefix = source_prefix + "/"
    result = [
        {
            "path": item["path"][len(prefix) :],
            "sha256": item["sha256"],
            "size_bytes": item["size_bytes"],
        }
        for item in snapshot["files"]  # type: ignore[index]
        if item["path"].startswith(prefix)  # type: ignore[index]
    ]
    if not result:
        _fail(
            "runtime_implementation_package_missing",
            f"snapshot contains no files under {source_prefix}",
        )
    return sorted(result, key=lambda item: _utf8_key(item["path"]))


def _lock_references(
    platform_locks: Sequence[Mapping[str, object]],
) -> list[dict[str, Any]]:
    result = [
        {
            "lock_id": lock["lock_id"],
            "content_hash": lock["content_hash"],
            "os": lock["platform"]["os"],  # type: ignore[index]
            "python_minor": lock["python"]["minor"],  # type: ignore[index]
            "abi": lock["python"]["abi"],  # type: ignore[index]
        }
        for lock in platform_locks
    ]
    return sorted(result, key=lambda item: _utf8_key(item["lock_id"]))


def build_runtime_implementation(
    *,
    adapter: object,
    snapshot: object,
    platform_locks: Sequence[object],
) -> dict[str, Any]:
    try:
        checked_adapter = validate_runtime_adapter_document(adapter)
        checked_snapshot = validate_runtime_snapshot_document(snapshot)
        checked_locks = tuple(
            validate_runtime_platform_lock_document(lock) for lock in platform_locks
        )
    except (RuntimeContractError, RuntimePlatformLockError) as exc:
        _fail("runtime_implementation_input_invalid", str(exc))
    if len(checked_locks) != 4:
        _fail(
            "runtime_implementation_platform_locks_incomplete",
            "exactly four audited platform locks are required",
        )
    policy = _ENTRY_POINT_POLICY.get(checked_adapter["adapter_id"])
    if policy is None or checked_adapter.get("adapter_version") != "1.1.0":
        _fail(
            "runtime_implementation_adapter_unsupported",
            "adapter does not have a code-owned executable policy",
        )
    descriptor_identity = next(
        (
            item
            for item in checked_snapshot["adapter_descriptors"]
            if item["id"] == checked_adapter["adapter_id"]
        ),
        None,
    )
    if (
        descriptor_identity is None
        or descriptor_identity["content_hash"] != checked_adapter["content_hash"]
    ):
        _fail(
            "runtime_implementation_adapter_snapshot_mismatch",
            "adapter is not the exact descriptor captured by the snapshot",
        )

    packages = []
    for package, destination_root, role in _PACKAGE_POLICY:
        records = _snapshot_package_records(checked_snapshot, package)
        packages.append(
            {
                "package": package,
                "source_prefix": package,
                "destination_root": destination_root,
                "role": role,
                "classification": "immutable_runtime_source",
                "files": records,
                "tree_hash": _hash({"files": records}),
            }
        )
    seed: dict[str, Any] = {
        "format": RUNTIME_IMPLEMENTATION_FORMAT,
        "format_version": RUNTIME_IMPLEMENTATION_VERSION,
        "adapter": {
            "adapter_id": checked_adapter["adapter_id"],
            "adapter_version": checked_adapter["adapter_version"],
            "content_hash": checked_adapter["content_hash"],
        },
        "snapshot": {
            "snapshot_id": checked_snapshot["snapshot_id"],
            "content_hash": checked_snapshot["content_hash"],
            "tree_hash": checked_snapshot["tree_hash"],
        },
        "runtime_api": copy.deepcopy(checked_snapshot["runtime_api"]),
        "packages": packages,
        "entry_points": [
            {"role": role, "module": module, "symbol": symbol} for role, module, symbol in policy
        ],
        "platform_locks": _lock_references(checked_locks),
        "materialization_policy": {
            "version": 1,
            "standalone_source_root": "src",
            "immutable_runtime": True,
            "runtime_ai": False,
        },
    }
    document = {
        **seed,
        "implementation_id": "runtime_implementation_" + _hash(seed)[:40],
        "content_hash": "",
    }
    document["content_hash"] = _hash(document)
    return validate_runtime_implementation_document(
        document,
        adapter=checked_adapter,
        snapshot=checked_snapshot,
        platform_locks=checked_locks,
    )


def validate_runtime_implementation_document(
    value: object,
    *,
    adapter: object | None = None,
    snapshot: object | None = None,
    platform_locks: Sequence[object] | None = None,
) -> dict[str, Any]:
    try:
        _validate_json_structure(value, context="runtime implementation")
        document = _object(value, "runtime implementation")
        _exact_keys(document, _IMPLEMENTATION_FIELDS, "runtime implementation")
        if document.get("format") != RUNTIME_IMPLEMENTATION_FORMAT:
            _fail(
                "runtime_implementation_format_mismatch",
                f"format must be {RUNTIME_IMPLEMENTATION_FORMAT}",
            )
        if document.get("format_version") != RUNTIME_IMPLEMENTATION_VERSION:
            _fail(
                "runtime_implementation_version_mismatch",
                "format_version must be 1",
            )
        implementation_id = _identifier(
            document.get("implementation_id"),
            "runtime implementation.implementation_id",
        )
        _sha256(
            document.get("content_hash"),
            "runtime implementation.content_hash",
        )

        adapter_identity = _object(
            document.get("adapter"),
            "runtime implementation.adapter",
        )
        _exact_keys(
            adapter_identity,
            _ADAPTER_FIELDS,
            "runtime implementation.adapter",
        )
        adapter_id = _identifier(
            adapter_identity.get("adapter_id"),
            "runtime implementation.adapter.adapter_id",
        )
        _semver(
            adapter_identity.get("adapter_version"),
            "runtime implementation.adapter.adapter_version",
        )
        _sha256(
            adapter_identity.get("content_hash"),
            "runtime implementation.adapter.content_hash",
        )
        policy = _ENTRY_POINT_POLICY.get(adapter_id)
        if policy is None or adapter_identity["adapter_version"] != "1.1.0":
            _fail(
                "runtime_implementation_adapter_unsupported",
                "adapter identity does not have a code-owned policy",
            )
        if adapter_identity["content_hash"] != _TRUSTED_ADAPTER_HASHES[adapter_id]:
            _fail(
                "runtime_implementation_adapter_not_certified",
                "adapter identity does not match the certified bounded adapter",
            )

        snapshot_identity = _object(
            document.get("snapshot"),
            "runtime implementation.snapshot",
        )
        _exact_keys(
            snapshot_identity,
            _SNAPSHOT_FIELDS,
            "runtime implementation.snapshot",
        )
        _identifier(
            snapshot_identity.get("snapshot_id"),
            "runtime implementation.snapshot.snapshot_id",
        )
        _sha256(
            snapshot_identity.get("content_hash"),
            "runtime implementation.snapshot.content_hash",
        )
        _sha256(
            snapshot_identity.get("tree_hash"),
            "runtime implementation.snapshot.tree_hash",
        )
        if snapshot_identity != _TRUSTED_SNAPSHOT_IDENTITY:
            _fail(
                "runtime_implementation_snapshot_not_certified",
                "snapshot identity does not match the certified runtime snapshot",
            )

        runtime_api = _object(
            document.get("runtime_api"),
            "runtime implementation.runtime_api",
        )
        _exact_keys(
            runtime_api,
            _RUNTIME_API_FIELDS,
            "runtime implementation.runtime_api",
        )
        if runtime_api != {"id": "gamepack_runtime", "version": "1.0.0"}:
            _fail(
                "runtime_implementation_runtime_api_mismatch",
                "runtime_api must be gamepack_runtime 1.0.0",
            )

        raw_packages = document.get("packages")
        if not isinstance(raw_packages, list) or len(raw_packages) != len(_PACKAGE_POLICY):
            _fail(
                "runtime_implementation_packages_invalid",
                "packages must contain the exact executable package projections",
            )
        expected_package_names = [item[0] for item in _PACKAGE_POLICY]
        if [
            package.get("package") if isinstance(package, dict) else None
            for package in raw_packages
        ] != expected_package_names:
            _fail(
                "runtime_implementation_packages_invalid",
                "packages must use the code-owned canonical order",
            )
        packages: list[dict[str, Any]] = []
        for index, ((package_name, destination_root, role), raw) in enumerate(
            zip(_PACKAGE_POLICY, raw_packages, strict=True)
        ):
            context = f"runtime implementation.packages/{index}"
            package = _object(raw, context)
            _exact_keys(package, _PACKAGE_FIELDS, context)
            if (
                package.get("package") != package_name
                or package.get("source_prefix") != package_name
                or package.get("destination_root") != destination_root
                or package.get("role") != role
                or package.get("classification") != "immutable_runtime_source"
            ):
                _fail(
                    "runtime_implementation_package_policy_mismatch",
                    f"{package_name} projection is not code-owned",
                )
            records = _validate_file_records(package.get("files"), f"{context}.files")
            _sha256(package.get("tree_hash"), f"{context}.tree_hash")
            if package["tree_hash"] != _hash({"files": records}):
                _fail(
                    "runtime_implementation_package_tree_hash_mismatch",
                    f"{package_name} tree_hash is not canonical",
                )
            if package["tree_hash"] != _TRUSTED_PACKAGE_TREE_HASHES[package_name]:
                _fail(
                    "runtime_implementation_package_not_certified",
                    f"{package_name} bytes do not match the certified package projection",
                )
            packages.append(copy.deepcopy(package))

        raw_entry_points = document.get("entry_points")
        expected_entry_points = [
            {"role": role, "module": module, "symbol": symbol} for role, module, symbol in policy
        ]
        if raw_entry_points != expected_entry_points:
            _fail(
                "runtime_implementation_entry_point_policy_mismatch",
                "entry points must exactly match the code-owned adapter policy",
            )
        for index, raw in enumerate(raw_entry_points):
            item = _object(raw, f"runtime implementation.entry_points/{index}")
            _exact_keys(
                item,
                _ENTRY_POINT_FIELDS,
                f"runtime implementation.entry_points/{index}",
            )

        raw_lock_refs = document.get("platform_locks")
        if not isinstance(raw_lock_refs, list) or len(raw_lock_refs) != 4:
            _fail(
                "runtime_implementation_platform_locks_incomplete",
                "platform_locks must contain exactly four lock references",
            )
        lock_ids: set[str] = set()
        lock_refs: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_lock_refs):
            context = f"runtime implementation.platform_locks/{index}"
            item = _object(raw, context)
            _exact_keys(item, _LOCK_REFERENCE_FIELDS, context)
            lock_id = _identifier(item.get("lock_id"), f"{context}.lock_id")
            if lock_id in lock_ids:
                _fail(
                    "runtime_implementation_platform_locks_incomplete",
                    "platform lock IDs must be unique",
                )
            lock_ids.add(lock_id)
            _sha256(item.get("content_hash"), f"{context}.content_hash")
            if item.get("os") not in {"linux", "windows"}:
                _fail(
                    "runtime_implementation_platform_lock_invalid",
                    f"{context}.os is invalid",
                )
            if item.get("python_minor") not in {"3.11", "3.12"}:
                _fail(
                    "runtime_implementation_platform_lock_invalid",
                    f"{context}.python_minor is invalid",
                )
            if item.get("abi") not in {"cp311", "cp312"}:
                _fail(
                    "runtime_implementation_platform_lock_invalid",
                    f"{context}.abi is invalid",
                )
            lock_refs.append(copy.deepcopy(item))
        if [item["lock_id"] for item in lock_refs] != sorted(
            (item["lock_id"] for item in lock_refs),
            key=_utf8_key,
        ):
            _fail(
                "runtime_implementation_platform_lock_order_invalid",
                "platform lock references must use UTF-8 lock_id order",
            )
        if lock_refs != _lock_references(build_builtin_runtime_platform_locks()):
            _fail(
                "runtime_implementation_platform_lock_not_certified",
                "platform lock references do not match the audited four-lock set",
            )

        materialization_policy = _object(
            document.get("materialization_policy"),
            "runtime implementation.materialization_policy",
        )
        _exact_keys(
            materialization_policy,
            _MATERIALIZATION_POLICY_FIELDS,
            "runtime implementation.materialization_policy",
        )
        if materialization_policy != {
            "version": 1,
            "standalone_source_root": "src",
            "immutable_runtime": True,
            "runtime_ai": False,
        }:
            _fail(
                "runtime_implementation_materialization_policy_invalid",
                "materialization policy must be the closed v1 policy",
            )

        seed = {
            key: item
            for key, item in document.items()
            if key not in {"implementation_id", "content_hash"}
        }
        expected_id = "runtime_implementation_" + _hash(seed)[:40]
        if implementation_id != expected_id:
            _fail(
                "runtime_implementation_id_mismatch",
                "implementation_id is not canonical",
            )
        if document["content_hash"] != _hash(document):
            _fail(
                "runtime_implementation_content_hash_mismatch",
                "content_hash is not canonical",
            )

        checked_snapshot = None
        if snapshot is not None:
            checked_snapshot = validate_runtime_snapshot_document(snapshot)
            if snapshot_identity != {
                "snapshot_id": checked_snapshot["snapshot_id"],
                "content_hash": checked_snapshot["content_hash"],
                "tree_hash": checked_snapshot["tree_hash"],
            }:
                _fail(
                    "runtime_implementation_snapshot_mismatch",
                    "implementation does not identify the supplied snapshot",
                )
            if runtime_api != checked_snapshot["runtime_api"]:
                _fail(
                    "runtime_implementation_runtime_api_mismatch",
                    "runtime API does not match the supplied snapshot",
                )
            for package in packages:
                expected_records = _snapshot_package_records(
                    checked_snapshot,
                    package["source_prefix"],
                )
                if package["files"] != expected_records:
                    _fail(
                        "runtime_implementation_package_snapshot_mismatch",
                        f"{package['package']} files do not match the snapshot",
                    )

        if adapter is not None:
            checked_adapter = validate_runtime_adapter_document(adapter)
            if adapter_identity != {
                "adapter_id": checked_adapter["adapter_id"],
                "adapter_version": checked_adapter["adapter_version"],
                "content_hash": checked_adapter["content_hash"],
            }:
                _fail(
                    "runtime_implementation_adapter_mismatch",
                    "implementation does not identify the supplied adapter",
                )
            if checked_snapshot is not None:
                descriptor = next(
                    (
                        item
                        for item in checked_snapshot["adapter_descriptors"]
                        if item["id"] == checked_adapter["adapter_id"]
                    ),
                    None,
                )
                if (
                    descriptor is None
                    or descriptor["content_hash"] != checked_adapter["content_hash"]
                ):
                    _fail(
                        "runtime_implementation_adapter_snapshot_mismatch",
                        "adapter is not captured by the supplied snapshot",
                    )

        if platform_locks is not None:
            checked_locks = [
                validate_runtime_platform_lock_document(lock) for lock in platform_locks
            ]
            expected_refs = _lock_references(checked_locks)
            if lock_refs != expected_refs or len(expected_refs) != 4:
                _fail(
                    "runtime_implementation_platform_lock_mismatch",
                    "platform lock references do not match the supplied audited locks",
                )
        return copy.deepcopy(document)
    except RuntimeImplementationError:
        raise
    except (CreationContractError, RuntimeContractError, RuntimePlatformLockError) as exc:
        _fail("runtime_implementation_invalid", str(exc))


def serialize_runtime_implementation(value: object) -> bytes:
    return canonical_json_bytes(validate_runtime_implementation_document(value))


def load_runtime_implementation(source: object) -> dict[str, Any]:
    try:
        if isinstance(source, (bytes, bytearray)):
            document = _decode_creation_object(
                bytes(source),
                "runtime implementation",
            )
        elif isinstance(source, str) and source.lstrip().startswith("{"):
            document = _decode_creation_object(
                source.encode("utf-8"),
                "runtime implementation",
            )
        else:
            document = read_creation_object(source)  # type: ignore[arg-type]
        return validate_runtime_implementation_document(document)
    except RuntimeImplementationError:
        raise
    except (CreationContractError, OSError, TypeError) as exc:
        _fail("runtime_implementation_invalid", str(exc))
