from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from worldforge.creation_contracts import (
    ACTIVITY_MODULE_FORMAT,
    CREATION_PROFILE_FORMAT,
    CREATION_PROJECT_FORMAT,
    CREATION_SOURCE_MANIFEST_FORMAT,
    LOGIC_MODULE_FORMAT,
    NARRATIVE_MODULE_FORMAT,
    SYSTEM_MODULE_FORMAT,
    WORLD_MODULE_FORMAT,
    CreationContractError,
    ExtensionValidator,
    LoadedCreationProject,
    _exact_keys,
    _extensions,
    _identifier,
    _non_empty_string,
    _object,
    _sha256,
    canonical_creation_hash,
    load_creation_project,
    read_creation_object,
    validate_creation_documents,
)
from worldforge.validation_memo import validation_memo_scope

PHASE_REPORT_V3_FORMAT = "world-forge.phase_report"
PHASE_REPORT_V3_VERSION = 3
PHASE_OUTPUT_EVIDENCE_FORMAT = "world-forge.phase_output_evidence"
PHASE_OUTPUT_EVIDENCE_VERSION = 2
PHASE_REPORT_V3_PHASE_IDS = (
    "p00_brief",
    "p01_genre_style",
    "p02_world_laws",
    "p03_geography",
    "p04_timeline",
    "p05_societies",
    "p06_characters",
    "p07_systems",
    "p08_world_arcs",
    "p09_narrative_content",
    "p10_canon_lock",
    "p11_art_audio",
    "p12_asset_specs",
    "p13_asset_production",
    "p14_handoff",
)
PHASE_IDS = PHASE_REPORT_V3_PHASE_IDS

_REPORT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "project",
        "profile",
        "source_manifest",
        "phase",
        "status",
        "rationale",
        "evidence",
        "output_evidence",
        "reviewer",
        "invalidation_dependencies",
        "extensions",
        "content_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_GAME_RUNTIME_BUNDLE_ID_RE = re.compile(r"^game_runtime_bundle_[0-9a-f]{48}$")
_EVIDENCE_FIELDS = frozenset({"evidence_id", "claim", "subject"})
_OUTPUT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "phase",
        "role",
        "subject",
        "reviewer",
        "content_hash",
    }
)
_RATIONALE_FIELDS = frozenset({"code", "message"})
_REVIEWER_FIELDS = frozenset({"id", "role"})

_IDENTITY_FIELD_BY_FORMAT = {
    CREATION_PROJECT_FORMAT: "project_id",
    CREATION_PROFILE_FORMAT: "profile_id",
    CREATION_SOURCE_MANIFEST_FORMAT: "project_id",
    WORLD_MODULE_FORMAT: "module_id",
    ACTIVITY_MODULE_FORMAT: "module_id",
    NARRATIVE_MODULE_FORMAT: "module_id",
    SYSTEM_MODULE_FORMAT: "module_id",
    LOGIC_MODULE_FORMAT: "module_id",
    "world-forge.gamepack": "game_id",
    "world-forge.game_analysis": "analysis_id",
    "world-forge.mechanic_capability_ledger": "ledger_id",
    "world-forge.asset_subject": "subject_id",
    "world-forge.asset_target": "target_id",
    "world-forge.asset_style": "style_id",
    "world-forge.asset_inventory": "inventory_id",
    "world-forge.asset_spec": "spec_id",
    "world-forge.asset_production_request": "request_id",
    "world-forge.asset_production_receipt": "receipt_id",
    "world-forge.asset_selection": "selection_id",
    "world-forge.asset_provenance_record": "provenance_id",
    "world-forge.asset_license_record": "license_record_id",
    "world-forge.asset_processing_recipe": "recipe_id",
    "world-forge.asset_processing_receipt": "processing_receipt_id",
    "world-forge.asset_qa_report": "qa_report_id",
    "world-forge.asset_qa_review_receipt": "review_receipt_id",
    "world-forge.asset_release_authority": "release_authority_id",
    "world-forge.asset_manifest": "manifest_id",
    "world-forge.assetpack": "assetpack_id",
    "world-forge.runtime_adapter": "adapter_id",
    "world-forge.runtime_adapter_registry": "registry_id",
    "world-forge.game_runtime_snapshot": "snapshot_id",
    "world-forge.game_runtime_composition": "composition_id",
    "world-forge.runtime_evidence": "evidence_id",
    "world-forge.runtime_support_report": "report_id",
    "world-forge.runtime_support_authority": "authority_id",
    "world-forge.game_execution_script": "script_id",
    "world-forge.game_runtime_bundle": "bundle_id",
    "world-forge.game_materialization_bundle": "materialization_bundle_id",
    "world-forge.standalone_game": "game_id",
    "world-forge.game_package": "package_id",
    "world-forge.game_package_extraction": "extraction_id",
    "world-forge.creation_workflow_status": "workflow_id",
    "world-forge.creation_readiness": "readiness_id",
    "world-forge.creation_handoff": "handoff_id",
}
_CREATION_FORMATS = frozenset(
    {
        CREATION_PROJECT_FORMAT,
        CREATION_PROFILE_FORMAT,
        CREATION_SOURCE_MANIFEST_FORMAT,
        WORLD_MODULE_FORMAT,
        ACTIVITY_MODULE_FORMAT,
        NARRATIVE_MODULE_FORMAT,
        SYSTEM_MODULE_FORMAT,
        LOGIC_MODULE_FORMAT,
    }
)
_ASSET_FORMATS = frozenset(
    {
        "world-forge.asset_subject",
        "world-forge.asset_target",
        "world-forge.asset_style",
        "world-forge.asset_inventory",
        "world-forge.asset_spec",
        "world-forge.asset_production_request",
        "world-forge.asset_production_receipt",
        "world-forge.asset_selection",
        "world-forge.asset_provenance_record",
        "world-forge.asset_license_record",
        "world-forge.asset_processing_recipe",
        "world-forge.asset_processing_receipt",
        "world-forge.asset_qa_report",
        "world-forge.asset_qa_review_receipt",
        "world-forge.asset_release_authority",
        "world-forge.asset_manifest",
        "world-forge.assetpack",
    }
)
_RUNTIME_FORMATS = frozenset(
    {
        "world-forge.runtime_adapter",
        "world-forge.runtime_adapter_registry",
        "world-forge.game_runtime_snapshot",
        "world-forge.game_runtime_composition",
        "world-forge.runtime_evidence",
        "world-forge.runtime_support_report",
        "world-forge.runtime_support_authority",
        "world-forge.game_execution_script",
        "world-forge.game_runtime_bundle",
        "world-forge.game_materialization_bundle",
        "world-forge.standalone_game",
        "world-forge.game_package",
        "world-forge.game_package_extraction",
    }
)
_EXTERNAL_ARTIFACT_FORMATS = frozenset(
    {
        "world-forge.gamepack",
        "world-forge.game_analysis",
        "world-forge.mechanic_capability_ledger",
        "world-forge.asset_subject",
        "world-forge.asset_target",
        "world-forge.asset_style",
        "world-forge.asset_inventory",
        "world-forge.asset_spec",
        "world-forge.asset_production_request",
        "world-forge.asset_production_receipt",
        "world-forge.asset_selection",
        "world-forge.asset_provenance_record",
        "world-forge.asset_license_record",
        "world-forge.asset_processing_recipe",
        "world-forge.asset_processing_receipt",
        "world-forge.asset_qa_report",
        "world-forge.asset_qa_review_receipt",
        "world-forge.asset_release_authority",
        "world-forge.asset_manifest",
        "world-forge.assetpack",
        "world-forge.runtime_adapter",
        "world-forge.runtime_adapter_registry",
        "world-forge.game_runtime_snapshot",
        "world-forge.game_runtime_composition",
        "world-forge.runtime_evidence",
        "world-forge.runtime_support_report",
        "world-forge.runtime_support_authority",
        "world-forge.game_execution_script",
        "world-forge.game_runtime_bundle",
        "world-forge.game_materialization_bundle",
        "world-forge.standalone_game",
        "world-forge.game_package",
        "world-forge.game_package_extraction",
        "world-forge.creation_workflow_status",
        "world-forge.creation_readiness",
        "world-forge.creation_handoff",
    }
)
_PHASE_ROLE_FORMATS = {
    "p00_brief": {"project_brief": frozenset({CREATION_PROJECT_FORMAT})},
    "p01_genre_style": {"experience_classification": frozenset({CREATION_PROFILE_FORMAT})},
    "p02_world_laws": {
        "interaction_ontology": frozenset(
            {
                CREATION_PROFILE_FORMAT,
                ACTIVITY_MODULE_FORMAT,
                SYSTEM_MODULE_FORMAT,
                LOGIC_MODULE_FORMAT,
            }
        )
    },
    "p03_geography": {"world_topology": frozenset({CREATION_PROFILE_FORMAT, WORLD_MODULE_FORMAT})},
    "p04_timeline": {
        "chronology": frozenset(
            {CREATION_PROFILE_FORMAT, WORLD_MODULE_FORMAT, SYSTEM_MODULE_FORMAT}
        )
    },
    "p05_societies": {
        "group_structures": frozenset({CREATION_PROFILE_FORMAT, WORLD_MODULE_FORMAT})
    },
    "p06_characters": {
        "player_actors": frozenset(
            {
                CREATION_PROFILE_FORMAT,
                WORLD_MODULE_FORMAT,
                ACTIVITY_MODULE_FORMAT,
                NARRATIVE_MODULE_FORMAT,
            }
        )
    },
    "p07_systems": {
        "systems_design": frozenset(
            {
                CREATION_PROFILE_FORMAT,
                ACTIVITY_MODULE_FORMAT,
                SYSTEM_MODULE_FORMAT,
                LOGIC_MODULE_FORMAT,
            }
        )
    },
    "p08_world_arcs": {
        "narrative_architecture": frozenset({CREATION_PROFILE_FORMAT, NARRATIVE_MODULE_FORMAT})
    },
    "p09_narrative_content": {
        "typed_content": frozenset(
            {
                CREATION_SOURCE_MANIFEST_FORMAT,
                WORLD_MODULE_FORMAT,
                ACTIVITY_MODULE_FORMAT,
                NARRATIVE_MODULE_FORMAT,
                SYSTEM_MODULE_FORMAT,
                LOGIC_MODULE_FORMAT,
            }
        )
    },
    "p10_canon_lock": {
        "content_lock": frozenset({CREATION_SOURCE_MANIFEST_FORMAT, "world-forge.gamepack"})
    },
    "p11_art_audio": {
        "presentation_direction": frozenset(
            {
                CREATION_PROFILE_FORMAT,
                "world-forge.asset_target",
                "world-forge.asset_style",
            }
        )
    },
    "p12_asset_specs": {
        "asset_plan": frozenset(
            {
                "world-forge.asset_inventory",
                "world-forge.asset_spec",
                "world-forge.asset_manifest",
            }
        )
    },
    "p13_asset_production": {
        "runtime_compatibility": frozenset(
            {
                "world-forge.creation_readiness",
                "world-forge.runtime_support_report",
            }
        )
    },
    "p14_handoff": {"implementation_handoff": frozenset({"world-forge.creation_handoff"})},
}
_NOT_APPLICABLE_CODES = {
    "p03_geography": "world_absent",
    "p04_timeline": "chronology_absent",
    "p05_societies": "group_structures_absent",
    "p06_characters": "actors_absent",
    "p08_world_arcs": "narrative_absent",
    "p11_art_audio": "assets_not_applicable",
    "p12_asset_specs": "assets_not_applicable",
    "p13_asset_production": "runtime_not_applicable",
}


class PhaseReportV3Error(ValueError):
    """Raised when a generic phase-report v3 fails closed validation."""


def _validated_project(
    project: LoadedCreationProject,
    registered_extensions: Mapping[str, ExtensionValidator] | None,
) -> LoadedCreationProject:
    if not isinstance(project, LoadedCreationProject):
        raise PhaseReportV3Error("phase report v3 requires a loaded creation project")
    try:
        return validate_creation_documents(
            project.project,
            project.profile,
            project.manifest,
            project.world_modules,
            project.activity_modules,
            project.narrative_modules,
            project.system_modules,
            project.logic_modules,
            registered_extensions=registered_extensions,
        )
    except CreationContractError as exc:
        raise PhaseReportV3Error(f"creation project is invalid: {exc}") from exc


def document_identity(document: Mapping[str, Any]) -> dict[str, Any]:
    format_name = document.get("format")
    field = _IDENTITY_FIELD_BY_FORMAT.get(format_name)
    if field is None:
        raise PhaseReportV3Error(f"unsupported artifact format: {format_name!r}")
    if format_name == "world-forge.gamepack":
        game = document.get("game")
        identifier = game.get("id") if isinstance(game, Mapping) else None
    else:
        identifier = document.get(field)
    return {
        "format": format_name,
        "format_version": document.get("format_version"),
        "id": identifier,
        "content_hash": document.get("content_hash"),
    }


def _identity_key(identity: Mapping[str, Any]) -> tuple[str, int, str, str]:
    return (
        str(identity["format"]),
        int(identity["format_version"]),
        str(identity["id"]),
        str(identity["content_hash"]),
    )


def _identity_sort_key(identity: Mapping[str, Any]) -> tuple[bytes, int, bytes, bytes]:
    format_name, version, identifier, content_hash = _identity_key(identity)
    return (
        format_name.encode("utf-8"),
        version,
        identifier.encode("utf-8"),
        content_hash.encode("ascii"),
    )


def _validate_identity(value: object, context: str) -> dict[str, Any]:
    identity = _object(value, context)
    _exact_keys(identity, _IDENTITY_FIELDS, context)
    format_name = _non_empty_string(identity.get("format"), f"{context}.format")
    if format_name not in _IDENTITY_FIELD_BY_FORMAT:
        raise PhaseReportV3Error(f"{context}.format is unsupported")
    version = identity.get("format_version")
    if type(version) is not int or version != 1:
        raise PhaseReportV3Error(f"{context}.format_version must be 1")
    identifier = identity.get("id")
    if format_name == "world-forge.game_runtime_bundle":
        if (
            not isinstance(identifier, str)
            or _GAME_RUNTIME_BUNDLE_ID_RE.fullmatch(identifier) is None
        ):
            raise PhaseReportV3Error(f"{context}.id must be the canonical game runtime bundle ID")
    else:
        _identifier(identifier, f"{context}.id")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    return identity


def validate_artifact_identity(
    value: object,
    *,
    context: str = "artifact identity",
) -> dict[str, Any]:
    """Validate one exact, supported v1 artifact identity without coercion."""

    try:
        return copy.deepcopy(_validate_identity(value, context))
    except PhaseReportV3Error:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        raise PhaseReportV3Error(str(exc)) from exc


def _project_documents(project: LoadedCreationProject) -> tuple[Mapping[str, Any], ...]:
    return (
        project.project,
        project.profile,
        project.manifest,
        *project.world_modules,
        *project.activity_modules,
        *project.narrative_modules,
        *project.system_modules,
        *project.logic_modules,
    )


def _dependency_identity(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PhaseReportV3Error(f"{context} must be an artifact identity")
    normalized = {
        "format": value.get("format"),
        "format_version": value.get("format_version"),
        "id": value.get("id"),
        "content_hash": value.get("content_hash"),
    }
    try:
        return copy.deepcopy(_validate_identity(normalized, context))
    except CreationContractError as exc:
        raise PhaseReportV3Error(str(exc)) from exc


def _dependency_array(value: object, context: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise PhaseReportV3Error(f"{context} must be an array")
    if not all(isinstance(item, Mapping) for item in value):
        raise PhaseReportV3Error(f"{context} must contain objects")
    return list(value)


def _receipt_parent_identity(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PhaseReportV3Error(f"{context} must be a receipt parent")
    if "receipt_id" not in value:
        return _dependency_identity(value, context)
    return _dependency_identity(
        {
            "format": "world-forge.asset_production_receipt",
            "format_version": 1,
            "id": value.get("receipt_id"),
            "content_hash": value.get("content_hash"),
        },
        context,
    )


_DIRECT_DEPENDENCY_FIELDS = {
    "world-forge.game_analysis": ("gamepack",),
    "world-forge.mechanic_capability_ledger": ("gamepack",),
    "world-forge.asset_target": ("asset_subject", "gamepack"),
    "world-forge.asset_style": ("asset_subject", "target", "gamepack"),
    "world-forge.asset_inventory": (
        "asset_subject",
        "target",
        "style",
        "gamepack",
    ),
    "world-forge.asset_spec": ("asset_subject", "target", "style", "inventory"),
    "world-forge.asset_production_request": (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
    ),
    "world-forge.asset_production_receipt": (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "request",
    ),
    "world-forge.asset_selection": (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "request",
        "receipt",
    ),
    "world-forge.asset_provenance_record": (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "request",
        "receipt",
        "selection",
    ),
    "world-forge.asset_license_record": (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "request",
        "receipt",
        "selection",
        "provenance",
    ),
    "world-forge.asset_processing_recipe": (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "request",
        "receipt",
        "selection",
        "provenance",
    ),
    "world-forge.asset_processing_receipt": (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "request",
        "receipt",
        "selection",
        "provenance",
        "recipe",
    ),
    "world-forge.asset_qa_report": (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "request",
        "receipt",
        "selection",
        "provenance",
        "recipe",
        "processing_receipt",
    ),
    "world-forge.asset_manifest": (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
    ),
    "world-forge.assetpack": (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "asset_inventory",
        "release_ready_manifest",
    ),
    "world-forge.runtime_adapter_registry": ("runtime_snapshot",),
    "world-forge.runtime_evidence": ("composition",),
    "world-forge.game_runtime_composition": (
        "gamepack",
        "asset_inventory",
        "assetpack",
        "registry",
        "runtime_snapshot",
    ),
    "world-forge.game_package_extraction": ("package",),
}


def artifact_dependency_identities(
    document: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Return the closed, exact typed dependencies declared by one artifact."""

    format_name = document.get("format")
    identities: list[dict[str, Any]] = []

    def add(value: object, context: str) -> None:
        identities.append(_dependency_identity(value, context))

    for field in _DIRECT_DEPENDENCY_FIELDS.get(str(format_name), ()):
        add(document.get(field), f"{format_name}.{field}")

    if format_name == "world-forge.asset_qa_review_receipt":
        lineage = document.get("lineage")
        if not isinstance(lineage, Mapping):
            raise PhaseReportV3Error("asset QA review receipt.lineage must be an object")
        for field in (
            "gamepack",
            "asset_subject",
            "target",
            "style",
            "inventory",
            "specification",
            "request",
            "receipt",
            "selection",
            "provenance",
            "recipe",
            "processing_receipt",
            "qa_report",
        ):
            add(lineage.get(field), f"asset QA review receipt.lineage.{field}")
    elif format_name == "world-forge.asset_release_authority":
        add(document.get("candidate_manifest"), "asset release authority.candidate_manifest")
        add(document.get("candidate_assetpack"), "asset release authority.candidate_assetpack")
        for index, identity in enumerate(
            _dependency_array(document.get("qa_reviews"), "asset release authority.qa_reviews")
        ):
            add(identity, f"asset release authority.qa_reviews/{index}")

    if format_name == "world-forge.gamepack":
        source = document.get("source")
        if not isinstance(source, Mapping):
            raise PhaseReportV3Error("world-forge.gamepack.source must be an object")
        for field in ("project", "profile", "source_manifest"):
            add(source.get(field), f"world-forge.gamepack.source.{field}")
        for index, identity in enumerate(
            _dependency_array(
                source.get("logic_modules"),
                "world-forge.gamepack.source.logic_modules",
            )
        ):
            add(identity, f"world-forge.gamepack.source.logic_modules/{index}")
        modules = document.get("modules")
        if not isinstance(modules, Mapping):
            raise PhaseReportV3Error("world-forge.gamepack.modules must be an object")
        for collection in ("world", "activities", "narrative", "systems"):
            for index, module in enumerate(
                _dependency_array(
                    modules.get(collection),
                    f"world-forge.gamepack.modules.{collection}",
                )
            ):
                add(
                    module.get("source"),
                    f"world-forge.gamepack.modules.{collection}/{index}.source",
                )
    elif format_name == "world-forge.asset_subject":
        add(document.get("subject"), "world-forge.asset_subject.subject")
    elif format_name == "world-forge.asset_production_receipt":
        for index, identity in enumerate(
            _dependency_array(
                document.get("lineage_parents"),
                "world-forge.asset_production_receipt.lineage_parents",
            )
        ):
            identities.append(
                _receipt_parent_identity(
                    identity,
                    f"world-forge.asset_production_receipt.lineage_parents/{index}",
                )
            )
    elif format_name == "world-forge.asset_selection":
        lineage = document.get("receipt_lineage")
        if not isinstance(lineage, Mapping):
            raise PhaseReportV3Error(
                "world-forge.asset_selection.receipt_lineage must be an object"
            )
        for index, closure in enumerate(
            _dependency_array(
                lineage.get("closures"),
                "world-forge.asset_selection.receipt_lineage.closures",
            )
        ):
            add(
                closure.get("root"),
                f"world-forge.asset_selection.receipt_lineage.closures/{index}.root",
            )
            for parent_index, parent in enumerate(
                _dependency_array(
                    closure.get("parents"),
                    (f"world-forge.asset_selection.receipt_lineage.closures/{index}.parents"),
                )
            ):
                add(
                    parent,
                    (
                        "world-forge.asset_selection.receipt_lineage."
                        f"closures/{index}.parents/{parent_index}"
                    ),
                )
        for index, candidate in enumerate(
            _dependency_array(
                document.get("rejected_candidates"),
                "world-forge.asset_selection.rejected_candidates",
            )
        ):
            add(
                candidate.get("receipt"),
                f"world-forge.asset_selection.rejected_candidates/{index}.receipt",
            )
    elif format_name == "world-forge.asset_processing_recipe":
        for index, identity in enumerate(
            _dependency_array(
                document.get("licenses"),
                "world-forge.asset_processing_recipe.licenses",
            )
        ):
            add(
                identity.get("license_record"),
                (f"world-forge.asset_processing_recipe.licenses/{index}.license_record"),
            )
    elif format_name in {"world-forge.asset_manifest", "world-forge.assetpack"}:
        for asset_index, asset in enumerate(
            _dependency_array(document.get("assets"), f"{format_name}.assets")
        ):
            fields = (
                "specification",
                "request",
                "receipt",
                "selection",
                "provenance",
                "processing_recipe",
                "processing_receipt",
                "qa_report",
            )
            for field in fields:
                value = asset.get(field)
                if value is not None:
                    add(value, f"{format_name}.assets/{asset_index}.{field}")
            for license_index, identity in enumerate(
                _dependency_array(
                    asset.get("licenses"),
                    f"{format_name}.assets/{asset_index}.licenses",
                )
            ):
                add(
                    identity,
                    f"{format_name}.assets/{asset_index}.licenses/{license_index}",
                )
            if format_name == "world-forge.assetpack":
                for output_index, output in enumerate(
                    _dependency_array(
                        asset.get("outputs"),
                        f"{format_name}.assets/{asset_index}.outputs",
                    )
                ):
                    add(
                        output.get("license_record"),
                        (
                            f"{format_name}.assets/{asset_index}."
                            f"outputs/{output_index}.license_record"
                        ),
                    )
    elif format_name == "world-forge.runtime_support_report":
        add(document.get("gamepack"), "world-forge.runtime_support_report.gamepack")
        add(document.get("composition"), "world-forge.runtime_support_report.composition")
        for index, identity in enumerate(
            _dependency_array(
                document.get("evidence"),
                "world-forge.runtime_support_report.evidence",
            )
        ):
            add(identity, f"world-forge.runtime_support_report.evidence/{index}")
    elif format_name == "world-forge.game_execution_script":
        bindings = document.get("bindings")
        if not isinstance(bindings, Mapping):
            raise PhaseReportV3Error("world-forge.game_execution_script.bindings must be an object")
        for field in (
            "runtime_bundle",
            "gamepack",
            "runtime_composition",
            "runtime_snapshot",
        ):
            add(
                bindings.get(field),
                f"world-forge.game_execution_script.bindings.{field}",
            )
    elif format_name == "world-forge.runtime_support_authority":
        for field in (
            "gamepack",
            "asset_inventory",
            "composition",
            "assetpack",
            "asset_release_authority",
            "registry",
            "runtime_snapshot",
            "runtime_support_report",
        ):
            add(document.get(field), f"world-forge.runtime_support_authority.{field}")
        for index, identity in enumerate(
            _dependency_array(
                document.get("runtime_evidence"),
                "world-forge.runtime_support_authority.runtime_evidence",
            )
        ):
            add(
                identity,
                f"world-forge.runtime_support_authority.runtime_evidence/{index}",
            )
        for index, headless in enumerate(
            _dependency_array(
                document.get("headless_evidence"),
                "world-forge.runtime_support_authority.headless_evidence",
            )
        ):
            if not isinstance(headless, Mapping):
                raise PhaseReportV3Error(
                    "world-forge.runtime_support_authority.headless_evidence entries "
                    "must be objects"
                )
            add(
                headless.get("execution_script"),
                (
                    "world-forge.runtime_support_authority."
                    f"headless_evidence/{index}.execution_script"
                ),
            )
    elif format_name == "world-forge.game_runtime_bundle":
        contracts = document.get("contracts")
        if not isinstance(contracts, Mapping):
            raise PhaseReportV3Error("world-forge.game_runtime_bundle.contracts must be an object")
        for field in (
            "gamepack",
            "runtime_snapshot",
            "runtime_adapter_registry",
            "runtime_composition",
            "runtime_support_report",
        ):
            add(
                contracts.get(field),
                f"world-forge.game_runtime_bundle.contracts.{field}",
            )
        assetpack = document.get("assetpack")
        if not isinstance(assetpack, Mapping):
            raise PhaseReportV3Error("world-forge.game_runtime_bundle.assetpack must be an object")
        add(assetpack.get("manifest"), "world-forge.game_runtime_bundle.assetpack.manifest")
    elif format_name == "world-forge.game_materialization_bundle":
        runtime_bundle = document.get("runtime_bundle")
        if not isinstance(runtime_bundle, Mapping):
            raise PhaseReportV3Error(
                "world-forge.game_materialization_bundle.runtime_bundle must be an object"
            )
        add(
            runtime_bundle.get("manifest"),
            "world-forge.game_materialization_bundle.runtime_bundle.manifest",
        )
    elif format_name == "world-forge.standalone_game":
        add(
            document.get("materialization_bundle"),
            "world-forge.standalone_game.materialization_bundle",
        )
    elif format_name == "world-forge.creation_readiness":
        for index, identity in enumerate(
            _dependency_array(
                document.get("evidence"),
                "world-forge.creation_readiness.evidence",
            )
        ):
            add(identity, f"world-forge.creation_readiness.evidence/{index}")
    elif format_name == "world-forge.creation_handoff":
        add(
            document.get("workflow_status"),
            "world-forge.creation_handoff.workflow_status",
        )
        add(document.get("readiness"), "world-forge.creation_handoff.readiness")
        for index, identity in enumerate(
            _dependency_array(
                document.get("artifacts"),
                "world-forge.creation_handoff.artifacts",
            )
        ):
            add(identity, f"world-forge.creation_handoff.artifacts/{index}")

    unique = {_identity_key(identity): identity for identity in identities}
    return tuple(sorted(unique.values(), key=_identity_sort_key))


def _resolve_artifact(
    value: object,
    *,
    registry: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
    context: str,
) -> Mapping[str, Any]:
    identity = _dependency_identity(value, context)
    document = registry.get(_identity_key(identity))
    if document is None:
        raise PhaseReportV3Error(f"{context} does not resolve to an exact registered artifact")
    return document


def _require_exact_dependency(
    document: Mapping[str, Any],
    field: str,
    dependency: Mapping[str, Any],
    *,
    context: str,
) -> None:
    actual = _dependency_identity(document.get(field), f"{context}.{field}")
    expected = document_identity(dependency)
    if actual != expected:
        raise PhaseReportV3Error(f"{context}.{field} crosses an exact artifact lineage")


_ASSET_LINEAGE_FIELDS = (
    "gamepack",
    "asset_subject",
    "target",
    "style",
    "inventory",
    "specification",
    "request",
    "receipt",
    "selection",
    "provenance",
    "recipe",
    "processing_receipt",
)


def _asset_lineage_projection(
    document: Mapping[str, Any],
    *,
    context: str,
) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for field in _ASSET_LINEAGE_FIELDS:
        source_field = (
            "processing_recipe"
            if field == "recipe" and "processing_recipe" in document and "recipe" not in document
            else field
        )
        value = document.get(source_field)
        if isinstance(value, Mapping) and {
            "format",
            "format_version",
            "id",
            "content_hash",
        }.issubset(value):
            projection[field] = _dependency_identity(
                value,
                f"{context}.{source_field}",
            )
    if "asset" in document:
        asset = document.get("asset")
        if not isinstance(asset, Mapping):
            raise PhaseReportV3Error(f"{context}.asset must be an object")
        projection["asset"] = copy.deepcopy(dict(asset))
    return projection


def _require_shared_asset_lineage(
    document: Mapping[str, Any],
    predecessor: Mapping[str, Any],
    *,
    context: str,
) -> None:
    actual = _asset_lineage_projection(document, context=context)
    expected = _asset_lineage_projection(
        predecessor,
        context=f"{context}.predecessor",
    )
    for field in sorted(set(actual) & set(expected)):
        if actual[field] != expected[field]:
            raise PhaseReportV3Error(f"{context}.{field} crosses exact predecessor lineage")


def _resolve_asset_dependencies(
    document: Mapping[str, Any],
    *,
    registry: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
    context: str,
) -> dict[str, Mapping[str, Any]]:
    format_name = str(document.get("format"))
    dependencies: dict[str, Mapping[str, Any]] = {}
    for field in _DIRECT_DEPENDENCY_FIELDS.get(format_name, ()):
        dependency = _resolve_artifact(
            document.get(field),
            registry=registry,
            context=f"{context}.{field}",
        )
        _require_exact_dependency(
            document,
            field,
            dependency,
            context=context,
        )
        _require_shared_asset_lineage(
            document,
            dependency,
            context=f"{context}.{field}",
        )
        dependencies[field] = dependency
    return dependencies


def _receipt_parent_documents(
    receipt: Mapping[str, Any],
    *,
    registry: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
    context: str,
) -> tuple[Mapping[str, Any], ...]:
    parents: list[Mapping[str, Any]] = []
    for index, parent in enumerate(
        _dependency_array(receipt.get("lineage_parents"), f"{context}.lineage_parents")
    ):
        parent_identity = _receipt_parent_identity(
            parent,
            f"{context}.lineage_parents/{index}",
        )
        resolved = _resolve_artifact(
            parent_identity,
            registry=registry,
            context=f"{context}.lineage_parents/{index}",
        )
        _require_shared_asset_lineage(
            receipt,
            resolved,
            context=f"{context}.lineage_parents/{index}",
        )
        if resolved.get("status") != "completed":
            raise PhaseReportV3Error(
                f"{context}.lineage_parents/{index} is not a completed predecessor"
            )
        parents.append(resolved)
    return tuple(parents)


def _transitive_receipt_parent_identities(
    receipt: Mapping[str, Any],
    *,
    registry: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
    context: str,
) -> tuple[dict[str, Any], ...]:
    pending = list(
        _receipt_parent_documents(
            receipt,
            registry=registry,
            context=context,
        )
    )
    closure: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    while pending:
        parent = pending.pop()
        identity = document_identity(parent)
        key = _identity_key(identity)
        if key in closure:
            continue
        closure[key] = identity
        pending.extend(
            _receipt_parent_documents(
                parent,
                registry=registry,
                context=f"{context}.parent[{identity['id']}]",
            )
        )
    return tuple(sorted(closure.values(), key=_identity_sort_key))


def _validate_selection_receipt_lineage(
    selection: Mapping[str, Any],
    *,
    receipt: Mapping[str, Any],
    registry: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
    context: str,
) -> None:
    if receipt.get("status") != "completed":
        raise PhaseReportV3Error(f"{context}.receipt is not a completed predecessor")
    expected_selected = [
        {
            key: output[key]
            for key in (
                "candidate_artifact_id",
                "role",
                "media_type",
                "size_bytes",
                "sha256",
            )
        }
        for output in receipt.get("outputs", ())
    ]
    if selection.get("selected_outputs") != expected_selected:
        raise PhaseReportV3Error(f"{context}.selected_outputs crosses exact receipt predecessor")

    roots = {_identity_key(document_identity(receipt)): receipt}
    selected_candidate_ids = {
        str(output["candidate_artifact_id"]).casefold() for output in expected_selected
    }
    for index, rejected in enumerate(
        _dependency_array(
            selection.get("rejected_candidates"),
            f"{context}.rejected_candidates",
        )
    ):
        rejected_receipt = _resolve_artifact(
            rejected.get("receipt"),
            registry=registry,
            context=f"{context}.rejected_candidates/{index}.receipt",
        )
        _require_shared_asset_lineage(
            selection,
            rejected_receipt,
            context=f"{context}.rejected_candidates/{index}.receipt",
        )
        if rejected_receipt.get("status") != "completed":
            raise PhaseReportV3Error(
                f"{context}.rejected_candidates/{index}.receipt is not completed"
            )
        candidate_id = str(rejected.get("candidate_artifact_id"))
        if candidate_id.casefold() in selected_candidate_ids or not any(
            output.get("candidate_artifact_id") == candidate_id
            for output in rejected_receipt.get("outputs", ())
        ):
            raise PhaseReportV3Error(
                f"{context}.rejected_candidates/{index} crosses its receipt predecessor"
            )
        roots[_identity_key(document_identity(rejected_receipt))] = rejected_receipt

    receipt_lineage = selection.get("receipt_lineage")
    if not isinstance(receipt_lineage, Mapping):
        raise PhaseReportV3Error(f"{context}.receipt_lineage must be an object")
    closures = _dependency_array(
        receipt_lineage.get("closures"),
        f"{context}.receipt_lineage.closures",
    )
    actual_roots = {
        _identity_key(
            _dependency_identity(
                closure.get("root"),
                f"{context}.receipt_lineage.closures/{index}.root",
            )
        )
        for index, closure in enumerate(closures)
    }
    if actual_roots != set(roots):
        raise PhaseReportV3Error(
            f"{context}.receipt_lineage roots do not exactly cover predecessors"
        )
    for index, closure in enumerate(closures):
        root_identity = _dependency_identity(
            closure.get("root"),
            f"{context}.receipt_lineage.closures/{index}.root",
        )
        root = roots[_identity_key(root_identity)]
        expected_parents = {
            _identity_key(identity)
            for identity in _transitive_receipt_parent_identities(
                root,
                registry=registry,
                context=f"{context}.receipt_lineage.closures/{index}",
            )
        }
        declared_parents = {
            _identity_key(
                _dependency_identity(
                    parent,
                    (f"{context}.receipt_lineage.closures/{index}.parents/{parent_index}"),
                )
            )
            for parent_index, parent in enumerate(
                _dependency_array(
                    closure.get("parents"),
                    f"{context}.receipt_lineage.closures/{index}.parents",
                )
            )
        }
        if declared_parents != expected_parents:
            raise PhaseReportV3Error(
                f"{context}.receipt_lineage.closures/{index}.parents "
                "do not exactly cover predecessor lineage"
            )


def _validate_recipe_license_lineage(
    recipe: Mapping[str, Any],
    *,
    selection: Mapping[str, Any],
    registry: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
    context: str,
) -> None:
    expected = {
        (
            str(output["candidate_artifact_id"]),
            str(output["role"]),
        )
        for output in selection.get("selected_outputs", ())
    }
    actual: set[tuple[str, str]] = set()
    for index, binding in enumerate(
        _dependency_array(recipe.get("licenses"), f"{context}.licenses")
    ):
        license_record = _resolve_artifact(
            binding.get("license_record"),
            registry=registry,
            context=f"{context}.licenses/{index}.license_record",
        )
        _require_shared_asset_lineage(
            recipe,
            license_record,
            context=f"{context}.licenses/{index}.license_record",
        )
        candidate = license_record.get("candidate")
        if not isinstance(candidate, Mapping):
            raise PhaseReportV3Error(f"{context}.licenses/{index} has no candidate predecessor")
        binding_key = (
            str(binding.get("candidate_artifact_id")),
            str(binding.get("role")),
        )
        if binding_key != (
            str(candidate.get("candidate_artifact_id")),
            str(candidate.get("role")),
        ):
            raise PhaseReportV3Error(
                f"{context}.licenses/{index} crosses exact license predecessor"
            )
        actual.add(binding_key)
    if actual != expected:
        raise PhaseReportV3Error(f"{context}.licenses do not exactly cover selected predecessors")


def _manifest_output_projection(output: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: output.get(field)
        for field in (
            "role",
            "media_type",
            "runtime_path",
            "locator",
            "sha256",
            "size_bytes",
        )
    }


def _validate_manifest_asset_lineage(
    manifest: Mapping[str, Any],
    asset: Mapping[str, Any],
    *,
    asset_index: int,
    registry: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
) -> None:
    context = f"asset manifest.assets/{asset_index}"
    dependencies: dict[str, Mapping[str, Any]] = {}
    for field in (
        "specification",
        "request",
        "receipt",
        "selection",
        "provenance",
        "processing_recipe",
        "processing_receipt",
        "qa_report",
    ):
        identity = asset.get(field)
        if identity is None:
            continue
        dependency = _resolve_artifact(
            identity,
            registry=registry,
            context=f"{context}.{field}",
        )
        _require_shared_asset_lineage(
            manifest,
            dependency,
            context=f"{context}.{field}.root",
        )
        _require_shared_asset_lineage(
            asset,
            dependency,
            context=f"{context}.{field}",
        )
        if dependency.get("asset") != asset.get("asset"):
            raise PhaseReportV3Error(f"{context}.{field} crosses exact asset predecessor lineage")
        dependencies[field] = dependency

    selection = dependencies["selection"]
    expected_licenses = {
        (
            str(output["candidate_artifact_id"]),
            str(output["role"]),
        )
        for output in selection.get("selected_outputs", ())
    }
    actual_licenses: set[tuple[str, str]] = set()
    license_identities: set[tuple[str, int, str, str]] = set()
    for license_index, identity in enumerate(
        _dependency_array(asset.get("licenses"), f"{context}.licenses")
    ):
        license_record = _resolve_artifact(
            identity,
            registry=registry,
            context=f"{context}.licenses/{license_index}",
        )
        _require_shared_asset_lineage(
            manifest,
            license_record,
            context=f"{context}.licenses/{license_index}.root",
        )
        _require_shared_asset_lineage(
            asset,
            license_record,
            context=f"{context}.licenses/{license_index}",
        )
        candidate = license_record.get("candidate")
        if not isinstance(candidate, Mapping):
            raise PhaseReportV3Error(
                f"{context}.licenses/{license_index} has no candidate predecessor"
            )
        actual_licenses.add(
            (
                str(candidate.get("candidate_artifact_id")),
                str(candidate.get("role")),
            )
        )
        license_identities.add(_identity_key(document_identity(license_record)))
    if actual_licenses != expected_licenses:
        raise PhaseReportV3Error(f"{context}.licenses do not exactly cover selected predecessors")

    recipe = dependencies.get("processing_recipe")
    if recipe is not None:
        recipe_licenses = {
            _identity_key(
                _dependency_identity(
                    binding.get("license_record"),
                    f"{context}.processing_recipe.licenses/{index}",
                )
            )
            for index, binding in enumerate(
                _dependency_array(
                    recipe.get("licenses"),
                    f"{context}.processing_recipe.licenses",
                )
            )
        }
        if license_identities != recipe_licenses:
            raise PhaseReportV3Error(f"{context}.licenses do not exactly cover recipe predecessors")

    output_source = (
        dependencies["receipt"]
        if asset.get("state") == "produced"
        else dependencies.get("processing_receipt")
    )
    if output_source is None:
        raise PhaseReportV3Error(f"{context} has no exact output predecessor")
    expected_outputs = [
        _manifest_output_projection(output) for output in output_source.get("outputs", ())
    ]
    if asset.get("outputs") != expected_outputs:
        raise PhaseReportV3Error(f"{context}.outputs cross exact processing predecessor lineage")
    qa_report = dependencies.get("qa_report")
    if asset.get("state") == "release_ready" and (
        qa_report is None or qa_report.get("status") != "passed"
    ):
        raise PhaseReportV3Error(f"{context}.qa_report is not a passed release predecessor")


def _validate_asset_external_artifact(
    document: Mapping[str, Any],
    *,
    registry: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    from worldforge.generic_asset_processing import (
        validate_asset_manifest_document,
        validate_asset_processing_receipt_document,
        validate_asset_processing_recipe_document,
        validate_asset_qa_report_document,
        validate_asset_qa_semantic_coherence,
        validate_processing_receipt_recipe_coherence,
    )
    from worldforge.generic_asset_production import (
        validate_asset_license_record_document,
        validate_asset_production_receipt_document,
        validate_asset_production_request,
        validate_asset_production_request_document,
        validate_asset_provenance_record_document,
        validate_asset_selection_document,
    )
    from worldforge.generic_assetpack import (
        validate_generic_assetpack_asset_semantics,
        validate_generic_assetpack_document,
    )
    from worldforge.generic_assets import (
        validate_asset_inventory,
        validate_asset_specification,
        validate_asset_style,
        validate_asset_subject,
        validate_asset_target,
    )

    format_name = str(document.get("format"))

    def resolve(value: object, context: str) -> Mapping[str, Any]:
        return _resolve_artifact(value, registry=registry, context=context)

    if format_name == "world-forge.asset_subject":
        gamepack = resolve(document.get("subject"), "asset subject.subject")
        return validate_asset_subject(document, gamepack=gamepack)
    if format_name == "world-forge.asset_target":
        gamepack = resolve(document.get("gamepack"), "asset target.gamepack")
        subject = resolve(document.get("asset_subject"), "asset target.asset_subject")
        return validate_asset_target(document, gamepack=gamepack, subject=subject)
    if format_name == "world-forge.asset_style":
        gamepack = resolve(document.get("gamepack"), "asset style.gamepack")
        subject = resolve(document.get("asset_subject"), "asset style.asset_subject")
        target = resolve(document.get("target"), "asset style.target")
        return validate_asset_style(
            document,
            gamepack=gamepack,
            subject=subject,
            target=target,
        )
    if format_name == "world-forge.asset_inventory":
        gamepack = resolve(document.get("gamepack"), "asset inventory.gamepack")
        subject = resolve(document.get("asset_subject"), "asset inventory.asset_subject")
        target = resolve(document.get("target"), "asset inventory.target")
        style = resolve(document.get("style"), "asset inventory.style")
        return validate_asset_inventory(
            document,
            gamepack=gamepack,
            subject=subject,
            target=target,
            style=style,
        )
    if format_name == "world-forge.asset_spec":
        subject = resolve(document.get("asset_subject"), "asset specification.asset_subject")
        target = resolve(document.get("target"), "asset specification.target")
        style = resolve(document.get("style"), "asset specification.style")
        inventory = resolve(document.get("inventory"), "asset specification.inventory")
        gamepack = resolve(inventory.get("gamepack"), "asset specification.gamepack")
        return validate_asset_specification(
            document,
            gamepack=gamepack,
            inventory=inventory,
            subject=subject,
            target=target,
            style=style,
        )

    structure_validators = {
        "world-forge.asset_production_request": validate_asset_production_request_document,
        "world-forge.asset_production_receipt": validate_asset_production_receipt_document,
        "world-forge.asset_selection": validate_asset_selection_document,
        "world-forge.asset_provenance_record": validate_asset_provenance_record_document,
        "world-forge.asset_license_record": validate_asset_license_record_document,
        "world-forge.asset_processing_recipe": validate_asset_processing_recipe_document,
        "world-forge.asset_processing_receipt": validate_asset_processing_receipt_document,
        "world-forge.asset_qa_report": validate_asset_qa_report_document,
    }
    if format_name in structure_validators:
        checked = structure_validators[format_name](document)
        dependencies = _resolve_asset_dependencies(
            checked,
            registry=registry,
            context=format_name,
        )
        gamepack = dependencies["gamepack"]
        subject = dependencies["asset_subject"]
        target = dependencies["target"]
        style = dependencies["style"]
        inventory = dependencies["inventory"]
        specification = dependencies["specification"]
        request_value = (
            checked
            if format_name == "world-forge.asset_production_request"
            else dependencies["request"]
        )
        request = validate_asset_production_request(
            request_value,
            gamepack=gamepack,
            subject=subject,
            target=target,
            style=style,
            inventory=inventory,
            specification=specification,
        )
        expected_dependencies = {
            "gamepack": gamepack,
            "asset_subject": subject,
            "target": target,
            "style": style,
            "inventory": inventory,
            "specification": specification,
            "request": request,
        }
        for field, dependency in expected_dependencies.items():
            if field in checked:
                _require_exact_dependency(
                    checked,
                    field,
                    dependency,
                    context=format_name,
                )
        if checked.get("asset") != specification.get("asset"):
            raise PhaseReportV3Error(f"{format_name}.asset crosses the exact specification")
        if format_name == "world-forge.asset_production_receipt":
            _receipt_parent_documents(
                checked,
                registry=registry,
                context=format_name,
            )
        elif format_name == "world-forge.asset_selection":
            _validate_selection_receipt_lineage(
                checked,
                receipt=dependencies["receipt"],
                registry=registry,
                context=format_name,
            )
        elif format_name == "world-forge.asset_license_record":
            provenance = dependencies["provenance"]
            if checked.get("candidate") not in provenance.get("candidates", ()):
                raise PhaseReportV3Error(
                    f"{format_name}.candidate crosses exact provenance predecessor"
                )
        elif format_name == "world-forge.asset_processing_recipe":
            _validate_recipe_license_lineage(
                checked,
                selection=dependencies["selection"],
                registry=registry,
                context=format_name,
            )
        elif format_name == "world-forge.asset_processing_receipt":
            validate_processing_receipt_recipe_coherence(
                checked,
                dependencies["recipe"],
            )
        elif format_name == "world-forge.asset_qa_report":
            validate_asset_qa_semantic_coherence(
                checked,
                processing_receipt=dependencies["processing_receipt"],
                recipe=dependencies["recipe"],
                specification=specification,
            )
        return checked

    if format_name == "world-forge.asset_manifest":
        checked = validate_asset_manifest_document(document)
        root_dependencies = _resolve_asset_dependencies(
            checked,
            registry=registry,
            context="asset manifest",
        )
        validate_asset_inventory(
            root_dependencies["inventory"],
            gamepack=root_dependencies["gamepack"],
            subject=root_dependencies["asset_subject"],
            target=root_dependencies["target"],
            style=root_dependencies["style"],
        )
        for asset_index, asset in enumerate(checked["assets"]):
            _validate_manifest_asset_lineage(
                checked,
                asset,
                asset_index=asset_index,
                registry=registry,
            )
        return checked

    if format_name == "world-forge.assetpack":
        checked = validate_generic_assetpack_document(document)
        manifest = resolve(
            checked.get("release_ready_manifest"),
            "generic assetpack.release_ready_manifest",
        )
        root_pairs = (
            ("gamepack", "gamepack"),
            ("asset_subject", "asset_subject"),
            ("target", "target"),
            ("style", "style"),
            ("asset_inventory", "inventory"),
        )
        for assetpack_field, manifest_field in root_pairs:
            if _dependency_identity(
                checked.get(assetpack_field),
                f"generic assetpack.{assetpack_field}",
            ) != _dependency_identity(
                manifest.get(manifest_field),
                f"generic asset manifest.{manifest_field}",
            ):
                raise PhaseReportV3Error(
                    f"generic assetpack.{assetpack_field} crosses release manifest lineage"
                )
        manifest_assets = {
            str(asset["asset"]["asset_id"]): asset for asset in manifest.get("assets", ())
        }
        if set(manifest_assets) != {str(asset["asset"]["asset_id"]) for asset in checked["assets"]}:
            raise PhaseReportV3Error(
                "generic assetpack assets do not exactly cover the release manifest"
            )
        for index, asset in enumerate(checked["assets"]):
            manifest_asset = manifest_assets[str(asset["asset"]["asset_id"])]
            if asset["asset"] != manifest_asset["asset"]:
                raise PhaseReportV3Error(
                    f"generic assetpack.assets/{index}.asset crosses release manifest lineage"
                )
            for field in (
                "specification",
                "request",
                "receipt",
                "selection",
                "provenance",
                "processing_recipe",
                "processing_receipt",
                "qa_report",
                "licenses",
            ):
                if asset[field] != manifest_asset[field]:
                    raise PhaseReportV3Error(
                        f"generic assetpack.assets/{index}.{field} crosses release manifest lineage"
                    )
            validate_generic_assetpack_asset_semantics(
                asset,
                manifest_entry=manifest_asset,
                specification=resolve(
                    manifest_asset["specification"],
                    f"generic assetpack.assets/{index}.specification",
                ),
                recipe=resolve(
                    manifest_asset["processing_recipe"],
                    f"generic assetpack.assets/{index}.processing_recipe",
                ),
                processing_receipt=resolve(
                    manifest_asset["processing_receipt"],
                    f"generic assetpack.assets/{index}.processing_receipt",
                ),
                qa_report=resolve(
                    manifest_asset["qa_report"],
                    f"generic assetpack.assets/{index}.qa_report",
                ),
                license_records=tuple(
                    resolve(
                        identity,
                        f"generic assetpack.assets/{index}.licenses/{license_index}",
                    )
                    for license_index, identity in enumerate(manifest_asset["licenses"])
                ),
            )
        return checked

    raise PhaseReportV3Error(f"asset validator is unavailable: {format_name!r}")


def _validate_external_artifact(
    document: Mapping[str, Any],
    *,
    project: LoadedCreationProject,
    registry: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    format_name = document.get("format")
    version = document.get("format_version")
    if (
        format_name not in _EXTERNAL_ARTIFACT_FORMATS
        or version != 1
        or isinstance(
            version,
            bool,
        )
    ):
        raise PhaseReportV3Error(
            f"artifact format/version is not registered: {format_name!r} v{version!r}"
        )
    try:
        if format_name == "world-forge.gamepack":
            from worldforge.gamepack import validate_gamepack

            return validate_gamepack(document, source_project=project)
        if format_name == "world-forge.game_analysis":
            from worldforge.game_analysis import validate_game_analysis

            gamepack = _resolve_artifact(
                document.get("gamepack"),
                registry=registry,
                context="game analysis.gamepack",
            )
            return dict(validate_game_analysis(document, gamepack))
        if format_name == "world-forge.mechanic_capability_ledger":
            from worldforge.gamepack import validate_capability_ledger_document

            gamepack = _resolve_artifact(
                document.get("gamepack"),
                registry=registry,
                context="mechanic capability ledger.gamepack",
            )
            return validate_capability_ledger_document(document, gamepack=gamepack)
        if format_name in {
            "world-forge.asset_qa_review_receipt",
            "world-forge.asset_release_authority",
        }:
            from worldforge.generic_asset_authority import (
                validate_asset_qa_review_receipt_document,
                validate_asset_release_authority_document,
            )

            validator = (
                validate_asset_qa_review_receipt_document
                if format_name == "world-forge.asset_qa_review_receipt"
                else validate_asset_release_authority_document
            )
            checked = validator(document)
            for index, identity in enumerate(artifact_dependency_identities(checked)):
                _resolve_artifact(
                    identity,
                    registry=registry,
                    context=f"{format_name}.dependency/{index}",
                )
            return checked
        if format_name in _ASSET_FORMATS:
            return _validate_asset_external_artifact(document, registry=registry)
        if format_name in {
            "world-forge.runtime_adapter",
            "world-forge.runtime_adapter_registry",
            "world-forge.game_runtime_snapshot",
            "world-forge.game_runtime_composition",
            "world-forge.runtime_evidence",
            "world-forge.runtime_support_report",
        }:
            from worldforge.generic_runtime import (
                validate_game_runtime_composition_document,
                validate_runtime_adapter_document,
                validate_runtime_adapter_registry_document,
                validate_runtime_evidence_document,
                validate_runtime_snapshot_document,
                validate_runtime_support_report,
            )

            if format_name == "world-forge.runtime_adapter":
                return validate_runtime_adapter_document(document)
            if format_name == "world-forge.game_runtime_snapshot":
                return validate_runtime_snapshot_document(document)
            if format_name == "world-forge.runtime_adapter_registry":
                snapshot = _resolve_artifact(
                    document.get("runtime_snapshot"),
                    registry=registry,
                    context="runtime adapter registry.runtime_snapshot",
                )
                return validate_runtime_adapter_registry_document(
                    document,
                    snapshot=snapshot,
                )
            if format_name == "world-forge.game_runtime_composition":
                return validate_game_runtime_composition_document(document)
            if format_name == "world-forge.runtime_evidence":
                composition = _resolve_artifact(
                    document.get("composition"),
                    registry=registry,
                    context="runtime evidence.composition",
                )
                return validate_runtime_evidence_document(
                    document,
                    composition=composition,
                )
            composition = _resolve_artifact(
                document.get("composition"),
                registry=registry,
                context="runtime support report.composition",
            )
            gamepack = _resolve_artifact(
                document.get("gamepack"),
                registry=registry,
                context="runtime support report.gamepack",
            )
            runtime_registry = _resolve_artifact(
                composition.get("registry"),
                registry=registry,
                context="runtime support report.registry",
            )
            snapshot = _resolve_artifact(
                composition.get("runtime_snapshot"),
                registry=registry,
                context="runtime support report.runtime_snapshot",
            )
            evidence = [
                _resolve_artifact(
                    {
                        "format": item.get("format"),
                        "format_version": item.get("format_version"),
                        "id": item.get("id"),
                        "content_hash": item.get("content_hash"),
                    },
                    registry=registry,
                    context=f"runtime support report.evidence/{index}",
                )
                for index, item in enumerate(document.get("evidence", ()))
            ]
            return validate_runtime_support_report(
                document,
                composition=composition,
                gamepack=gamepack,
                registry=runtime_registry,
                snapshot=snapshot,
                evidence=evidence,
            )
        if format_name == "world-forge.game_execution_script":
            from gamepack_runtime.headless import serialize_game_execution_script

            serialize_game_execution_script(document)
            return copy.deepcopy(dict(document))
        if format_name == "world-forge.runtime_support_authority":
            from worldforge.runtime_support_authority import (
                validate_runtime_support_authority_document,
            )

            checked = validate_runtime_support_authority_document(document)
            for index, identity in enumerate(artifact_dependency_identities(checked)):
                _resolve_artifact(
                    identity,
                    registry=registry,
                    context=f"runtime support authority.dependency/{index}",
                )
            return checked
        if format_name == "world-forge.game_package":
            from gamepack_runtime.game_package import validate_game_package_document

            return validate_game_package_document(document)
        if format_name == "world-forge.game_package_extraction":
            from worldforge.game_package_extraction import (
                validate_game_package_extraction_evidence,
            )

            package = _resolve_artifact(
                document.get("package"),
                registry=registry,
                context="game package extraction.package",
            )
            package_identity = document.get("package")
            if not isinstance(package_identity, Mapping):
                raise PhaseReportV3Error("game package extraction.package must be an object")
            return validate_game_package_extraction_evidence(
                document,
                package_manifest=package,
                archive_sha256=package_identity.get("archive_sha256"),
                archive_size_bytes=package_identity.get("size_bytes"),
            )
        if format_name == "world-forge.game_runtime_bundle":
            from worldforge.game_runtime_bundle import validate_game_runtime_bundle_document

            return validate_game_runtime_bundle_document(document)
        if format_name == "world-forge.game_materialization_bundle":
            from worldforge.game_materialization_bundle import (
                validate_game_materialization_bundle_document,
            )

            return validate_game_materialization_bundle_document(document)
        if format_name == "world-forge.standalone_game":
            from gamepack_runtime.distribution import validate_standalone_game_document

            materialization = _resolve_artifact(
                document.get("materialization_bundle"),
                registry=registry,
                context="standalone game.materialization_bundle",
            )
            checked = validate_standalone_game_document(document)
            lineage = materialization.get("lineage")
            if not isinstance(lineage, Mapping) or checked.get("lineage") != {
                "gamepack_hash": lineage.get("gamepack_hash"),
                "assetpack_hash": lineage.get("assetpack_hash"),
                "runtime_snapshot_hash": lineage.get("runtime_snapshot_hash"),
                "runtime_composition_hash": lineage.get("composition_hash"),
                "runtime_bundle_hash": lineage.get("runtime_bundle_hash"),
            }:
                raise PhaseReportV3Error(
                    "standalone game lineage differs from its exact materialization bundle"
                )
            return checked
        if format_name == "world-forge.creation_workflow_status":
            from worldforge.creation_workflow import validate_creation_workflow_status

            return validate_creation_workflow_status(document, project)
        if format_name == "world-forge.creation_readiness":
            from worldforge.creation_readiness import validate_creation_readiness

            artifacts = [
                _resolve_artifact(
                    identity,
                    registry=registry,
                    context=f"creation readiness.evidence/{index}",
                )
                for index, identity in enumerate(document.get("evidence", ()))
            ]
            return validate_creation_readiness(
                document,
                project,
                artifacts=artifacts,
            )
        if format_name == "world-forge.creation_handoff":
            from worldforge.creation_readiness import validate_creation_handoff

            status = _resolve_artifact(
                document.get("workflow_status"),
                registry=registry,
                context="creation handoff.workflow_status",
            )
            readiness = _resolve_artifact(
                document.get("readiness"),
                registry=registry,
                context="creation handoff.readiness",
            )
            artifacts = [
                _resolve_artifact(
                    identity,
                    registry=registry,
                    context=f"creation handoff.artifacts/{index}",
                )
                for index, identity in enumerate(document.get("artifacts", ()))
            ]
            return validate_creation_handoff(
                document,
                project,
                status=status,
                readiness=readiness,
                artifacts=artifacts,
            )
    except PhaseReportV3Error:
        raise
    except (TypeError, ValueError) as exc:
        raise PhaseReportV3Error(
            f"artifact {format_name!r} failed integral validation: {exc}"
        ) from exc
    raise PhaseReportV3Error(f"artifact validator is unavailable: {format_name!r}")


def _validate_artifact_documents_uncached(
    project: LoadedCreationProject,
    artifact_registry: Sequence[Mapping[str, Any]],
    *,
    allowed_formats: frozenset[str] | None = None,
) -> tuple[dict[str, Any], ...]:
    raw_registry: dict[tuple[str, int, str, str], Mapping[str, Any]] = {
        _identity_key(document_identity(document)): document
        for document in _project_documents(project)
    }
    logical_identities = {key[:3]: key[3] for key in raw_registry}
    external_keys: list[tuple[str, int, str, str]] = []
    for document in artifact_registry:
        if not isinstance(document, Mapping):
            raise PhaseReportV3Error("artifact registry entries must be objects")
        identity = _validate_identity(document_identity(document), "artifact registry identity")
        format_name = str(identity["format"])
        if allowed_formats is not None and format_name not in allowed_formats:
            raise PhaseReportV3Error(f"artifact format is not allowed here: {format_name}")
        key = _identity_key(identity)
        if key in raw_registry:
            raise PhaseReportV3Error("artifact registry contains a duplicate identity")
        logical_key = key[:3]
        existing_hash = logical_identities.get(logical_key)
        if existing_hash is not None and existing_hash != key[3]:
            raise PhaseReportV3Error(
                "artifact registry contains a logical identity with mismatched content"
            )
        raw_registry[key] = document
        logical_identities[logical_key] = key[3]
        external_keys.append(key)

    validated_registry: dict[tuple[str, int, str, str], Mapping[str, Any]] = {
        key: copy.deepcopy(dict(document))
        for key, document in raw_registry.items()
        if key not in external_keys
    }
    visiting: set[tuple[str, int, str, str]] = set()
    visited: set[tuple[str, int, str, str]] = set(validated_registry)

    def visit(key: tuple[str, int, str, str]) -> None:
        if key in visited:
            return
        if key in visiting:
            raise PhaseReportV3Error("artifact dependency graph contains a cycle")
        document = raw_registry.get(key)
        if document is None:
            raise PhaseReportV3Error(
                "artifact dependency does not resolve to an exact registered artifact"
            )
        visiting.add(key)
        for identity in artifact_dependency_identities(document):
            dependency_key = _identity_key(identity)
            if dependency_key not in raw_registry:
                raise PhaseReportV3Error(
                    "artifact dependency does not resolve to an exact registered artifact"
                )
            visit(dependency_key)
        checked = _validate_external_artifact(
            document,
            project=project,
            registry=validated_registry,
        )
        identity = _validate_identity(
            document_identity(checked),
            "validated artifact identity",
        )
        if _identity_key(identity) != key:
            raise PhaseReportV3Error("integral artifact validation changed its identity")
        visiting.remove(key)
        visited.add(key)
        validated_registry[key] = copy.deepcopy(checked)

    for key in external_keys:
        visit(key)
    return tuple(copy.deepcopy(dict(validated_registry[key])) for key in external_keys)


def validate_artifact_documents(
    project: LoadedCreationProject,
    artifact_registry: Sequence[Mapping[str, Any]],
    *,
    allowed_formats: frozenset[str] | None = None,
) -> tuple[dict[str, Any], ...]:
    with validation_memo_scope():
        return _validate_artifact_documents_uncached(
            project,
            artifact_registry,
            allowed_formats=allowed_formats,
        )


def build_artifact_registry(
    project: LoadedCreationProject,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
) -> dict[tuple[str, int, str, str], dict[str, Any]]:
    documents = _project_documents(project)
    external = validate_artifact_documents(project, artifact_registry)
    registry: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    for document in (*documents, *external):
        identity = _validate_identity(document_identity(document), "artifact registry identity")
        key = _identity_key(identity)
        if key in registry:
            raise PhaseReportV3Error("artifact registry contains a duplicate identity")
        registry[key] = copy.deepcopy(dict(document))
    return registry


def _artifact_dependency_closure(
    registry: Mapping[tuple[str, int, str, str], Mapping[str, Any]],
    roots: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    pending = [_validate_identity(root, "artifact dependency closure root") for root in roots]
    closure: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    while pending:
        identity = pending.pop()
        key = _identity_key(identity)
        if key in closure:
            continue
        document = registry.get(key)
        if document is None:
            raise PhaseReportV3Error(
                "artifact dependency closure contains an unknown or mismatched identity"
            )
        closure[key] = copy.deepcopy(identity)
        pending.extend(artifact_dependency_identities(document))
    return tuple(sorted(closure.values(), key=_identity_sort_key))


def _validate_reviewer(value: object, context: str) -> dict[str, Any]:
    reviewer = _object(value, context)
    _exact_keys(reviewer, _REVIEWER_FIELDS, context)
    _identifier(reviewer.get("id"), f"{context}.id")
    _identifier(reviewer.get("role"), f"{context}.role")
    return reviewer


def _not_applicable_is_proven(
    phase: str,
    project: LoadedCreationProject,
    registry: Mapping[tuple[str, int, str, str], object],
) -> bool:
    profile = project.profile
    world_absent = profile["world"]["presence"] == "none" and not project.world_modules
    if phase == "p03_geography":
        return world_absent
    if phase == "p04_timeline":
        has_chronology = any(
            module["module_type"] == "chronology" for module in project.world_modules
        )
        has_temporal_system = any(
            system["system_type"] in {"schedule", "season"} for system in project.systems
        )
        return (
            not has_chronology
            and not has_temporal_system
            and profile["world"]["time_model"] == "none"
        )
    if phase == "p05_societies":
        return not any(module["module_type"] == "group" for module in project.world_modules)
    if phase == "p06_characters":
        has_character = any(
            module["module_type"] == "character" for module in project.world_modules
        )
        has_participant = any(activity["participant_ids"] for activity in project.activities)
        return (
            not has_character
            and not has_participant
            and profile["narrative"]["protagonist_model"] == "none"
        )
    if phase == "p08_world_arcs":
        return profile["narrative"]["requirement"] == "none" and not project.narrative_modules
    if phase in {"p11_art_audio", "p12_asset_specs"}:
        has_asset_artifact = any(key[0] in _ASSET_FORMATS for key in registry)
        return (
            profile["production"]["content_modes"]["assets"] == "not_applicable"
            and not profile["runtime_target"]["asset_formats"]
            and not has_asset_artifact
        )
    if phase == "p13_asset_production":
        runtime_target = profile["runtime_target"]
        has_runtime_artifact = any(key[0] in _RUNTIME_FORMATS for key in registry)
        return (
            runtime_target["requested_adapter"] is None
            and runtime_target["accepted_logic_formats"] == []
            and runtime_target["required_features"] == []
            and runtime_target["optional_features"] == []
            and runtime_target["platforms"] == []
            and runtime_target["renderer"] == "none"
            and runtime_target["input_capabilities"] == []
            and runtime_target["asset_formats"] == []
            and runtime_target["save_expected"] is False
            and runtime_target["replay_expected"] is False
            and runtime_target["packaging_target"] == "none"
            and not has_runtime_artifact
        )
    return False


def build_phase_output_evidence_v2(
    *,
    evidence_id: str,
    phase: str,
    role: str,
    subject: Mapping[str, Any],
    reviewer_id: str,
    reviewer_role: str,
    source_project: LoadedCreationProject,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    project = _validated_project(source_project, None)
    registry = build_artifact_registry(project, artifact_registry)
    try:
        _identifier(evidence_id, "phase output evidence v2.id")
        if phase not in PHASE_REPORT_V3_PHASE_IDS:
            raise PhaseReportV3Error("phase output evidence v2.phase is unsupported")
        roles = _PHASE_ROLE_FORMATS[phase]
        if role not in roles:
            raise PhaseReportV3Error(f"phase output evidence v2.role is unsupported for {phase}")
        identity = _validate_identity(subject, "phase output evidence v2.subject")
        if identity["format"] not in roles[role]:
            raise PhaseReportV3Error(
                f"phase output evidence v2.subject format is unsupported for role {role}"
            )
        if _identity_key(identity) not in registry:
            raise PhaseReportV3Error(
                "phase output evidence v2.subject is not a registered exact artifact"
            )
        reviewer = {"id": reviewer_id, "role": reviewer_role}
        _validate_reviewer(reviewer, "phase output evidence v2.reviewer")
        result = {
            "format": PHASE_OUTPUT_EVIDENCE_FORMAT,
            "format_version": PHASE_OUTPUT_EVIDENCE_VERSION,
            "id": evidence_id,
            "phase": phase,
            "role": role,
            "subject": copy.deepcopy(identity),
            "reviewer": reviewer,
            "content_hash": "",
        }
        result["content_hash"] = canonical_creation_hash(result)
        return result
    except CreationContractError as exc:
        raise PhaseReportV3Error(str(exc)) from exc


def _validate_output_evidence(
    value: object,
    *,
    phase: str,
    status: str,
    reviewer: Mapping[str, Any],
    registry: Mapping[tuple[str, int, str, str], object],
) -> dict[str, Any] | None:
    if status == "not_applicable":
        if value is not None:
            raise PhaseReportV3Error("not_applicable reports require null output_evidence")
        return None
    item = _object(value, "phase report v3.output_evidence")
    _exact_keys(item, _OUTPUT_FIELDS, "phase report v3.output_evidence")
    if (
        item.get("format") != PHASE_OUTPUT_EVIDENCE_FORMAT
        or item.get("format_version") != PHASE_OUTPUT_EVIDENCE_VERSION
        or isinstance(item.get("format_version"), bool)
    ):
        raise PhaseReportV3Error("phase report v3 output evidence version is unsupported")
    _identifier(item.get("id"), "phase report v3.output_evidence.id")
    if item.get("phase") != phase:
        raise PhaseReportV3Error("phase report v3 output evidence phase does not match")
    roles = _PHASE_ROLE_FORMATS[phase]
    role = item.get("role")
    if role not in roles:
        raise PhaseReportV3Error("phase report v3 output evidence role is unsupported")
    subject = _validate_identity(
        item.get("subject"),
        "phase report v3.output_evidence.subject",
    )
    if subject["format"] not in roles[str(role)]:
        raise PhaseReportV3Error("phase report v3 output evidence subject format is unsupported")
    if _identity_key(subject) not in registry:
        raise PhaseReportV3Error("phase report v3 output evidence subject is unknown or mismatched")
    if _validate_reviewer(item.get("reviewer"), "phase report v3.output_evidence.reviewer") != dict(
        reviewer
    ):
        raise PhaseReportV3Error("phase report v3 output evidence reviewer does not match")
    if canonical_creation_hash(item) != item.get("content_hash"):
        raise PhaseReportV3Error("phase report v3 output evidence content hash does not match")
    return subject


def validate_phase_report_v3(
    value: object,
    source_project: LoadedCreationProject,
    *,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> dict[str, Any]:
    project = _validated_project(source_project, registered_extensions)
    registry = build_artifact_registry(project, artifact_registry)
    try:
        report = _object(value, "phase report v3")
        _exact_keys(report, _REPORT_FIELDS, "phase report v3")
        if report.get("format") != PHASE_REPORT_V3_FORMAT:
            raise PhaseReportV3Error("phase report v3 format is unsupported")
        if report.get("format_version") != PHASE_REPORT_V3_VERSION or isinstance(
            report.get("format_version"), bool
        ):
            raise PhaseReportV3Error("phase report v3 version is unsupported")
        phase = report.get("phase")
        if phase not in PHASE_REPORT_V3_PHASE_IDS:
            raise PhaseReportV3Error("phase report v3 phase is unsupported")
        status = report.get("status")
        if status not in {"ready", "not_applicable"}:
            raise PhaseReportV3Error("phase report v3 status is unsupported")

        expected_top = {
            "project": document_identity(project.project),
            "profile": document_identity(project.profile),
            "source_manifest": document_identity(project.manifest),
        }
        top_identities: list[dict[str, Any]] = []
        for field, expected in expected_top.items():
            identity = _validate_identity(report.get(field), f"phase report v3.{field}")
            if identity != expected:
                raise PhaseReportV3Error(
                    f"phase report v3.{field} does not match the loaded project"
                )
            top_identities.append(identity)

        rationale = _object(report.get("rationale"), "phase report v3.rationale")
        _exact_keys(rationale, _RATIONALE_FIELDS, "phase report v3.rationale")
        code = _identifier(rationale.get("code"), "phase report v3.rationale.code")
        _non_empty_string(rationale.get("message"), "phase report v3.rationale.message")
        if status == "ready":
            if code != "phase_ready":
                raise PhaseReportV3Error("ready phase reports require rationale phase_ready")
        else:
            expected_code = _NOT_APPLICABLE_CODES.get(str(phase))
            if expected_code is None:
                raise PhaseReportV3Error(f"{phase} cannot be not_applicable")
            if code != expected_code:
                raise PhaseReportV3Error(
                    f"{phase} not_applicable requires rationale {expected_code}"
                )
            if not _not_applicable_is_proven(str(phase), project, registry):
                raise PhaseReportV3Error(
                    f"{phase} profile does not prove that the phase is irrelevant"
                )

        evidence = report.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise PhaseReportV3Error("phase report v3.evidence must be a non-empty array")
        evidence_ids: set[str] = set()
        evidence_subjects: list[dict[str, Any]] = []
        for index, raw in enumerate(evidence):
            context = f"phase report v3.evidence/{index}"
            item = _object(raw, context)
            _exact_keys(item, _EVIDENCE_FIELDS, context)
            evidence_id = _identifier(item.get("evidence_id"), f"{context}.evidence_id")
            if evidence_id.casefold() in evidence_ids:
                raise PhaseReportV3Error("phase report v3 evidence contains an ID collision")
            evidence_ids.add(evidence_id.casefold())
            _non_empty_string(item.get("claim"), f"{context}.claim")
            subject = _validate_identity(item.get("subject"), f"{context}.subject")
            if _identity_key(subject) not in registry:
                raise PhaseReportV3Error(f"{context}.subject is unknown or mismatched")
            evidence_subjects.append(subject)
        if [item["evidence_id"] for item in evidence] != sorted(
            (item["evidence_id"] for item in evidence),
            key=lambda item: item.encode("utf-8"),
        ):
            raise PhaseReportV3Error("phase report v3 evidence must use canonical order")

        reviewer = _validate_reviewer(report.get("reviewer"), "phase report v3.reviewer")
        output_subject = _validate_output_evidence(
            report.get("output_evidence"),
            phase=str(phase),
            status=str(status),
            reviewer=reviewer,
            registry=registry,
        )

        raw_dependencies = report.get("invalidation_dependencies")
        if not isinstance(raw_dependencies, list) or not raw_dependencies:
            raise PhaseReportV3Error("phase report v3.invalidation_dependencies must be non-empty")
        dependencies = [
            _validate_identity(item, f"phase report v3.invalidation_dependencies/{index}")
            for index, item in enumerate(raw_dependencies)
        ]
        if dependencies != sorted(dependencies, key=_identity_sort_key):
            raise PhaseReportV3Error(
                "phase report v3 invalidation dependencies must use canonical order"
            )
        dependency_keys = {_identity_key(item) for item in dependencies}
        if len(dependency_keys) != len(dependencies):
            raise PhaseReportV3Error("phase report v3 invalidation dependencies contain duplicates")
        if any(key not in registry for key in dependency_keys):
            raise PhaseReportV3Error(
                "phase report v3 invalidation dependency is unknown or mismatched"
            )
        required_roots = (
            *top_identities,
            *evidence_subjects,
            *((output_subject,) if output_subject is not None else ()),
        )
        required = {
            _identity_key(item) for item in _artifact_dependency_closure(registry, required_roots)
        }
        if not required.issubset(dependency_keys):
            raise PhaseReportV3Error(
                "phase report v3 invalidation dependencies do not cover the closed artifact graph"
            )
        _extensions(
            report.get("extensions"),
            "phase report v3.extensions",
            {} if registered_extensions is None else registered_extensions,
        )
        if canonical_creation_hash(report) != report.get("content_hash"):
            raise PhaseReportV3Error("phase report v3 content hash does not match")
        return copy.deepcopy(report)
    except CreationContractError as exc:
        raise PhaseReportV3Error(str(exc)) from exc


def build_phase_report_v3(
    source_project: LoadedCreationProject,
    *,
    phase: str,
    status: str,
    rationale_code: str,
    rationale_message: str,
    evidence: Sequence[Mapping[str, Any]],
    output_evidence: Mapping[str, Any] | None,
    reviewer_id: str,
    reviewer_role: str,
    invalidation_dependencies: Sequence[Mapping[str, Any]] | None,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
    extensions: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    project = _validated_project(source_project, None)
    dependencies = invalidation_dependencies
    if dependencies is None:
        candidates: list[dict[str, Any]] = [
            document_identity(project.project),
            document_identity(project.profile),
            document_identity(project.manifest),
            *(dict(item["subject"]) for item in evidence),
        ]
        if output_evidence is not None:
            candidates.append(dict(output_evidence["subject"]))
        registry = build_artifact_registry(project, artifact_registry)
        dependencies = _artifact_dependency_closure(registry, candidates)
    report = {
        "format": PHASE_REPORT_V3_FORMAT,
        "format_version": PHASE_REPORT_V3_VERSION,
        "project": document_identity(project.project),
        "profile": document_identity(project.profile),
        "source_manifest": document_identity(project.manifest),
        "phase": phase,
        "status": status,
        "rationale": {
            "code": rationale_code,
            "message": rationale_message,
        },
        "evidence": [copy.deepcopy(dict(item)) for item in evidence],
        "output_evidence": (
            None if output_evidence is None else copy.deepcopy(dict(output_evidence))
        ),
        "reviewer": {"id": reviewer_id, "role": reviewer_role},
        "invalidation_dependencies": [copy.deepcopy(dict(item)) for item in dependencies],
        "extensions": [copy.deepcopy(dict(item)) for item in extensions],
        "content_hash": "",
    }
    report["content_hash"] = canonical_creation_hash(report)
    return validate_phase_report_v3(
        report,
        project,
        artifact_registry=artifact_registry,
    )


def load_phase_report_v3(
    path: str | Path,
    *,
    project_path: str | Path,
    artifact_registry: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    try:
        report = read_creation_object(path)
        project = load_creation_project(project_path)
        return validate_phase_report_v3(
            report,
            project,
            artifact_registry=artifact_registry,
        )
    except CreationContractError as exc:
        raise PhaseReportV3Error(str(exc)) from exc
