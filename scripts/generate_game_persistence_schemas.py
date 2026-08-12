"""Generate additive generic game save and replay v1 schemas."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
        "type": "object",
    }


def _array(
    items: dict[str, Any],
    *,
    maximum: int,
    unique: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "items": items,
        "maxItems": maximum,
        "type": "array",
    }
    if unique:
        result["uniqueItems"] = True
    return result


def _identity(format_name: str) -> dict[str, Any]:
    return _object(
        {
            "format": {"const": format_name},
            "format_version": {"const": 1},
            "id": {
                "maxLength": 128,
                "minLength": 2,
                "type": "string",
            },
            "content_hash": {"$ref": "#/$defs/sha256"},
        }
    )


def _definitions() -> dict[str, Any]:
    return {
        "sha256": {
            "pattern": "^[0-9a-f]{64}$",
            "type": "string",
        },
        "runtimeApi": _object(
            {
                "id": {"const": "gamepack_runtime"},
                "version": {"const": "1.0.0"},
            }
        ),
        "executionSemantics": _object(
            {
                "version": {"const": 1},
                "content_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "bindings": _object(
            {
                "gamepack": _identity("world-forge.gamepack"),
                "runtime_composition": _identity("world-forge.game_runtime_composition"),
                "runtime_bundle": _identity("world-forge.game_runtime_bundle"),
                "runtime_api": {"$ref": "#/$defs/runtimeApi"},
                "execution_semantics": {
                    "$ref": "#/$defs/executionSemantics",
                },
            }
        ),
        "runtimeId": {
            "maxLength": 64,
            "minLength": 2,
            "pattern": ("^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$"),
            "type": "string",
        },
        "classification": _object(
            {
                "goal_ids": _array(
                    {"$ref": "#/$defs/runtimeId"},
                    maximum=256,
                    unique=True,
                ),
                "ending_ids": _array(
                    {"$ref": "#/$defs/runtimeId"},
                    maximum=64,
                    unique=True,
                ),
                "ending_kind": {
                    "oneOf": [
                        {"type": "null"},
                        {
                            "maxLength": 64,
                            "minLength": 1,
                            "type": "string",
                        },
                    ]
                },
                "failure_ids": _array(
                    {"$ref": "#/$defs/runtimeId"},
                    maximum=256,
                    unique=True,
                ),
                "recovery_action_ids": _array(
                    {"$ref": "#/$defs/runtimeId"},
                    maximum=256,
                    unique=True,
                ),
                "terminal": {"type": "boolean"},
            }
        ),
        "stateValue": {
            "oneOf": [
                {"type": "boolean"},
                {
                    "maximum": 9_007_199_254_740_991,
                    "minimum": -9_007_199_254_740_991,
                    "type": "integer",
                },
                {
                    "maxLength": 4096,
                    "type": "string",
                },
                _array(
                    {
                        "maxLength": 4096,
                        "type": "string",
                    },
                    maximum=256,
                    unique=True,
                ),
            ]
        },
        "state": {
            "additionalProperties": {"$ref": "#/$defs/stateValue"},
            "maxProperties": 256,
            "type": "object",
        },
        "parameters": {
            "additionalProperties": {"$ref": "#/$defs/stateValue"},
            "maxProperties": 256,
            "type": "object",
        },
    }


def _base_schema(
    *,
    name: str,
    title: str,
    format_name: str,
    properties: dict[str, Any],
    coherent: str,
) -> dict[str, Any]:
    fields = {
        "format": {"const": format_name},
        "format_version": {"const": 1},
        **properties,
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": f"https://world-forge.local/schemas/{name}",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": fields,
        "required": list(fields),
        "title": title,
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-game-persistence-coherent": coherent,
        "$defs": _definitions(),
    }


def _save_schema() -> dict[str, Any]:
    return _base_schema(
        name="game-save.schema.json",
        title="World Forge deterministic game save v1",
        format_name="world-forge.game_save",
        coherent="game_save",
        properties={
            "save_id": {
                "pattern": "^game_save_[0-9a-f]{48}$",
                "type": "string",
            },
            "bindings": {"$ref": "#/$defs/bindings"},
            "state": _object(
                {
                    "saved": {"$ref": "#/$defs/state"},
                    "saved_hash": {"$ref": "#/$defs/sha256"},
                    "restored_state_hash": {"$ref": "#/$defs/sha256"},
                    "classification": {"$ref": "#/$defs/classification"},
                }
            ),
        },
    )


def _replay_schema() -> dict[str, Any]:
    step = _object(
        {
            "index": {
                "maximum": 127,
                "minimum": 0,
                "type": "integer",
            },
            "action_id": {"$ref": "#/$defs/runtimeId"},
            "parameters": {"$ref": "#/$defs/parameters"},
            "pre_state_hash": {"$ref": "#/$defs/sha256"},
            "post_state_hash": {"$ref": "#/$defs/sha256"},
            "events": _array(
                {"$ref": "#/$defs/runtimeId"},
                maximum=256,
            ),
        }
    )
    return _base_schema(
        name="game-replay.schema.json",
        title="World Forge deterministic accepted-action replay v1",
        format_name="world-forge.game_replay",
        coherent="game_replay",
        properties={
            "replay_id": {
                "pattern": "^game_replay_[0-9a-f]{48}$",
                "type": "string",
            },
            "bindings": {"$ref": "#/$defs/bindings"},
            "initial_state_hash": {"$ref": "#/$defs/sha256"},
            "steps": _array(step, maximum=128),
            "final_state_hash": {"$ref": "#/$defs/sha256"},
            "classification": {"$ref": "#/$defs/classification"},
            "trace_hash": {"$ref": "#/$defs/sha256"},
        },
    )


def _generation_schema() -> dict[str, Any]:
    fields = {
        "format": {"const": "world-forge.persistence_generation"},
        "format_version": {"const": 1},
        "kind": {"enum": ["replay", "save"]},
        "slot": {
            "maxLength": 32,
            "pattern": "^[a-z][a-z0-9_-]{0,31}$",
            "type": "string",
        },
        "sequence": {
            "maximum": 9_007_199_254_740_991,
            "minimum": 0,
            "type": "integer",
        },
        "parent_hashes": _array(
            {"$ref": "#/$defs/sha256"},
            maximum=128,
            unique=True,
        ),
        "operation": {
            "enum": [
                "conflict_resolution",
                "legacy_migration",
                "rollback",
                "write",
            ]
        },
        "payload": {
            "oneOf": [
                {"$ref": "game-replay.schema.json"},
                {"$ref": "game-save.schema.json"},
            ]
        },
        "payload_hash": {"$ref": "#/$defs/sha256"},
        "content_hash": {"$ref": "#/$defs/sha256"},
    }
    return {
        "$id": "https://world-forge.local/schemas/persistence-generation.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "allOf": [
            {
                "if": {"properties": {"kind": {"const": "replay"}}},
                "then": {
                    "properties": {
                        "payload": {"$ref": "game-replay.schema.json"},
                    }
                },
            },
            {
                "if": {"properties": {"kind": {"const": "save"}}},
                "then": {
                    "properties": {
                        "payload": {"$ref": "game-save.schema.json"},
                    }
                },
            },
        ],
        "properties": fields,
        "required": list(fields),
        "title": "World Forge immutable persistence generation v1",
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-persistence-generation-coherent": True,
        "$defs": {
            "sha256": {
                "pattern": "^[0-9a-f]{64}$",
                "type": "string",
            }
        },
    }


def build_schemas() -> dict[str, dict[str, Any]]:
    return {
        "schemas/game-replay.schema.json": _replay_schema(),
        "schemas/game-save.schema.json": _save_schema(),
        "schemas/persistence-generation.schema.json": _generation_schema(),
    }


def generate(*, check: bool = False) -> None:
    for relative, schema in build_schemas().items():
        path = ROOT / relative
        payload = canonical_json_bytes(schema)
        if check:
            if not path.exists() or path.read_bytes() != payload:
                raise SystemExit(f"{path} is out of date")
        else:
            path.write_bytes(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generate(check=args.check)


if __name__ == "__main__":
    main()
