from __future__ import annotations

import copy
import hashlib
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from worldforge.asset_io import (
    AssetContractError,
    write_json_atomic,
)
from worldforge.creation_contracts import (
    CREATION_PROFILE_FORMAT,
    CREATION_PROJECT_FORMAT,
    CREATION_SOURCE_MANIFEST_FORMAT,
    LOGIC_MODULE_FORMAT,
    CreationContractError,
    LoadedCreationProject,
    _exact_keys,
    _extensions,
    _identifier,
    _identifier_array,
    _integer,
    _locale,
    _logic_runtime_string,
    _non_empty_string,
    _object,
    _preflight_logic_object,
    _reject_logic_unsafe_content,
    _semver,
    _sha256,
    _string_array,
    _validate_json_structure,
    _validate_presentation,
    canonical_creation_hash,
    load_creation_project,
    read_creation_object,
    validate_creation_document,
    validate_creation_documents,
)
from worldforge.file_stat import is_link_or_reparse, path_file_stat
from worldforge.game_logic import (
    ANALYSIS_LIMITS,
    ANALYZERS,
    EXECUTION_SEMANTICS,
    analysis_requirements_for,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.validation_memo import memoize_document_validation

GAMEPACK_FORMAT = "world-forge.gamepack"
GAMEPACK_VERSION = 1
CAPABILITY_LEDGER_FORMAT = "world-forge.mechanic_capability_ledger"
CAPABILITY_LEDGER_VERSION = 1
GAMEPACK_SCHEMA_MAXIMA = {
    "asset_requirements": 1024,
    "asset_referencing_subjects": 1024,
    "module_collections": 256,
    "projected_payloads": 1024,
    "world_projected_payloads": 4096,
    "id_arrays": 256,
    "state_schema": 129,
    "initial_state": 129,
    "narrative_transitions": 128,
    "localization_references": 4096,
    "localization_supported_locales": 64,
    "mechanic_requirements": 128,
    "provenance": 1024,
    "registered_extensions": 64,
    "runtime_accepted_logic_formats": 64,
    "runtime_features": 256,
    "runtime_platform_matrix": 32,
}
CAPABILITY_LEDGER_SCHEMA_MAXIMA = {
    "mechanics": 128,
    "features": 256,
    "id_arrays": 256,
    "evidence": 64,
    "adapter_version": 64,
}
_MAX_ASSET_REFERENCE_EXPANSION = 32_768

_GAMEPACK_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "game",
        "source",
        "modules",
        "logic",
        "presentation",
        "asset_requirements",
        "runtime_requirements",
        "analysis_requirements",
        "localization",
        "mechanic_requirements",
        "provenance",
        "registered_extensions",
        "content_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_GAME_FIELDS = frozenset({"id", "title", "version", "default_locale"})
_SOURCE_FIELDS = frozenset({"project", "profile", "source_manifest", "logic_modules"})
_MODULE_FIELDS = frozenset({"world", "activities", "narrative", "systems"})
_LOGIC_FIELDS = frozenset(
    {
        "source",
        "title",
        "state_schema",
        "initial_state",
        "core_verbs",
        "actions",
        "conditions",
        "effects",
        "rules",
        "goals",
        "failures",
        "endings",
        "events",
        "presentation_hooks",
        "mechanics",
        "narrative_cursor",
        "narrative_transitions",
        "execution_semantics",
    }
)
_ANALYSIS_REQUIREMENT_FIELDS = frozenset(
    {
        "profile",
        "analyzer_id",
        "analyzer_version",
        "reason_code",
        "limits",
        "content_hash",
    }
)
_ANALYSIS_LIMIT_FIELDS = frozenset(ANALYSIS_LIMITS)
_ASSET_REQUIREMENT_FIELDS = frozenset(
    {
        "binding_id",
        "required",
        "accepted_formats",
        "roles",
        "usage_contexts",
        "referencing_subjects",
    }
)
_ASSET_SUBJECT_FIELDS = frozenset({"kind", "id"})
_RUNTIME_FIELDS = frozenset(
    {
        "requested_adapter",
        "accepted_logic_formats",
        "required_features",
        "optional_features",
        "presentation",
        "platform_matrix",
        "input_capabilities",
        "asset_formats",
        "save_expected",
        "replay_expected",
        "packaging_target",
    }
)
_RUNTIME_PRESENTATION_FIELDS = frozenset({"mode", "camera", "perspective", "renderer"})
_ACCEPTED_LOGIC_FORMAT_FIELDS = frozenset({"format", "versions"})
_PLATFORM_FIELDS = frozenset(
    {"platform_id", "platform_family", "architecture", "backend", "renderer"}
)
_LOCALIZATION_FIELDS = frozenset(
    {"source_locale", "supported_locales", "externalized_text", "references"}
)
_LOCALIZATION_REFERENCE_FIELDS = frozenset(
    {"key", "subject_kind", "subject_id", "field", "source_text"}
)
_PROVENANCE_FIELDS = frozenset({"kind", "subject"})
_MECHANIC_REQUIREMENT_FIELDS = frozenset(
    {"mechanic_id", "core_verb_id", "action_id", "required_feature_ids"}
)
_CURSOR_FIELDS = frozenset(
    {
        "compiler_owned",
        "id",
        "type",
        "initial",
        "allowed_values",
        "mutability",
        "persistence",
    }
)
_TRANSITION_FIELDS = frozenset(
    {
        "compiler_owned",
        "id",
        "action_id",
        "source_unit_id",
        "option_id",
        "target_unit_id",
        "precondition",
        "effect",
        "atomic_source_condition_ids",
        "atomic_source_effect_ids",
    }
)
_TRANSITION_PRECONDITION_FIELDS = frozenset(
    {"compiler_owned", "id", "operator", "cursor_state_id", "value"}
)
_TRANSITION_EFFECT_FIELDS = frozenset(
    {
        "compiler_owned",
        "id",
        "operation",
        "cursor_state_id",
        "value",
        "invalid_transition_policy",
    }
)
_LEDGER_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "ledger_id",
        "gamepack",
        "adapter",
        "mechanics",
        "features",
        "content_hash",
    }
)
_ADAPTER_FIELDS = frozenset({"adapter_id", "adapter_version", "status"})
_LEDGER_MECHANIC_FIELDS = frozenset(
    {
        "mechanic_id",
        "core_verb_id",
        "runtime_action_id",
        "authoritative_state_ids",
        "condition_ids",
        "rule_ids",
        "effect_ids",
        "presentation_hook_ids",
        "asset_binding_ids",
        "save_replay",
        "test_evidence",
        "native_evidence",
        "status",
        "reason_code",
        "missing_feature_ids",
        "extension",
    }
)
_SAVE_REPLAY_FIELDS = frozenset({"state_ids", "event_ids"})
_LEDGER_FEATURE_FIELDS = frozenset(
    {
        "feature_id",
        "status",
        "reason_code",
        "test_evidence",
        "native_evidence",
        "missing_feature_ids",
        "extension",
    }
)
_EVIDENCE_FIELDS = frozenset({"evidence_id", "content_hash"})
_CAPABILITY_STATUSES = frozenset(
    {
        "supported_current",
        "game_extension_verified",
        "authoring_only",
        "blocked",
    }
)
_WORLD_PAYLOAD_FIELDS = {
    "canon": "facts",
    "chronology": "events",
    "space": "spaces",
    "group": "groups",
    "character": "characters",
    "knowledge": "knowledge_items",
}
_WORLD_RECORD_FIELDS = {
    "canon": ("id", "statement", "status"),
    "chronology": ("id", "sequence", "summary"),
    "space": ("id", "name", "topology"),
    "group": ("id", "name", "group_type"),
    "character": ("id", "name", "role"),
    "knowledge": ("id", "statement", "access"),
}
_ACTIVITY_FIELDS = (
    "id",
    "activity_type",
    "title",
    "participant_ids",
    "spatial_context_ids",
    "start_condition_ids",
    "end_condition_ids",
    "success_condition_ids",
    "failure_condition_ids",
    "effect_ids",
    "event_ids",
    "presentation_hook_ids",
    "asset_binding_ids",
)
_SYSTEM_FIELDS = (
    "id",
    "system_type",
    "title",
    "precondition_ids",
    "effect_ids",
    "event_ids",
    "asset_binding_ids",
)
_NARRATIVE_COMMON_FIELDS = (
    "id",
    "unit_type",
    "title",
    "prerequisite_ids",
    "effect_ids",
    "next_unit_ids",
    "asset_binding_ids",
)
_ROLE_BY_HOOK_KIND = {
    "board": "board_visual",
    "text": "text_ui",
    "feedback": "interaction_feedback",
    "ending": "ending_ui",
}
_SUPPORTED_PLATFORM_TOKENS = {
    "platform:linux_x86_64": ("platform:linux", "architecture:x86_64"),
    "platform:windows_x86_64": ("platform:windows", "architecture:x86_64"),
}


class GamepackError(ValueError):
    """Raised when generic compilation or gamepack verification fails closed."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


class GamepackPartialPublicationError(GamepackError):
    """Carries a hash-bound recovery receipt when a later output fails."""

    def __init__(
        self,
        *,
        published: PublishedGameArtifact,
        failed_output: Path,
        cause: BaseException,
    ) -> None:
        self.receipt = {
            "status": "partial_publication",
            "reason_code": "secondary_output_publish_failed",
            "published": [
                {
                    "path": os.fspath(published.path),
                    "format": published.format,
                    "content_hash": published.content_hash,
                }
            ],
            "failed_output": os.fspath(failed_output),
            "cause": str(cause),
        }
        super().__init__(
            "partial_publication",
            "the exact first artifact was retained because the secondary output failed",
        )


@dataclass(frozen=True, slots=True)
class PublishedGameArtifact:
    path: Path
    parent_identity: tuple[int, int]
    identity: tuple[int, int]
    content_hash: str
    format: str


@dataclass(frozen=True, slots=True)
class CapabilityEvidenceSource:
    evidence_id: str
    category: Literal["test", "native"]
    payload: bytes


@dataclass(frozen=True, slots=True)
class RegisteredRuntimeAdapter:
    adapter_id: str
    adapter_version: str
    accepted_logic_formats: tuple[tuple[str, tuple[int, ...]], ...]
    platform_matrix: tuple[tuple[str, str, str, str, str], ...]
    supported_features: frozenset[str]
    supported_mechanics: frozenset[str]


@dataclass(frozen=True, slots=True)
class RegisteredGameExtension:
    extension_id: str
    extension_version: int
    content_hash: str
    supported_features: frozenset[str]
    supported_mechanics: frozenset[str]


def _canonical_hash(value: Mapping[str, object]) -> str:
    return canonical_creation_hash(value)


def _fail(reason_code: str, detail: str) -> None:
    raise GamepackError(reason_code, detail)


def _identity(document: Mapping[str, Any], identity_field: str) -> dict[str, Any]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[identity_field],
        "content_hash": document["content_hash"],
    }


def _identity_sort_key(value: Mapping[str, Any]) -> tuple[bytes, int, bytes, bytes]:
    return (
        str(value["format"]).encode("utf-8"),
        int(value["format_version"]),
        str(value["id"]).encode("utf-8"),
        str(value["content_hash"]).encode("ascii"),
    )


def _checked_identity(
    value: object,
    context: str,
    *,
    allowed_formats: frozenset[str] | None = None,
) -> dict[str, Any]:
    identity = _object(value, context)
    _exact_keys(identity, _IDENTITY_FIELDS, context)
    format_name = _non_empty_string(identity.get("format"), f"{context}.format")
    if allowed_formats is not None and format_name not in allowed_formats:
        _fail("identity_format_unsupported", f"{context}.format is unsupported")
    version = _integer(identity.get("format_version"), f"{context}.format_version", minimum=1)
    if version != 1:
        _fail("identity_version_unsupported", f"{context}.format_version must be 1")
    _identifier(identity.get("id"), f"{context}.id")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    return identity


def _validated_project(source: LoadedCreationProject) -> LoadedCreationProject:
    if not isinstance(source, LoadedCreationProject):
        _fail("source_project_invalid", "build_gamepack requires a loaded creation project")
    if source.project.get("project_kind") == "game":
        if not source.logic_modules:
            _fail(
                "logic_module_required",
                "gamepack compilation requires one logic module",
            )
        if len(source.logic_modules) != 1:
            _fail(
                "logic_module_count_unsupported",
                "gamepack v1 compilation supports exactly one logic module",
            )
    documents = (
        source.project,
        source.profile,
        source.manifest,
        *source.world_modules,
        *source.activity_modules,
        *source.narrative_modules,
        *source.system_modules,
        *source.logic_modules,
    )
    for index, document in enumerate(documents):
        extensions = document.get("extensions") if isinstance(document, Mapping) else None
        if not isinstance(extensions, list):
            continue
        known_ids = {
            extension.get("id"): (lambda _value: None)
            for extension in extensions
            if isinstance(extension, Mapping) and isinstance(extension.get("id"), str)
        }
        try:
            _extensions(
                extensions,
                f"source document {index}.extensions",
                known_ids,
                maximum=GAMEPACK_SCHEMA_MAXIMA["registered_extensions"],
            )
        except CreationContractError as exc:
            _fail("source_project_invalid", str(exc))
        for extension in extensions:
            if isinstance(extension, Mapping) and extension.get("required") is True:
                _fail(
                    "required_extension_unsupported",
                    f"required extension {extension.get('id')} has no registered gamepack compiler",
                )
    try:
        return validate_creation_documents(
            source.project,
            source.profile,
            source.manifest,
            source.world_modules,
            source.activity_modules,
            source.narrative_modules,
            source.system_modules,
            source.logic_modules,
        )
    except CreationContractError as exc:
        _fail("source_project_invalid", str(exc))


def _runtime_string_tree(value: object, context: str = "gamepack") -> None:
    stack: list[tuple[str, object]] = [(context, value)]
    while stack:
        current_context, current = stack.pop()
        if isinstance(current, dict):
            stack.extend((f"{current_context}.{key}", item) for key, item in current.items())
        elif isinstance(current, list):
            stack.extend((f"{current_context}/{index}", item) for index, item in enumerate(current))
        elif isinstance(current, str):
            try:
                _logic_runtime_string(current, current_context)
            except CreationContractError as exc:
                _fail("unsafe_runtime_string", str(exc))


def _preflight_runtime_document(value: Mapping[str, object], context: str) -> None:
    try:
        _preflight_logic_object(value)
    except CreationContractError as exc:
        _fail("document_bounds_exceeded", f"{context}: {exc}")


def _source_extensions(project: LoadedCreationProject) -> list[dict[str, Any]]:
    documents = (
        project.project,
        project.profile,
        project.manifest,
        *project.world_modules,
        *project.activity_modules,
        *project.narrative_modules,
        *project.system_modules,
        *project.logic_modules,
    )
    indexed: dict[str, dict[str, Any]] = {}
    for document in documents:
        for extension in document["extensions"]:
            if extension["required"]:
                _fail(
                    "required_extension_unsupported",
                    f"required extension {extension['id']} has no registered gamepack compiler",
                )
            key = extension["id"].casefold()
            previous = indexed.get(key)
            if previous is not None and previous != extension:
                _fail(
                    "extension_identity_conflict",
                    f"extension {extension['id']} has conflicting identities",
                )
            indexed[key] = copy.deepcopy(extension)
    if len(indexed) > GAMEPACK_SCHEMA_MAXIMA["registered_extensions"]:
        _fail(
            "extension_limit_exceeded",
            "compiled extension inventory exceeds the 64-item gamepack limit",
        )
    return sorted(
        indexed.values(),
        key=lambda item: (
            item["id"].encode("utf-8"),
            item["version"],
            item["required"],
            item["content_hash"].encode("ascii"),
        ),
    )


def _world_projection(module: Mapping[str, Any]) -> dict[str, Any]:
    module_type = module["module_type"]
    field = _WORLD_PAYLOAD_FIELDS[module_type]
    records = [
        {key: copy.deepcopy(record[key]) for key in _WORLD_RECORD_FIELDS[module_type]}
        for record in module[field]
    ]
    return {
        "source": _identity(module, "module_id"),
        "module_type": module_type,
        "title": module["title"],
        "records": records,
    }


def _activity_projection(module: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": _identity(module, "module_id"),
        "title": module["title"],
        "activities": [
            {key: copy.deepcopy(activity[key]) for key in _ACTIVITY_FIELDS}
            for activity in module["activities"]
        ],
    }


def _narrative_projection(module: Mapping[str, Any]) -> dict[str, Any]:
    units: list[dict[str, Any]] = []
    for unit in module["units"]:
        projected = {key: copy.deepcopy(unit[key]) for key in _NARRATIVE_COMMON_FIELDS}
        if unit["unit_type"] == "choice":
            projected["options"] = [
                {
                    "id": option["id"],
                    "label": option["label"],
                    "next_unit_id": option["next_unit_id"],
                    "condition_ids": copy.deepcopy(option["condition_ids"]),
                    "effect_ids": copy.deepcopy(option["effect_ids"]),
                }
                for option in unit["options"]
            ]
        elif unit["unit_type"] == "ending":
            projected["ending_kind"] = unit["ending_kind"]
        units.append(projected)
    return {
        "source": _identity(module, "module_id"),
        "title": module["title"],
        "entry_unit_ids": copy.deepcopy(module["entry_unit_ids"]),
        "units": units,
    }


def _system_projection(module: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": _identity(module, "module_id"),
        "title": module["title"],
        "systems": [
            {key: copy.deepcopy(system[key]) for key in _SYSTEM_FIELDS}
            for system in module["systems"]
        ],
    }


def _narrative_runtime(
    project: LoadedCreationProject,
    logic: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if project.profile["narrative"]["requirement"] == "none":
        return None, []
    if project.profile["narrative"]["topology"] == "branching":
        return _derive_narrative_runtime(project.narrative_modules, logic)
    return None, []


def _validate_authored_narrative_projection(
    narrative_modules: Sequence[Mapping[str, Any]],
) -> None:
    if any(
        unit["unit_type"] == "choice" for module in narrative_modules for unit in module["units"]
    ):
        _fail(
            "compiled_logic_invalid",
            "authored narrative projection cannot contain executable choice units",
        )


def _derive_narrative_runtime(
    narrative_modules: Sequence[Mapping[str, Any]],
    logic: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    entries = [entry for module in narrative_modules for entry in module["entry_unit_ids"]]
    if len(entries) != 1:
        _fail(
            "narrative_entry_count_unsupported",
            "gamepack v1 branching execution requires exactly one narrative entry",
        )
    units = {
        unit["id"].casefold(): unit for module in narrative_modules for unit in module["units"]
    }
    entry_key = entries[0].casefold()
    if entry_key not in units:
        _fail(
            "narrative_entry_unknown",
            f"narrative entry {entries[0]} does not identify a projected unit",
        )
    reachable: set[str] = set()
    pending = [entry_key]
    while pending:
        unit_key = pending.pop()
        if unit_key in reachable:
            continue
        unit = units.get(unit_key)
        if unit is None:
            _fail(
                "narrative_transition_target_unknown",
                f"narrative transition targets unknown unit {unit_key}",
            )
        reachable.add(unit_key)
        targets = (
            [option["next_unit_id"] for option in unit["options"]]
            if unit["unit_type"] == "choice"
            else unit["next_unit_ids"]
        )
        for target in reversed(targets):
            target_key = target.casefold()
            if target_key not in units:
                _fail(
                    "narrative_transition_target_unknown",
                    f"narrative transition targets unknown unit {target}",
                )
            pending.append(target_key)
    if reachable != set(units):
        unreachable = sorted(
            (units[key]["id"] for key in set(units) - reachable),
            key=lambda item: item.encode("utf-8"),
        )
        _fail(
            "narrative_unit_unreachable",
            "branching narrative contains unreachable units: " + ", ".join(unreachable),
        )
    for unit_key in sorted(reachable):
        unit = units[unit_key]
        if unit["unit_type"] == "ending":
            if unit["next_unit_ids"]:
                _fail(
                    "narrative_ending_has_transition",
                    f"ending {unit['id']} cannot have an outgoing transition",
                )
            continue
        if unit["unit_type"] != "choice":
            _fail(
                "narrative_transition_unsupported",
                "gamepack v1 cannot lower reachable non-choice narrative unit "
                f"{unit['id']} ({unit['unit_type']})",
            )
    actions_by_option: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for action in logic["actions"]:
        narrative_bindings = [
            binding
            for binding in action["source_bindings"]
            if binding["kind"] == "narrative_option"
        ]
        if len(narrative_bindings) > 1:
            _fail(
                "narrative_action_binding_ambiguous",
                f"action {action['id']} binds more than one narrative option",
            )
        if narrative_bindings:
            binding = narrative_bindings[0]
            actions_by_option.setdefault(
                (
                    binding["source_id"].casefold(),
                    binding["option_id"].casefold(),
                ),
                [],
            ).append(action)

    rules = {rule["id"].casefold(): rule for rule in logic["rules"]}
    transitions: list[dict[str, Any]] = []
    for module in narrative_modules:
        for unit in module["units"]:
            if unit["unit_type"] != "choice":
                continue
            for option in unit["options"]:
                bound = actions_by_option.get(
                    (unit["id"].casefold(), option["id"].casefold()),
                    [],
                )
                if not bound:
                    _fail(
                        "narrative_option_action_missing",
                        f"narrative option {unit['id']}/{option['id']} has no executable action",
                    )
                if len(bound) != 1:
                    _fail(
                        "narrative_option_action_ambiguous",
                        f"narrative option {unit['id']}/{option['id']} has multiple actions",
                    )
                action = bound[0]
                owned_rules = [rules[rule_id.casefold()] for rule_id in action["rule_ids"]]
                owned_rules.sort(
                    key=lambda item: (
                        int(item["order"]),
                        str(item["id"]).encode("utf-8"),
                    )
                )
                source_condition_ids = [
                    condition_id for rule in owned_rules for condition_id in rule["condition_ids"]
                ]
                source_effect_ids = [
                    effect_id for rule in owned_rules for effect_id in rule["effect_ids"]
                ]
                transition_id = f"wf_internal_transition_{action['id']}"
                transitions.append(
                    {
                        "compiler_owned": True,
                        "id": transition_id,
                        "action_id": action["id"],
                        "source_unit_id": unit["id"],
                        "option_id": option["id"],
                        "target_unit_id": option["next_unit_id"],
                        "precondition": {
                            "compiler_owned": True,
                            "id": f"wf_internal_cursor_at_{action['id']}",
                            "operator": "cursor_equals",
                            "cursor_state_id": "wf_internal_narrative_cursor",
                            "value": unit["id"],
                        },
                        "effect": {
                            "compiler_owned": True,
                            "id": f"wf_internal_advance_{action['id']}",
                            "operation": "set_cursor",
                            "cursor_state_id": "wf_internal_narrative_cursor",
                            "value": option["next_unit_id"],
                            "invalid_transition_policy": "reject_transition",
                        },
                        "atomic_source_condition_ids": source_condition_ids,
                        "atomic_source_effect_ids": source_effect_ids,
                    }
                )
    transitions.sort(key=lambda item: item["id"].encode("utf-8"))
    cursor = {
        "compiler_owned": True,
        "id": "wf_internal_narrative_cursor",
        "type": "string",
        "initial": entries[0],
        "allowed_values": sorted(
            (units[key]["id"] for key in reachable),
            key=lambda item: item.encode("utf-8"),
        ),
        "mutability": "mutable",
        "persistence": "saved",
    }
    return cursor, transitions


def _initial_state(
    states: Sequence[Mapping[str, Any]],
    cursor: Mapping[str, Any] | None,
) -> dict[str, Any]:
    result = {state["id"]: copy.deepcopy(state["initial"]) for state in states}
    if cursor is not None:
        result[cursor["id"]] = cursor["initial"]
    return dict(sorted(result.items(), key=lambda item: item[0].encode("utf-8")))


def _source_subjects(
    modules: Mapping[str, Sequence[Mapping[str, Any]]],
    logic: Mapping[str, Any],
) -> tuple[dict[str, set[tuple[str, str]]], dict[str, set[str]], dict[str, set[str]]]:
    subjects: dict[str, set[tuple[str, str]]] = {}
    contexts: dict[str, set[str]] = {}
    roles: dict[str, set[str]] = {}

    def add(binding: str, kind: str, subject_id: str, context: str, role: str) -> None:
        subjects.setdefault(binding, set()).add((kind, subject_id))
        contexts.setdefault(binding, set()).add(context)
        roles.setdefault(binding, set()).add(role)

    for module in modules["activities"]:
        for activity in module["activities"]:
            for binding in activity["asset_binding_ids"]:
                add(binding, "activity", activity["id"], "activity", "activity_visual")
    for module in modules["systems"]:
        for system in module["systems"]:
            for binding in system["asset_binding_ids"]:
                add(binding, "system", system["id"], "system", "system_feedback")
    for module in modules["narrative"]:
        for unit in module["units"]:
            for binding in unit["asset_binding_ids"]:
                add(binding, "narrative_unit", unit["id"], "narrative", "narrative_ui")
    hooks = {hook["id"].casefold(): hook for hook in logic["presentation_hooks"]}
    for hook in logic["presentation_hooks"]:
        for binding in hook["asset_binding_ids"]:
            add(
                binding,
                "presentation_hook",
                hook["id"],
                f"presentation:{hook['kind']}",
                _ROLE_BY_HOOK_KIND[hook["kind"]],
            )
    for mechanic in logic["mechanics"]:
        for binding in mechanic["asset_binding_ids"]:
            add(binding, "mechanic", mechanic["id"], "mechanic", "mechanic_feedback")
        for hook_id in mechanic["presentation_hook_ids"]:
            hook = hooks[hook_id.casefold()]
            for binding in hook["asset_binding_ids"]:
                add(
                    binding,
                    "mechanic",
                    mechanic["id"],
                    f"mechanic:{hook['kind']}",
                    _ROLE_BY_HOOK_KIND[hook["kind"]],
                )
    return subjects, contexts, roles


def _preflight_asset_reference_expansion(
    modules: Mapping[str, Sequence[Mapping[str, Any]]],
    logic: Mapping[str, Any],
) -> None:
    """Bound source-to-asset fan-out before allocating subject sets."""

    count = 0

    def add(bindings: object, context: str) -> None:
        nonlocal count
        if not isinstance(bindings, list):
            _fail(
                "asset_requirement_expansion_invalid",
                f"{context} asset bindings must be an array",
            )
        count += len(bindings)
        if count > _MAX_ASSET_REFERENCE_EXPANSION:
            _fail(
                "asset_requirement_expansion_limit",
                "source asset references exceed the bounded gamepack v1 expansion limit",
            )

    for module in modules["activities"]:
        for activity in module["activities"]:
            add(activity.get("asset_binding_ids"), f"activity {activity.get('id')}")
    for module in modules["systems"]:
        for system in module["systems"]:
            add(system.get("asset_binding_ids"), f"system {system.get('id')}")
    for module in modules["narrative"]:
        for unit in module["units"]:
            add(unit.get("asset_binding_ids"), f"narrative unit {unit.get('id')}")
    for hook in logic["presentation_hooks"]:
        add(hook.get("asset_binding_ids"), f"presentation hook {hook.get('id')}")
    for mechanic in logic["mechanics"]:
        add(mechanic.get("asset_binding_ids"), f"mechanic {mechanic.get('id')}")


def _asset_requirements(
    modules: Mapping[str, Sequence[Mapping[str, Any]]],
    logic: Mapping[str, Any],
    runtime_target: Mapping[str, Any],
) -> list[dict[str, Any]]:
    _preflight_asset_reference_expansion(modules, logic)
    subjects, contexts, roles = _source_subjects(modules, logic)
    requirements: list[dict[str, Any]] = []
    for binding in sorted(subjects, key=lambda item: item.encode("utf-8")):
        requirements.append(
            {
                "binding_id": binding,
                "required": True,
                "accepted_formats": copy.deepcopy(runtime_target["asset_formats"]),
                "roles": sorted(roles[binding], key=lambda item: item.encode("utf-8")),
                "usage_contexts": sorted(
                    contexts[binding],
                    key=lambda item: item.encode("utf-8"),
                ),
                "referencing_subjects": [
                    {"kind": kind, "id": subject_id}
                    for kind, subject_id in sorted(
                        subjects[binding],
                        key=lambda item: (
                            item[0].encode("utf-8"),
                            item[1].encode("utf-8"),
                        ),
                    )
                ],
            }
        )
    return requirements


def _platform_matrix(target: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for token in target["platforms"]:
        details = _SUPPORTED_PLATFORM_TOKENS.get(token)
        if details is None:
            _fail(
                "platform_identity_unsupported",
                f"gamepack v1 cannot normalize declared platform {token}",
            )
        platform_family, architecture = details
        result.append(
            {
                "platform_id": token,
                "platform_family": platform_family,
                "architecture": architecture,
                "backend": "backend:unspecified",
                "renderer": target["renderer"],
            }
        )
    return result


def _runtime_requirements(
    profile: Mapping[str, Any],
    logic: Mapping[str, Any],
) -> dict[str, Any]:
    target = profile["runtime_target"]
    accepts_gamepack = any(
        item["format"] == GAMEPACK_FORMAT and GAMEPACK_VERSION in item["versions"]
        for item in target["accepted_logic_formats"]
    )
    if not accepts_gamepack:
        _fail(
            "runtime_target_rejects_gamepack_v1",
            "runtime target does not accept world-forge.gamepack v1",
        )
    mechanic_features = {
        feature for mechanic in logic["mechanics"] for feature in mechanic["required_feature_ids"]
    }
    required = set(target["required_features"]) | mechanic_features
    optional = set(target["optional_features"]) - required
    presentation = profile["presentation"]
    return {
        "requested_adapter": target["requested_adapter"],
        "accepted_logic_formats": copy.deepcopy(target["accepted_logic_formats"]),
        "required_features": sorted(required, key=lambda item: item.encode("utf-8")),
        "optional_features": sorted(optional, key=lambda item: item.encode("utf-8")),
        "presentation": {
            "mode": target["presentation_mode"],
            "camera": presentation["camera"],
            "perspective": presentation["perspective"],
            "renderer": target["renderer"],
        },
        "platform_matrix": _platform_matrix(target),
        "input_capabilities": copy.deepcopy(target["input_capabilities"]),
        "asset_formats": copy.deepcopy(target["asset_formats"]),
        "save_expected": target["save_expected"],
        "replay_expected": target["replay_expected"],
        "packaging_target": target["packaging_target"],
    }


def _localization_references(
    game: Mapping[str, Any],
    modules: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []

    def add(kind: str, subject_id: str, field: str, text: str) -> None:
        references.append(
            {
                "key": f"{kind}.{subject_id}.{field}",
                "subject_kind": kind,
                "subject_id": subject_id,
                "field": field,
                "source_text": text,
            }
        )

    add("game", game["id"], "title", game["title"])
    for collection, kind in (
        ("world", "world_module"),
        ("activities", "activity_module"),
        ("narrative", "narrative_module"),
        ("systems", "system_module"),
    ):
        for module in modules[collection]:
            module_id = module["source"]["id"]
            add(kind, module_id, "title", module["title"])
    for module in modules["activities"]:
        for activity in module["activities"]:
            add("activity", activity["id"], "title", activity["title"])
    for module in modules["narrative"]:
        for unit in module["units"]:
            add("narrative_unit", unit["id"], "title", unit["title"])
            for option in unit.get("options", []):
                add(
                    "narrative_option",
                    f"{unit['id']}_{option['id']}",
                    "label",
                    option["label"],
                )
    for module in modules["systems"]:
        for system in module["systems"]:
            add("system", system["id"], "title", system["title"])
    references.sort(key=lambda item: item["key"].encode("utf-8"))
    return references


def _mechanic_requirements(logic: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "mechanic_id": mechanic["id"],
            "core_verb_id": mechanic["core_verb_id"],
            "action_id": mechanic["action_id"],
            "required_feature_ids": copy.deepcopy(mechanic["required_feature_ids"]),
        }
        for mechanic in logic["mechanics"]
    ]


def _build_gamepack_document(source_project: LoadedCreationProject) -> dict[str, Any]:
    project = _validated_project(source_project)
    if project.project["project_kind"] != "game":
        _fail("project_kind_not_executable", "only game projects compile to gamepack v1")
    if not project.logic_modules:
        _fail("logic_module_required", "gamepack compilation requires one logic module")
    if len(project.logic_modules) != 1:
        _fail(
            "logic_module_count_unsupported",
            "gamepack v1 compilation supports exactly one logic module",
        )
    source_logic = project.logic_modules[0]
    if source_logic["extensions"]:
        _fail(
            "logic_extension_unsupported",
            "gamepack v1 does not execute logic-module extensions",
        )
    registered_extensions = _source_extensions(project)
    world = sorted(
        (_world_projection(module) for module in project.world_modules),
        key=lambda item: item["source"]["id"].encode("utf-8"),
    )
    activities = sorted(
        (_activity_projection(module) for module in project.activity_modules),
        key=lambda item: item["source"]["id"].encode("utf-8"),
    )
    narrative = sorted(
        (_narrative_projection(module) for module in project.narrative_modules),
        key=lambda item: item["source"]["id"].encode("utf-8"),
    )
    systems = sorted(
        (_system_projection(module) for module in project.system_modules),
        key=lambda item: item["source"]["id"].encode("utf-8"),
    )
    modules = {
        "world": world,
        "activities": activities,
        "narrative": narrative,
        "systems": systems,
    }
    cursor, transitions = _narrative_runtime(project, source_logic)
    state_schema = copy.deepcopy(source_logic["state_variables"])
    if cursor is not None:
        state_schema.append(copy.deepcopy(cursor))
    compiled_logic = {
        "source": _identity(source_logic, "module_id"),
        "title": source_logic["title"],
        "state_schema": state_schema,
        "initial_state": _initial_state(source_logic["state_variables"], cursor),
        "core_verbs": sorted(
            copy.deepcopy(project.profile["gameplay"]["core_verbs"]),
            key=lambda item: item["id"].encode("utf-8"),
        ),
        "actions": copy.deepcopy(source_logic["actions"]),
        "conditions": copy.deepcopy(source_logic["conditions"]),
        "effects": copy.deepcopy(source_logic["effects"]),
        "rules": copy.deepcopy(source_logic["rules"]),
        "goals": copy.deepcopy(source_logic["goals"]),
        "failures": copy.deepcopy(source_logic["failures"]),
        "endings": copy.deepcopy(source_logic["endings"]),
        "events": copy.deepcopy(source_logic["events"]),
        "presentation_hooks": copy.deepcopy(source_logic["presentation_hooks"]),
        "mechanics": copy.deepcopy(source_logic["mechanics"]),
        "narrative_cursor": copy.deepcopy(cursor),
        "narrative_transitions": transitions,
        "execution_semantics": dict(EXECUTION_SEMANTICS),
    }
    runtime = _runtime_requirements(project.profile, compiled_logic)
    source_identities = {
        "project": _identity(project.project, "project_id"),
        "profile": _identity(project.profile, "profile_id"),
        "source_manifest": _identity(project.manifest, "project_id"),
        "logic_modules": [_identity(source_logic, "module_id")],
    }
    provenance_subjects = sorted(
        (
            source_identities["project"],
            source_identities["profile"],
            source_identities["source_manifest"],
            source_identities["logic_modules"][0],
            *(module["source"] for collection in modules.values() for module in collection),
        ),
        key=_identity_sort_key,
    )
    localization_profile = project.profile["presentation"]["localization"]
    game = {
        "id": project.project["project_id"],
        "title": project.project["title"],
        "version": project.project["project_version"],
        "default_locale": project.project["default_locale"],
    }
    document: dict[str, Any] = {
        "format": GAMEPACK_FORMAT,
        "format_version": GAMEPACK_VERSION,
        "game": game,
        "source": source_identities,
        "modules": modules,
        "logic": compiled_logic,
        "presentation": copy.deepcopy(project.profile["presentation"]),
        "asset_requirements": _asset_requirements(modules, compiled_logic, runtime),
        "runtime_requirements": runtime,
        "analysis_requirements": analysis_requirements_for(modules, compiled_logic),
        "localization": {
            "source_locale": localization_profile["source_locale"],
            "supported_locales": copy.deepcopy(localization_profile["supported_locales"]),
            "externalized_text": localization_profile["externalized_text"],
            "references": _localization_references(game, modules),
        },
        "mechanic_requirements": _mechanic_requirements(compiled_logic),
        "provenance": [
            {"kind": "compiled_from", "subject": copy.deepcopy(subject)}
            for subject in provenance_subjects
        ],
        "registered_extensions": registered_extensions,
    }
    _preflight_runtime_document(document, "gamepack")
    _reject_logic_unsafe_content(document, context="gamepack")
    _runtime_string_tree(document)
    document["content_hash"] = _canonical_hash(document)
    return validate_gamepack_document(document)


def build_gamepack(source_project: LoadedCreationProject) -> dict[str, Any]:
    """Build one deterministic gamepack from the exact validated source graph."""

    return _build_gamepack_document(source_project)


def _validate_compiler_owned_state_schema(
    value: object,
    cursor: object,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > GAMEPACK_SCHEMA_MAXIMA["state_schema"]
    ):
        _fail(
            "compiled_logic_invalid",
            "logic.state_schema must be a bounded non-empty array",
        )
    checked_states: list[dict[str, Any]] = []
    compiler_owned: list[dict[str, Any]] = []
    for index, raw_state in enumerate(value):
        if not isinstance(raw_state, dict):
            _fail(
                "compiled_logic_invalid",
                f"gamepack.logic.state_schema/{index} must be an object",
            )
        state = raw_state
        state_id = state.get("id")
        if state.get("compiler_owned") is True or (
            isinstance(state_id, str) and state_id.startswith("wf_internal_")
        ):
            compiler_owned.append(state)
        else:
            checked_states.append(copy.deepcopy(state))

    if cursor is None:
        if compiler_owned:
            _fail(
                "compiled_logic_invalid",
                "narrative-free logic cannot contain compiler-owned state",
            )
        return checked_states, None

    if not isinstance(cursor, dict):
        _fail(
            "compiled_logic_invalid",
            "gamepack.logic.narrative_cursor must be an object or null",
        )
    cursor_object = cursor
    try:
        _exact_keys(cursor_object, _CURSOR_FIELDS, "gamepack.logic.narrative_cursor")
    except CreationContractError as exc:
        _fail("compiled_logic_invalid", str(exc))
    if cursor_object.get("id") != "wf_internal_narrative_cursor":
        _fail("compiled_logic_invalid", "internal narrative cursor ID is invalid")
    if (
        cursor_object.get("compiler_owned") is not True
        or cursor_object.get("type") != "string"
        or cursor_object.get("mutability") != "mutable"
        or cursor_object.get("persistence") != "saved"
    ):
        _fail("compiled_logic_invalid", "internal narrative cursor contract is invalid")
    try:
        allowed = _string_array(
            cursor_object.get("allowed_values"),
            "gamepack.logic.narrative_cursor.allowed_values",
            allow_empty=False,
            canonical_order=True,
        )
    except CreationContractError as exc:
        _fail("compiled_logic_invalid", str(exc))
    if cursor_object.get("initial") not in allowed:
        _fail("compiled_logic_invalid", "narrative cursor initial value is not allowed")
    if len(allowed) > GAMEPACK_SCHEMA_MAXIMA["id_arrays"]:
        _fail(
            "compiled_logic_invalid",
            "narrative cursor allowed_values exceeds its schema maximum",
        )
    if len(compiler_owned) != 1:
        _fail(
            "compiled_logic_invalid",
            "state schema must contain exactly one compiler-owned narrative cursor",
        )
    if compiler_owned[0] != cursor_object or value[-1] != cursor_object:
        _fail(
            "compiled_logic_invalid",
            "compiler-owned narrative cursor must be exact and last in state schema",
        )
    return checked_states, copy.deepcopy(cursor_object)


def _validate_compiled_source_logic(
    logic: Mapping[str, Any],
    *,
    game_id: str,
) -> None:
    core_verbs = logic["core_verbs"]
    if not isinstance(core_verbs, list) or not 1 <= len(core_verbs) <= 128:
        _fail(
            "compiled_logic_invalid",
            "logic.core_verbs must be a bounded non-empty array",
        )
    core_verb_ids: list[str] = []
    for index, raw_core_verb in enumerate(core_verbs):
        context = f"gamepack.logic.core_verbs/{index}"
        core_verb = _object(raw_core_verb, context)
        _exact_keys(core_verb, frozenset({"id", "description"}), context)
        core_verb_ids.append(_identifier(core_verb.get("id"), f"{context}.id"))
        _non_empty_string(core_verb.get("description"), f"{context}.description")
    if core_verb_ids != sorted(core_verb_ids, key=lambda item: item.encode("utf-8")):
        _fail("compiled_logic_invalid", "logic.core_verbs is not canonical")
    if len({item.casefold() for item in core_verb_ids}) != len(core_verb_ids):
        _fail("compiled_logic_invalid", "logic.core_verbs contains duplicate IDs")

    cursor = logic["narrative_cursor"]
    state_schema = logic["state_schema"]
    source_states, checked_cursor = _validate_compiler_owned_state_schema(
        state_schema,
        cursor,
    )
    pseudo: dict[str, Any] = {
        "format": LOGIC_MODULE_FORMAT,
        "format_version": 1,
        "module_id": logic["source"]["id"],
        "project_id": game_id,
        "title": logic["title"],
        "state_variables": source_states,
        "actions": copy.deepcopy(logic["actions"]),
        "conditions": copy.deepcopy(logic["conditions"]),
        "effects": copy.deepcopy(logic["effects"]),
        "rules": copy.deepcopy(logic["rules"]),
        "goals": copy.deepcopy(logic["goals"]),
        "failures": copy.deepcopy(logic["failures"]),
        "endings": copy.deepcopy(logic["endings"]),
        "events": copy.deepcopy(logic["events"]),
        "presentation_hooks": copy.deepcopy(logic["presentation_hooks"]),
        "mechanics": copy.deepcopy(logic["mechanics"]),
        "extensions": [],
    }
    pseudo["content_hash"] = _canonical_hash(pseudo)
    try:
        validate_creation_document(pseudo, expected_format=LOGIC_MODULE_FORMAT)
    except CreationContractError as exc:
        _fail("compiled_logic_invalid", str(exc))
    mapped_core_verbs = {action["core_verb_id"].casefold() for action in logic["actions"]}
    if mapped_core_verbs != {item.casefold() for item in core_verb_ids}:
        _fail(
            "compiled_logic_invalid",
            "logic actions must map every exact compiled core verb",
        )

    initial_state = logic["initial_state"]
    if (
        not isinstance(initial_state, dict)
        or not initial_state
        or len(initial_state) > GAMEPACK_SCHEMA_MAXIMA["initial_state"]
    ):
        _fail("compiled_logic_invalid", "logic.initial_state must be an object")
    expected_initial = {state["id"]: copy.deepcopy(state["initial"]) for state in source_states}
    if checked_cursor is None:
        if logic["narrative_transitions"]:
            _fail(
                "narrative_transition_invalid",
                "narrative transitions require an internal cursor",
            )
    else:
        expected_initial[checked_cursor["id"]] = checked_cursor["initial"]
    expected_initial = dict(
        sorted(expected_initial.items(), key=lambda item: item[0].encode("utf-8"))
    )
    if initial_state != expected_initial:
        _fail(
            "compiled_logic_invalid",
            "logic.initial_state does not exactly match its typed state schema",
        )

    actions = {action["id"].casefold(): action for action in logic["actions"]}
    conditions = {condition["id"].casefold() for condition in logic["conditions"]}
    effects = {effect["id"].casefold() for effect in logic["effects"]}
    transitions = logic["narrative_transitions"]
    if (
        not isinstance(transitions, list)
        or len(transitions) > GAMEPACK_SCHEMA_MAXIMA["narrative_transitions"]
    ):
        _fail(
            "compiled_logic_invalid",
            "logic.narrative_transitions must be a bounded array",
        )
    seen_transitions: set[str] = set()
    for index, raw_transition in enumerate(transitions):
        context = f"gamepack.logic.narrative_transitions/{index}"
        transition = _object(raw_transition, context)
        _exact_keys(transition, _TRANSITION_FIELDS, context)
        transition_id = _non_empty_string(transition.get("id"), f"{context}.id")
        if transition.get("compiler_owned") is not True or not transition_id.startswith(
            "wf_internal_transition_"
        ):
            _fail("compiled_logic_invalid", f"{context}.id is not compiler-owned")
        if transition_id.casefold() in seen_transitions:
            _fail("compiled_logic_invalid", "narrative transitions contain duplicate IDs")
        seen_transitions.add(transition_id.casefold())
        action_id = _identifier(transition.get("action_id"), f"{context}.action_id")
        action = actions.get(action_id.casefold())
        if action is None:
            _fail("compiled_logic_invalid", f"{context} references an unknown action")
        source_unit_id = _identifier(
            transition.get("source_unit_id"),
            f"{context}.source_unit_id",
        )
        option_id = _identifier(transition.get("option_id"), f"{context}.option_id")
        target_unit_id = _identifier(
            transition.get("target_unit_id"),
            f"{context}.target_unit_id",
        )
        if {
            (binding["source_id"], binding["option_id"])
            for binding in action["source_bindings"]
            if binding["kind"] == "narrative_option"
        } != {(source_unit_id, option_id)}:
            _fail(
                "compiled_logic_invalid",
                f"{context} does not exactly match its narrative-option binding",
            )
        precondition = _object(transition.get("precondition"), f"{context}.precondition")
        _exact_keys(
            precondition,
            _TRANSITION_PRECONDITION_FIELDS,
            f"{context}.precondition",
        )
        effect = _object(transition.get("effect"), f"{context}.effect")
        _exact_keys(effect, _TRANSITION_EFFECT_FIELDS, f"{context}.effect")
        if (
            precondition.get("compiler_owned") is not True
            or precondition.get("id") != f"wf_internal_cursor_at_{action_id}"
            or precondition.get("operator") != "cursor_equals"
            or precondition.get("cursor_state_id") != "wf_internal_narrative_cursor"
            or precondition.get("value") != source_unit_id
        ):
            _fail("compiled_logic_invalid", f"{context}.precondition is not exact")
        if (
            effect.get("compiler_owned") is not True
            or effect.get("id") != f"wf_internal_advance_{action_id}"
            or effect.get("operation") != "set_cursor"
            or effect.get("cursor_state_id") != "wf_internal_narrative_cursor"
            or effect.get("value") != target_unit_id
            or effect.get("invalid_transition_policy") != "reject_transition"
        ):
            _fail("compiled_logic_invalid", f"{context}.effect is not exact")
        raw_atomic_conditions = transition.get("atomic_source_condition_ids")
        if not isinstance(raw_atomic_conditions, list):
            _fail(
                "compiled_logic_invalid",
                f"{context}.atomic_source_condition_ids must be an array",
            )
        atomic_conditions = [
            _identifier(
                item,
                f"{context}.atomic_source_condition_ids/{index}",
            )
            for index, item in enumerate(raw_atomic_conditions)
        ]
        if len(atomic_conditions) > GAMEPACK_SCHEMA_MAXIMA["id_arrays"]:
            _fail(
                "compiled_logic_invalid",
                f"{context}.atomic_source_condition_ids exceeds its schema maximum",
            )
        if any(item.casefold() not in conditions for item in atomic_conditions):
            _fail(
                "compiled_logic_invalid",
                f"{context} references an unknown atomic source condition",
            )
        raw_atomic_effects = transition.get("atomic_source_effect_ids")
        if not isinstance(raw_atomic_effects, list) or not raw_atomic_effects:
            _fail(
                "compiled_logic_invalid",
                f"{context}.atomic_source_effect_ids must be a non-empty array",
            )
        atomic_effects = [
            _identifier(
                item,
                f"{context}.atomic_source_effect_ids/{index}",
            )
            for index, item in enumerate(raw_atomic_effects)
        ]
        if len(atomic_effects) > GAMEPACK_SCHEMA_MAXIMA["id_arrays"]:
            _fail(
                "compiled_logic_invalid",
                f"{context}.atomic_source_effect_ids exceeds its schema maximum",
            )
        if any(item.casefold() not in effects for item in atomic_effects):
            _fail(
                "compiled_logic_invalid",
                f"{context} references an unknown atomic source effect",
            )


def _validate_projection_identity(
    value: object,
    context: str,
    *,
    expected_format: str,
) -> None:
    _checked_identity(
        value,
        context,
        allowed_formats=frozenset({expected_format}),
    )


def _validate_projected_world_record(
    value: object,
    context: str,
    *,
    module_type: str,
) -> None:
    record = _object(value, context)
    fields = frozenset(_WORLD_RECORD_FIELDS[module_type])
    _exact_keys(record, fields, context)
    _identifier(record.get("id"), f"{context}.id")
    if module_type == "canon":
        _non_empty_string(record.get("statement"), f"{context}.statement")
        if record.get("status") not in {"canon", "provisional"}:
            _fail("gamepack_invalid", f"{context}.status is unsupported")
    elif module_type == "chronology":
        _integer(record.get("sequence"), f"{context}.sequence")
        _non_empty_string(record.get("summary"), f"{context}.summary")
    elif module_type == "space":
        _non_empty_string(record.get("name"), f"{context}.name")
        if record.get("topology") not in {"abstract", "symbolic", "diegetic"}:
            _fail("gamepack_invalid", f"{context}.topology is unsupported")
    elif module_type == "group":
        _non_empty_string(record.get("name"), f"{context}.name")
        _non_empty_string(record.get("group_type"), f"{context}.group_type")
    elif module_type == "character":
        _non_empty_string(record.get("name"), f"{context}.name")
        _non_empty_string(record.get("role"), f"{context}.role")
    else:
        _non_empty_string(record.get("statement"), f"{context}.statement")
        if record.get("access") not in {"public", "restricted", "secret"}:
            _fail("gamepack_invalid", f"{context}.access is unsupported")


def _validate_projected_activity(value: object, context: str) -> None:
    activity = _object(value, context)
    _exact_keys(activity, frozenset(_ACTIVITY_FIELDS), context)
    _identifier(activity.get("id"), f"{context}.id")
    if activity.get("activity_type") not in {
        "level",
        "mission",
        "quest",
        "scenario",
        "match",
        "race",
        "puzzle",
        "encounter",
        "contract",
        "expedition",
        "run",
        "tutorial",
        "challenge",
    }:
        _fail("gamepack_invalid", f"{context}.activity_type is unsupported")
    _non_empty_string(activity.get("title"), f"{context}.title")
    for field in _ACTIVITY_FIELDS[3:]:
        _identifier_array(activity.get(field), f"{context}.{field}")


def _validate_projected_system(value: object, context: str) -> None:
    system = _object(value, context)
    _exact_keys(system, frozenset(_SYSTEM_FIELDS), context)
    _identifier(system.get("id"), f"{context}.id")
    if system.get("system_type") not in {
        "rule",
        "event",
        "consequence",
        "schedule",
        "economy",
        "production_process",
        "simulation_scenario",
        "world_modifier",
        "season",
    }:
        _fail("gamepack_invalid", f"{context}.system_type is unsupported")
    _non_empty_string(system.get("title"), f"{context}.title")
    for field in _SYSTEM_FIELDS[3:]:
        _identifier_array(system.get(field), f"{context}.{field}")


def _validate_projected_narrative_unit(value: object, context: str) -> None:
    unit = _object(value, context)
    unit_type = unit.get("unit_type")
    extra = (
        frozenset({"options"})
        if unit_type == "choice"
        else frozenset({"ending_kind"})
        if unit_type == "ending"
        else frozenset()
    )
    _exact_keys(unit, frozenset(_NARRATIVE_COMMON_FIELDS) | extra, context)
    _identifier(unit.get("id"), f"{context}.id")
    if unit_type not in {
        "arc",
        "beat",
        "scene",
        "dialogue",
        "storylet",
        "clue",
        "reveal",
        "memory",
        "episode",
        "choice",
        "ending",
    }:
        _fail("gamepack_invalid", f"{context}.unit_type is unsupported")
    _non_empty_string(unit.get("title"), f"{context}.title")
    for field in (
        "prerequisite_ids",
        "effect_ids",
        "next_unit_ids",
        "asset_binding_ids",
    ):
        _identifier_array(unit.get(field), f"{context}.{field}")
    if unit_type == "choice":
        options = unit.get("options")
        if not isinstance(options, list) or len(options) < 2 or len(options) > 64:
            _fail("gamepack_invalid", f"{context}.options must be a bounded choice array")
        for index, raw_option in enumerate(options):
            option_context = f"{context}.options/{index}"
            option = _object(raw_option, option_context)
            _exact_keys(
                option,
                frozenset(
                    {
                        "id",
                        "label",
                        "next_unit_id",
                        "condition_ids",
                        "effect_ids",
                    }
                ),
                option_context,
            )
            _identifier(option.get("id"), f"{option_context}.id")
            _non_empty_string(option.get("label"), f"{option_context}.label")
            _identifier(
                option.get("next_unit_id"),
                f"{option_context}.next_unit_id",
            )
            for field in ("condition_ids", "effect_ids"):
                _identifier_array(option.get(field), f"{option_context}.{field}")
    elif unit_type == "ending":
        if unit.get("ending_kind") not in {"success", "failure", "neutral"}:
            _fail("gamepack_invalid", f"{context}.ending_kind is unsupported")
        if unit["next_unit_ids"]:
            _fail("gamepack_invalid", f"{context} ending has outgoing transitions")


def _validate_modules(value: object, *, game_id: str) -> dict[str, Any]:
    modules = _object(value, "gamepack.modules")
    _exact_keys(modules, _MODULE_FIELDS, "gamepack.modules")
    for collection in _MODULE_FIELDS:
        items = modules.get(collection)
        if not isinstance(items, list) or len(items) > GAMEPACK_SCHEMA_MAXIMA["module_collections"]:
            _fail(
                "gamepack_invalid",
                f"gamepack.modules.{collection} must be a bounded array",
            )
        source_ids: list[str] = []
        for index, raw in enumerate(items):
            context = f"gamepack.modules.{collection}/{index}"
            item = _object(raw, context)
            if collection == "world":
                fields = frozenset({"source", "module_type", "title", "records"})
            elif collection == "activities":
                fields = frozenset({"source", "title", "activities"})
            elif collection == "narrative":
                fields = frozenset({"source", "title", "entry_unit_ids", "units"})
            else:
                fields = frozenset({"source", "title", "systems"})
            _exact_keys(item, fields, context)
            expected_format = {
                "world": "world-forge.world_module",
                "activities": "world-forge.activity_module",
                "narrative": "world-forge.narrative_module",
                "systems": "world-forge.system_module",
            }[collection]
            _validate_projection_identity(
                item.get("source"),
                f"{context}.source",
                expected_format=expected_format,
            )
            _non_empty_string(item.get("title"), f"{context}.title")
            source_ids.append(item["source"]["id"])
            payload = (
                item["records"]
                if collection == "world"
                else item["units"]
                if collection == "narrative"
                else item[collection]
            )
            payload_maximum = (
                GAMEPACK_SCHEMA_MAXIMA["world_projected_payloads"]
                if collection == "world"
                else GAMEPACK_SCHEMA_MAXIMA["projected_payloads"]
            )
            if not isinstance(payload, list) or not payload or len(payload) > payload_maximum:
                _fail("gamepack_invalid", f"{context} payload must be non-empty")
            if collection == "world":
                module_type = item.get("module_type")
                if module_type not in _WORLD_PAYLOAD_FIELDS:
                    _fail("gamepack_invalid", f"{context}.module_type is unsupported")
                for record_index, record in enumerate(payload):
                    _validate_projected_world_record(
                        record,
                        f"{context}.records/{record_index}",
                        module_type=module_type,
                    )
            elif collection == "activities":
                for activity_index, activity in enumerate(payload):
                    _validate_projected_activity(
                        activity,
                        f"{context}.activities/{activity_index}",
                    )
            elif collection == "narrative":
                _identifier_array(
                    item.get("entry_unit_ids"),
                    f"{context}.entry_unit_ids",
                    allow_empty=False,
                )
                for unit_index, unit in enumerate(payload):
                    _validate_projected_narrative_unit(
                        unit,
                        f"{context}.units/{unit_index}",
                    )
            else:
                for system_index, system in enumerate(payload):
                    _validate_projected_system(
                        system,
                        f"{context}.systems/{system_index}",
                    )
            pseudo: dict[str, Any] = {
                "format": expected_format,
                "format_version": 1,
                "module_id": item["source"]["id"],
                "project_id": game_id,
                "title": item["title"],
                "extensions": [],
            }
            if collection == "world":
                pseudo["module_type"] = item["module_type"]
                pseudo[_WORLD_PAYLOAD_FIELDS[item["module_type"]]] = copy.deepcopy(item["records"])
            else:
                if collection == "activities":
                    pseudo["activities"] = [
                        {
                            **copy.deepcopy(activity),
                            "validation_profile": "compiled_projection",
                            "provenance": "compiled_projection",
                        }
                        for activity in payload
                    ]
                else:
                    pseudo[
                        {
                            "narrative": "units",
                            "systems": "systems",
                        }[collection]
                    ] = copy.deepcopy(payload)
                if collection == "narrative":
                    pseudo["entry_unit_ids"] = copy.deepcopy(item["entry_unit_ids"])
            pseudo["content_hash"] = _canonical_hash(pseudo)
            validate_creation_document(pseudo, expected_format=expected_format)
        if source_ids != sorted(source_ids, key=lambda item: item.encode("utf-8")):
            _fail(
                "gamepack_invalid",
                f"gamepack.modules.{collection} must use canonical source order",
            )
        if len({item.casefold() for item in source_ids}) != len(source_ids):
            _fail(
                "gamepack_invalid",
                f"gamepack.modules.{collection} contains duplicate source IDs",
            )
    return modules


def _validate_global_source_ids(
    modules: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    source_ids: dict[str, str] = {}
    for collection, records_field, kind in (
        ("activities", "activities", "activity"),
        ("systems", "systems", "system"),
        ("narrative", "units", "narrative unit"),
    ):
        for module in modules[collection]:
            for record in module[records_field]:
                key = record["id"].casefold()
                previous = source_ids.get(key)
                if previous is not None:
                    _fail(
                        "gamepack_invalid",
                        f"global source ID collision between {previous} and {kind}: {record['id']}",
                    )
                source_ids[key] = kind


def _validate_logic_source_bindings(
    modules: Mapping[str, Sequence[Mapping[str, Any]]],
    logic: Mapping[str, Any],
) -> None:
    activities = {
        activity["id"].casefold()
        for module in modules["activities"]
        for activity in module["activities"]
    }
    systems = {
        system["id"].casefold() for module in modules["systems"] for system in module["systems"]
    }
    narrative_options = {
        (unit["id"].casefold(), option["id"].casefold())
        for module in modules["narrative"]
        for unit in module["units"]
        if unit["unit_type"] == "choice"
        for option in unit["options"]
    }
    used_options: dict[tuple[str, str], str] = {}
    for action in logic["actions"]:
        for binding in action["source_bindings"]:
            source_id = binding["source_id"].casefold()
            if binding["kind"] == "activity":
                if source_id not in activities:
                    _fail(
                        "compiled_logic_invalid",
                        f"action {action['id']} binds unknown activity {binding['source_id']}",
                    )
            elif binding["kind"] == "system":
                if source_id not in systems:
                    _fail(
                        "compiled_logic_invalid",
                        f"action {action['id']} binds unknown system {binding['source_id']}",
                    )
            else:
                option_key = (source_id, binding["option_id"].casefold())
                if option_key not in narrative_options:
                    _fail(
                        "compiled_logic_invalid",
                        f"action {action['id']} binds unknown narrative option "
                        f"{binding['source_id']}/{binding['option_id']}",
                    )
                previous = used_options.get(option_key)
                if previous is not None:
                    _fail(
                        "compiled_logic_invalid",
                        f"narrative option {binding['source_id']}/"
                        f"{binding['option_id']} is ambiguously bound by "
                        f"{previous} and {action['id']}",
                    )
                used_options[option_key] = action["id"]
    if set(used_options) != narrative_options:
        missing = sorted(narrative_options - set(used_options))
        rendered = ", ".join(f"{unit}/{option}" for unit, option in missing)
        _fail(
            "compiled_logic_invalid",
            f"narrative options require exact action bindings: {rendered}",
        )


def _validate_asset_requirements(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > GAMEPACK_SCHEMA_MAXIMA["asset_requirements"]:
        _fail("gamepack_invalid", "asset_requirements must be a bounded array")
    bindings: list[str] = []
    checked: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        context = f"gamepack.asset_requirements/{index}"
        requirement = _object(raw, context)
        _exact_keys(requirement, _ASSET_REQUIREMENT_FIELDS, context)
        binding_id = _identifier(requirement.get("binding_id"), f"{context}.binding_id")
        if requirement.get("required") is not True:
            _fail(
                "gamepack_invalid",
                f"{context}.required must be true for a compiled runtime binding",
            )
        for field in ("accepted_formats", "roles", "usage_contexts"):
            values = _string_array(
                requirement.get(field),
                f"{context}.{field}",
                allow_empty=False,
                canonical_order=True,
            )
            if len(values) > GAMEPACK_SCHEMA_MAXIMA["id_arrays"]:
                _fail(
                    "gamepack_invalid",
                    f"{context}.{field} exceeds its schema maximum",
                )
        subjects = requirement.get("referencing_subjects")
        if (
            not isinstance(subjects, list)
            or not subjects
            or len(subjects) > GAMEPACK_SCHEMA_MAXIMA["asset_referencing_subjects"]
        ):
            _fail("gamepack_invalid", f"{context}.referencing_subjects must be non-empty")
        subject_keys: list[tuple[str, str]] = []
        for subject_index, raw_subject in enumerate(subjects):
            subject_context = f"{context}.referencing_subjects/{subject_index}"
            subject = _object(raw_subject, subject_context)
            _exact_keys(subject, _ASSET_SUBJECT_FIELDS, subject_context)
            subject_keys.append(
                (
                    _non_empty_string(subject.get("kind"), f"{subject_context}.kind"),
                    _identifier(subject.get("id"), f"{subject_context}.id"),
                )
            )
        if subject_keys != sorted(
            subject_keys,
            key=lambda item: (
                item[0].encode("utf-8"),
                item[1].encode("utf-8"),
            ),
        ):
            _fail("gamepack_invalid", f"{context}.referencing_subjects is not canonical")
        bindings.append(binding_id)
        checked.append(requirement)
    if bindings != sorted(bindings, key=lambda item: item.encode("utf-8")):
        _fail("gamepack_invalid", "asset_requirements must use canonical binding order")
    if len({item.casefold() for item in bindings}) != len(bindings):
        _fail("gamepack_invalid", "asset_requirements contain duplicate bindings")
    return checked


def _validate_runtime(value: object) -> dict[str, Any]:
    runtime = _object(value, "gamepack.runtime_requirements")
    _exact_keys(runtime, _RUNTIME_FIELDS, "gamepack.runtime_requirements")
    adapter = runtime.get("requested_adapter")
    if adapter is not None:
        _identifier(adapter, "gamepack.runtime_requirements.requested_adapter")
    accepted = runtime.get("accepted_logic_formats")
    if (
        not isinstance(accepted, list)
        or not 1 <= len(accepted) <= GAMEPACK_SCHEMA_MAXIMA["runtime_accepted_logic_formats"]
    ):
        _fail("gamepack_invalid", "runtime accepted_logic_formats must be non-empty")
    accepts_gamepack = False
    accepted_names: list[str] = []
    for index, raw_item in enumerate(accepted):
        context = f"gamepack.runtime_requirements.accepted_logic_formats/{index}"
        item = _object(raw_item, context)
        _exact_keys(item, _ACCEPTED_LOGIC_FORMAT_FIELDS, context)
        format_name = _non_empty_string(item.get("format"), f"{context}.format")
        versions = item.get("versions")
        if not isinstance(versions, list) or not versions:
            _fail("gamepack_invalid", f"{context}.versions must be non-empty")
        checked_versions = [
            _integer(version, f"{context}.versions/{version_index}", minimum=1)
            for version_index, version in enumerate(versions)
        ]
        if checked_versions != sorted(set(checked_versions)):
            _fail(
                "gamepack_invalid",
                f"{context}.versions must be unique and canonical",
            )
        accepted_names.append(format_name)
        if format_name == GAMEPACK_FORMAT and GAMEPACK_VERSION in checked_versions:
            accepts_gamepack = True
    if accepted_names != sorted(accepted_names, key=lambda item: item.encode("utf-8")):
        _fail("gamepack_invalid", "runtime accepted_logic_formats is not canonical")
    if len({item.casefold() for item in accepted_names}) != len(accepted_names):
        _fail("gamepack_invalid", "runtime accepted_logic_formats contains duplicates")
    if not accepts_gamepack:
        _fail(
            "gamepack_invalid",
            "runtime requirements do not accept this gamepack format/version",
        )
    required = _string_array(
        runtime.get("required_features"),
        "gamepack.runtime_requirements.required_features",
        allow_empty=False,
        tokens=True,
        canonical_order=True,
    )
    optional = _string_array(
        runtime.get("optional_features"),
        "gamepack.runtime_requirements.optional_features",
        tokens=True,
        canonical_order=True,
    )
    if (
        len(required) > GAMEPACK_SCHEMA_MAXIMA["runtime_features"]
        or len(optional) > GAMEPACK_SCHEMA_MAXIMA["runtime_features"]
    ):
        _fail("gamepack_invalid", "runtime feature arrays exceed their schema maximum")
    if {item.casefold() for item in required}.intersection(item.casefold() for item in optional):
        _fail("gamepack_invalid", "required and optional runtime features overlap")
    presentation = _object(
        runtime.get("presentation"),
        "gamepack.runtime_requirements.presentation",
    )
    _exact_keys(
        presentation,
        _RUNTIME_PRESENTATION_FIELDS,
        "gamepack.runtime_requirements.presentation",
    )
    if presentation.get("mode") not in {
        "text",
        "2d",
        "2_5d",
        "3d",
        "mixed",
        "vr",
        "ar",
    }:
        _fail("gamepack_invalid", "runtime presentation mode is unsupported")
    for field in ("camera", "perspective", "renderer"):
        _non_empty_string(
            presentation.get(field),
            f"gamepack.runtime_requirements.presentation.{field}",
        )
    platforms = runtime.get("platform_matrix")
    if (
        not isinstance(platforms, list)
        or not 1 <= len(platforms) <= GAMEPACK_SCHEMA_MAXIMA["runtime_platform_matrix"]
    ):
        _fail("gamepack_invalid", "runtime platform_matrix must be non-empty")
    platform_ids: list[str] = []
    for index, raw_platform in enumerate(platforms):
        context = f"gamepack.runtime_requirements.platform_matrix/{index}"
        platform = _object(raw_platform, context)
        _exact_keys(platform, _PLATFORM_FIELDS, context)
        platform_id = platform.get("platform_id")
        expected_platform = _SUPPORTED_PLATFORM_TOKENS.get(platform_id)
        if expected_platform is None:
            _fail("gamepack_invalid", f"{context}.platform_id is unsupported")
        expected_family, expected_architecture = expected_platform
        if (
            platform.get("platform_family") != expected_family
            or platform.get("architecture") != expected_architecture
            or platform.get("backend") != "backend:unspecified"
            or platform.get("renderer") != presentation["renderer"]
        ):
            _fail("gamepack_invalid", f"{context} is not an exact platform projection")
        platform_ids.append(platform_id)
    if platform_ids != sorted(platform_ids, key=lambda item: item.encode("utf-8")):
        _fail("gamepack_invalid", "runtime platform_matrix is not canonical")
    if len(set(platform_ids)) != len(platform_ids):
        _fail("gamepack_invalid", "runtime platform_matrix contains duplicates")
    for field in ("input_capabilities", "asset_formats"):
        checked_values = _string_array(
            runtime.get(field),
            f"gamepack.runtime_requirements.{field}",
            allow_empty=field == "asset_formats",
            tokens=True,
            canonical_order=True,
        )
        if len(checked_values) > GAMEPACK_SCHEMA_MAXIMA["runtime_features"]:
            _fail("gamepack_invalid", f"runtime {field} exceeds its schema maximum")
    for field in ("save_expected", "replay_expected"):
        if not isinstance(runtime.get(field), bool):
            _fail("gamepack_invalid", f"runtime {field} must be boolean")
    _non_empty_string(
        runtime.get("packaging_target"),
        "gamepack.runtime_requirements.packaging_target",
    )
    return runtime


def _validate_execution_semantics(value: object) -> None:
    semantics = _object(value, "gamepack.logic.execution_semantics")
    _exact_keys(
        semantics,
        frozenset(EXECUTION_SEMANTICS),
        "gamepack.logic.execution_semantics",
    )
    if semantics != EXECUTION_SEMANTICS:
        _fail(
            "execution_semantics_unsupported",
            "logic.execution_semantics must equal the exact compiler-owned v1 policy",
        )


def _validate_analysis_requirements(
    value: object,
    *,
    modules: Mapping[str, object],
    logic: Mapping[str, object],
) -> dict[str, Any]:
    requirement = _object(value, "gamepack.analysis_requirements")
    _exact_keys(
        requirement,
        _ANALYSIS_REQUIREMENT_FIELDS,
        "gamepack.analysis_requirements",
    )
    profile = requirement.get("profile")
    if profile not in ANALYZERS:
        _fail(
            "analysis_profile_unsupported",
            "analysis_requirements.profile is not a compiler-owned profile",
        )
    analyzer_id, analyzer_version = ANALYZERS[str(profile)]
    if (
        requirement.get("analyzer_id") != analyzer_id
        or requirement.get("analyzer_version") != analyzer_version
        or isinstance(requirement.get("analyzer_version"), bool)
    ):
        _fail(
            "analysis_analyzer_unsupported",
            "analysis_requirements analyzer identity/version is unsupported",
        )
    reason = requirement.get("reason_code")
    expected_reason = "analysis_profile_unsupported" if profile == "unsupported" else None
    if reason != expected_reason:
        _fail(
            "analysis_requirements_invalid",
            "analysis_requirements reason_code is inconsistent",
        )
    limits = _object(requirement.get("limits"), "gamepack.analysis_requirements.limits")
    _exact_keys(limits, _ANALYSIS_LIMIT_FIELDS, "gamepack.analysis_requirements.limits")
    if limits != ANALYSIS_LIMITS:
        _fail(
            "analysis_limits_unsupported",
            "analysis_requirements.limits must equal the exact v1 bounds",
        )
    _sha256(
        requirement.get("content_hash"),
        "gamepack.analysis_requirements.content_hash",
    )
    if requirement.get("content_hash") != _canonical_hash(requirement):
        _fail(
            "analysis_requirements_hash_mismatch",
            "analysis_requirements.content_hash does not match",
        )
    expected = analysis_requirements_for(modules, logic)
    if requirement != expected:
        _fail(
            "analysis_requirements_invalid",
            "analysis requirements do not exactly derive from compiled structure",
        )
    return requirement


def _validate_gamepack_document_uncached(value: object) -> dict[str, Any]:
    try:
        document = _object(value, "gamepack")
        _preflight_runtime_document(document, "gamepack")
        _validate_json_structure(document, context="gamepack")
        _reject_logic_unsafe_content(document, context="gamepack")
        _exact_keys(document, _GAMEPACK_FIELDS, "gamepack")
        if document.get("format") != GAMEPACK_FORMAT:
            _fail("gamepack_format_invalid", f"format must be {GAMEPACK_FORMAT}")
        if document.get("format_version") != GAMEPACK_VERSION or isinstance(
            document.get("format_version"), bool
        ):
            _fail("gamepack_version_unsupported", "format_version must be 1")
        if document.get("content_hash") != _canonical_hash(document):
            _fail("content_hash_mismatch", "gamepack content_hash does not match")
        game = _object(document.get("game"), "gamepack.game")
        _exact_keys(game, _GAME_FIELDS, "gamepack.game")
        game_id = _identifier(game.get("id"), "gamepack.game.id")
        _non_empty_string(game.get("title"), "gamepack.game.title")
        _semver(game.get("version"), "gamepack.game.version")
        _locale(game.get("default_locale"), "gamepack.game.default_locale")
        source = _object(document.get("source"), "gamepack.source")
        _exact_keys(source, _SOURCE_FIELDS, "gamepack.source")
        project_source = _checked_identity(
            source.get("project"),
            "gamepack.source.project",
            allowed_formats=frozenset({CREATION_PROJECT_FORMAT}),
        )
        profile_source = _checked_identity(
            source.get("profile"),
            "gamepack.source.profile",
            allowed_formats=frozenset({CREATION_PROFILE_FORMAT}),
        )
        manifest_source = _checked_identity(
            source.get("source_manifest"),
            "gamepack.source.source_manifest",
            allowed_formats=frozenset({CREATION_SOURCE_MANIFEST_FORMAT}),
        )
        if project_source["id"] != game_id or manifest_source["id"] != game_id:
            _fail(
                "gamepack_invalid",
                "project and source-manifest identities must match game.id",
            )
        logic_sources = source.get("logic_modules")
        if not isinstance(logic_sources, list) or len(logic_sources) != 1:
            _fail("gamepack_invalid", "source.logic_modules must contain exactly one identity")
        logic_source = _checked_identity(
            logic_sources[0],
            "gamepack.source.logic_modules/0",
            allowed_formats=frozenset({LOGIC_MODULE_FORMAT}),
        )
        modules = _validate_modules(document.get("modules"), game_id=game_id)
        _validate_global_source_ids(modules)
        logic = _object(document.get("logic"), "gamepack.logic")
        _exact_keys(logic, _LOGIC_FIELDS, "gamepack.logic")
        checked_logic_source = _checked_identity(
            logic.get("source"),
            "gamepack.logic.source",
            allowed_formats=frozenset({LOGIC_MODULE_FORMAT}),
        )
        if checked_logic_source != logic_source:
            _fail("gamepack_invalid", "logic source identity is inconsistent")
        _validate_execution_semantics(logic.get("execution_semantics"))
        _validate_compiled_source_logic(logic, game_id=game_id)
        if logic.get("narrative_cursor") is None and logic.get("narrative_transitions") != []:
            _fail(
                "narrative_transition_invalid",
                "narrative transitions require a compiler-owned narrative cursor",
            )
        if modules["narrative"] and logic["narrative_cursor"] is None:
            _validate_authored_narrative_projection(modules["narrative"])
        _validate_logic_source_bindings(modules, logic)
        if modules["narrative"]:
            if logic["narrative_cursor"] is None:
                expected_cursor, expected_transitions = None, []
            else:
                expected_cursor, expected_transitions = _derive_narrative_runtime(
                    modules["narrative"],
                    logic,
                )
        else:
            expected_cursor, expected_transitions = None, []
        if (
            logic["narrative_cursor"] != expected_cursor
            or logic["narrative_transitions"] != expected_transitions
        ):
            _fail(
                "compiled_logic_invalid",
                "narrative cursor/transitions do not exactly derive from projected units",
            )
        _validate_analysis_requirements(
            document.get("analysis_requirements"),
            modules=modules,
            logic=logic,
        )
        assets = _validate_asset_requirements(document.get("asset_requirements"))
        runtime = _validate_runtime(document.get("runtime_requirements"))
        presentation = _object(document.get("presentation"), "gamepack.presentation")
        _validate_presentation(presentation)
        if (
            presentation.get("mode") != runtime["presentation"]["mode"]
            or presentation.get("camera") != runtime["presentation"]["camera"]
            or presentation.get("perspective") != runtime["presentation"]["perspective"]
        ):
            _fail("gamepack_invalid", "presentation contract is inconsistent")
        expected_required_features = sorted(
            {
                feature
                for mechanic in logic["mechanics"]
                for feature in mechanic["required_feature_ids"]
            },
            key=lambda item: item.encode("utf-8"),
        )
        if runtime["required_features"] != expected_required_features:
            _fail(
                "gamepack_invalid",
                "runtime required_features must equal exact mechanic requirements",
            )
        expected_assets = _asset_requirements(modules, logic, runtime)
        if assets != expected_assets:
            _fail(
                "gamepack_invalid",
                "asset_requirements do not exactly match derived runtime bindings",
            )
        localization = _object(document.get("localization"), "gamepack.localization")
        _exact_keys(localization, _LOCALIZATION_FIELDS, "gamepack.localization")
        source_locale = _locale(
            localization.get("source_locale"),
            "gamepack.localization.source_locale",
        )
        supported_locales = _string_array(
            localization.get("supported_locales"),
            "gamepack.localization.supported_locales",
            allow_empty=False,
            canonical_order=True,
        )
        if len(supported_locales) > GAMEPACK_SCHEMA_MAXIMA["localization_supported_locales"]:
            _fail(
                "gamepack_invalid",
                "localization.supported_locales exceeds its schema maximum",
            )
        for index, locale in enumerate(supported_locales):
            _locale(locale, f"gamepack.localization.supported_locales/{index}")
        if source_locale.casefold() not in {locale.casefold() for locale in supported_locales}:
            _fail(
                "gamepack_invalid",
                "localization.supported_locales must contain source_locale",
            )
        if localization.get("externalized_text") is not True:
            _fail(
                "gamepack_invalid",
                "gamepack v1 requires externalized localization text",
            )
        presentation_localization = presentation["localization"]
        if (
            source_locale != game["default_locale"]
            or source_locale != presentation_localization["source_locale"]
            or supported_locales != presentation_localization["supported_locales"]
            or localization["externalized_text"] != presentation_localization["externalized_text"]
        ):
            _fail(
                "gamepack_invalid",
                "localization is inconsistent with game and presentation",
            )
        references = localization.get("references")
        if (
            not isinstance(references, list)
            or not references
            or len(references) > GAMEPACK_SCHEMA_MAXIMA["localization_references"]
        ):
            _fail("gamepack_invalid", "localization.references must be non-empty")
        reference_keys: list[str] = []
        for index, raw_reference in enumerate(references):
            context = f"gamepack.localization.references/{index}"
            reference = _object(raw_reference, context)
            _exact_keys(reference, _LOCALIZATION_REFERENCE_FIELDS, context)
            reference_keys.append(_non_empty_string(reference.get("key"), f"{context}.key"))
        if reference_keys != sorted(
            reference_keys,
            key=lambda item: item.encode("utf-8"),
        ):
            _fail("gamepack_invalid", "localization references are not canonical")
        if references != _localization_references(game, modules):
            _fail(
                "gamepack_invalid",
                "localization.references do not exactly match projected source text",
            )
        mechanics = document.get("mechanic_requirements")
        if (
            not isinstance(mechanics, list)
            or not mechanics
            or len(mechanics) > GAMEPACK_SCHEMA_MAXIMA["mechanic_requirements"]
        ):
            _fail("gamepack_invalid", "mechanic_requirements must be non-empty")
        required_features = set(runtime["required_features"])
        logic_mechanics = {item["id"]: item for item in logic["mechanics"]}
        for index, raw_mechanic in enumerate(mechanics):
            context = f"gamepack.mechanic_requirements/{index}"
            mechanic = _object(raw_mechanic, context)
            _exact_keys(mechanic, _MECHANIC_REQUIREMENT_FIELDS, context)
            mechanic_id = _identifier(mechanic.get("mechanic_id"), f"{context}.mechanic_id")
            source_mechanic = logic_mechanics.get(mechanic_id)
            if source_mechanic is None:
                _fail("gamepack_invalid", f"{context} references an unknown mechanic")
            if mechanic != {
                "mechanic_id": source_mechanic["id"],
                "core_verb_id": source_mechanic["core_verb_id"],
                "action_id": source_mechanic["action_id"],
                "required_feature_ids": source_mechanic["required_feature_ids"],
            }:
                _fail("gamepack_invalid", f"{context} does not match its logic mechanic")
            if not set(mechanic["required_feature_ids"]).issubset(required_features):
                _fail(
                    "gamepack_invalid",
                    f"{context} hides a required mechanic feature",
                )
        if mechanics != _mechanic_requirements(logic):
            _fail(
                "gamepack_invalid",
                "mechanic_requirements do not exactly match compiled mechanics",
            )
        binding_ids = {item["binding_id"] for item in assets}
        logic_bindings = {
            binding for mechanic in logic["mechanics"] for binding in mechanic["asset_binding_ids"]
        } | {
            binding for hook in logic["presentation_hooks"] for binding in hook["asset_binding_ids"]
        }
        if not logic_bindings.issubset(binding_ids):
            _fail("gamepack_invalid", "asset requirements omit a logic binding")
        provenance = document.get("provenance")
        if (
            not isinstance(provenance, list)
            or not provenance
            or len(provenance) > GAMEPACK_SCHEMA_MAXIMA["provenance"]
        ):
            _fail("gamepack_invalid", "provenance must be non-empty")
        checked_provenance: list[dict[str, Any]] = []
        for index, raw_entry in enumerate(provenance):
            context = f"gamepack.provenance/{index}"
            entry = _object(raw_entry, context)
            _exact_keys(entry, _PROVENANCE_FIELDS, context)
            if entry.get("kind") != "compiled_from":
                _fail("gamepack_invalid", f"{context}.kind is unsupported")
            subject = _checked_identity(entry.get("subject"), f"{context}.subject")
            checked_provenance.append({"kind": "compiled_from", "subject": subject})
        provenance_subjects = [
            project_source,
            profile_source,
            manifest_source,
            *source["logic_modules"],
        ]
        for collection in modules.values():
            provenance_subjects.extend(module["source"] for module in collection)
        expected_provenance = [
            {"kind": "compiled_from", "subject": subject}
            for subject in sorted(provenance_subjects, key=_identity_sort_key)
        ]
        if checked_provenance != expected_provenance:
            _fail(
                "gamepack_invalid",
                "provenance must exactly and canonically cover every source identity",
            )
        extensions = document.get("registered_extensions")
        _extensions(
            extensions,
            "gamepack.registered_extensions",
            {},
            maximum=64,
        )
        _runtime_string_tree(document)
    except GamepackError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("gamepack_invalid", str(exc))
    return copy.deepcopy(document)


def validate_gamepack_document(value: object) -> dict[str, Any]:
    return memoize_document_validation(
        "validate_gamepack_document",
        value,
        _validate_gamepack_document_uncached,
    )


def validate_gamepack(
    value: object,
    *,
    source_project: LoadedCreationProject,
) -> dict[str, Any]:
    checked = validate_gamepack_document(value)
    rebuilt = _build_gamepack_document(source_project)
    if canonical_json_bytes(rebuilt) != canonical_json_bytes(checked):
        _fail(
            "source_binding_mismatch",
            "gamepack does not exactly rebuild from the supplied creation project",
        )
    return checked


def serialize_gamepack(value: object) -> bytes:
    return canonical_json_bytes(validate_gamepack_document(value))


def _resolve_project_path(project_path: str | Path) -> Path:
    source = Path(os.path.abspath(os.fspath(project_path)))
    try:
        info = source.lstat()
    except OSError:
        return source
    if stat.S_ISLNK(info.st_mode):
        _fail(
            "creation_project_root_linked",
            "Creation project root contains a symbolic link or reparse point",
        )
    if stat.S_ISDIR(info.st_mode):
        return source / "project.json"
    return source


def load_game_source_project(project_path: str | Path) -> LoadedCreationProject:
    """Securely load either a creation-project file or its containing root."""

    try:
        return load_creation_project(_resolve_project_path(project_path))
    except CreationContractError as exc:
        reason_code = (
            "source_project_invalid"
            if exc.reason_code == "creation_contract_invalid"
            else exc.reason_code
        )
        _fail(reason_code, exc.detail)
    except OSError:
        _fail(
            "creation_project_inspection_failed",
            "Creation project could not be inspected safely",
        )


def preflight_game_artifact_output(path: str | Path) -> Path:
    try:
        destination = Path(os.path.abspath(path))
        try:
            info = path_file_stat(destination)
        except FileNotFoundError:
            return destination
        except OSError as exc:
            _fail("output_preflight_failed", f"could not inspect {path}: {exc}")
        if is_link_or_reparse(info):
            _fail(
                "output_preflight_failed",
                f"output is a symbolic link or reparse point: {path}",
            )
        _fail("output_exists", f"refusing to overwrite {path}")
    except AssetContractError as exc:
        _fail("output_preflight_failed", str(exc))


def _published_artifact(path: Path, document: Mapping[str, Any]) -> PublishedGameArtifact:
    try:
        parent_before = path_file_stat(path.parent)
        info_before = path_file_stat(path)
    except OSError as exc:
        _fail("output_identity_failed", f"could not inspect published output {path}: {exc}")
    if is_link_or_reparse(parent_before) or not stat.S_ISDIR(parent_before.st_mode):
        _fail("output_identity_failed", f"published output parent is unsafe: {path.parent}")
    if (
        is_link_or_reparse(info_before)
        or not stat.S_ISREG(info_before.st_mode)
        or info_before.st_nlink != 1
    ):
        _fail("output_identity_failed", f"published output is not standalone: {path}")
    parent_identity = (parent_before.st_dev, parent_before.st_ino)
    identity = (info_before.st_dev, info_before.st_ino)
    try:
        current = read_creation_object(path)
        parent_after = path_file_stat(path.parent)
        info_after = path_file_stat(path)
    except (CreationContractError, OSError) as exc:
        _fail("output_identity_failed", f"could not verify published output {path}: {exc}")
    if (
        is_link_or_reparse(parent_after)
        or not stat.S_ISDIR(parent_after.st_mode)
        or (parent_after.st_dev, parent_after.st_ino) != parent_identity
        or not stat.S_ISREG(info_after.st_mode)
        or is_link_or_reparse(info_after)
        or info_after.st_nlink != 1
        or (info_after.st_dev, info_after.st_ino) != identity
    ):
        _fail("output_identity_failed", f"published output identity changed: {path}")
    if canonical_json_bytes(current) != canonical_json_bytes(document):
        _fail("output_identity_failed", f"published output content changed: {path}")
    return PublishedGameArtifact(
        path=path,
        parent_identity=parent_identity,
        identity=identity,
        content_hash=str(document["content_hash"]),
        format=str(document["format"]),
    )


def publish_gamepack(path: str | Path, value: object) -> PublishedGameArtifact:
    document = validate_gamepack_document(value)
    destination = preflight_game_artifact_output(path)
    try:
        write_json_atomic(destination, document, durable_parent=True)
    except AssetContractError as exc:
        reason = "output_exists" if "overwrite" in str(exc).casefold() else "output_publish_failed"
        _fail(reason, str(exc))
    return _published_artifact(destination, document)


def compile_game_project(
    project_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    project = load_game_source_project(project_path)
    document = build_gamepack(project)
    publish_gamepack(output_path, document)
    return document


def load_gamepack(
    gamepack_path: str | Path,
    *,
    source_project: LoadedCreationProject | None = None,
) -> dict[str, Any]:
    try:
        value = read_creation_object(gamepack_path)
    except CreationContractError as exc:
        _fail("invalid_json", str(exc))
    if source_project is None:
        return validate_gamepack_document(value)
    return validate_gamepack(value, source_project=source_project)


def _evidence(value: object, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > CAPABILITY_LEDGER_SCHEMA_MAXIMA["evidence"]:
        _fail("capability_ledger_invalid", f"{context} must be a bounded array")
    checked: list[dict[str, Any]] = []
    evidence_ids: list[str] = []
    for index, raw in enumerate(value):
        item_context = f"{context}/{index}"
        item = _object(raw, item_context)
        _exact_keys(item, _EVIDENCE_FIELDS, item_context)
        evidence_ids.append(_identifier(item.get("evidence_id"), f"{item_context}.evidence_id"))
        _sha256(item.get("content_hash"), f"{item_context}.content_hash")
        checked.append(item)
    if evidence_ids != sorted(evidence_ids, key=lambda item: item.encode("utf-8")):
        _fail("capability_ledger_invalid", f"{context} is not canonical")
    if len({item.casefold() for item in evidence_ids}) != len(evidence_ids):
        _fail("capability_ledger_invalid", f"{context} contains duplicate evidence")
    return checked


def _capability_extension(value: object, context: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        _fail("capability_ledger_invalid", f"{context} must be an extension or null")
    try:
        _extensions([value], context, {}, maximum=1)
    except CreationContractError as exc:
        _fail("capability_ledger_invalid", str(exc))
    return value


def _validate_capability_claim(
    entry: Mapping[str, Any],
    *,
    context: str,
    adapter_status: object,
    tests: Sequence[Mapping[str, Any]],
    native: Sequence[Mapping[str, Any]],
) -> None:
    status = entry.get("status")
    if status not in _CAPABILITY_STATUSES:
        _fail("capability_ledger_invalid", f"{context}.status is unsupported")
    reason = _identifier(entry.get("reason_code"), f"{context}.reason_code")
    missing = _string_array(
        entry.get("missing_feature_ids"),
        f"{context}.missing_feature_ids",
        tokens=True,
        canonical_order=True,
    )
    if len(missing) > CAPABILITY_LEDGER_SCHEMA_MAXIMA["id_arrays"]:
        _fail(
            "capability_ledger_invalid",
            f"{context}.missing_feature_ids exceeds its schema maximum",
        )
    extension = _capability_extension(entry.get("extension"), f"{context}.extension")
    if status == "authoring_only":
        exact = (
            reason == "adapter_not_evaluated"
            and not missing
            and extension is None
            and not tests
            and not native
        )
    elif status == "blocked":
        exact = (
            reason == "missing_required_capability"
            and bool(missing)
            and extension is None
            and not tests
            and not native
        )
    elif status == "supported_current":
        exact = (
            reason == "adapter_verified"
            and not missing
            and extension is None
            and bool(tests)
            and bool(native)
            and adapter_status == "verified"
        )
    else:
        exact = (
            reason == "game_extension_verified"
            and not missing
            and extension is not None
            and bool(tests)
            and bool(native)
            and adapter_status == "verified"
        )
    if not exact:
        _fail(
            "capability_status_inconsistent",
            f"{context} fields are inconsistent with status {status}",
        )


def build_authoring_capability_ledger(gamepack: object) -> dict[str, Any]:
    checked = validate_gamepack_document(gamepack)
    states = {state["id"]: state for state in checked["logic"]["state_schema"]}
    adapter_id = checked["runtime_requirements"]["requested_adapter"]
    mechanics: list[dict[str, Any]] = []
    for mechanic in checked["logic"]["mechanics"]:
        saved_states = sorted(
            (
                state_id
                for state_id in mechanic["authoritative_state_ids"]
                if states[state_id]["persistence"] == "saved"
            ),
            key=lambda item: item.encode("utf-8"),
        )
        mechanics.append(
            {
                "mechanic_id": mechanic["id"],
                "core_verb_id": mechanic["core_verb_id"],
                "runtime_action_id": mechanic["action_id"],
                "authoritative_state_ids": copy.deepcopy(mechanic["authoritative_state_ids"]),
                "condition_ids": copy.deepcopy(mechanic["condition_ids"]),
                "rule_ids": copy.deepcopy(mechanic["rule_ids"]),
                "effect_ids": copy.deepcopy(mechanic["effect_ids"]),
                "presentation_hook_ids": copy.deepcopy(mechanic["presentation_hook_ids"]),
                "asset_binding_ids": copy.deepcopy(mechanic["asset_binding_ids"]),
                "save_replay": {
                    "state_ids": saved_states,
                    "event_ids": copy.deepcopy(mechanic["event_ids"]),
                },
                "test_evidence": [],
                "native_evidence": [],
                "status": "authoring_only",
                "reason_code": "adapter_not_evaluated",
                "missing_feature_ids": [],
                "extension": None,
            }
        )
    features = [
        {
            "feature_id": feature,
            "status": "authoring_only",
            "reason_code": "adapter_not_evaluated",
            "test_evidence": [],
            "native_evidence": [],
            "missing_feature_ids": [],
            "extension": None,
        }
        for feature in checked["runtime_requirements"]["required_features"]
    ]
    document: dict[str, Any] = {
        "format": CAPABILITY_LEDGER_FORMAT,
        "format_version": CAPABILITY_LEDGER_VERSION,
        "ledger_id": f"{checked['game']['id']}_authoring_capabilities",
        "gamepack": {
            "format": checked["format"],
            "format_version": checked["format_version"],
            "id": checked["game"]["id"],
            "content_hash": checked["content_hash"],
        },
        "adapter": {
            "adapter_id": adapter_id,
            "adapter_version": None,
            "status": "declared" if adapter_id is not None else "absent",
        },
        "mechanics": mechanics,
        "features": features,
    }
    _preflight_runtime_document(document, "capability ledger")
    document["content_hash"] = _canonical_hash(document)
    return validate_capability_ledger_document(document, gamepack=checked)


def _validate_capability_ledger_structure_impl(
    value: object,
    *,
    gamepack: object | None = None,
    allow_verified_claims: bool,
) -> dict[str, Any]:
    try:
        document = _object(value, "capability ledger")
        _preflight_runtime_document(document, "capability ledger")
        _validate_json_structure(document, context="capability ledger")
        _reject_logic_unsafe_content(document, context="capability ledger")
        _exact_keys(document, _LEDGER_FIELDS, "capability ledger")
        if document.get("format") != CAPABILITY_LEDGER_FORMAT:
            _fail(
                "capability_ledger_invalid",
                f"format must be {CAPABILITY_LEDGER_FORMAT}",
            )
        if document.get("format_version") != CAPABILITY_LEDGER_VERSION or isinstance(
            document.get("format_version"), bool
        ):
            _fail("capability_ledger_invalid", "format_version must be 1")
        _identifier(document.get("ledger_id"), "capability ledger.ledger_id")
        if document.get("content_hash") != _canonical_hash(document):
            _fail("content_hash_mismatch", "capability ledger content_hash does not match")
        gamepack_identity = _checked_identity(
            document.get("gamepack"),
            "capability ledger.gamepack",
            allowed_formats=frozenset({GAMEPACK_FORMAT}),
        )
        adapter = _object(document.get("adapter"), "capability ledger.adapter")
        _exact_keys(adapter, _ADAPTER_FIELDS, "capability ledger.adapter")
        adapter_id = adapter.get("adapter_id")
        adapter_version = adapter.get("adapter_version")
        if adapter_id is not None:
            _identifier(adapter_id, "capability ledger.adapter.adapter_id")
        if adapter_version is not None:
            checked_adapter_version = _non_empty_string(
                adapter_version,
                "capability ledger.adapter.adapter_version",
            )
            if len(checked_adapter_version) > CAPABILITY_LEDGER_SCHEMA_MAXIMA["adapter_version"]:
                _fail(
                    "capability_ledger_invalid",
                    "capability ledger.adapter.adapter_version exceeds 64 characters",
                )
        if adapter.get("status") not in {"absent", "declared", "verified"}:
            _fail("capability_ledger_invalid", "adapter.status is unsupported")
        if (
            (
                adapter["status"] == "absent"
                and (adapter_id is not None or adapter_version is not None)
            )
            or (
                adapter["status"] == "declared"
                and (adapter_id is None or adapter_version is not None)
            )
            or (adapter["status"] == "verified" and (adapter_id is None or adapter_version is None))
        ):
            _fail(
                "capability_ledger_invalid",
                "adapter identity/version is inconsistent with adapter.status",
            )
        mechanics = document.get("mechanics")
        if (
            not isinstance(mechanics, list)
            or not 1 <= len(mechanics) <= CAPABILITY_LEDGER_SCHEMA_MAXIMA["mechanics"]
        ):
            _fail("capability_ledger_invalid", "mechanics must be non-empty")
        mechanic_ids: list[str] = []
        for index, raw in enumerate(mechanics):
            context = f"capability ledger.mechanics/{index}"
            mechanic = _object(raw, context)
            _exact_keys(mechanic, _LEDGER_MECHANIC_FIELDS, context)
            mechanic_ids.append(_identifier(mechanic.get("mechanic_id"), f"{context}.mechanic_id"))
            _identifier(mechanic.get("core_verb_id"), f"{context}.core_verb_id")
            _identifier(
                mechanic.get("runtime_action_id"),
                f"{context}.runtime_action_id",
            )
            for field in (
                "authoritative_state_ids",
                "condition_ids",
                "rule_ids",
                "effect_ids",
                "presentation_hook_ids",
                "asset_binding_ids",
            ):
                identifiers = _identifier_array(
                    mechanic.get(field),
                    f"{context}.{field}",
                )
                if len(identifiers) > CAPABILITY_LEDGER_SCHEMA_MAXIMA["id_arrays"]:
                    _fail(
                        "capability_ledger_invalid",
                        f"{context}.{field} exceeds its schema maximum",
                    )
            save_replay = _object(mechanic.get("save_replay"), f"{context}.save_replay")
            _exact_keys(save_replay, _SAVE_REPLAY_FIELDS, f"{context}.save_replay")
            for field in ("state_ids", "event_ids"):
                identifiers = _identifier_array(
                    save_replay.get(field),
                    f"{context}.save_replay.{field}",
                )
                if len(identifiers) > CAPABILITY_LEDGER_SCHEMA_MAXIMA["id_arrays"]:
                    _fail(
                        "capability_ledger_invalid",
                        f"{context}.save_replay.{field} exceeds its schema maximum",
                    )
            tests = _evidence(mechanic.get("test_evidence"), f"{context}.test_evidence")
            native = _evidence(
                mechanic.get("native_evidence"),
                f"{context}.native_evidence",
            )
            _validate_capability_claim(
                mechanic,
                context=context,
                adapter_status=adapter.get("status"),
                tests=tests,
                native=native,
            )
        if mechanic_ids != sorted(mechanic_ids, key=lambda item: item.encode("utf-8")):
            _fail("capability_ledger_invalid", "mechanics is not canonical")
        if len({item.casefold() for item in mechanic_ids}) != len(mechanic_ids):
            _fail("capability_ledger_invalid", "mechanics contains duplicate IDs")
        features = document.get("features")
        if (
            not isinstance(features, list)
            or not 1 <= len(features) <= CAPABILITY_LEDGER_SCHEMA_MAXIMA["features"]
        ):
            _fail("capability_ledger_invalid", "features must be non-empty")
        feature_ids: list[str] = []
        for index, raw in enumerate(features):
            context = f"capability ledger.features/{index}"
            feature = _object(raw, context)
            _exact_keys(feature, _LEDGER_FEATURE_FIELDS, context)
            feature_id = _non_empty_string(
                feature.get("feature_id"),
                f"{context}.feature_id",
            )
            _string_array(
                [feature_id],
                f"{context}.feature_id",
                allow_empty=False,
                tokens=True,
            )
            feature_ids.append(feature_id)
            tests = _evidence(feature.get("test_evidence"), f"{context}.test_evidence")
            native = _evidence(feature.get("native_evidence"), f"{context}.native_evidence")
            _validate_capability_claim(
                feature,
                context=context,
                adapter_status=adapter.get("status"),
                tests=tests,
                native=native,
            )
        if feature_ids != sorted(feature_ids, key=lambda item: item.encode("utf-8")):
            _fail("capability_ledger_invalid", "features is not canonical")
        if len({item.casefold() for item in feature_ids}) != len(feature_ids):
            _fail("capability_ledger_invalid", "features contains duplicate IDs")
        if gamepack is not None:
            checked_gamepack = validate_gamepack_document(gamepack)
            expected_identity = {
                "format": checked_gamepack["format"],
                "format_version": checked_gamepack["format_version"],
                "id": checked_gamepack["game"]["id"],
                "content_hash": checked_gamepack["content_hash"],
            }
            if gamepack_identity != expected_identity:
                _fail(
                    "capability_ledger_binding_mismatch",
                    "ledger gamepack identity does not match the supplied gamepack",
                )
            expected_adapter_id = checked_gamepack["runtime_requirements"]["requested_adapter"]
            if adapter_id != expected_adapter_id:
                _fail(
                    "capability_ledger_binding_mismatch",
                    "ledger adapter identity does not match the gamepack request",
                )
            states = {state["id"]: state for state in checked_gamepack["logic"]["state_schema"]}
            expected_mechanics = {
                mechanic["id"]: {
                    "core_verb_id": mechanic["core_verb_id"],
                    "runtime_action_id": mechanic["action_id"],
                    "authoritative_state_ids": mechanic["authoritative_state_ids"],
                    "condition_ids": mechanic["condition_ids"],
                    "rule_ids": mechanic["rule_ids"],
                    "effect_ids": mechanic["effect_ids"],
                    "presentation_hook_ids": mechanic["presentation_hook_ids"],
                    "asset_binding_ids": mechanic["asset_binding_ids"],
                    "save_replay": {
                        "state_ids": sorted(
                            (
                                state_id
                                for state_id in mechanic["authoritative_state_ids"]
                                if states[state_id]["persistence"] == "saved"
                            ),
                            key=lambda item: item.encode("utf-8"),
                        ),
                        "event_ids": mechanic["event_ids"],
                    },
                }
                for mechanic in checked_gamepack["logic"]["mechanics"]
            }
            if set(mechanic_ids) != set(expected_mechanics):
                _fail(
                    "capability_ledger_binding_mismatch",
                    "ledger does not cover every exact gamepack mechanic",
                )
            for mechanic in mechanics:
                expected = expected_mechanics[mechanic["mechanic_id"]]
                for field, expected_value in expected.items():
                    if mechanic[field] != expected_value:
                        _fail(
                            "capability_ledger_binding_mismatch",
                            f"ledger mechanic {mechanic['mechanic_id']} has non-exact {field}",
                        )
            expected_features = checked_gamepack["runtime_requirements"]["required_features"]
            if feature_ids != expected_features:
                _fail(
                    "capability_ledger_binding_mismatch",
                    "ledger does not cover every required runtime feature",
                )
        if not allow_verified_claims and (
            adapter["status"] == "verified"
            or any(
                entry["status"] in {"supported_current", "game_extension_verified"}
                for entry in (*mechanics, *features)
            )
        ):
            _fail(
                "trusted_capability_resolver_required",
                "verified capability claims require trusted registries and evidence resolution",
            )
        _runtime_string_tree(document, context="capability ledger")
    except GamepackError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("capability_ledger_invalid", str(exc))
    return copy.deepcopy(document)


def validate_capability_ledger_structure(
    value: object,
    *,
    gamepack: object | None = None,
) -> dict[str, Any]:
    """Validate ledger shape and exact gamepack binding without trusting support claims."""

    return _validate_capability_ledger_structure_impl(
        value,
        gamepack=gamepack,
        allow_verified_claims=True,
    )


def validate_capability_ledger_document(
    value: object,
    *,
    gamepack: object | None = None,
) -> dict[str, Any]:
    """Validate authoring-only ledgers; verified claims fail closed by default."""

    return _validate_capability_ledger_structure_impl(
        value,
        gamepack=gamepack,
        allow_verified_claims=False,
    )


def _valid_string_frozenset(value: object) -> bool:
    return type(value) is frozenset and all(isinstance(item, str) for item in value)


def _valid_adapter_descriptor(value: object) -> bool:
    if type(value) is not RegisteredRuntimeAdapter:
        return False
    descriptor = value
    return (
        isinstance(descriptor.adapter_id, str)
        and isinstance(descriptor.adapter_version, str)
        and type(descriptor.accepted_logic_formats) is tuple
        and all(
            type(item) is tuple
            and len(item) == 2
            and isinstance(item[0], str)
            and type(item[1]) is tuple
            and all(
                isinstance(version, int) and not isinstance(version, bool) for version in item[1]
            )
            for item in descriptor.accepted_logic_formats
        )
        and type(descriptor.platform_matrix) is tuple
        and all(
            type(item) is tuple and len(item) == 5 and all(isinstance(field, str) for field in item)
            for item in descriptor.platform_matrix
        )
        and _valid_string_frozenset(descriptor.supported_features)
        and _valid_string_frozenset(descriptor.supported_mechanics)
    )


def _validated_adapter_registry(
    value: object,
) -> dict[tuple[str, str], RegisteredRuntimeAdapter]:
    if type(value) is not dict:
        _fail("adapter_registry_invalid", "adapter_registry must be an exact dictionary")
    snapshot = dict(value)
    for key, descriptor in snapshot.items():
        if (
            type(key) is not tuple
            or len(key) != 2
            or not all(isinstance(item, str) for item in key)
            or not _valid_adapter_descriptor(descriptor)
            or key != (descriptor.adapter_id, descriptor.adapter_version)
        ):
            _fail(
                "adapter_registry_invalid",
                "adapter_registry contains a malformed key or adapter descriptor",
            )
    return snapshot


def _valid_extension_descriptor(value: object) -> bool:
    if type(value) is not RegisteredGameExtension:
        return False
    descriptor = value
    return (
        isinstance(descriptor.extension_id, str)
        and isinstance(descriptor.extension_version, int)
        and not isinstance(descriptor.extension_version, bool)
        and descriptor.extension_version >= 1
        and isinstance(descriptor.content_hash, str)
        and len(descriptor.content_hash) == 64
        and all(character in "0123456789abcdef" for character in descriptor.content_hash)
        and _valid_string_frozenset(descriptor.supported_features)
        and _valid_string_frozenset(descriptor.supported_mechanics)
    )


def _validated_extension_registry(
    value: object,
) -> dict[tuple[str, int], RegisteredGameExtension]:
    if type(value) is not dict:
        _fail("extension_registry_invalid", "extension_registry must be an exact dictionary")
    snapshot = dict(value)
    for key, descriptor in snapshot.items():
        if (
            type(key) is not tuple
            or len(key) != 2
            or not isinstance(key[0], str)
            or not isinstance(key[1], int)
            or isinstance(key[1], bool)
            or not _valid_extension_descriptor(descriptor)
            or key != (descriptor.extension_id, descriptor.extension_version)
        ):
            _fail(
                "extension_registry_invalid",
                "extension_registry contains a malformed key or extension descriptor",
            )
    return snapshot


def _validated_evidence_sources(
    value: object,
) -> dict[str, CapabilityEvidenceSource]:
    if type(value) is not dict:
        _fail("evidence_sources_invalid", "evidence_sources must be an exact dictionary")
    snapshot = dict(value)
    for key, descriptor in snapshot.items():
        if (
            not isinstance(key, str)
            or type(descriptor) is not CapabilityEvidenceSource
            or not isinstance(descriptor.evidence_id, str)
            or descriptor.evidence_id != key
            or descriptor.category not in {"test", "native"}
            or type(descriptor.payload) is not bytes
        ):
            _fail(
                "evidence_source_invalid",
                "evidence_sources contains a malformed key or evidence descriptor",
            )
    return snapshot


def resolve_capability_ledger(
    value: object,
    *,
    gamepack: object,
    adapter_registry: Mapping[
        tuple[str, str],
        RegisteredRuntimeAdapter,
    ],
    extension_registry: Mapping[
        tuple[str, int],
        RegisteredGameExtension,
    ],
    evidence_sources: Mapping[str, CapabilityEvidenceSource],
) -> dict[str, Any]:
    """Resolve verified claims against independently supplied trusted registries."""

    checked_adapters = _validated_adapter_registry(adapter_registry)
    checked_extensions = _validated_extension_registry(extension_registry)
    checked_evidence_sources = _validated_evidence_sources(evidence_sources)
    checked_gamepack = validate_gamepack_document(gamepack)
    document = validate_capability_ledger_structure(value, gamepack=checked_gamepack)
    adapter_identity = document["adapter"]
    adapter_id = adapter_identity["adapter_id"]
    adapter_version = adapter_identity["adapter_version"]
    if (
        adapter_identity["status"] != "verified"
        or not isinstance(adapter_id, str)
        or not isinstance(adapter_version, str)
    ):
        _fail(
            "adapter_registry_mismatch",
            "trusted capability resolution requires an exact verified adapter identity",
        )
    adapter = checked_adapters.get((adapter_id, adapter_version))
    if (
        adapter is None
        or adapter.adapter_id != adapter_id
        or adapter.adapter_version != adapter_version
    ):
        _fail(
            "adapter_registry_mismatch",
            "ledger adapter is not present in the trusted adapter registry",
        )
    runtime = checked_gamepack["runtime_requirements"]
    expected_formats = tuple(
        (item["format"], tuple(item["versions"])) for item in runtime["accepted_logic_formats"]
    )
    expected_platforms = tuple(
        (
            item["platform_id"],
            item["platform_family"],
            item["architecture"],
            item["backend"],
            item["renderer"],
        )
        for item in runtime["platform_matrix"]
    )
    if (
        adapter.accepted_logic_formats != expected_formats
        or adapter.platform_matrix != expected_platforms
    ):
        _fail(
            "adapter_registry_mismatch",
            "trusted adapter descriptor does not exactly cover logic formats and platforms",
        )

    registered_extensions = {
        (extension["id"], extension["version"]): extension
        for extension in checked_gamepack["registered_extensions"]
    }
    seen_evidence: set[str] = set()
    for collection_name, supported_ids, id_field in (
        ("mechanics", adapter.supported_mechanics, "mechanic_id"),
        ("features", adapter.supported_features, "feature_id"),
    ):
        for entry in document[collection_name]:
            status = entry["status"]
            capability_id = entry[id_field]
            if status == "supported_current" and capability_id not in supported_ids:
                _fail(
                    "adapter_capability_mismatch",
                    f"trusted adapter does not support {collection_name[:-1]} {capability_id}",
                )
            if status == "game_extension_verified":
                extension = entry["extension"]
                assert isinstance(extension, dict)
                key = (extension["id"], extension["version"])
                if registered_extensions.get(key) != extension:
                    _fail(
                        "extension_registry_mismatch",
                        f"capability extension {extension['id']} is not exactly "
                        "registered in the gamepack",
                    )
                registered = checked_extensions.get(key)
                extension_support = (
                    registered.supported_mechanics
                    if collection_name == "mechanics" and registered is not None
                    else registered.supported_features
                    if registered is not None
                    else frozenset()
                )
                if (
                    registered is None
                    or registered.extension_id != extension["id"]
                    or registered.extension_version != extension["version"]
                    or registered.content_hash != extension["content_hash"]
                    or capability_id not in extension_support
                ):
                    _fail(
                        "extension_registry_mismatch",
                        f"trusted extension does not exactly support {capability_id}",
                    )
            for category, field in (
                ("test", "test_evidence"),
                ("native", "native_evidence"),
            ):
                for evidence in entry[field]:
                    evidence_id = evidence["evidence_id"]
                    evidence_key = evidence_id.casefold()
                    if evidence_key in seen_evidence:
                        _fail(
                            "evidence_reused",
                            f"evidence {evidence_id} is reused across capability claims",
                        )
                    seen_evidence.add(evidence_key)
                    source = checked_evidence_sources.get(evidence_id)
                    if source is None:
                        _fail(
                            "evidence_source_missing",
                            f"{category} evidence {evidence_id} is absent from trusted sources",
                        )
                    if source.category != category:
                        _fail(
                            "evidence_category_mismatch",
                            f"evidence {evidence_id} is not trusted as {category} evidence",
                        )
                    if (
                        evidence["content_hash"] == "0" * 64
                        or hashlib.sha256(source.payload).hexdigest() != evidence["content_hash"]
                    ):
                        _fail(
                            "evidence_hash_mismatch",
                            f"{category} evidence {evidence_id} is not independently "
                            "loaded and hash-verified",
                        )
    return document


def serialize_capability_ledger(value: object) -> bytes:
    return canonical_json_bytes(validate_capability_ledger_document(value))


def publish_capability_ledger(
    path: str | Path,
    value: object,
) -> PublishedGameArtifact:
    document = validate_capability_ledger_document(value)
    destination = preflight_game_artifact_output(path)
    try:
        write_json_atomic(destination, document, durable_parent=True)
    except AssetContractError as exc:
        reason = "output_exists" if "overwrite" in str(exc).casefold() else "output_publish_failed"
        _fail(reason, str(exc))
    return _published_artifact(destination, document)
