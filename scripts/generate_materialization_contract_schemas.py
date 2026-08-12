"""Generate additive executable-materialization contract schemas."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = {
    ROOT / "schemas/runtime-implementation.schema.json": "runtime_implementation",
    ROOT / "schemas/runtime-platform-lock.schema.json": "runtime_platform_lock",
    ROOT / "schemas/game-materialization-bundle.schema.json": "game_materialization_bundle",
    ROOT / "schemas/standalone-game.schema.json": "standalone_game",
    ROOT / "schemas/standalone-game-lock.schema.json": "standalone_game_lock",
    ROOT / "schemas/standalone-platform.schema.json": "standalone_platform",
    ROOT / "schemas/game-package.schema.json": "game_package",
    ROOT / "schemas/game-package-extraction.schema.json": "game_package_extraction",
}

ID_PATTERN = r"^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$"
PATH_PATTERN = (
    r"^(?![./])(?!.*/(?:\.{1,2})(?:/|$))"
    r"(?!(?:.*[/])?(?:aux|con|nul|prn|com[1-9]|lpt[1-9])(?:[./]|$))"
    r"[a-zA-Z0-9_][a-zA-Z0-9_.@ -]*(?:/[a-zA-Z0-9_.@ -]+)*$"
)
GAME_PACKAGE_PATH_PATTERN = (
    r"^(?!(?:[Aa][Uu][Xx]|[Cc][Oo][Nn]|[Nn][Uu][Ll]|[Pp][Rr][Nn]"
    r"|[Cc][Oo][Mm][1-9]|[Ll][Pp][Tt][1-9])(?:[.]|/|$))"
    r"[A-Za-z0-9_.@ -]*[A-Za-z0-9_@-]"
    r"(?:/(?!(?:[Aa][Uu][Xx]|[Cc][Oo][Nn]|[Nn][Uu][Ll]|[Pp][Rr][Nn]"
    r"|[Cc][Oo][Mm][1-9]|[Ll][Pp][Tt][1-9])(?:[.]|/|$))"
    r"[A-Za-z0-9_.@ -]*[A-Za-z0-9_@-])*$"
)


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "type": "object",
    }


def _definitions() -> dict[str, Any]:
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
        "file": _object(
            {
                "path": {"$ref": "#/$defs/path"},
                "sha256": {"$ref": "#/$defs/sha256"},
                "size_bytes": {
                    "maximum": 32 * 1024 * 1024,
                    "minimum": 0,
                    "type": "integer",
                },
            }
        ),
    }


def _canonical_array(
    items: dict[str, Any],
    *,
    order_by: str,
    minimum: int = 1,
    maximum: int = 512,
) -> dict[str, Any]:
    return {
        "items": items,
        "maxItems": maximum,
        "minItems": minimum,
        "type": "array",
        "uniqueItems": True,
        "x-world-forge-canonical-object-array": {
            "orderBy": [order_by],
            "uniqueBy": [[order_by]],
        },
    }


def build_runtime_platform_lock_schema() -> dict[str, Any]:
    definitions = _definitions()
    properties = {
        "format": {"const": "world-forge.runtime_platform_lock"},
        "format_version": {"const": 1},
        "lock_id": {
            "pattern": "^runtime_platform_lock_[0-9a-f]{40}$",
            "type": "string",
        },
        "platform": _object(
            {
                "os": {"enum": ["linux", "windows"]},
                "architecture": {"const": "x86_64"},
                "backend": {"const": "backend:raylib"},
                "renderer": {"const": "raylib"},
            }
        ),
        "python": _object(
            {
                "implementation": {"const": "cpython"},
                "minor": {"enum": ["3.11", "3.12"]},
                "abi": {"enum": ["cp311", "cp312"]},
                "requires_python": {"const": ">=3.11,<3.13"},
            }
        ),
        "dependency": _object(
            {
                "distribution": {"const": "raylib"},
                "version": {"const": "6.0.1.0"},
                "pin": {"const": "raylib==6.0.1.0"},
                "import_module": {"const": "pyray"},
                "native_api": {"const": "raylib-5.5"},
                "artifact": _object(
                    {
                        "filename": {
                            "pattern": "^raylib-6\\.0\\.1\\.0-cp31[12]-cp31[12]-.*\\.whl$",
                            "type": "string",
                        },
                        "size_bytes": {
                            "maximum": 16 * 1024 * 1024,
                            "minimum": 1,
                            "type": "integer",
                        },
                        "url": {"const": "https://pypi.org/project/raylib/6.0.1.0/#files"},
                        "sha256": {"$ref": "#/$defs/sha256"},
                    }
                ),
            }
        ),
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": "https://world-forge.local/schemas/runtime-platform-lock.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "title": "World Forge audited runtime platform lock v1",
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-runtime-platform-lock-audited": True,
        "$defs": definitions,
    }


def build_runtime_implementation_schema() -> dict[str, Any]:
    definitions = _definitions()
    package_file_array = _canonical_array(
        {"$ref": "#/$defs/file"},
        order_by="path",
        maximum=64,
    )
    properties = {
        "format": {"const": "world-forge.runtime_implementation"},
        "format_version": {"const": 1},
        "implementation_id": {
            "pattern": "^runtime_implementation_[0-9a-f]{40}$",
            "type": "string",
        },
        "adapter": _object(
            {
                "adapter_id": {"$ref": "#/$defs/id"},
                "adapter_version": {"$ref": "#/$defs/semver"},
                "content_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "snapshot": _object(
            {
                "snapshot_id": {"$ref": "#/$defs/id"},
                "content_hash": {"$ref": "#/$defs/sha256"},
                "tree_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "runtime_api": _object(
            {
                "id": {"const": "gamepack_runtime"},
                "version": {"const": "1.0.0"},
            }
        ),
        "packages": {
            "items": _object(
                {
                    "package": {"enum": ["gamepack_raylib_2d", "gamepack_runtime"]},
                    "source_prefix": {"enum": ["gamepack_raylib_2d", "gamepack_runtime"]},
                    "destination_root": {
                        "enum": [
                            "src/gamepack_raylib_2d",
                            "src/gamepack_runtime",
                        ]
                    },
                    "role": {"enum": ["raylib_2d_adapter", "deterministic_kernel"]},
                    "classification": {"const": "immutable_runtime_source"},
                    "files": package_file_array,
                    "tree_hash": {"$ref": "#/$defs/sha256"},
                }
            ),
            "maxItems": 2,
            "minItems": 2,
            "type": "array",
            "uniqueItems": True,
            "x-world-forge-canonical-object-array": {
                "orderBy": ["package"],
                "uniqueBy": [["package"], ["destination_root"]],
            },
        },
        "entry_points": {
            "items": _object(
                {
                    "role": {
                        "enum": [
                            "application_factory",
                            "backend_factory",
                            "bundle_loader",
                            "native_smoke",
                        ]
                    },
                    "module": {
                        "enum": [
                            "gamepack_raylib_2d.app",
                            "gamepack_raylib_2d.backend",
                            "gamepack_raylib_2d.native_smoke",
                            "gamepack_raylib_2d.resources",
                        ]
                    },
                    "symbol": {
                        "enum": [
                            "PyrayBackend",
                            "RuntimeApp.from_bundle",
                            "load_runtime_bundle",
                            "native_smoke",
                        ]
                    },
                }
            ),
            "maxItems": 4,
            "minItems": 4,
            "type": "array",
            "uniqueItems": True,
            "x-world-forge-canonical-object-array": {
                "orderBy": ["role"],
                "uniqueBy": [["role"]],
            },
        },
        "platform_locks": _canonical_array(
            _object(
                {
                    "lock_id": {"$ref": "#/$defs/id"},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                    "os": {"enum": ["linux", "windows"]},
                    "python_minor": {"enum": ["3.11", "3.12"]},
                    "abi": {"enum": ["cp311", "cp312"]},
                }
            ),
            order_by="lock_id",
            minimum=4,
            maximum=4,
        ),
        "materialization_policy": _object(
            {
                "version": {"const": 1},
                "standalone_source_root": {"const": "src"},
                "immutable_runtime": {"const": True},
                "runtime_ai": {"const": False},
            }
        ),
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": "https://world-forge.local/schemas/runtime-implementation.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "title": "World Forge executable runtime implementation identity v1",
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-runtime-implementation-coherent": True,
        "$defs": definitions,
    }


def build_game_materialization_bundle_schema() -> dict[str, Any]:
    definitions = _definitions()
    file_array = _canonical_array(
        {"$ref": "#/$defs/file"},
        order_by="path",
        maximum=512,
    )
    lock_identity = _object(
        {
            "path": {"$ref": "#/$defs/path"},
            "format": {"const": "world-forge.runtime_platform_lock"},
            "format_version": {"const": 1},
            "id": {"$ref": "#/$defs/id"},
            "content_hash": {"$ref": "#/$defs/sha256"},
            "os": {"enum": ["linux", "windows"]},
            "python_minor": {"enum": ["3.11", "3.12"]},
            "abi": {"enum": ["cp311", "cp312"]},
        }
    )
    lineage_names = (
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
    )
    properties = {
        "format": {"const": "world-forge.game_materialization_bundle"},
        "format_version": {"const": 1},
        "materialization_bundle_id": {
            "pattern": "^game_materialization_bundle_[0-9a-f]{36}$",
            "type": "string",
        },
        "state": {"enum": ["contract_only", "materialization_ready"]},
        "materialization_ready": {"type": "boolean"},
        "missing_launcher_roles": {
            "items": {
                "enum": [
                    "game_launcher",
                    "game_packager",
                    "game_verifier",
                    "native_smoke_launcher",
                ]
            },
            "maxItems": 4,
            "type": "array",
            "uniqueItems": True,
        },
        "runtime_bundle": _object(
            {
                "root": {"const": "runtime-bundle"},
                "manifest": _object(
                    {
                        "path": {"const": "runtime-bundle/game-runtime-bundle.json"},
                        "format": {"const": "world-forge.game_runtime_bundle"},
                        "format_version": {"const": 1},
                        "id": {
                            "pattern": "^game_runtime_bundle_[0-9a-f]{48}$",
                            "type": "string",
                        },
                        "content_hash": {"$ref": "#/$defs/sha256"},
                        "tree_hash": {"$ref": "#/$defs/sha256"},
                    }
                ),
            }
        ),
        "runtime_implementation": _object(
            {
                "path": {"const": "contracts/runtime-implementation.json"},
                "format": {"const": "world-forge.runtime_implementation"},
                "format_version": {"const": 1},
                "id": {"$ref": "#/$defs/id"},
                "content_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "platform_locks": _object(
            {
                "root": {"const": "contracts/platform-locks"},
                "set_hash": {"$ref": "#/$defs/sha256"},
                "locks": _canonical_array(
                    lock_identity,
                    order_by="id",
                    minimum=4,
                    maximum=4,
                ),
            }
        ),
        "launchers": _object(
            {
                "root": {"const": "launchers"},
                "policy_version": {"const": 1},
                "required_roles": {
                    "const": [
                        "game_launcher",
                        "game_packager",
                        "game_verifier",
                        "native_smoke_launcher",
                    ],
                    "type": "array",
                },
                "inventory": _canonical_array(
                    _object(
                        {
                            "path": {
                                "maxLength": 1024,
                                "minLength": 1,
                                "type": "string",
                            },
                            "output_path": {
                                "maxLength": 1024,
                                "minLength": 1,
                                "type": "string",
                            },
                            "role": {
                                "enum": [
                                    "game_launcher",
                                    "game_package",
                                    "game_packager",
                                    "game_readme",
                                    "game_source",
                                    "game_test",
                                    "game_verifier",
                                    "gitignore",
                                    "materialization_policy",
                                    "native_smoke_launcher",
                                    "offline_smoke_launcher",
                                    "requirements",
                                    "third_party_notices",
                                ]
                            },
                            "sha256": {"$ref": "#/$defs/sha256"},
                            "size_bytes": {
                                "maximum": 64 * 1024,
                                "minimum": 1,
                                "type": "integer",
                            },
                        }
                    ),
                    order_by="path",
                    maximum=14,
                ),
                "tree_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "lineage": _object({name: {"$ref": "#/$defs/sha256"} for name in lineage_names}),
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
                }
            }
        ),
        "files": file_array,
        "tree_hash": {"$ref": "#/$defs/sha256"},
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": "https://world-forge.local/schemas/game-materialization-bundle.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "title": "World Forge executable game materialization envelope v1",
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-game-materialization-bundle-coherent": True,
        "$defs": definitions,
        "allOf": [
            {
                "if": {
                    "properties": {"materialization_ready": {"const": True}},
                    "required": ["materialization_ready"],
                },
                "then": {
                    "properties": {
                        "state": {"const": "materialization_ready"},
                        "missing_launcher_roles": {"const": []},
                    }
                },
                "else": {
                    "properties": {
                        "state": {"const": "contract_only"},
                        "missing_launcher_roles": {
                            "const": [
                                "game_launcher",
                                "game_packager",
                                "game_verifier",
                                "native_smoke_launcher",
                            ]
                        },
                    }
                },
            }
        ],
    }


def build_standalone_game_lock_schema() -> dict[str, Any]:
    definitions = _definitions()
    properties = {
        "format": {"const": "world-forge.standalone_game_lock"},
        "format_version": {"const": 1},
        "lock_id": {
            "pattern": "^standalone_game_lock_[0-9a-f]{40}$",
            "type": "string",
        },
        "files": _canonical_array(
            {"$ref": "#/$defs/file"},
            order_by="path",
            maximum=768,
        ),
        "tree_hash": {"$ref": "#/$defs/sha256"},
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": "https://world-forge.local/schemas/standalone-game-lock.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "title": "World Forge standalone payload lock v1",
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-standalone-game-lock-coherent": True,
        "$defs": definitions,
    }


def build_standalone_platform_schema() -> dict[str, Any]:
    definitions = _definitions()
    lock = _object(
        {
            "lock_id": {"$ref": "#/$defs/id"},
            "content_hash": {"$ref": "#/$defs/sha256"},
            "os": {"enum": ["linux", "windows"]},
            "python_minor": {"enum": ["3.11", "3.12"]},
            "abi": {"enum": ["cp311", "cp312"]},
        }
    )
    properties = {
        "format": {"const": "world-forge.standalone_platform"},
        "format_version": {"const": 1},
        "platform_set_id": {
            "pattern": "^standalone_platform_[0-9a-f]{40}$",
            "type": "string",
        },
        "requires_python": {"const": ">=3.11,<3.13"},
        "dependency": _object(
            {
                "distribution": {"const": "raylib"},
                "version": {"const": "6.0.1.0"},
                "pin": {"const": "raylib==6.0.1.0"},
                "import_module": {"const": "pyray"},
                "native_api": {"const": "raylib-5.5"},
            }
        ),
        "adapter": _object(
            {
                "adapter_id": {"$ref": "#/$defs/id"},
                "adapter_version": {"$ref": "#/$defs/semver"},
                "content_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "runtime_implementation": _object(
            {
                "implementation_id": {"$ref": "#/$defs/id"},
                "content_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "runtime_snapshot": _object(
            {
                "snapshot_id": {"$ref": "#/$defs/id"},
                "content_hash": {"$ref": "#/$defs/sha256"},
                "tree_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "platform_locks": _canonical_array(
            lock,
            order_by="lock_id",
            minimum=4,
            maximum=4,
        ),
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": "https://world-forge.local/schemas/standalone-platform.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "title": "World Forge standalone runtime platform set v1",
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-standalone-platform-coherent": True,
        "$defs": definitions,
    }


def build_standalone_game_schema() -> dict[str, Any]:
    definitions = _definitions()

    def identity(format_name: str) -> dict[str, Any]:
        return _object(
            {
                "format": {"const": format_name},
                "format_version": {"const": 1},
                "id": {"$ref": "#/$defs/id"},
                "content_hash": {"$ref": "#/$defs/sha256"},
            }
        )

    properties = {
        "format": {"const": "world-forge.standalone_game"},
        "format_version": {"const": 1},
        "game_id": {"$ref": "#/$defs/id"},
        "state": {"const": "materialized"},
        "lineage": _object(
            {
                name: {"$ref": "#/$defs/sha256"}
                for name in (
                    "gamepack_hash",
                    "assetpack_hash",
                    "runtime_snapshot_hash",
                    "runtime_composition_hash",
                    "runtime_bundle_hash",
                )
            }
        ),
        "materialization_bundle": identity("world-forge.game_materialization_bundle"),
        "runtime_implementation": identity("world-forge.runtime_implementation"),
        "platform_set": identity("world-forge.standalone_platform"),
        "payload_lock": _object(
            {
                **identity("world-forge.standalone_game_lock")["properties"],
                "tree_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "entry_points": _object(
            {
                "game": {"const": "run_game.py"},
                "verifier": {"const": "scripts/verify_game.py"},
                "offline_smoke": {"const": "scripts/offline_smoke.py"},
                "native_smoke": {"const": "scripts/native_smoke.py"},
            }
        ),
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": "https://world-forge.local/schemas/standalone-game.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "title": "World Forge standalone game manifest v1",
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-standalone-game-coherent": True,
        "$defs": definitions,
    }


def build_game_package_schema() -> dict[str, Any]:
    definitions = _definitions()
    definitions["path"] = {
        "maxLength": 1024,
        "minLength": 1,
        "pattern": GAME_PACKAGE_PATH_PATTERN,
        "type": "string",
    }
    identity = _object(
        {
            "format": {"const": "world-forge.standalone_game"},
            "format_version": {"const": 1},
            "game_id": {"$ref": "#/$defs/id"},
            "content_hash": {"$ref": "#/$defs/sha256"},
        }
    )
    lock_identity = _object(
        {
            "format": {"const": "world-forge.standalone_game_lock"},
            "format_version": {"const": 1},
            "id": {"$ref": "#/$defs/id"},
            "content_hash": {"$ref": "#/$defs/sha256"},
            "tree_hash": {"$ref": "#/$defs/sha256"},
        }
    )
    properties = {
        "format": {"const": "world-forge.game_package"},
        "format_version": {"const": 1},
        "package_id": {
            "pattern": "^game_package_[0-9a-f]{40}$",
            "type": "string",
        },
        "game_id": {"$ref": "#/$defs/id"},
        "lineage": _object(
            {
                name: {"$ref": "#/$defs/sha256"}
                for name in (
                    "gamepack_hash",
                    "assetpack_hash",
                    "runtime_snapshot_hash",
                    "runtime_composition_hash",
                    "runtime_bundle_hash",
                )
            }
        ),
        "standalone_game": identity,
        "payload_lock": lock_identity,
        "files": _canonical_array(
            {"$ref": "#/$defs/file"},
            order_by="path",
            minimum=2,
            maximum=768,
        ),
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": "https://world-forge.local/schemas/game-package.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "title": "World Forge deterministic standalone game package manifest v1",
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-game-package-coherent": True,
        "$defs": definitions,
    }


def build_game_package_extraction_schema() -> dict[str, Any]:
    definitions = _definitions()
    lineage = _object(
        {
            name: {"$ref": "#/$defs/sha256"}
            for name in (
                "gamepack_hash",
                "assetpack_hash",
                "runtime_snapshot_hash",
                "runtime_composition_hash",
                "runtime_bundle_hash",
            )
        }
    )
    standalone_identity = _object(
        {
            "format": {"const": "world-forge.standalone_game"},
            "format_version": {"const": 1},
            "game_id": {"$ref": "#/$defs/id"},
            "content_hash": {"$ref": "#/$defs/sha256"},
        }
    )
    lock_identity = _object(
        {
            "format": {"const": "world-forge.standalone_game_lock"},
            "format_version": {"const": 1},
            "id": {"$ref": "#/$defs/id"},
            "content_hash": {"$ref": "#/$defs/sha256"},
            "tree_hash": {"$ref": "#/$defs/sha256"},
        }
    )
    properties = {
        "format": {"const": "world-forge.game_package_extraction"},
        "format_version": {"const": 1},
        "extraction_id": {
            "pattern": "^game_package_extraction_[0-9a-f]{40}$",
            "type": "string",
        },
        "package": _object(
            {
                "format": {"const": "world-forge.game_package"},
                "format_version": {"const": 1},
                "id": {
                    "pattern": "^game_package_[0-9a-f]{40}$",
                    "type": "string",
                },
                "content_hash": {"$ref": "#/$defs/sha256"},
                "archive_sha256": {"$ref": "#/$defs/sha256"},
                "size_bytes": {
                    "maximum": 264 * 1024 * 1024,
                    "minimum": 1,
                    "type": "integer",
                },
            }
        ),
        "standalone_game": standalone_identity,
        "payload_lock": lock_identity,
        "lineage": lineage,
        "extracted_tree_hash": {"$ref": "#/$defs/sha256"},
        "verification_status": {"const": "verified"},
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": "https://world-forge.local/schemas/game-package-extraction.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "title": "World Forge game package extraction evidence v1",
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-game-package-extraction-coherent": True,
        "$defs": definitions,
    }


def generate(*, check: bool = False) -> None:
    documents = {
        "runtime_implementation": build_runtime_implementation_schema(),
        "runtime_platform_lock": build_runtime_platform_lock_schema(),
        "game_materialization_bundle": build_game_materialization_bundle_schema(),
        "standalone_game": build_standalone_game_schema(),
        "standalone_game_lock": build_standalone_game_lock_schema(),
        "standalone_platform": build_standalone_platform_schema(),
        "game_package": build_game_package_schema(),
        "game_package_extraction": build_game_package_extraction_schema(),
    }
    for path, key in OUTPUTS.items():
        payload = canonical_json_bytes(documents[key])
        if check:
            if not path.exists() or path.read_bytes() != payload:
                raise SystemExit(f"{path} is out of date")
        else:
            path.write_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generate(check=args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
