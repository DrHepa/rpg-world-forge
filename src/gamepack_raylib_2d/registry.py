"""Closed implementation dispatch for exact descriptor/snapshot combinations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from gamepack_raylib_2d.descriptor_policy import ADAPTER_DESCRIPTOR_HASHES
from gamepack_raylib_2d.executable_shape import (
    AdapterExecutableShapeError,
    inspect_adapter_executable_shape,
)
from gamepack_raylib_2d.resources import LoadedRuntimeBundle


class AdapterResolutionError(ValueError):
    def __init__(self, reason_code: str, detail: str | None = None) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code if detail is None else f"{reason_code}: {detail}")


@dataclass(frozen=True, slots=True)
class AdapterImplementation:
    adapter_id: str
    adapter_version: str
    descriptor_hash: str
    runtime_snapshot_hash: str
    controller_kind: str
    max_actions: int


def _canonical_hash(document: dict[str, object]) -> str:
    payload = dict(document)
    payload.pop("content_hash", None)
    return hashlib.sha256(
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def resolve_adapter(bundle: LoadedRuntimeBundle) -> AdapterImplementation:
    if type(bundle) is not LoadedRuntimeBundle:
        raise AdapterResolutionError("adapter_bundle_invalid")
    adapter = bundle.adapter
    snapshot = bundle.snapshot
    if adapter.get("content_hash") != _canonical_hash(adapter):
        raise AdapterResolutionError("adapter_descriptor_hash_mismatch")
    if snapshot.get("content_hash") != _canonical_hash(snapshot):
        raise AdapterResolutionError("runtime_snapshot_hash_mismatch")
    adapter_id = adapter.get("adapter_id")
    adapter_version = adapter.get("adapter_version")
    if type(adapter_id) is not str or type(adapter_version) is not str:
        raise AdapterResolutionError("adapter_identity_invalid")
    key = f"{adapter_id}@{adapter_version}"
    expected_hash = ADAPTER_DESCRIPTOR_HASHES.get(key)
    if expected_hash is None or adapter.get("content_hash") != expected_hash:
        raise AdapterResolutionError("adapter_combination_untrusted")
    descriptor_path = f"descriptors/{key}.json"
    records = snapshot.get("files")
    if not isinstance(records, list):
        raise AdapterResolutionError("runtime_snapshot_files_invalid")
    record = next(
        (
            item
            for item in records
            if isinstance(item, dict) and item.get("path") == descriptor_path
        ),
        None,
    )
    descriptor_payload = bundle.files.get(f"runtime/snapshot-tree/{descriptor_path}")
    if (
        not isinstance(record, dict)
        or descriptor_payload is None
        or record.get("size_bytes") != len(descriptor_payload)
        or record.get("sha256") != hashlib.sha256(descriptor_payload).hexdigest()
    ):
        raise AdapterResolutionError("adapter_snapshot_binding_mismatch")
    required_prefixes = {"gamepack_runtime/": False, "gamepack_raylib_2d/": False}
    for item in records:
        if not isinstance(item, dict):
            raise AdapterResolutionError("runtime_snapshot_files_invalid")
        path = item.get("path")
        if not isinstance(path, str):
            raise AdapterResolutionError("runtime_snapshot_files_invalid")
        for prefix in required_prefixes:
            if path.startswith(prefix):
                required_prefixes[prefix] = True
    if not all(required_prefixes.values()):
        raise AdapterResolutionError("adapter_runtime_package_missing")
    manifest_snapshot = bundle.manifest["contracts"]["runtime_snapshot"]
    if manifest_snapshot.get("content_hash") != snapshot.get(
        "content_hash"
    ) or manifest_snapshot.get("id") != snapshot.get("snapshot_id"):
        raise AdapterResolutionError("adapter_snapshot_identity_mismatch")
    kind = {
        "gamepack_raylib_2d_puzzle": "puzzle",
        "gamepack_raylib_2d_text": "narrative_text",
    }.get(adapter_id)
    if kind is None:
        raise AdapterResolutionError("adapter_implementation_missing")
    try:
        inspect_adapter_executable_shape(bundle.gamepack, adapter_id)
    except AdapterExecutableShapeError as exc:
        raise AdapterResolutionError(exc.reason_code, exc.detail) from exc
    budgets = adapter.get("budgets")
    max_actions = budgets.get("max_actions") if isinstance(budgets, dict) else None
    if type(max_actions) is not int or max_actions < 1:
        raise AdapterResolutionError("adapter_action_budget_invalid")
    return AdapterImplementation(
        adapter_id,
        adapter_version,
        expected_hash,
        snapshot["content_hash"],
        kind,
        max_actions,
    )


__all__ = [
    "AdapterImplementation",
    "AdapterResolutionError",
    "resolve_adapter",
]
