"""Generate the additive pre-execution generic game runtime bundle v1 schema."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "schemas" / "game-runtime-bundle.schema.json"

ID_PATTERN = r"^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$"
PATH_PATTERN = (
    r"^(?![./])(?!.*/(?:\.{1,2})(?:/|$))"
    r"(?!(?:.*[/])?(?:aux|con|nul|prn|com[1-9]|lpt[1-9])(?:[./]|$))"
    r"[a-zA-Z0-9_][a-zA-Z0-9_.@ -]*(?:/[a-zA-Z0-9_][a-zA-Z0-9_.@ -]*)*$"
)


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "type": "object",
    }


def _identity(format_name: str, path: str) -> dict[str, Any]:
    return _object(
        {
            "path": {"const": path},
            "format": {"const": format_name},
            "format_version": {"const": 1},
            "id": {"$ref": "#/$defs/id"},
            "content_hash": {"$ref": "#/$defs/sha256"},
        }
    )


def build_schema() -> dict[str, Any]:
    file_record = _object(
        {
            "path": {"$ref": "#/$defs/path"},
            "sha256": {"$ref": "#/$defs/sha256"},
            "size_bytes": {
                "maximum": 4 * 1024 * 1024,
                "minimum": 0,
                "type": "integer",
            },
        }
    )
    definitions: dict[str, Any] = {
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
        "file": file_record,
        "gamepackIdentity": _identity(
            "world-forge.gamepack",
            "contracts/gamepack.json",
        ),
        "snapshotIdentity": _identity(
            "world-forge.game_runtime_snapshot",
            "contracts/runtime-snapshot.json",
        ),
        "registryIdentity": _identity(
            "world-forge.runtime_adapter_registry",
            "contracts/runtime-adapter-registry.json",
        ),
        "compositionIdentity": _identity(
            "world-forge.game_runtime_composition",
            "contracts/runtime-composition.json",
        ),
        "supportIdentity": _identity(
            "world-forge.runtime_support_report",
            "status/runtime-support-report.json",
        ),
        "assetpackIdentity": _identity(
            "world-forge.assetpack",
            "assetpack/assetpack.json",
        ),
        "adapterIdentity": _object(
            {
                "path": {
                    "pattern": (
                        "^runtime/snapshot-tree/descriptors/"
                        "[a-z][a-z0-9_]{1,63}@"
                        "(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\."
                        "(0|[1-9][0-9]*)\\.json$"
                    ),
                    "type": "string",
                },
                "format": {"const": "world-forge.runtime_adapter"},
                "format_version": {"const": 1},
                "id": {"$ref": "#/$defs/id"},
                "adapter_version": {"$ref": "#/$defs/semver"},
                "content_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "binding": _object(
            {
                "binding_id": {"$ref": "#/$defs/id"},
                "asset_id": {"$ref": "#/$defs/id"},
                "role": {"$ref": "#/$defs/id"},
                "media_type": {
                    "maxLength": 128,
                    "minLength": 1,
                    "type": "string",
                },
                "runtime_path": {"$ref": "#/$defs/path"},
                "bundle_path": {
                    "allOf": [
                        {"$ref": "#/$defs/path"},
                        {"pattern": "^assetpack/", "type": "string"},
                    ]
                },
                "sha256": {"$ref": "#/$defs/sha256"},
                "size_bytes": {
                    "maximum": 4 * 1024 * 1024,
                    "minimum": 1,
                    "type": "integer",
                },
            }
        ),
    }
    file_array = {
        "items": {"$ref": "#/$defs/file"},
        "maxItems": 256,
        "minItems": 1,
        "type": "array",
        "uniqueItems": True,
        "x-world-forge-canonical-object-array": {
            "orderBy": ["path"],
            "uniqueBy": [["path"]],
        },
    }
    properties = {
        "format": {"const": "world-forge.game_runtime_bundle"},
        "format_version": {"const": 1},
        "bundle_id": {
            "pattern": "^game_runtime_bundle_[0-9a-f]{48}$",
            "type": "string",
        },
        "state": {"const": "pre_execution"},
        "contracts": _object(
            {
                "gamepack": {"$ref": "#/$defs/gamepackIdentity"},
                "runtime_snapshot": {"$ref": "#/$defs/snapshotIdentity"},
                "runtime_adapter": {"$ref": "#/$defs/adapterIdentity"},
                "runtime_adapter_registry": {"$ref": "#/$defs/registryIdentity"},
                "runtime_composition": {"$ref": "#/$defs/compositionIdentity"},
                "runtime_support_report": {"$ref": "#/$defs/supportIdentity"},
            }
        ),
        "assetpack": _object(
            {
                "root": {"const": "assetpack"},
                "manifest": {"$ref": "#/$defs/assetpackIdentity"},
                "root_hash": {"$ref": "#/$defs/sha256"},
                "inventory_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "runtime_snapshot_tree": _object(
            {
                "root": {"const": "runtime/snapshot-tree"},
                "runtime_api": _object(
                    {
                        "id": {"const": "gamepack_runtime"},
                        "version": {"const": "1.0.0"},
                    }
                ),
                "tree_hash": {"$ref": "#/$defs/sha256"},
                "file_count": {
                    "maximum": 256,
                    "minimum": 1,
                    "type": "integer",
                },
                "total_bytes": {
                    "maximum": 32 * 1024 * 1024,
                    "minimum": 1,
                    "type": "integer",
                },
            }
        ),
        "bindings": {
            "items": {"$ref": "#/$defs/binding"},
            "maxItems": 256,
            "minItems": 1,
            "type": "array",
            "x-world-forge-canonical-object-array": {
                "orderBy": ["binding_id"],
                "uniqueBy": [["binding_id"]],
            },
        },
        "legal": _object(
            {
                "bundle_license": {
                    "allOf": [
                        {"$ref": "#/$defs/file"},
                        {
                            "properties": {
                                "path": {"const": "licenses/world-forge-mit.txt"},
                                "sha256": {
                                    "const": (
                                        "2e55c53ff294650e049d844f2544fec947c3516440"
                                        "aeffca4b2334cf94b13eeb"
                                    )
                                },
                                "size_bytes": {"const": 1063},
                            },
                            "type": "object",
                        },
                    ]
                },
                "asset_notices": {
                    **file_array,
                    "minItems": 0,
                },
            }
        ),
        "files": file_array,
        "tree_hash": {"$ref": "#/$defs/sha256"},
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": "https://world-forge.local/schemas/game-runtime-bundle.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "title": "World Forge pre-execution game runtime bundle v1",
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-game-runtime-bundle-coherent": True,
        "$defs": definitions,
    }


def generate(*, check: bool = False) -> None:
    payload = canonical_json_bytes(build_schema())
    if check:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != payload:
            raise SystemExit(f"{OUTPUT} is out of date")
        return
    OUTPUT.write_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generate(check=args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
