"""Generate the additive sealed generic assetpack v1 schema."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
OUTPUT = SCHEMAS / "generic-assetpack.schema.json"


def _read(name: str) -> dict[str, Any]:
    value = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain an object")
    return value


def _identity(format_name: str) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": {
            "content_hash": {"$ref": "#/$defs/sha256"},
            "format": {"const": format_name},
            "format_version": {"const": 1},
            "id": {"$ref": "#/$defs/id"},
        },
        "required": ["format", "format_version", "id", "content_hash"],
        "type": "object",
    }


def _merge_definition(
    definitions: dict[str, Any],
    source: dict[str, Any],
    name: str,
) -> None:
    value = copy.deepcopy(source["$defs"][name])
    prior = definitions.setdefault(name, value)
    if prior != value:
        raise ValueError(f"correlated generic asset definition drifted: {name}")
    for reference in _definition_references(value):
        if reference not in source["$defs"]:
            raise ValueError(
                f"generic asset definition {name} references missing definition {reference}"
            )
        _merge_definition(definitions, source, reference)


def _definition_references(value: Any) -> set[str]:
    references: set[str] = set()
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            reference = current.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                references.add(reference.rsplit("/", 1)[-1])
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return references


def _output_variants(
    recipe: dict[str, Any],
    qa: dict[str, Any],
    definitions: dict[str, Any],
) -> list[dict[str, Any]]:
    recipe_variants = {
        (
            value["properties"]["role"]["const"],
            value["properties"]["media_type"]["const"],
        ): value
        for value in recipe["$defs"]["step"]["oneOf"]
    }
    qa_variants = {
        (
            value["properties"]["role"]["const"],
            value["properties"]["media_type"]["const"],
        ): value
        for value in qa["$defs"]["qaOutput"]["oneOf"]
    }
    if set(recipe_variants) != set(qa_variants) or len(recipe_variants) != 12:
        raise ValueError("D2 role/media matrix is not the exact 12-pair contract")
    variants = []
    for role_media in sorted(recipe_variants):
        recipe_properties = recipe_variants[role_media]["properties"]
        qa_properties = qa_variants[role_media]["properties"]
        constraints = copy.deepcopy(recipe_properties["expectations"])
        metadata = copy.deepcopy(qa_properties["metadata"])
        for value in (constraints, metadata):
            for name in _definition_references(value):
                source = recipe if name in recipe["$defs"] else qa
                _merge_definition(definitions, source, name)
        role, media_type = role_media
        variants.append(
            {
                "additionalProperties": False,
                "properties": {
                    "constraints": constraints,
                    "license_record": {"$ref": "#/$defs/licenseIdentity"},
                    "media_type": {"const": media_type},
                    "metadata": metadata,
                    "role": {"const": role},
                    "runtime_notice": {"$ref": "#/$defs/notice"},
                    "runtime_path": {"$ref": "#/$defs/path"},
                    "sha256": {"$ref": "#/$defs/sha256"},
                    "size_bytes": {
                        "maximum": 16 * 1024 * 1024,
                        "minimum": 1,
                        "type": "integer",
                    },
                },
                "required": [
                    "role",
                    "media_type",
                    "runtime_path",
                    "sha256",
                    "size_bytes",
                    "constraints",
                    "metadata",
                    "license_record",
                    "runtime_notice",
                ],
                "type": "object",
            }
        )
    return variants


def build_schema() -> dict[str, Any]:
    recipe = _read("generic-asset-processing-recipe.schema.json")
    qa = _read("generic-asset-qa-report.schema.json")
    definitions: dict[str, Any] = {}
    for name in ("asset", "id", "path", "sha256"):
        _merge_definition(definitions, recipe, name)
    definitions.update(
        {
            "gamepackIdentity": _identity("world-forge.gamepack"),
            "assetSubjectIdentity": _identity("world-forge.asset_subject"),
            "targetIdentity": _identity("world-forge.asset_target"),
            "styleIdentity": _identity("world-forge.asset_style"),
            "inventoryIdentity": _identity("world-forge.asset_inventory"),
            "manifestIdentity": _identity("world-forge.asset_manifest"),
            "specificationIdentity": _identity("world-forge.asset_spec"),
            "requestIdentity": _identity("world-forge.asset_production_request"),
            "productionReceiptIdentity": _identity("world-forge.asset_production_receipt"),
            "selectionIdentity": _identity("world-forge.asset_selection"),
            "provenanceIdentity": _identity("world-forge.asset_provenance_record"),
            "recipeIdentity": _identity("world-forge.asset_processing_recipe"),
            "processingReceiptIdentity": _identity("world-forge.asset_processing_receipt"),
            "qaReportIdentity": _identity("world-forge.asset_qa_report"),
            "licenseIdentity": _identity("world-forge.asset_license_record"),
            "notice": {
                "additionalProperties": False,
                "properties": {
                    "path": {
                        "allOf": [
                            {"$ref": "#/$defs/path"},
                            {
                                "pattern": "^notices/[0-9a-f]{64}\\.txt$",
                                "type": "string",
                            },
                        ]
                    },
                    "sha256": {"$ref": "#/$defs/sha256"},
                    "size_bytes": {
                        "maximum": 4096,
                        "minimum": 0,
                        "type": "integer",
                    },
                },
                "required": ["path", "sha256", "size_bytes"],
                "type": "object",
            },
            "file": {
                "additionalProperties": False,
                "properties": {
                    "path": {"$ref": "#/$defs/path"},
                    "sha256": {"$ref": "#/$defs/sha256"},
                    "size_bytes": {
                        "maximum": 16 * 1024 * 1024,
                        "minimum": 0,
                        "type": "integer",
                    },
                },
                "required": ["path", "sha256", "size_bytes"],
                "type": "object",
            },
        }
    )
    definitions["output"] = {"oneOf": _output_variants(recipe, qa, definitions)}
    definitions["assetpackAsset"] = {
        "additionalProperties": False,
        "properties": {
            "asset": {"$ref": "#/$defs/asset"},
            "licenses": {
                "items": {"$ref": "#/$defs/licenseIdentity"},
                "maxItems": 4,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["id"],
                    "uniqueBy": [["id"], ["content_hash"]],
                },
            },
            "outputs": {
                "items": {"$ref": "#/$defs/output"},
                "maxItems": 4,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["role"],
                    "uniqueBy": [["role"], ["runtime_path"]],
                },
            },
            "processing_receipt": {"$ref": "#/$defs/processingReceiptIdentity"},
            "processing_recipe": {"$ref": "#/$defs/recipeIdentity"},
            "provenance": {"$ref": "#/$defs/provenanceIdentity"},
            "qa_report": {"$ref": "#/$defs/qaReportIdentity"},
            "receipt": {"$ref": "#/$defs/productionReceiptIdentity"},
            "request": {"$ref": "#/$defs/requestIdentity"},
            "selection": {"$ref": "#/$defs/selectionIdentity"},
            "specification": {"$ref": "#/$defs/specificationIdentity"},
        },
        "required": [
            "asset",
            "specification",
            "request",
            "receipt",
            "selection",
            "provenance",
            "processing_recipe",
            "processing_receipt",
            "qa_report",
            "licenses",
            "outputs",
        ],
        "type": "object",
    }
    definitions["fileInventory"] = {
        "additionalProperties": False,
        "properties": {
            "content_hash": {"$ref": "#/$defs/sha256"},
            "file_count": {
                "maximum": 8192,
                "minimum": 1,
                "type": "integer",
            },
            "files": {
                "items": {"$ref": "#/$defs/file"},
                "maxItems": 8192,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["path"],
                    "uniqueBy": [["path"]],
                },
                "x-world-forge-portable-path-tree": "path",
            },
            "total_bytes": {
                "maximum": 512 * 1024 * 1024,
                "minimum": 0,
                "type": "integer",
            },
        },
        "required": ["file_count", "total_bytes", "files", "content_hash"],
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
    }
    return {
        "$id": "https://world-forge.local/schemas/generic-assetpack.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": {
            "asset_inventory": {"$ref": "#/$defs/inventoryIdentity"},
            "asset_subject": {"$ref": "#/$defs/assetSubjectIdentity"},
            "assetpack_id": {
                "pattern": "^assetpack_[0-9a-f]{48}$",
                "type": "string",
            },
            "assets": {
                "items": {"$ref": "#/$defs/assetpackAsset"},
                "maxItems": 1024,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["asset.asset_id"],
                    "uniqueBy": [["asset.asset_id"]],
                },
            },
            "content_hash": {"$ref": "#/$defs/sha256"},
            "format": {"const": "world-forge.assetpack"},
            "format_version": {"const": 1},
            "gamepack": {"$ref": "#/$defs/gamepackIdentity"},
            "inventory": {"$ref": "#/$defs/fileInventory"},
            "release_ready_manifest": {"$ref": "#/$defs/manifestIdentity"},
            "state": {"const": "sealed"},
            "style": {"$ref": "#/$defs/styleIdentity"},
            "target": {"$ref": "#/$defs/targetIdentity"},
        },
        "required": [
            "format",
            "format_version",
            "assetpack_id",
            "gamepack",
            "asset_subject",
            "target",
            "style",
            "asset_inventory",
            "release_ready_manifest",
            "state",
            "assets",
            "inventory",
            "content_hash",
        ],
        "title": "World Forge sealed generic assetpack v1",
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
        "x-world-forge-generic-assetpack-coherent": True,
        "$defs": definitions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    expected = canonical_json_bytes(build_schema())
    if arguments.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != expected:
            raise SystemExit(
                f"stale generic assetpack schema: {OUTPUT.relative_to(ROOT).as_posix()}"
            )
    else:
        OUTPUT.write_bytes(expected)
    print(f"OK generic_assetpack_schema=1 mode={'check' if arguments.check else 'write'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
