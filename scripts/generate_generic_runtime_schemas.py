"""Generate the additive World Forge generic runtime v1 schemas."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from worldforge.creation_contracts import canonical_creation_hash, read_creation_object
from worldforge.generic_runtime import (
    RUNTIME_EXECUTION_SEMANTICS_POLICY,
    capture_trusted_runtime_snapshot_files,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.runtime_support_authority import RUNTIME_SUPPORT_AUTHORITY_FORMAT

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
STUDIO_RUNTIME_POLICY = ROOT / "apps" / "studio" / "scripts" / "generic-runtime-policy.mjs"
STUDIO_RUNTIME_TRUSTED_FILES = (
    ROOT / "apps" / "studio" / "scripts" / "generic-runtime-trusted-files.mjs"
)
STUDIO_RUNTIME_TRUSTED_FILES_TYPES = (
    ROOT / "apps" / "studio" / "scripts" / "generic-runtime-trusted-files.d.mts"
)
RUNTIME_SNAPSHOT = ROOT / "examples" / "multigenre-contracts" / "runtime" / "snapshot.json"
RUNTIME_REGISTRY = ROOT / "examples" / "multigenre-contracts" / "runtime" / "registry.json"

ID_PATTERN = r"^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$"
TOKEN_PATTERN = r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_.-]*$"
PATH_PATTERN = (
    r"^(?![./])(?!.*/(?:\.{1,2})(?:/|$))"
    r"(?!(?:.*[/])?(?:aux|con|nul|prn|com[1-9]|lpt[1-9])(?:[./]|$))"
    r"[a-zA-Z0-9_][a-zA-Z0-9_.@ -]*(?:/[a-zA-Z0-9_][a-zA-Z0-9_.@ -]*)*$"
)
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _array(
    items: dict[str, Any],
    *,
    minimum: int = 0,
    maximum: int = 256,
    unique: bool = True,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "items": items,
        "maxItems": maximum,
        "minItems": minimum,
        "type": "array",
    }
    if unique:
        value["uniqueItems"] = True
    return value


def _object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": properties,
        "required": required if required is not None else list(properties),
        "type": "object",
    }


def _identity(format_name: str) -> dict[str, Any]:
    return _object(
        {
            "format": {"const": format_name},
            "format_version": {"const": 1},
            "id": {"$ref": "#/$defs/id"},
            "content_hash": {"$ref": "#/$defs/sha256"},
        }
    )


def _format_versions(format_name: str | None = None) -> dict[str, Any]:
    return _object(
        {
            "format": (
                {"const": format_name}
                if format_name is not None
                else {"maxLength": 128, "minLength": 1, "type": "string"}
            ),
            "versions": _array(
                {
                    "maximum": MAX_SAFE_INTEGER,
                    "minimum": 1,
                    "type": "integer",
                },
                minimum=1,
                maximum=16,
            ),
        }
    )


def _platform() -> dict[str, Any]:
    return {
        "oneOf": [
            _object(
                {
                    "platform_id": {"const": platform_id},
                    "platform_family": {"const": platform_family},
                    "architecture": {"const": "architecture:x86_64"},
                    "backend": {"const": "backend:raylib"},
                    "renderer": {"const": "raylib"},
                }
            )
            for platform_id, platform_family in (
                ("platform:linux_x86_64", "platform:linux"),
                ("platform:windows_x86_64", "platform:windows"),
            )
        ]
    }


def _base_defs() -> dict[str, Any]:
    return {
        "id": {
            "maxLength": 64,
            "minLength": 2,
            "pattern": ID_PATTERN,
            "type": "string",
        },
        "path": {
            "maxLength": 1024,
            "minLength": 1,
            "pattern": PATH_PATTERN,
            "type": "string",
        },
        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
        "semver": {
            "pattern": r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$",
            "type": "string",
        },
        "text": {
            "maxLength": 4096,
            "minLength": 1,
            "type": "string",
        },
        "token": {
            "maxLength": 128,
            "minLength": 3,
            "pattern": TOKEN_PATTERN,
            "type": "string",
        },
        "runtimeApi": _object(
            {
                "id": {"$ref": "#/$defs/id"},
                "version": {"$ref": "#/$defs/semver"},
            }
        ),
        "platform": _platform(),
    }


def _schema(
    *,
    name: str,
    title: str,
    format_name: str,
    properties: dict[str, Any],
    definitions: dict[str, Any],
) -> dict[str, Any]:
    all_properties = {
        "format": {"const": format_name},
        "format_version": {"const": 1},
        **properties,
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": f"https://world-forge.local/schemas/{name}",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": all_properties,
        "required": list(all_properties),
        "title": title,
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-generic-runtime-coherent": name.removesuffix(".schema.json"),
        "$defs": definitions,
    }


def _adapter_schema() -> dict[str, Any]:
    defs = _base_defs()
    defs.update(
        {
            "formatVersions": _format_versions(),
            "presentation": _object(
                {
                    "mode": {"$ref": "#/$defs/text"},
                    "camera": {"$ref": "#/$defs/text"},
                    "perspective": {"$ref": "#/$defs/text"},
                    "requested_renderer": {"const": "raylib"},
                }
            ),
            "assetBinding": _object(
                {
                    "binding_id": {"$ref": "#/$defs/id"},
                    "asset_id": {"$ref": "#/$defs/id"},
                    "role": {"$ref": "#/$defs/id"},
                    "media_type": {"$ref": "#/$defs/text"},
                    "runtime_path": {"$ref": "#/$defs/path"},
                }
            ),
            "persistenceTarget": _object(
                {
                    "required": {"type": "boolean"},
                    "format": {"$ref": "#/$defs/text"},
                    "versions": _array(
                        {
                            "maximum": MAX_SAFE_INTEGER,
                            "minimum": 1,
                            "type": "integer",
                        },
                        minimum=1,
                        maximum=16,
                    ),
                }
            ),
        }
    )
    return _schema(
        name="generic-runtime-adapter.schema.json",
        title="World Forge declarative runtime adapter v1",
        format_name="world-forge.runtime_adapter",
        properties={
            "adapter_id": {"$ref": "#/$defs/id"},
            "adapter_version": {"$ref": "#/$defs/semver"},
            "state": {"enum": ["declared", "verified"]},
            "accepted_logic_formats": _array(
                {"$ref": "#/$defs/formatVersions"},
                minimum=1,
                maximum=16,
            ),
            "execution_semantics": {
                "const": dict(RUNTIME_EXECUTION_SEMANTICS_POLICY),
            },
            "supported_profiles": _array(
                {"$ref": "#/$defs/token"},
                minimum=1,
            ),
            "supported_features": _array(
                {"$ref": "#/$defs/token"},
                minimum=1,
            ),
            "supported_semantics": _object(
                {
                    "action_parameter_types": _array({"$ref": "#/$defs/text"}),
                    "condition_operators": _array({"$ref": "#/$defs/text"}),
                    "effect_operations": _array({"$ref": "#/$defs/text"}),
                    "ending_kinds": _array({"$ref": "#/$defs/text"}),
                    "narrative_cursor": {"type": "boolean"},
                }
            ),
            "presentations": _array(
                {"$ref": "#/$defs/presentation"},
                minimum=1,
                maximum=16,
            ),
            "assetpacks": _array(
                {"$ref": "#/$defs/formatVersions"},
                minimum=1,
                maximum=16,
            ),
            "asset_formats": _array(
                {"$ref": "#/$defs/token"},
                minimum=1,
            ),
            "asset_bindings": _array(
                {"$ref": "#/$defs/assetBinding"},
                minimum=1,
            ),
            "platforms": _array(
                {"$ref": "#/$defs/platform"},
                minimum=1,
                maximum=32,
            ),
            "input_capabilities": _array(
                {"$ref": "#/$defs/token"},
                minimum=1,
            ),
            "persistence": _object(
                {
                    "save": {"$ref": "#/$defs/persistenceTarget"},
                    "replay": {"$ref": "#/$defs/persistenceTarget"},
                }
            ),
            "packaging_targets": _array(
                {"$ref": "#/$defs/text"},
                minimum=1,
            ),
            "implementation": _object(
                {
                    "backend": {"const": "backend:raylib"},
                    "renderer": {"const": "raylib"},
                    "runtime_api": {"$ref": "#/$defs/runtimeApi"},
                    "snapshot": _format_versions("world-forge.game_runtime_snapshot"),
                }
            ),
            "budgets": _object(
                {
                    field: {
                        "maximum": MAX_SAFE_INTEGER,
                        "minimum": 1,
                        "type": "integer",
                    }
                    for field in (
                        "max_actions",
                        "max_assets",
                        "max_loaded_bytes",
                        "max_state_bytes",
                        "target_frame_milliseconds",
                    )
                }
            ),
            "limitations": _array(
                {"$ref": "#/$defs/text"},
                minimum=1,
            ),
            "evidence_requirements": _array(
                {"$ref": "#/$defs/token"},
                minimum=1,
            ),
        },
        definitions=defs,
    )


def _snapshot_schema() -> dict[str, Any]:
    defs = _base_defs()
    defs.update(
        {
            "adapterIdentity": _identity("world-forge.runtime_adapter"),
            "file": _object(
                {
                    "path": {"$ref": "#/$defs/path"},
                    "sha256": {"$ref": "#/$defs/sha256"},
                    "size_bytes": {
                        "maximum": 4 * 1024 * 1024,
                        "minimum": 0,
                        "type": "integer",
                    },
                }
            ),
        }
    )
    return _schema(
        name="game-runtime-snapshot.schema.json",
        title="World Forge deterministic game-runtime snapshot v1",
        format_name="world-forge.game_runtime_snapshot",
        properties={
            "snapshot_id": {"$ref": "#/$defs/id"},
            "runtime_api": {"$ref": "#/$defs/runtimeApi"},
            "adapter_descriptors": _array(
                {"$ref": "#/$defs/adapterIdentity"},
                minimum=1,
                maximum=32,
            ),
            "files": _array(
                {"$ref": "#/$defs/file"},
                minimum=1,
                maximum=256,
            ),
            "tree_hash": {"$ref": "#/$defs/sha256"},
        },
        definitions=defs,
    )


def _registry_schema(adapter: dict[str, Any]) -> dict[str, Any]:
    defs = _base_defs()
    defs.update({key: value for key, value in adapter["$defs"].items() if key not in defs})
    defs.update(
        {
            "snapshotIdentity": _identity("world-forge.game_runtime_snapshot"),
            "adapter": {
                **{
                    key: value
                    for key, value in adapter.items()
                    if key
                    not in {
                        "$id",
                        "$schema",
                        "title",
                        "$defs",
                    }
                }
            },
        }
    )
    return _schema(
        name="generic-runtime-adapter-registry.schema.json",
        title="World Forge trusted runtime-adapter registry v1",
        format_name="world-forge.runtime_adapter_registry",
        properties={
            "registry_id": {"$ref": "#/$defs/id"},
            "runtime_snapshot": {"$ref": "#/$defs/snapshotIdentity"},
            "adapters": _array(
                {"$ref": "#/$defs/adapter"},
                minimum=1,
                maximum=32,
            ),
        },
        definitions=defs,
    )


def _composition_schema() -> dict[str, Any]:
    defs = _base_defs()
    defs.update(
        {
            "gamepackIdentity": _identity("world-forge.gamepack"),
            "inventoryIdentity": _identity("world-forge.asset_inventory"),
            "adapterIdentity": _identity("world-forge.runtime_adapter"),
            "registryIdentity": _identity("world-forge.runtime_adapter_registry"),
            "snapshotIdentity": _identity("world-forge.game_runtime_snapshot"),
            "assetpackIdentity": _object(
                {
                    "format": {"const": "world-forge.assetpack"},
                    "format_version": {"const": 1},
                    "id": {"$ref": "#/$defs/id"},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                    "root_hash": {"$ref": "#/$defs/sha256"},
                    "inventory_hash": {"$ref": "#/$defs/sha256"},
                }
            ),
            "binding": _object(
                {
                    "binding_id": {"$ref": "#/$defs/id"},
                    "asset_id": {"$ref": "#/$defs/id"},
                    "role": {"$ref": "#/$defs/id"},
                    "media_type": {"$ref": "#/$defs/text"},
                    "runtime_path": {"$ref": "#/$defs/path"},
                    "sha256": {"$ref": "#/$defs/sha256"},
                    "size_bytes": {
                        "maximum": 16 * 1024 * 1024,
                        "minimum": 1,
                        "type": "integer",
                    },
                }
            ),
        }
    )
    return _schema(
        name="game-runtime-composition.schema.json",
        title="World Forge exact game-runtime composition v1",
        format_name="world-forge.game_runtime_composition",
        properties={
            "composition_id": {"$ref": "#/$defs/id"},
            "gamepack": {"$ref": "#/$defs/gamepackIdentity"},
            "asset_inventory": {"$ref": "#/$defs/inventoryIdentity"},
            "assetpack": {"$ref": "#/$defs/assetpackIdentity"},
            "adapter": {"$ref": "#/$defs/adapterIdentity"},
            "registry": {"$ref": "#/$defs/registryIdentity"},
            "runtime_snapshot": {"$ref": "#/$defs/snapshotIdentity"},
            "platforms": _array(
                {"$ref": "#/$defs/platform"},
                minimum=1,
                maximum=32,
            ),
            "bindings": _array(
                {"$ref": "#/$defs/binding"},
                minimum=1,
                maximum=256,
            ),
        },
        definitions=defs,
    )


def _evidence_schema() -> dict[str, Any]:
    defs = _base_defs()
    defs.update(
        {
            "compositionIdentity": _identity("world-forge.game_runtime_composition"),
            "adapterIdentity": _identity("world-forge.runtime_adapter"),
            "check": _object(
                {
                    "check_id": {
                        "enum": [
                            "check:headless_determinism",
                            "check:native_raylib",
                            "check:package_verification",
                            "check:save_replay",
                        ]
                    },
                    "kind": {
                        "enum": [
                            "headless",
                            "native",
                            "packaging",
                            "save_replay",
                        ]
                    },
                    "status": {"enum": ["passed", "failed"]},
                    "evidence_id": {"$ref": "#/$defs/id"},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                }
            ),
        }
    )
    return _schema(
        name="generic-runtime-evidence.schema.json",
        title="World Forge external runtime evidence claim v1",
        format_name="world-forge.runtime_evidence",
        properties={
            "evidence_id": {"$ref": "#/$defs/id"},
            "composition": {"$ref": "#/$defs/compositionIdentity"},
            "adapter": {"$ref": "#/$defs/adapterIdentity"},
            "platform": {"$ref": "#/$defs/platform"},
            "execution_status": {"enum": ["headless_verified", "native_verified", "failed"]},
            "packaging_status": {"enum": ["unverified", "verified", "failed"]},
            "checks": _array(
                {"$ref": "#/$defs/check"},
                minimum=1,
                maximum=64,
            ),
        },
        definitions=defs,
    )


def _support_report_schema() -> dict[str, Any]:
    defs = _base_defs()
    identity_formats = {
        "gamepackIdentity": "world-forge.gamepack",
        "compositionIdentity": "world-forge.game_runtime_composition",
        "adapterIdentity": "world-forge.runtime_adapter",
    }
    defs.update({name: _identity(format_name) for name, format_name in identity_formats.items()})
    positive_status = {
        "enum": [
            "supported_current",
            "game_extension_verified",
        ]
    }
    negative_status = {"enum": ["authoring_only", "blocked"]}
    mechanic_common = {
        "mechanic_id": {"$ref": "#/$defs/id"},
        "core_verb_id": {"$ref": "#/$defs/id"},
        "runtime_action_id": {"$ref": "#/$defs/id"},
        "authoritative_state_ids": _array({"$ref": "#/$defs/id"}),
        "condition_ids": _array({"$ref": "#/$defs/id"}),
        "rule_ids": _array({"$ref": "#/$defs/id"}),
        "effect_ids": _array({"$ref": "#/$defs/id"}),
        "presentation_hook_ids": _array({"$ref": "#/$defs/id"}),
        "asset_binding_ids": _array({"$ref": "#/$defs/id"}),
        "required_feature_ids": _array(
            {"$ref": "#/$defs/token"},
            minimum=1,
        ),
        "save_replay": {"$ref": "#/$defs/saveReplay"},
    }
    defs.update(
        {
            "evidenceReference": _object(
                {
                    "format": {"const": "world-forge.runtime_evidence"},
                    "format_version": {"const": 1},
                    "id": {"$ref": "#/$defs/id"},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                    "platform": {"$ref": "#/$defs/platform"},
                    "execution_status": {
                        "enum": [
                            "headless_verified",
                            "native_verified",
                            "failed",
                        ]
                    },
                    "packaging_status": {"enum": ["unverified", "verified", "failed"]},
                    "passed_check_kinds": _array(
                        {
                            "enum": [
                                "headless",
                                "native",
                                "packaging",
                                "save_replay",
                            ]
                        },
                        maximum=4,
                    ),
                }
            ),
            "execution": {
                "oneOf": [
                    _object(
                        {
                            "platform": {"$ref": "#/$defs/platform"},
                            "status": {"const": "untested"},
                            "evidence_ids": _array(
                                {"$ref": "#/$defs/id"},
                                maximum=0,
                            ),
                        }
                    ),
                    _object(
                        {
                            "platform": {"$ref": "#/$defs/platform"},
                            "status": {
                                "enum": [
                                    "headless_verified",
                                    "native_verified",
                                    "failed",
                                ]
                            },
                            "evidence_ids": _array(
                                {"$ref": "#/$defs/id"},
                                minimum=1,
                                maximum=1,
                            ),
                        }
                    ),
                ]
            },
            "saveReplay": _object(
                {
                    "state_ids": _array({"$ref": "#/$defs/id"}),
                    "event_ids": _array({"$ref": "#/$defs/id"}),
                }
            ),
            "mechanic": {
                "oneOf": [
                    _object(
                        {
                            **mechanic_common,
                            "status": positive_status,
                            "reason_codes": _array(
                                {"$ref": "#/$defs/id"},
                                maximum=0,
                            ),
                            "test_evidence": _array(
                                {"$ref": "#/$defs/id"},
                                minimum=1,
                            ),
                            "native_evidence": _array(
                                {"$ref": "#/$defs/id"},
                                minimum=1,
                            ),
                        }
                    ),
                    _object(
                        {
                            **mechanic_common,
                            "status": negative_status,
                            "reason_codes": _array(
                                {"$ref": "#/$defs/id"},
                                minimum=1,
                            ),
                            "test_evidence": _array(
                                {"$ref": "#/$defs/id"},
                                maximum=0,
                            ),
                            "native_evidence": _array(
                                {"$ref": "#/$defs/id"},
                                maximum=0,
                            ),
                        }
                    ),
                ]
            },
            "feature": {
                "oneOf": [
                    _object(
                        {
                            "feature_id": {"$ref": "#/$defs/token"},
                            "status": positive_status,
                            "reason_codes": _array(
                                {"$ref": "#/$defs/id"},
                                maximum=0,
                            ),
                            "evidence_ids": _array(
                                {"$ref": "#/$defs/id"},
                                minimum=1,
                            ),
                        }
                    ),
                    _object(
                        {
                            "feature_id": {"$ref": "#/$defs/token"},
                            "status": negative_status,
                            "reason_codes": _array(
                                {"$ref": "#/$defs/id"},
                                minimum=1,
                            ),
                            "evidence_ids": _array(
                                {"$ref": "#/$defs/id"},
                                maximum=0,
                            ),
                        }
                    ),
                ]
            },
        }
    )
    return _schema(
        name="generic-runtime-support-report.schema.json",
        title="World Forge multidimensional runtime support report v1",
        format_name="world-forge.runtime_support_report",
        properties={
            "report_id": {"$ref": "#/$defs/id"},
            "gamepack": {"$ref": "#/$defs/gamepackIdentity"},
            "composition": {"$ref": "#/$defs/compositionIdentity"},
            "adapter": {"$ref": "#/$defs/adapterIdentity"},
            "evidence": _array(
                {"$ref": "#/$defs/evidenceReference"},
                maximum=64,
            ),
            "dimensions": _object(
                {
                    "authoring": {"enum": ["valid", "invalid"]},
                    "compilation": {
                        "enum": [
                            "not_requested",
                            "compiled",
                            "unsupported",
                            "failed",
                        ]
                    },
                    "assets": {
                        "enum": [
                            "unplanned",
                            "planned",
                            "produced",
                            "processed",
                            "sealed",
                            "failed",
                        ]
                    },
                    "adapter": {"enum": ["absent", "declared", "verified"]},
                    "execution": _array(
                        {"$ref": "#/$defs/execution"},
                        minimum=1,
                        maximum=32,
                    ),
                    "packaging": {"enum": ["unverified", "verified", "failed"]},
                    "release": {"enum": ["blocked", "ready"]},
                }
            ),
            "compatibility_status": {
                "enum": [
                    "supported",
                    "partially_supported",
                    "unsupported",
                ]
            },
            "mechanics": _array(
                {"$ref": "#/$defs/mechanic"},
                minimum=1,
                maximum=128,
            ),
            "features": _array(
                {"$ref": "#/$defs/feature"},
                minimum=1,
                maximum=256,
            ),
            "missing_capabilities": _array({"$ref": "#/$defs/token"}),
            "reason_codes": _array({"$ref": "#/$defs/id"}),
            "supported": {"type": "boolean"},
        },
        definitions=defs,
    )


def _authority_schema() -> dict[str, Any]:
    defs = _base_defs()
    defs["authorityId"] = {
        "maxLength": 128,
        "minLength": 2,
        "pattern": r"^[a-z][a-z0-9_]{1,127}$",
        "type": "string",
    }

    def identity(format_name: str) -> dict[str, Any]:
        return _object(
            {
                "format": {"const": format_name},
                "format_version": {"const": 1},
                "id": {"$ref": "#/$defs/authorityId"},
                "content_hash": {"$ref": "#/$defs/sha256"},
            }
        )

    defs.update(
        {
            "gamepackIdentity": identity("world-forge.gamepack"),
            "inventoryIdentity": identity("world-forge.asset_inventory"),
            "compositionIdentity": identity("world-forge.game_runtime_composition"),
            "assetReleaseIdentity": identity("world-forge.asset_release_authority"),
            "adapterIdentity": identity("world-forge.runtime_adapter"),
            "registryIdentity": identity("world-forge.runtime_adapter_registry"),
            "snapshotIdentity": identity("world-forge.game_runtime_snapshot"),
            "evidenceSetIdentity": identity("world-forge.headless_evidence_set"),
            "runtimeBundleIdentity": identity("world-forge.game_runtime_bundle"),
            "executionScriptIdentity": identity("world-forge.game_execution_script"),
            "headlessReceiptIdentity": identity("world-forge.headless_execution_receipt"),
            "extractionIdentity": identity("world-forge.game_package_extraction"),
            "assetpackIdentity": _object(
                {
                    "format": {"const": "world-forge.assetpack"},
                    "format_version": {"const": 1},
                    "id": {"$ref": "#/$defs/authorityId"},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                    "root_hash": {"$ref": "#/$defs/sha256"},
                    "inventory_hash": {"$ref": "#/$defs/sha256"},
                }
            ),
            "runtimeEvidenceReference": _object(
                {
                    "format": {"const": "world-forge.runtime_evidence"},
                    "format_version": {"const": 1},
                    "id": {"$ref": "#/$defs/authorityId"},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                    "platform": {"$ref": "#/$defs/platform"},
                    "execution_status": {"const": "headless_verified"},
                    "packaging_status": {"enum": ["unverified", "verified"]},
                }
            ),
        }
    )
    defs["headlessEvidence"] = _object(
        {
            "platform": {"$ref": "#/$defs/platform"},
            "evidence_set": {"$ref": "#/$defs/evidenceSetIdentity"},
            "runtime_bundle": {"$ref": "#/$defs/runtimeBundleIdentity"},
            "execution_script": {"$ref": "#/$defs/executionScriptIdentity"},
            "headless_receipt": {"$ref": "#/$defs/headlessReceiptIdentity"},
            "runtime_evidence": {"$ref": "#/$defs/runtimeEvidenceReference"},
        }
    )
    defs["packageEvidence"] = _object(
        {
            "package": _object(
                {
                    "format": {"const": "world-forge.game_package"},
                    "format_version": {"const": 1},
                    "id": {"$ref": "#/$defs/authorityId"},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                    "archive_sha256": {"$ref": "#/$defs/sha256"},
                    "size_bytes": {
                        "maximum": MAX_SAFE_INTEGER,
                        "minimum": 1,
                        "type": "integer",
                    },
                }
            ),
            "extraction": {"$ref": "#/$defs/extractionIdentity"},
            "standalone_game": _object(
                {
                    "format": {"const": "world-forge.standalone_game"},
                    "format_version": {"const": 1},
                    "game_id": {"$ref": "#/$defs/id"},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                }
            ),
            "payload_lock": _object(
                {
                    "format": {"const": "world-forge.standalone_game_lock"},
                    "format_version": {"const": 1},
                    "id": {"$ref": "#/$defs/authorityId"},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                    "tree_hash": {"$ref": "#/$defs/sha256"},
                }
            ),
            "runtime_bundle_hash": {"$ref": "#/$defs/sha256"},
        }
    )
    defs["supportReference"] = _object(
        {
            "format": {"const": "world-forge.runtime_support_report"},
            "format_version": {"const": 1},
            "id": {"$ref": "#/$defs/authorityId"},
            "content_hash": {"$ref": "#/$defs/sha256"},
            "compatibility_status": {"enum": ["supported", "partially_supported", "unsupported"]},
            "packaging_status": {"enum": ["unverified", "verified", "failed"]},
            "release_status": {"const": "blocked"},
            "supported": {"const": False},
        }
    )
    return _schema(
        name="runtime-support-authority.schema.json",
        title="World Forge trusted runtime support authority v1",
        format_name=RUNTIME_SUPPORT_AUTHORITY_FORMAT,
        properties={
            "authority_id": {"$ref": "#/$defs/authorityId"},
            "gamepack": {"$ref": "#/$defs/gamepackIdentity"},
            "asset_inventory": {"$ref": "#/$defs/inventoryIdentity"},
            "composition": {"$ref": "#/$defs/compositionIdentity"},
            "assetpack": {"$ref": "#/$defs/assetpackIdentity"},
            "asset_release_authority": {"$ref": "#/$defs/assetReleaseIdentity"},
            "adapter": {"$ref": "#/$defs/adapterIdentity"},
            "registry": {"$ref": "#/$defs/registryIdentity"},
            "runtime_snapshot": {"$ref": "#/$defs/snapshotIdentity"},
            "headless_evidence": _array(
                {"$ref": "#/$defs/headlessEvidence"},
                maximum=32,
            ),
            "package_evidence": {
                "oneOf": [
                    {"type": "null"},
                    {"$ref": "#/$defs/packageEvidence"},
                ]
            },
            "runtime_evidence": _array(
                {"$ref": "#/$defs/runtimeEvidenceReference"},
                maximum=32,
            ),
            "runtime_support_report": {"$ref": "#/$defs/supportReference"},
            "native_status": {"const": "unavailable"},
            "release_status": {"const": "blocked"},
            "supported": {"const": False},
            "reason_codes": _array(
                {"$ref": "#/$defs/id"},
                minimum=1,
                maximum=64,
            ),
        },
        definitions=defs,
    )


def build_schemas() -> dict[str, dict[str, Any]]:
    adapter = _adapter_schema()
    schemas = {
        "generic-runtime-adapter.schema.json": adapter,
        "game-runtime-snapshot.schema.json": _snapshot_schema(),
        "generic-runtime-adapter-registry.schema.json": _registry_schema(adapter),
        "game-runtime-composition.schema.json": _composition_schema(),
        "generic-runtime-evidence.schema.json": _evidence_schema(),
        "generic-runtime-support-report.schema.json": (_support_report_schema()),
        "runtime-support-authority.schema.json": _authority_schema(),
    }
    return dict(sorted(schemas.items()))


def build_studio_runtime_policy_module() -> bytes:
    encoded = json.dumps(
        RUNTIME_EXECUTION_SEMANTICS_POLICY,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return (
        "/* AUTO-GENERATED from the neutral Python execution policy. */\n"
        f"export const GENERIC_RUNTIME_EXECUTION_POLICY = Object.freeze({encoded});\n"
    ).encode()


def build_studio_runtime_trusted_files_module() -> bytes:
    snapshot = read_creation_object(RUNTIME_SNAPSHOT)
    registry = read_creation_object(RUNTIME_REGISTRY)
    files = capture_trusted_runtime_snapshot_files(
        snapshot=snapshot,
        registry=registry,
    )
    records = [
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
    if snapshot["files"] != records:
        raise RuntimeError("trusted Studio runtime files differ from the canonical snapshot")
    tree_hash = canonical_creation_hash({"files": records})
    if snapshot["tree_hash"] != tree_hash:
        raise RuntimeError("trusted Studio runtime tree hash differs from the canonical snapshot")
    encoded_entries = ",".join(
        "Object.freeze("
        + json.dumps(
            {
                **record,
                "base64": base64.b64encode(files[record["path"]]).decode("ascii"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + ")"
        for record in records
    )
    return (
        "/* AUTO-GENERATED from the exact neutral Python runtime snapshot policy. */\n"
        "export const GENERIC_RUNTIME_TRUSTED_SNAPSHOT = Object.freeze({"
        f'"content_hash":"{snapshot["content_hash"]}",'
        f'"files":Object.freeze([{encoded_entries}]),'
        f'"snapshot_id":"{snapshot["snapshot_id"]}","tree_hash":"{tree_hash}"'
        "});\n"
    ).encode()


def build_studio_runtime_trusted_files_types() -> bytes:
    return (
        b"export const GENERIC_RUNTIME_TRUSTED_SNAPSHOT: Readonly<{\n"
        b"  content_hash: string;\n"
        b"  files: readonly Readonly<{\n"
        b"    base64: string;\n"
        b"    path: string;\n"
        b"    sha256: string;\n"
        b"    size_bytes: number;\n"
        b"  }>[];\n"
        b"  snapshot_id: string;\n"
        b"  tree_hash: string;\n"
        b"}>;\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    mismatches: list[str] = []
    for name, schema in build_schemas().items():
        path = SCHEMAS / name
        payload = canonical_json_bytes(schema)
        if args.check:
            if not path.exists() or path.read_bytes() != payload:
                mismatches.append(name)
        else:
            path.write_bytes(payload)
    policy_payload = build_studio_runtime_policy_module()
    if args.check:
        if (
            not STUDIO_RUNTIME_POLICY.exists()
            or STUDIO_RUNTIME_POLICY.read_bytes() != policy_payload
        ):
            mismatches.append(STUDIO_RUNTIME_POLICY.name)
    else:
        STUDIO_RUNTIME_POLICY.write_bytes(policy_payload)
    trusted_files_payload = build_studio_runtime_trusted_files_module()
    trusted_files_types = build_studio_runtime_trusted_files_types()
    for path, payload in (
        (STUDIO_RUNTIME_TRUSTED_FILES, trusted_files_payload),
        (STUDIO_RUNTIME_TRUSTED_FILES_TYPES, trusted_files_types),
    ):
        if args.check:
            if not path.exists() or path.read_bytes() != payload:
                mismatches.append(path.name)
        else:
            path.write_bytes(payload)
    if mismatches:
        raise SystemExit("generic runtime schemas are stale: " + ", ".join(mismatches))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
