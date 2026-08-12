"""Generate the additive generic creation workflow contract schemas."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from worldforge.integrity import canonical_json_bytes
from worldforge.phase_report_v3 import (
    _NOT_APPLICABLE_CODES,
    _PHASE_ROLE_FORMATS,
    PHASE_REPORT_V3_PHASE_IDS,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
ID_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"
GAME_RUNTIME_BUNDLE_ID_PATTERN = r"^game_runtime_bundle_[0-9a-f]{48}$"
SHA_PATTERN = r"^[0-9a-f]{64}$"


def _object(properties: dict[str, Any], *, required: list[str] | None = None) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties) if required is None else required,
        "type": "object",
    }


def _array(items: dict[str, Any], *, minimum: int = 0, maximum: int = 256) -> dict[str, Any]:
    return {
        "items": items,
        "maxItems": maximum,
        "minItems": minimum,
        "type": "array",
    }


def _identity(format_name: str | None = None) -> dict[str, Any]:
    identity = _object(
        {
            "format": (
                {"const": format_name}
                if format_name is not None
                else {"maxLength": 128, "minLength": 1, "type": "string"}
            ),
            "format_version": {"const": 1},
            "id": (
                {"maxLength": 128, "minLength": 1, "type": "string"}
                if format_name is None
                else {"$ref": "#/$defs/id"}
            ),
            "content_hash": {"$ref": "#/$defs/sha256"},
        }
    )
    if format_name is None:
        identity["allOf"] = [
            {
                "if": {
                    "properties": {"format": {"const": "world-forge.game_runtime_bundle"}},
                    "required": ["format"],
                },
                "then": {
                    "properties": {
                        "id": {
                            "pattern": GAME_RUNTIME_BUNDLE_ID_PATTERN,
                            "type": "string",
                        }
                    }
                },
                "else": {"properties": {"id": {"$ref": "#/$defs/id"}}},
            }
        ]
    return identity


def _base_defs() -> dict[str, Any]:
    return {
        "id": {"pattern": ID_PATTERN, "type": "string"},
        "sha256": {"pattern": SHA_PATTERN, "type": "string"},
        "identity": _identity(),
        "projectIdentity": _identity("world-forge.project"),
        "profileIdentity": _identity("world-forge.creation_profile"),
        "manifestIdentity": _identity("world-forge.creation_source_manifest"),
    }


def _phase_report_schema() -> dict[str, Any]:
    defs = _base_defs()
    defs["reviewer"] = _object(
        {
            "id": {"$ref": "#/$defs/id"},
            "role": {"$ref": "#/$defs/id"},
        }
    )
    defs["evidence"] = _object(
        {
            "evidence_id": {"$ref": "#/$defs/id"},
            "claim": {"minLength": 1, "type": "string"},
            "subject": {"$ref": "#/$defs/identity"},
        }
    )
    defs["rationale"] = _object(
        {
            "code": {"$ref": "#/$defs/id"},
            "message": {"minLength": 1, "type": "string"},
        }
    )
    all_roles = sorted({role for roles in _PHASE_ROLE_FORMATS.values() for role in roles})
    all_subject_formats = sorted(
        {
            format_name
            for roles in _PHASE_ROLE_FORMATS.values()
            for formats in roles.values()
            for format_name in formats
        }
    )
    defs["outputEvidence"] = _object(
        {
            "format": {"const": "world-forge.phase_output_evidence"},
            "format_version": {"const": 2},
            "id": {"$ref": "#/$defs/id"},
            "phase": {"enum": list(PHASE_REPORT_V3_PHASE_IDS)},
            "role": {"enum": all_roles},
            "subject": {
                "allOf": [
                    {"$ref": "#/$defs/identity"},
                    {
                        "type": "object",
                        "properties": {"format": {"enum": all_subject_formats}},
                    },
                ]
            },
            "reviewer": {"$ref": "#/$defs/reviewer"},
            "content_hash": {"$ref": "#/$defs/sha256"},
        }
    )
    common = _object(
        {
            "format": {"const": "world-forge.phase_report"},
            "format_version": {"const": 3},
            "project": {"$ref": "#/$defs/projectIdentity"},
            "profile": {"$ref": "#/$defs/profileIdentity"},
            "source_manifest": {"$ref": "#/$defs/manifestIdentity"},
            "phase": {"enum": list(PHASE_REPORT_V3_PHASE_IDS)},
            "status": {"enum": ["ready", "not_applicable"]},
            "rationale": {"$ref": "#/$defs/rationale"},
            "evidence": _array({"$ref": "#/$defs/evidence"}, minimum=1),
            "output_evidence": {
                "oneOf": [
                    {"$ref": "#/$defs/outputEvidence"},
                    {"type": "null"},
                ]
            },
            "reviewer": {"$ref": "#/$defs/reviewer"},
            "invalidation_dependencies": _array(
                {"$ref": "#/$defs/identity"},
                minimum=1,
            ),
            "extensions": _array({"type": "object"}),
            "content_hash": {"$ref": "#/$defs/sha256"},
        }
    )
    defs["reportCommon"] = common
    variants: list[dict[str, Any]] = []
    for phase in PHASE_REPORT_V3_PHASE_IDS:
        for role, formats in _PHASE_ROLE_FORMATS[phase].items():
            variants.append(
                {
                    "allOf": [
                        {"$ref": "#/$defs/reportCommon"},
                        {
                            "type": "object",
                            "properties": {
                                "phase": {"const": phase},
                                "status": {"const": "ready"},
                                "rationale": {
                                    "type": "object",
                                    "properties": {"code": {"const": "phase_ready"}},
                                },
                                "output_evidence": {
                                    "allOf": [
                                        {"$ref": "#/$defs/outputEvidence"},
                                        {
                                            "type": "object",
                                            "properties": {
                                                "phase": {"const": phase},
                                                "role": {"const": role},
                                                "subject": {
                                                    "allOf": [
                                                        {"$ref": "#/$defs/identity"},
                                                        {
                                                            "type": "object",
                                                            "properties": {
                                                                "format": {"enum": sorted(formats)}
                                                            },
                                                        },
                                                    ]
                                                },
                                            },
                                        },
                                    ]
                                },
                            },
                        },
                    ]
                }
            )
    for phase, code in _NOT_APPLICABLE_CODES.items():
        variants.append(
            {
                "allOf": [
                    {"$ref": "#/$defs/reportCommon"},
                    {
                        "type": "object",
                        "properties": {
                            "phase": {"const": phase},
                            "status": {"const": "not_applicable"},
                            "rationale": {
                                "type": "object",
                                "properties": {"code": {"const": code}},
                            },
                            "output_evidence": {"type": "null"},
                        },
                    },
                ]
            }
        )
    return {
        "$id": "https://world-forge.local/schemas/phase-report-v3.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "World Forge phase report v3",
        "type": "object",
        "properties": {
            "format": {"const": "world-forge.phase_report"},
            "format_version": {"const": 3},
        },
        "oneOf": variants,
        "$defs": defs,
    }


def _workflow_schema() -> dict[str, Any]:
    defs = _base_defs()
    phase_enum = list(PHASE_REPORT_V3_PHASE_IDS)
    defs["reportReference"] = _object(
        {
            "phase": {"enum": phase_enum},
            "status": {"enum": ["ready", "not_applicable"]},
            "path": {
                "pattern": (
                    r"^\.worldforge/phase_reports/p[0-9]{2}_[a-z0-9_]+-"
                    r"[0-9a-f]{64}\.json$"
                ),
                "type": "string",
            },
            "content_hash": {"$ref": "#/$defs/sha256"},
            "invalidation_dependencies": _array(
                {"$ref": "#/$defs/identity"},
                minimum=1,
            ),
        }
    )
    defs["invalidatedReport"] = _object(
        {
            "phase": {"enum": phase_enum},
            "report_content_hash": {"$ref": "#/$defs/sha256"},
            "reason": {"minLength": 1, "type": "string"},
            "revision": {"minimum": 1, "type": "integer"},
        }
    )
    return {
        "$id": "https://world-forge.local/schemas/creation-workflow-status.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "World Forge creation workflow status v1",
        **_object(
            {
                "format": {"const": "world-forge.creation_workflow_status"},
                "format_version": {"const": 1},
                "workflow_id": {"$ref": "#/$defs/id"},
                "project": {"$ref": "#/$defs/projectIdentity"},
                "profile": {"$ref": "#/$defs/profileIdentity"},
                "source_manifest": {"$ref": "#/$defs/manifestIdentity"},
                "current_phase": {"oneOf": [{"enum": phase_enum}, {"type": "null"}]},
                "completed_phases": _array({"enum": phase_enum}, maximum=15),
                "reports": _array(
                    {"$ref": "#/$defs/reportReference"},
                    maximum=15,
                ),
                "invalidated_reports": _array(
                    {"$ref": "#/$defs/invalidatedReport"},
                    maximum=256,
                ),
                "revision": {"minimum": 0, "type": "integer"},
                "content_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "$defs": defs,
    }


def _readiness_schema() -> dict[str, Any]:
    defs = _base_defs()
    defs["execution"] = _object(
        {
            "platform": {"pattern": r"^platform:[a-z0-9_.-]+$", "type": "string"},
            "status": {
                "enum": [
                    "untested",
                    "headless_verified",
                    "native_verified",
                    "failed",
                ]
            },
            "evidence_ids": _array({"$ref": "#/$defs/id"}),
        }
    )
    defs["dimensions"] = _object(
        {
            "authoring": {"const": "valid"},
            "compilation": {"enum": ["not_requested", "compiled", "unsupported", "failed"]},
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
            "execution": _array({"$ref": "#/$defs/execution"}, maximum=32),
            "packaging": {"enum": ["unverified", "verified", "failed"]},
            "release": {"enum": ["blocked", "ready"]},
        }
    )
    return {
        "$id": "https://world-forge.local/schemas/creation-readiness.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "World Forge creation readiness v1",
        **_object(
            {
                "format": {"const": "world-forge.creation_readiness"},
                "format_version": {"const": 1},
                "readiness_id": {"$ref": "#/$defs/id"},
                "project": {"$ref": "#/$defs/projectIdentity"},
                "profile": {"$ref": "#/$defs/profileIdentity"},
                "source_manifest": {"$ref": "#/$defs/manifestIdentity"},
                "dimensions": {"$ref": "#/$defs/dimensions"},
                "blocker_reason_codes": _array({"$ref": "#/$defs/id"}),
                "release_ready": {"type": "boolean"},
                "evidence": _array({"$ref": "#/$defs/identity"}),
                "content_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "$defs": defs,
    }


def _handoff_schema() -> dict[str, Any]:
    defs = _base_defs()
    return {
        "$id": "https://world-forge.local/schemas/creation-handoff.schema.json",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "World Forge creation handoff v1",
        **_object(
            {
                "format": {"const": "world-forge.creation_handoff"},
                "format_version": {"const": 1},
                "handoff_id": {"$ref": "#/$defs/id"},
                "project": {"$ref": "#/$defs/projectIdentity"},
                "profile": {"$ref": "#/$defs/profileIdentity"},
                "source_manifest": {"$ref": "#/$defs/manifestIdentity"},
                "workflow_status": {
                    "allOf": [
                        {"$ref": "#/$defs/identity"},
                        {
                            "type": "object",
                            "properties": {
                                "format": {"const": "world-forge.creation_workflow_status"}
                            },
                        },
                    ]
                },
                "readiness": {
                    "allOf": [
                        {"$ref": "#/$defs/identity"},
                        {
                            "type": "object",
                            "properties": {"format": {"const": "world-forge.creation_readiness"}},
                        },
                    ]
                },
                "artifacts": _array({"$ref": "#/$defs/identity"}),
                "handoff_status": {"enum": ["authoring_ready", "implementation_ready"]},
                "release_blockers": _array({"$ref": "#/$defs/id"}),
                "content_hash": {"$ref": "#/$defs/sha256"},
            }
        ),
        "$defs": defs,
    }


def generated_schemas() -> dict[Path, bytes]:
    return {
        SCHEMAS / "phase-report-v3.schema.json": canonical_json_bytes(_phase_report_schema()),
        SCHEMAS / "creation-workflow-status.schema.json": canonical_json_bytes(_workflow_schema()),
        SCHEMAS / "creation-readiness.schema.json": canonical_json_bytes(_readiness_schema()),
        SCHEMAS / "creation-handoff.schema.json": canonical_json_bytes(_handoff_schema()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate or verify generic creation workflow schemas",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    mismatches: list[Path] = []
    for path, payload in generated_schemas().items():
        if args.write:
            path.write_bytes(payload)
        elif not path.is_file() or path.read_bytes() != payload:
            mismatches.append(path)
    if mismatches:
        for path in mismatches:
            print(f"ERROR schema differs: {path.relative_to(ROOT)}")
        return 1
    print(
        f"OK creation_workflow_schemas={len(generated_schemas())} "
        f"mode={'write' if args.write else 'check'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
