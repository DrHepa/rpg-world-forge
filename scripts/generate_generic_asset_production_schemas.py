"""Generate correlated D2 generic asset production and processing schemas."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from worldforge.generic_asset_limits import MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"

ROLE_MEDIA = (
    ("animation", "model/gltf-binary"),
    ("audio", "audio/wav"),
    ("clipset", "application/json"),
    ("collision", "model/gltf-binary"),
    ("font", "font/otf"),
    ("font", "font/ttf"),
    ("fragment_shader", "text/x-glsl"),
    ("localized_text", "application/json"),
    ("model", "model/gltf-binary"),
    ("skeleton", "model/gltf-binary"),
    ("texture", "image/png"),
    ("vertex_shader", "text/x-glsl"),
)
PRODUCTION_FILES = (
    "generic-asset-production-request.schema.json",
    "generic-asset-production-receipt.schema.json",
    "generic-asset-selection.schema.json",
    "generic-asset-provenance-record.schema.json",
    "generic-asset-license-record.schema.json",
)
PROCESSING_FILES = (
    "generic-asset-processing-recipe.schema.json",
    "generic-asset-processing-receipt.schema.json",
    "generic-asset-qa-report.schema.json",
    "generic-asset-manifest.schema.json",
)
AUTHORITY_FILES = (
    "generic-asset-qa-review-receipt.schema.json",
    "generic-asset-release-authority.schema.json",
)
ROLE_MEDIA_OPERATION = (
    ("animation", "model/gltf-binary", "validate_copy_glb"),
    ("audio", "audio/wav", "validate_copy_pcm16_wav"),
    ("clipset", "application/json", "canonicalize_clipset_json"),
    ("collision", "model/gltf-binary", "validate_copy_glb"),
    ("font", "font/otf", "validate_copy_font"),
    ("font", "font/ttf", "validate_copy_font"),
    ("fragment_shader", "text/x-glsl", "validate_copy_fragment_glsl"),
    ("localized_text", "application/json", "canonicalize_localization_json"),
    ("model", "model/gltf-binary", "validate_copy_glb"),
    ("skeleton", "model/gltf-binary", "validate_copy_glb"),
    ("texture", "image/png", "validate_copy_png"),
    ("vertex_shader", "text/x-glsl", "validate_copy_vertex_glsl"),
)
QA_CHECK_ORDER = (
    "hash",
    "media",
    "path",
    "license",
    "png",
    "wav",
    "font",
    "glsl",
    "json",
    "glb",
)
QA_MEDIA_CHECK = {
    "image/png": "png",
    "audio/wav": "wav",
    "font/ttf": "font",
    "font/otf": "font",
    "text/x-glsl": "glsl",
    "application/json": "json",
    "model/gltf-binary": "glb",
}


def _read(name: str) -> dict[str, Any]:
    value = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain an object")
    return value


def _role_media_variants(base: dict[str, Any]) -> dict[str, Any]:
    if "oneOf" in base:
        existing = base["oneOf"]
        if not isinstance(existing, list) or not existing:
            raise ValueError("role/media variants must contain at least one object")
        base = existing[0]
    variants = []
    for role, media_type in ROLE_MEDIA:
        variant = copy.deepcopy(base)
        properties = variant["properties"]
        properties["role"] = {"const": role}
        properties["media_type"] = {"const": media_type}
        variants.append(variant)
    return {"oneOf": variants}


def _receipt_output_variants(schema: dict[str, Any]) -> None:
    existing = schema["properties"]["outputs"]["items"]["oneOf"]
    by_media = {item["properties"]["media_type"]["const"]: item for item in existing}
    variants = []
    for role, media_type in ROLE_MEDIA:
        variant = copy.deepcopy(by_media[media_type])
        properties = variant["properties"]
        properties["role"] = {"const": role}
        if media_type == "text/x-glsl":
            stage = "vertex" if role == "vertex_shader" else "fragment"
            properties["metadata"]["properties"]["stage"] = {"const": stage}
        if media_type == "model/gltf-binary":
            metrics = properties["metadata"]["properties"]["metrics"]
            metric_property = {
                "maximum": 100_000_000,
                "minimum": 0,
                "type": "integer",
            }
            metric_names = (
                "nodes",
                "meshes",
                "primitives",
                "materials",
                "joints",
                "animations",
                "triangles",
            )
            metrics["properties"] = {name: copy.deepcopy(metric_property) for name in metric_names}
            metrics["required"] = list(metric_names)
        variants.append(variant)
    schema["properties"]["outputs"]["items"] = {"oneOf": variants}


def _receipt_identity(schema: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(schema["properties"]["receipt"])


def _add_receipt_lineage(schema: dict[str, Any]) -> None:
    identity = _receipt_identity(schema)
    parent_array = {
        "items": copy.deepcopy(identity),
        "maxItems": 64,
        "minItems": 0,
        "type": "array",
        "x-world-forge-canonical-object-array": {
            "orderBy": ["id"],
            "uniqueBy": [["id"], ["content_hash"]],
        },
    }
    closure = {
        "additionalProperties": False,
        "properties": {
            "parents": parent_array,
            "root": copy.deepcopy(identity),
        },
        "required": ["root", "parents"],
        "type": "object",
    }
    schema["properties"]["receipt_lineage"] = {
        "additionalProperties": False,
        "properties": {
            "closures": {
                "items": closure,
                "maxItems": 33,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["root.id"],
                    "uniqueBy": [["root.id"], ["root.content_hash"]],
                },
            },
            "format": {"const": "world-forge.asset_receipt_lineage"},
            "format_version": {"const": 1},
        },
        "required": ["format", "format_version", "closures"],
        "type": "object",
    }
    if "receipt_lineage" not in schema["required"]:
        receipt_index = schema["required"].index("receipt")
        schema["required"].insert(receipt_index + 1, "receipt_lineage")
    schema["x-world-forge-receipt-lineage-roots"] = True


def _require_recorded_procedural_seed(schema: dict[str, Any]) -> None:
    condition = {
        "if": {
            "properties": {
                "production_class": {"const": "procedural_offline"},
                "reproducibility": {
                    "properties": {"seed_policy": {"const": "recorded"}},
                    "required": ["seed_policy"],
                    "type": "object",
                },
            },
            "required": ["production_class", "reproducibility"],
            "type": "object",
        },
        "then": {
            "properties": {
                "toolchain_requirements": {
                    "properties": {"seed": {"type": "integer"}},
                    "required": ["seed"],
                    "type": "object",
                }
            },
            "type": "object",
        },
    }
    filtered = [
        item
        for item in schema["allOf"]
        if not (
            item.get("if", {}).get("properties", {}).get("production_class", {}).get("const")
            == "procedural_offline"
            and item.get("if", {})
            .get("properties", {})
            .get("reproducibility", {})
            .get("properties", {})
            .get("seed_policy", {})
            .get("const")
            == "recorded"
        )
    ]
    fixed_index = next(
        index
        for index, item in enumerate(filtered)
        if item.get("if", {})
        .get("properties", {})
        .get("reproducibility", {})
        .get("properties", {})
        .get("seed_policy", {})
        .get("const")
        == "fixed"
    )
    filtered.insert(fixed_index + 1, condition)
    schema["allOf"] = filtered


def _identity(format_name: str) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": {
            "format": {"const": format_name},
            "format_version": {"const": 1},
            "id": {"$ref": "#/$defs/id"},
            "content_hash": {"$ref": "#/$defs/sha256"},
        },
        "required": ["format", "format_version", "id", "content_hash"],
        "type": "object",
    }


def _asset_identity() -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": {
            "asset_id": {"$ref": "#/$defs/id"},
            "content_hash": {"$ref": "#/$defs/sha256"},
        },
        "required": ["asset_id", "content_hash"],
        "type": "object",
    }


def _license_binding_identity() -> dict[str, Any]:
    identity = _identity("world-forge.asset_license_record")
    identity["properties"]["candidate_artifact_id"] = {"$ref": "#/$defs/id"}
    identity["properties"]["role"] = {"$ref": "#/$defs/id"}
    identity["required"].extend(["candidate_artifact_id", "role"])
    return identity


def _common_definitions() -> dict[str, Any]:
    source = _read("generic-asset-production-request.schema.json")
    specification = _read("generic-asset-spec.schema.json")
    definitions = {
        "id": copy.deepcopy(source["$defs"]["id"]),
        "path": copy.deepcopy(source["$defs"]["path"]),
        "sha256": {"pattern": "^[0-9a-f]{64}$", "type": "string"},
        "asset": _asset_identity(),
        "gamepackIdentity": _identity("world-forge.gamepack"),
        "assetSubjectIdentity": _identity("world-forge.asset_subject"),
        "targetIdentity": _identity("world-forge.asset_target"),
        "styleIdentity": _identity("world-forge.asset_style"),
        "inventoryIdentity": _identity("world-forge.asset_inventory"),
        "specificationIdentity": _identity("world-forge.asset_spec"),
        "requestIdentity": _identity("world-forge.asset_production_request"),
        "productionReceiptIdentity": _identity("world-forge.asset_production_receipt"),
        "selectionIdentity": _identity("world-forge.asset_selection"),
        "provenanceIdentity": _identity("world-forge.asset_provenance_record"),
        "licenseIdentity": _identity("world-forge.asset_license_record"),
        "recipeIdentity": _identity("world-forge.asset_processing_recipe"),
        "processingReceiptIdentity": _identity("world-forge.asset_processing_receipt"),
        "qaReportIdentity": _identity("world-forge.asset_qa_report"),
        "processor": {
            "additionalProperties": False,
            "properties": {
                "processor_id": {"const": "world_forge_generic_asset_processor"},
                "version": {"const": 1},
            },
            "required": ["processor_id", "version"],
            "type": "object",
        },
    }
    for name in (
        "maxBytes",
        "runtimeString",
        "runtimeShortString",
        "pngExpectation",
        "wavExpectation",
        "fontExpectation",
        "glslExpectation",
        "jsonExpectation",
        "glbExpectation",
    ):
        definitions[name] = copy.deepcopy(specification["$defs"][name])
    return definitions


def _common_properties() -> dict[str, Any]:
    return {
        "gamepack": {"$ref": "#/$defs/gamepackIdentity"},
        "asset_subject": {"$ref": "#/$defs/assetSubjectIdentity"},
        "target": {"$ref": "#/$defs/targetIdentity"},
        "style": {"$ref": "#/$defs/styleIdentity"},
        "inventory": {"$ref": "#/$defs/inventoryIdentity"},
        "specification": {"$ref": "#/$defs/specificationIdentity"},
        "asset": {"$ref": "#/$defs/asset"},
        "request": {"$ref": "#/$defs/requestIdentity"},
        "receipt": {"$ref": "#/$defs/productionReceiptIdentity"},
        "selection": {"$ref": "#/$defs/selectionIdentity"},
        "provenance": {"$ref": "#/$defs/provenanceIdentity"},
    }


def _base_schema(
    name: str,
    format_name: str,
    id_field: str,
    properties: dict[str, Any],
    required: list[str],
    definitions: dict[str, Any],
) -> dict[str, Any]:
    return {
        "$defs": definitions,
        "$id": f"https://world-forge.local/schemas/{name}",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": {
            "format": {"const": format_name},
            "format_version": {"const": 1},
            id_field: {"$ref": "#/$defs/id"},
            **properties,
            "content_hash": {"$ref": "#/$defs/sha256"},
        },
        "required": [
            "format",
            "format_version",
            id_field,
            *required,
            "content_hash",
        ],
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
    }


def _expectation_by_role_media() -> dict[tuple[str, str], dict[str, Any]]:
    specification = _read("generic-asset-spec.schema.json")
    definitions = specification["$defs"]
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for reference in definitions["physicalOutput"]["oneOf"]:
        name = reference["$ref"].rsplit("/", 1)[-1]
        variant = definitions[name]
        properties = variant["properties"]
        roles = (
            [properties["role"]["const"]]
            if "const" in properties["role"]
            else properties["role"]["enum"]
        )
        for role in roles:
            result[(role, properties["media_type"]["const"])] = copy.deepcopy(
                properties["expectations"]
            )
    return result


def _step_variants() -> list[dict[str, Any]]:
    expectations = _expectation_by_role_media()
    variants = []
    for role, media_type, operation in ROLE_MEDIA_OPERATION:
        variants.append(
            {
                "additionalProperties": False,
                "properties": {
                    "step_id": {"$ref": "#/$defs/id"},
                    "candidate_artifact_id": {"$ref": "#/$defs/id"},
                    "source_locator": {"$ref": "#/$defs/path"},
                    "source_sha256": {"$ref": "#/$defs/sha256"},
                    "source_size_bytes": {
                        "maximum": 16777216,
                        "minimum": 1,
                        "type": "integer",
                    },
                    "role": {"const": role},
                    "media_type": {"const": media_type},
                    "runtime_path": {"$ref": "#/$defs/path"},
                    "operation": {"const": operation},
                    "output_locator": {"$ref": "#/$defs/path"},
                    "expectations": expectations[(role, media_type)],
                    "license_record": {"$ref": "#/$defs/licenseBindingIdentity"},
                },
                "required": [
                    "step_id",
                    "candidate_artifact_id",
                    "source_locator",
                    "source_sha256",
                    "source_size_bytes",
                    "role",
                    "media_type",
                    "runtime_path",
                    "operation",
                    "output_locator",
                    "expectations",
                    "license_record",
                ],
                "type": "object",
            }
        )
    return variants


def _processed_output_variants() -> list[dict[str, Any]]:
    receipt = _read("generic-asset-production-receipt.schema.json")
    variants = []
    for source in receipt["properties"]["outputs"]["items"]["oneOf"]:
        variant = copy.deepcopy(source)
        properties = variant["properties"]
        properties["step_id"] = {"$ref": "#/$defs/id"}
        properties["source_sha256"] = {"$ref": "#/$defs/sha256"}
        variant["required"] = [
            "step_id",
            "candidate_artifact_id",
            "source_sha256",
            "locator",
            "runtime_path",
            "role",
            "size_bytes",
            "sha256",
            "media_type",
            "metadata",
        ]
        variants.append(variant)
    return variants


def _qa_checks(media_type: str) -> dict[str, Any]:
    media_check = QA_MEDIA_CHECK[media_type]
    return {
        "items": False,
        "maxItems": 10,
        "minItems": 10,
        "prefixItems": [
            {
                "additionalProperties": False,
                "properties": {
                    "check_id": {"const": check_id},
                    "status": {
                        (
                            "const"
                            if check_id in {"path", "license"}
                            or check_id not in {"hash", "media", "path", "license", media_check}
                            else "enum"
                        ): (
                            ("passed" if check_id in {"path", "license"} else "not_applicable")
                            if check_id not in {"hash", "media", media_check}
                            else ["passed", "failed"]
                        )
                    },
                },
                "required": ["check_id", "status"],
                "type": "object",
            }
            for check_id in QA_CHECK_ORDER
        ],
        "type": "array",
    }


def _qa_output_variants() -> list[dict[str, Any]]:
    variants = []
    for source in _processed_output_variants():
        variant = copy.deepcopy(source)
        properties = variant["properties"]
        properties.pop("step_id")
        properties.pop("source_sha256")
        properties["metadata"] = {
            "oneOf": [
                copy.deepcopy(properties["metadata"]),
                {"type": "null"},
            ]
        }
        properties["checks"] = _qa_checks(properties["media_type"]["const"])
        variant["required"] = [
            item for item in variant["required"] if item not in {"step_id", "source_sha256"}
        ] + ["checks"]
        variants.append(variant)
    return variants


def _manifest_output_variants() -> list[dict[str, Any]]:
    variants = []
    for source in _processed_output_variants():
        variant = copy.deepcopy(source)
        properties = variant["properties"]
        for field in (
            "step_id",
            "candidate_artifact_id",
            "source_sha256",
            "metadata",
        ):
            properties.pop(field)
        variant["required"] = [
            "locator",
            "runtime_path",
            "role",
            "size_bytes",
            "sha256",
            "media_type",
        ]
        variants.append(variant)
    return variants


def _processing_schemas() -> dict[str, dict[str, Any]]:
    common = _common_properties()
    common_required = list(common)
    recipe_definitions = _common_definitions()
    recipe_definitions["licenseBindingIdentity"] = _license_binding_identity()
    recipe_definitions["step"] = {"oneOf": _step_variants()}
    recipe = _base_schema(
        PROCESSING_FILES[0],
        "world-forge.asset_processing_recipe",
        "recipe_id",
        {
            **common,
            "licenses": {
                "items": {
                    "additionalProperties": False,
                    "properties": {
                        "candidate_artifact_id": {"$ref": "#/$defs/id"},
                        "role": {"$ref": "#/$defs/id"},
                        "license_record": {"$ref": "#/$defs/licenseBindingIdentity"},
                    },
                    "required": [
                        "candidate_artifact_id",
                        "role",
                        "license_record",
                    ],
                    "type": "object",
                },
                "maxItems": 4,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["role"],
                    "uniqueBy": [
                        ["candidate_artifact_id"],
                        ["role"],
                        ["license_record.content_hash"],
                    ],
                },
            },
            "processor": {"$ref": "#/$defs/processor"},
            "steps": {
                "items": {"$ref": "#/$defs/step"},
                "maxItems": 4,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["role"],
                    "uniqueBy": [
                        ["step_id"],
                        ["candidate_artifact_id"],
                        ["role"],
                        ["runtime_path"],
                        ["output_locator"],
                    ],
                },
                "x-world-forge-portable-path-tree": "output_locator",
            },
        },
        [*common_required, "licenses", "processor", "steps"],
        recipe_definitions,
    )
    recipe["title"] = "World Forge deterministic asset processing recipe v1"
    recipe["x-world-forge-d2b-coherent"] = "recipe"

    receipt_definitions = _common_definitions()
    receipt_definitions["processedOutput"] = {"oneOf": _processed_output_variants()}
    receipt_definitions["recovery"] = {
        "additionalProperties": False,
        "properties": {
            "failure_code": {"$ref": "#/$defs/id"},
            "recipe": {"$ref": "#/$defs/recipeIdentity"},
            "retained_artifacts": {
                "items": {"$ref": "#/$defs/processedOutput"},
                "maxItems": 4,
                "minItems": 0,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["role"],
                    "uniqueBy": [
                        ["step_id"],
                        ["candidate_artifact_id"],
                        ["role"],
                        ["locator"],
                    ],
                },
                "x-world-forge-portable-path-tree": "locator",
            },
            "content_hash": {"$ref": "#/$defs/sha256"},
        },
        "required": [
            "failure_code",
            "recipe",
            "retained_artifacts",
            "content_hash",
        ],
        "type": "object",
        "x-world-forge-canonical-content-hash": True,
    }
    processing_receipt = _base_schema(
        PROCESSING_FILES[1],
        "world-forge.asset_processing_receipt",
        "processing_receipt_id",
        {
            **common,
            "recipe": {"$ref": "#/$defs/recipeIdentity"},
            "processor": {"$ref": "#/$defs/processor"},
            "status": {"enum": ["completed", "failed"]},
            "outputs": {
                "items": {"$ref": "#/$defs/processedOutput"},
                "maxItems": 4,
                "minItems": 0,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["role"],
                    "uniqueBy": [
                        ["step_id"],
                        ["candidate_artifact_id"],
                        ["role"],
                        ["locator"],
                    ],
                },
                "x-world-forge-portable-path-tree": "locator",
            },
            "failure_reasons": {
                "items": {"$ref": "#/$defs/id"},
                "maxItems": 64,
                "minItems": 0,
                "type": "array",
                "uniqueItems": True,
                "x-world-forge-canonical-string-array": True,
            },
            "recovery": {
                "oneOf": [
                    {"$ref": "#/$defs/recovery"},
                    {"type": "null"},
                ]
            },
        },
        [
            *common_required,
            "recipe",
            "processor",
            "status",
            "outputs",
            "failure_reasons",
            "recovery",
        ],
        receipt_definitions,
    )
    processing_receipt["title"] = "World Forge deterministic asset processing receipt v1"
    processing_receipt["x-world-forge-d2b-coherent"] = "receipt"
    processing_receipt["allOf"] = [
        {
            "if": {
                "properties": {"status": {"const": "completed"}},
                "required": ["status"],
                "type": "object",
            },
            "then": {
                "properties": {
                    "outputs": {"minItems": 1, "type": "array"},
                    "failure_reasons": {"maxItems": 0, "type": "array"},
                    "recovery": {"type": "null"},
                },
                "type": "object",
            },
        },
        {
            "if": {
                "properties": {"status": {"const": "failed"}},
                "required": ["status"],
                "type": "object",
            },
            "then": {
                "properties": {
                    "outputs": {"maxItems": 0, "type": "array"},
                    "failure_reasons": {
                        "maxItems": 1,
                        "minItems": 1,
                        "type": "array",
                    },
                    "recovery": {"$ref": "#/$defs/recovery"},
                },
                "type": "object",
            },
        },
    ]

    qa_definitions = _common_definitions()
    qa_definitions["qaOutput"] = {"oneOf": _qa_output_variants()}
    qa = _base_schema(
        PROCESSING_FILES[2],
        "world-forge.asset_qa_report",
        "qa_report_id",
        {
            **common,
            "recipe": {"$ref": "#/$defs/recipeIdentity"},
            "processing_receipt": {"$ref": "#/$defs/processingReceiptIdentity"},
            "status": {"enum": ["passed", "failed"]},
            "outputs": {
                "items": {"$ref": "#/$defs/qaOutput"},
                "maxItems": 4,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["role"],
                    "uniqueBy": [
                        ["candidate_artifact_id"],
                        ["role"],
                        ["locator"],
                    ],
                },
                "x-world-forge-portable-path-tree": "locator",
            },
            "acceptance_criteria": {
                "items": {
                    "additionalProperties": False,
                    "properties": {
                        "criterion_index": {
                            "maximum": MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS - 1,
                            "minimum": 0,
                            "type": "integer",
                        },
                        "criterion_sha256": {"$ref": "#/$defs/sha256"},
                        "status": {"enum": ["passed", "failed"]},
                        "evidence_hashes": {
                            "items": {"$ref": "#/$defs/sha256"},
                            "maxItems": MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS,
                            "minItems": 1,
                            "type": "array",
                            "uniqueItems": True,
                            "x-world-forge-canonical-string-array": True,
                        },
                    },
                    "required": [
                        "criterion_index",
                        "criterion_sha256",
                        "status",
                        "evidence_hashes",
                    ],
                    "type": "object",
                },
                "maxItems": MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["criterion_index"],
                    "uniqueBy": [
                        ["criterion_index"],
                        ["criterion_sha256"],
                    ],
                },
            },
            "multi_output_check": {
                "additionalProperties": False,
                "properties": {
                    "status": {"enum": ["passed", "not_applicable"]},
                    "roles": {
                        "items": {"$ref": "#/$defs/id"},
                        "maxItems": 4,
                        "minItems": 1,
                        "type": "array",
                        "uniqueItems": True,
                        "x-world-forge-canonical-string-array": True,
                    },
                },
                "required": ["status", "roles"],
                "type": "object",
            },
            "blockers": {
                "items": {"$ref": "#/$defs/id"},
                "maxItems": 64,
                "minItems": 0,
                "type": "array",
                "uniqueItems": True,
                "x-world-forge-canonical-string-array": True,
            },
        },
        [
            *common_required,
            "recipe",
            "processing_receipt",
            "status",
            "outputs",
            "acceptance_criteria",
            "multi_output_check",
            "blockers",
        ],
        qa_definitions,
    )
    qa["title"] = "World Forge retained-byte asset QA report v1"
    qa["x-world-forge-d2b-coherent"] = "qa"
    qa["allOf"] = [
        {
            "if": {
                "properties": {"status": {"const": "passed"}},
                "required": ["status"],
                "type": "object",
            },
            "then": {
                "properties": {
                    "acceptance_criteria": {
                        "items": {
                            "properties": {"status": {"const": "passed"}},
                            "required": ["status"],
                            "type": "object",
                        },
                        "type": "array",
                    },
                    "blockers": {"maxItems": 0, "type": "array"},
                },
                "type": "object",
            },
        },
        {
            "if": {
                "properties": {"status": {"const": "failed"}},
                "required": ["status"],
                "type": "object",
            },
            "then": {
                "anyOf": [
                    {
                        "properties": {
                            "acceptance_criteria": {
                                "contains": {
                                    "properties": {"status": {"const": "failed"}},
                                    "required": ["status"],
                                    "type": "object",
                                },
                                "type": "array",
                            }
                        },
                        "type": "object",
                    },
                    {
                        "properties": {
                            "outputs": {
                                "contains": {
                                    "properties": {
                                        "checks": {
                                            "contains": {
                                                "properties": {"status": {"const": "failed"}},
                                                "required": ["status"],
                                                "type": "object",
                                            },
                                            "type": "array",
                                        }
                                    },
                                    "required": ["checks"],
                                    "type": "object",
                                },
                                "type": "array",
                            }
                        },
                        "type": "object",
                    },
                ],
                "properties": {
                    "blockers": {"minItems": 1, "type": "array"},
                },
                "type": "object",
            },
        },
        {
            "if": {
                "properties": {"outputs": {"maxItems": 1, "type": "array"}},
                "required": ["outputs"],
                "type": "object",
            },
            "then": {
                "properties": {
                    "multi_output_check": {
                        "properties": {"status": {"const": "not_applicable"}},
                        "required": ["status"],
                        "type": "object",
                    }
                },
                "type": "object",
            },
        },
        {
            "if": {
                "properties": {"outputs": {"minItems": 2, "type": "array"}},
                "required": ["outputs"],
                "type": "object",
            },
            "then": {
                "properties": {
                    "multi_output_check": {
                        "properties": {"status": {"const": "passed"}},
                        "required": ["status"],
                        "type": "object",
                    }
                },
                "type": "object",
            },
        },
    ]

    manifest_definitions = _common_definitions()
    manifest_definitions["manifestOutput"] = {"oneOf": _manifest_output_variants()}
    manifest_definitions["manifestAsset"] = {
        "additionalProperties": False,
        "properties": {
            "asset": {"$ref": "#/$defs/asset"},
            "specification": {"$ref": "#/$defs/specificationIdentity"},
            "request": {"$ref": "#/$defs/requestIdentity"},
            "receipt": {"$ref": "#/$defs/productionReceiptIdentity"},
            "selection": {"$ref": "#/$defs/selectionIdentity"},
            "provenance": {"$ref": "#/$defs/provenanceIdentity"},
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
            "processing_recipe": {
                "oneOf": [
                    {"$ref": "#/$defs/recipeIdentity"},
                    {"type": "null"},
                ]
            },
            "processing_receipt": {
                "oneOf": [
                    {"$ref": "#/$defs/processingReceiptIdentity"},
                    {"type": "null"},
                ]
            },
            "qa_report": {
                "oneOf": [
                    {"$ref": "#/$defs/qaReportIdentity"},
                    {"type": "null"},
                ]
            },
            "state": {"enum": ["produced", "processed", "release_ready"]},
            "outputs": {
                "items": {"$ref": "#/$defs/manifestOutput"},
                "maxItems": 4,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["role"],
                    "uniqueBy": [
                        ["role"],
                        ["runtime_path"],
                        ["locator"],
                    ],
                },
                "x-world-forge-portable-path-tree": "locator",
            },
        },
        "required": [
            "asset",
            "specification",
            "request",
            "receipt",
            "selection",
            "provenance",
            "licenses",
            "processing_recipe",
            "processing_receipt",
            "qa_report",
            "state",
            "outputs",
        ],
        "type": "object",
    }
    manifest = _base_schema(
        PROCESSING_FILES[3],
        "world-forge.asset_manifest",
        "manifest_id",
        {
            "gamepack": {"$ref": "#/$defs/gamepackIdentity"},
            "asset_subject": {"$ref": "#/$defs/assetSubjectIdentity"},
            "target": {"$ref": "#/$defs/targetIdentity"},
            "style": {"$ref": "#/$defs/styleIdentity"},
            "inventory": {"$ref": "#/$defs/inventoryIdentity"},
            "state": {"enum": ["produced", "processed", "release_ready"]},
            "assets": {
                "items": {"$ref": "#/$defs/manifestAsset"},
                "maxItems": 1024,
                "minItems": 0,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["asset.asset_id"],
                    "uniqueBy": [["asset.asset_id"]],
                },
            },
        },
        [
            "gamepack",
            "asset_subject",
            "target",
            "style",
            "inventory",
            "state",
            "assets",
        ],
        manifest_definitions,
    )
    manifest["title"] = "World Forge generic asset release manifest v1"
    manifest["x-world-forge-d2b-coherent"] = "manifest"
    state_bindings = {
        "produced": {
            "processing_recipe": {"type": "null"},
            "processing_receipt": {"type": "null"},
            "qa_report": {"type": "null"},
        },
        "processed": {
            "processing_recipe": {"$ref": "#/$defs/recipeIdentity"},
            "processing_receipt": {"$ref": "#/$defs/processingReceiptIdentity"},
            "qa_report": {"type": "null"},
        },
        "release_ready": {
            "processing_recipe": {"$ref": "#/$defs/recipeIdentity"},
            "processing_receipt": {"$ref": "#/$defs/processingReceiptIdentity"},
            "qa_report": {"$ref": "#/$defs/qaReportIdentity"},
        },
    }
    manifest["allOf"] = [
        {
            "if": {
                "properties": {"state": {"const": state}},
                "required": ["state"],
                "type": "object",
            },
            "then": {
                "properties": {
                    "assets": {
                        "items": {
                            "properties": {
                                "state": {"const": state},
                                **bindings,
                            },
                            "required": [
                                "state",
                                "processing_recipe",
                                "processing_receipt",
                                "qa_report",
                            ],
                            "type": "object",
                        },
                        "type": "array",
                    }
                },
                "type": "object",
            },
        }
        for state, bindings in state_bindings.items()
    ]
    return dict(
        zip(
            PROCESSING_FILES,
            (recipe, processing_receipt, qa, manifest),
            strict=True,
        )
    )


def _authority_binding(operation: str) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": {
            "workspace_id": {"$ref": "#/$defs/workspaceId"},
            "root_generation": {
                "maximum": 9007199254740991,
                "minimum": 0,
                "type": "integer",
            },
            "source_revision": {"$ref": "#/$defs/sha256"},
            "workflow_status_hash": {
                "oneOf": [
                    {"$ref": "#/$defs/sha256"},
                    {"type": "null"},
                ]
            },
            "artifact_snapshot_hash": {"$ref": "#/$defs/sha256"},
            "producer_job_id": {"$ref": "#/$defs/entityId"},
            "producer_operation": {"const": operation},
            "producer_output_position": {
                "maximum": 4095,
                "minimum": 0,
                "type": "integer",
            },
        },
        "required": [
            "workspace_id",
            "root_generation",
            "source_revision",
            "workflow_status_hash",
            "artifact_snapshot_hash",
            "producer_job_id",
            "producer_operation",
            "producer_output_position",
        ],
        "type": "object",
    }


def _reviewed_output_variants() -> list[dict[str, Any]]:
    variants = []
    for role, media_type in ROLE_MEDIA:
        variants.append(
            {
                "additionalProperties": False,
                "properties": {
                    "candidate_artifact_id": {"$ref": "#/$defs/id"},
                    "role": {"const": role},
                    "media_type": {"const": media_type},
                    "runtime_path": {"$ref": "#/$defs/path"},
                    "locator": {"$ref": "#/$defs/path"},
                    "sha256": {"$ref": "#/$defs/sha256"},
                    "size_bytes": {
                        "maximum": 16777216,
                        "minimum": 1,
                        "type": "integer",
                    },
                },
                "required": [
                    "candidate_artifact_id",
                    "role",
                    "media_type",
                    "runtime_path",
                    "locator",
                    "sha256",
                    "size_bytes",
                ],
                "type": "object",
            }
        )
    return variants


def _authority_schemas() -> dict[str, dict[str, Any]]:
    definitions = _common_definitions()
    definitions.update(
        {
            "workspaceId": {
                "maxLength": 64,
                "minLength": 2,
                "pattern": "^[a-z][a-z0-9_-]{1,63}$",
                "type": "string",
            },
            "entityId": {
                "maxLength": 128,
                "minLength": 1,
                "pattern": "^[a-z0-9][a-z0-9_-]{0,127}$",
                "type": "string",
            },
            "reviewIdentity": _identity("world-forge.asset_qa_review_receipt"),
            "manifestIdentity": _identity("world-forge.asset_manifest"),
            "assetpackIdentity": _identity("world-forge.assetpack"),
            "reviewAuthority": _authority_binding("asset.qa.review"),
            "releaseAuthority": _authority_binding("asset.release.authorize"),
            "reviewedOutput": {"oneOf": _reviewed_output_variants()},
        }
    )
    lineage_properties = {
        "gamepack": {"$ref": "#/$defs/gamepackIdentity"},
        "asset_subject": {"$ref": "#/$defs/assetSubjectIdentity"},
        "target": {"$ref": "#/$defs/targetIdentity"},
        "style": {"$ref": "#/$defs/styleIdentity"},
        "inventory": {"$ref": "#/$defs/inventoryIdentity"},
        "specification": {"$ref": "#/$defs/specificationIdentity"},
        "request": {"$ref": "#/$defs/requestIdentity"},
        "receipt": {"$ref": "#/$defs/productionReceiptIdentity"},
        "selection": {"$ref": "#/$defs/selectionIdentity"},
        "provenance": {"$ref": "#/$defs/provenanceIdentity"},
        "recipe": {"$ref": "#/$defs/recipeIdentity"},
        "processing_receipt": {"$ref": "#/$defs/processingReceiptIdentity"},
        "qa_report": {"$ref": "#/$defs/qaReportIdentity"},
    }
    definitions["reviewLineage"] = {
        "additionalProperties": False,
        "properties": lineage_properties,
        "required": list(lineage_properties),
        "type": "object",
    }
    definitions["reviewCriterion"] = {
        "additionalProperties": False,
        "properties": {
            "criterion_index": {
                "maximum": MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS - 1,
                "minimum": 0,
                "type": "integer",
            },
            "criterion_sha256": {"$ref": "#/$defs/sha256"},
            "decision": {"enum": ["approved", "rejected"]},
        },
        "required": ["criterion_index", "criterion_sha256", "decision"],
        "type": "object",
    }
    blockers = {
        "items": {"$ref": "#/$defs/id"},
        "maxItems": 64,
        "minItems": 0,
        "type": "array",
        "uniqueItems": True,
        "x-world-forge-canonical-string-array": True,
    }
    review = _base_schema(
        AUTHORITY_FILES[0],
        "world-forge.asset_qa_review_receipt",
        "review_receipt_id",
        {
            "asset": {"$ref": "#/$defs/asset"},
            "lineage": {"$ref": "#/$defs/reviewLineage"},
            "reviewed_output": {"$ref": "#/$defs/reviewedOutput"},
            "criteria": {
                "items": {"$ref": "#/$defs/reviewCriterion"},
                "maxItems": MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["criterion_index"],
                    "uniqueBy": [["criterion_index"], ["criterion_sha256"]],
                },
            },
            "status": {"enum": ["approved", "rejected"]},
            "blockers": copy.deepcopy(blockers),
            "authority": {"$ref": "#/$defs/reviewAuthority"},
        },
        [
            "asset",
            "lineage",
            "reviewed_output",
            "criteria",
            "status",
            "blockers",
            "authority",
        ],
        copy.deepcopy(definitions),
    )
    review["title"] = "World Forge retained asset QA review receipt v1"
    review["allOf"] = [
        {
            "if": {
                "properties": {"status": {"const": "approved"}},
                "required": ["status"],
                "type": "object",
            },
            "then": {
                "properties": {
                    "criteria": {
                        "items": {
                            "properties": {"decision": {"const": "approved"}},
                            "required": ["decision"],
                            "type": "object",
                        },
                        "type": "array",
                    },
                    "blockers": {"maxItems": 0, "type": "array"},
                },
                "type": "object",
            },
        },
        {
            "if": {
                "properties": {"status": {"const": "rejected"}},
                "required": ["status"],
                "type": "object",
            },
            "then": {
                "properties": {
                    "criteria": {
                        "contains": {
                            "properties": {"decision": {"const": "rejected"}},
                            "required": ["decision"],
                            "type": "object",
                        },
                        "type": "array",
                    },
                    "blockers": {"minItems": 1, "type": "array"},
                },
                "type": "object",
            },
        },
    ]

    release = _base_schema(
        AUTHORITY_FILES[1],
        "world-forge.asset_release_authority",
        "release_authority_id",
        {
            "candidate_manifest": {"$ref": "#/$defs/manifestIdentity"},
            "candidate_assetpack": {"$ref": "#/$defs/assetpackIdentity"},
            "qa_reviews": {
                "items": {"$ref": "#/$defs/reviewIdentity"},
                "maxItems": 4096,
                "minItems": 1,
                "type": "array",
                "x-world-forge-canonical-object-array": {
                    "orderBy": ["id"],
                    "uniqueBy": [["id"], ["content_hash"]],
                },
            },
            "status": {"enum": ["authorized", "blocked"]},
            "blockers": copy.deepcopy(blockers),
            "authority": {"$ref": "#/$defs/releaseAuthority"},
        },
        [
            "candidate_manifest",
            "candidate_assetpack",
            "qa_reviews",
            "status",
            "blockers",
            "authority",
        ],
        copy.deepcopy(definitions),
    )
    release["title"] = "World Forge asset release authority companion v1"
    release["allOf"] = [
        {
            "if": {
                "properties": {"status": {"const": "authorized"}},
                "required": ["status"],
                "type": "object",
            },
            "then": {
                "properties": {"blockers": {"maxItems": 0, "type": "array"}},
                "type": "object",
            },
        },
        {
            "if": {
                "properties": {"status": {"const": "blocked"}},
                "required": ["status"],
                "type": "object",
            },
            "then": {
                "properties": {"blockers": {"minItems": 1, "type": "array"}},
                "type": "object",
            },
        },
    ]
    return dict(zip(AUTHORITY_FILES, (review, release), strict=True))


def build_schemas() -> dict[str, dict[str, Any]]:
    specification = _read("generic-asset-spec.schema.json")
    if (
        specification["properties"]["acceptance_criteria"].get("maxItems")
        != MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
    ):
        raise ValueError("generic asset specification acceptance_criteria bound is not canonical")
    schemas = {name: _read(name) for name in PRODUCTION_FILES}
    request = schemas["generic-asset-production-request.schema.json"]
    receipt = schemas["generic-asset-production-receipt.schema.json"]
    selection = schemas["generic-asset-selection.schema.json"]
    provenance = schemas["generic-asset-provenance-record.schema.json"]
    license_record = schemas["generic-asset-license-record.schema.json"]

    _require_recorded_procedural_seed(request)
    _receipt_output_variants(receipt)
    selection["properties"]["selected_outputs"]["items"] = _role_media_variants(
        selection["properties"]["selected_outputs"]["items"]
    )
    provenance["properties"]["candidates"]["items"] = _role_media_variants(
        provenance["properties"]["candidates"]["items"]
    )
    license_record["properties"]["candidate"] = _role_media_variants(
        license_record["properties"]["candidate"]
    )
    _add_receipt_lineage(selection)
    schemas.update(_processing_schemas())
    schemas.update(_authority_schemas())
    return schemas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    mismatches = []
    for name, schema in build_schemas().items():
        path = SCHEMAS / name
        expected = canonical_json_bytes(schema)
        if arguments.check:
            if path.read_bytes() != expected:
                mismatches.append(path.relative_to(ROOT).as_posix())
        else:
            path.write_bytes(expected)
    if mismatches:
        raise SystemExit("stale generic asset production schemas: " + ", ".join(mismatches))
    print(
        "OK generic_asset_production_schemas="
        f"{len(PRODUCTION_FILES) + len(PROCESSING_FILES) + len(AUTHORITY_FILES)} "
        f"mode={'check' if arguments.check else 'write'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
