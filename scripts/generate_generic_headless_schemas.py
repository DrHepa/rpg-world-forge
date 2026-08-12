"""Generate additive generic headless execution v1 schemas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from worldforge.generic_headless import headless_authority_result_policy_document
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
    minimum: int = 0,
    unique: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "items": items,
        "maxItems": maximum,
        "minItems": minimum,
        "type": "array",
    }
    if unique:
        result["uniqueItems"] = True
    return result


def _identity(format_name: str | None = None) -> dict[str, Any]:
    return _object(
        {
            "format": (
                {"const": format_name}
                if format_name is not None
                else {"maxLength": 128, "minLength": 1, "type": "string"}
            ),
            "format_version": {"const": 1},
            "id": {"maxLength": 128, "minLength": 2, "type": "string"},
            "content_hash": {"$ref": "#/$defs/sha256"},
        }
    )


def _classification() -> dict[str, Any]:
    ids = _array(
        {"$ref": "#/$defs/id"},
        maximum=256,
        unique=True,
    )
    return _object(
        {
            "goal_ids": ids,
            "ending_ids": _array(
                {"$ref": "#/$defs/id"},
                maximum=64,
                unique=True,
            ),
            "ending_kind": {
                "oneOf": [
                    {"type": "null"},
                    {"maxLength": 64, "minLength": 1, "type": "string"},
                ]
            },
            "failure_ids": ids,
            "recovery_action_ids": ids,
            "terminal": {"type": "boolean"},
        }
    )


def _state_value() -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "boolean"},
            {
                "maximum": 9_007_199_254_740_991,
                "minimum": -9_007_199_254_740_991,
                "type": "integer",
            },
            {"maxLength": 4096, "type": "string"},
            _array(
                {"maxLength": 4096, "type": "string"},
                maximum=256,
            ),
        ]
    }


def _defs() -> dict[str, Any]:
    return {
        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
        "id": {
            "maxLength": 64,
            "minLength": 2,
            "pattern": "^[a-z][a-z0-9_]{1,63}$",
            "type": "string",
        },
        "token": {
            "maxLength": 128,
            "minLength": 3,
            "pattern": "^[a-z][a-z0-9_-]*:[a-z][a-z0-9_.-]*$",
            "type": "string",
        },
        "identity": _identity(),
        "classification": _classification(),
        "parameters": {
            "additionalProperties": _state_value(),
            "maxProperties": 256,
            "type": "object",
        },
        "host": _object(
            {
                "platform_id": {
                    "enum": [
                        "platform:linux_x86_64",
                        "platform:windows_x86_64",
                    ]
                },
                "platform_family": {
                    "enum": ["platform:linux", "platform:windows"],
                },
                "architecture": {"const": "architecture:x86_64"},
                "backend": {"const": "backend:raylib"},
                "renderer": {"const": "raylib"},
            }
        ),
    }


def _base(
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
        "x-world-forge-generic-headless-coherent": coherent,
        "$defs": _defs(),
    }


def _bindings(*, script: bool = False) -> dict[str, Any]:
    fields = {
        "gamepack": _identity("world-forge.gamepack"),
        "runtime_composition": _identity("world-forge.game_runtime_composition"),
        "runtime_bundle": _identity("world-forge.game_runtime_bundle"),
        "adapter": _identity("world-forge.runtime_adapter"),
        "runtime_snapshot": _identity("world-forge.game_runtime_snapshot"),
    }
    if script:
        fields["execution_script"] = _identity("world-forge.game_execution_script")
    return _object(fields)


def _script_schema() -> dict[str, Any]:
    scenario = _object(
        {
            "scenario_id": {"$ref": "#/$defs/id"},
            "actions": _array(
                _object(
                    {
                        "action_id": {"$ref": "#/$defs/id"},
                        "parameters": {"$ref": "#/$defs/parameters"},
                    }
                ),
                maximum=128,
            ),
            "expected_initial_state_hash": {"$ref": "#/$defs/sha256"},
            "expected_final_state_hash": {"$ref": "#/$defs/sha256"},
            "expected_classification": {"$ref": "#/$defs/classification"},
        }
    )
    return _base(
        name="game-execution-script.schema.json",
        title="World Forge deterministic game execution script v1",
        format_name="world-forge.game_execution_script",
        coherent="game_execution_script",
        properties={
            "script_id": {
                "pattern": "^game_execution_script_[0-9a-f]{40}$",
                "type": "string",
            },
            "bindings": _bindings(),
            "scenarios": _array(
                scenario,
                maximum=32,
                minimum=1,
            ),
        },
    )


def _coverage() -> dict[str, Any]:
    return _object(
        {
            "complete": {"const": True},
            "actions": _array(
                _object(
                    {
                        "action_id": {"$ref": "#/$defs/id"},
                        "mechanic_ids": _array(
                            {"$ref": "#/$defs/id"},
                            maximum=256,
                            minimum=1,
                            unique=True,
                        ),
                        "scenario_ids": _array(
                            {"$ref": "#/$defs/id"},
                            maximum=32,
                            minimum=1,
                            unique=True,
                        ),
                    }
                ),
                maximum=256,
            ),
            "required_features": _array(
                _object(
                    {
                        "feature_id": {"$ref": "#/$defs/token"},
                        "mechanic_ids": _array(
                            {"$ref": "#/$defs/id"},
                            maximum=256,
                            minimum=1,
                            unique=True,
                        ),
                        "scenario_ids": _array(
                            {"$ref": "#/$defs/id"},
                            maximum=32,
                            minimum=1,
                            unique=True,
                        ),
                    }
                ),
                maximum=256,
            ),
        }
    )


def _receipt_schema() -> dict[str, Any]:
    scenario = _object(
        {
            "scenario_id": {"$ref": "#/$defs/id"},
            "action_count": {"maximum": 128, "minimum": 0, "type": "integer"},
            "trace_hash": {"$ref": "#/$defs/sha256"},
            "final_state_hash": {"$ref": "#/$defs/sha256"},
            "classification": {"$ref": "#/$defs/classification"},
            "save": _object(
                {
                    "id": {
                        "pattern": "^game_save_[0-9a-f]{48}$",
                        "type": "string",
                    },
                    "content_hash": {"$ref": "#/$defs/sha256"},
                    "restored_state_hash": {"$ref": "#/$defs/sha256"},
                }
            ),
            "replay": _object(
                {
                    "id": {
                        "pattern": "^game_replay_[0-9a-f]{48}$",
                        "type": "string",
                    },
                    "content_hash": {"$ref": "#/$defs/sha256"},
                    "replayed_state_hash": {"$ref": "#/$defs/sha256"},
                }
            ),
        }
    )
    check = _object(
        {
            "check_id": {
                "enum": [
                    "check:headless_determinism",
                    "check:save_replay",
                ]
            },
            "kind": {"enum": ["headless", "save_replay"]},
            "status": {"enum": ["passed", "failed"]},
            "evidence_id": {
                "pattern": "^headless_check_[0-9a-f]{40}$",
                "type": "string",
            },
            "content_hash": {"$ref": "#/$defs/sha256"},
        }
    )
    return _base(
        name="headless-execution-receipt.schema.json",
        title="World Forge bounded headless execution receipt v1",
        format_name="world-forge.headless_execution_receipt",
        coherent="headless_execution_receipt",
        properties={
            "receipt_id": {
                "pattern": "^headless_execution_receipt_[0-9a-f]{40}$",
                "type": "string",
            },
            "bindings": _bindings(script=True),
            "host": {"$ref": "#/$defs/host"},
            "executor": _object(
                {
                    "key": {"const": "gamepack_runtime.headless.v1"},
                    "adapter_id": {"$ref": "#/$defs/id"},
                    "adapter_version": {
                        "pattern": "^(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$",
                        "type": "string",
                    },
                    "adapter_hash": {"$ref": "#/$defs/sha256"},
                }
            ),
            "runtime_api": _object(
                {
                    "id": {"const": "gamepack_runtime"},
                    "version": {"const": "1.0.0"},
                }
            ),
            "execution_semantics": _object(
                {
                    "version": {"const": 1},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                }
            ),
            "policies": _object(
                {
                    "verifier_policy_hash": {"$ref": "#/$defs/sha256"},
                    "audit_policy_hash": {"$ref": "#/$defs/sha256"},
                }
            ),
            "native_execution": {"const": False},
            "scenarios": _array(
                scenario,
                maximum=32,
                minimum=1,
            ),
            "coverage": _coverage(),
            "checks": {
                "items": check,
                "maxItems": 2,
                "minItems": 2,
                "type": "array",
                "uniqueItems": True,
            },
            "status": {"const": "passed"},
            "failure": {"type": "null"},
        },
    )


def _evidence_set_schema() -> dict[str, Any]:
    file_record = _object(
        {
            "path": {
                "maxLength": 1024,
                "minLength": 1,
                "pattern": "^[A-Za-z0-9_][A-Za-z0-9_.@ /-]*$",
                "type": "string",
            },
            "sha256": {"$ref": "#/$defs/sha256"},
            "size_bytes": {
                "maximum": 4 * 1024 * 1024,
                "minimum": 1,
                "type": "integer",
            },
        }
    )
    return _base(
        name="headless-evidence-set.schema.json",
        title="World Forge immutable external headless evidence set v1",
        format_name="world-forge.headless_evidence_set",
        coherent="headless_evidence_set",
        properties={
            "evidence_set_id": {
                "pattern": "^headless_evidence_set_[0-9a-f]{40}$",
                "type": "string",
            },
            "state": {"const": "committed"},
            "runtime_bundle": _identity("world-forge.game_runtime_bundle"),
            "execution_script": _identity("world-forge.game_execution_script"),
            "headless_receipt": _identity("world-forge.headless_execution_receipt"),
            "runtime_evidence": _object(
                {
                    "format": {"const": "world-forge.runtime_evidence"},
                    "format_version": {"const": 1},
                    "id": {"maxLength": 128, "minLength": 2, "type": "string"},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                    "execution_status": {"const": "headless_verified"},
                    "platform": {"$ref": "#/$defs/host"},
                }
            ),
            "support": _object(
                {
                    "format": {"const": "world-forge.runtime_support_report"},
                    "format_version": {"const": 1},
                    "id": {"maxLength": 128, "minLength": 2, "type": "string"},
                    "content_hash": {"$ref": "#/$defs/sha256"},
                    "compatibility_status": {"const": "partially_supported"},
                    "release": {"const": "blocked"},
                    "supported": {"const": False},
                }
            ),
            "files": _array(
                file_record,
                maximum=70,
                minimum=7,
                unique=True,
            ),
            "tree_hash": {"$ref": "#/$defs/sha256"},
            "file_count": {"maximum": 70, "minimum": 7, "type": "integer"},
            "total_bytes": {
                "maximum": 64 * 1024 * 1024,
                "minimum": 1,
                "type": "integer",
            },
        },
    )


def build_schemas() -> dict[str, dict[str, Any]]:
    return {
        "schemas/game-execution-script.schema.json": _script_schema(),
        "schemas/headless-evidence-set.schema.json": _evidence_set_schema(),
        "schemas/headless-execution-receipt.schema.json": _receipt_schema(),
    }


def _studio_authority_policy_module() -> bytes:
    policy = headless_authority_result_policy_document()
    fields = json.dumps(policy["fields"], ensure_ascii=False, separators=(",", ":"))
    return (
        "/* AUTO-GENERATED from the Python headless authority result policy. */\n"
        "export const GENERIC_HEADLESS_AUTHORITY_RESULT_POLICY = Object.freeze({\n"
        f'  execution_status: "{policy["execution_status"]}",\n'
        f"  fields: Object.freeze({fields}),\n"
        f'  integrity: "{policy["integrity"]}",\n'
        f'  release: "{policy["release"]}",\n'
        f"  supported: {str(policy['supported']).lower()},\n"
        f"  version: {policy['version']},\n"
        "});\n"
    ).encode()


def _studio_authority_policy_declaration() -> bytes:
    fields = ", ".join(
        json.dumps(field)
        for field in headless_authority_result_policy_document()["fields"]  # type: ignore[union-attr]
    )
    return (
        "export declare const GENERIC_HEADLESS_AUTHORITY_RESULT_POLICY: Readonly<{\n"
        '  readonly execution_status: "headless_verified";\n'
        f"  readonly fields: readonly [{fields}];\n"
        '  readonly integrity: "valid";\n'
        '  readonly release: "blocked";\n'
        "  readonly supported: false;\n"
        "  readonly version: 1;\n"
        "}>;\n"
    ).encode()


def generate(*, check: bool = False) -> None:
    generated = {
        relative: canonical_json_bytes(schema) for relative, schema in build_schemas().items()
    }
    generated.update(
        {
            "apps/studio/scripts/generic-headless-authority-result.d.mts": (
                _studio_authority_policy_declaration()
            ),
            "apps/studio/scripts/generic-headless-authority-result.mjs": (
                _studio_authority_policy_module()
            ),
        }
    )
    for relative, payload in generated.items():
        path = ROOT / relative
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
