from __future__ import annotations

import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from worldforge.asset_io import encoded_json
from worldforge.creation_contracts import (
    CreationContractError,
    canonical_creation_hash,
    validate_creation_documents,
)
from worldforge.creation_vocabulary import (
    CREATION_CONTENT_MODES,
    CREATION_PROJECT_KINDS,
    GAMEPLAY_FAMILIES,
    NARRATIVE_AUTHORSHIP_MODES,
    NARRATIVE_REQUIREMENTS,
    NARRATIVE_TOPOLOGIES,
    PRESENTATION_MODES,
    RUNTIME_SUPPORT_INTENTS,
    WORLD_PRESENCES,
    is_creation_identifier,
)
from worldforge.creation_workflow import (
    initial_creation_workflow_status,
    phase_catalog,
)
from worldforge.directory_publish import (
    DirectoryPublishError,
    DirectoryPublishRecoveryRequiredError,
    create_retained_stage,
    directory_identity,
    fsync_directory,
    publish_directory_noreplace,
    quarantine_and_remove_verified_directory,
)
from worldforge.repository_boundary import (
    RepositoryBoundaryError,
    assert_new_repository_target,
)


class CreationScaffoldError(ValueError):
    """Raised when a generic creation project cannot be scaffolded safely."""

    def __init__(
        self,
        detail: str,
        *,
        reason_code: str = "creation_scaffold_failed",
        recovery_evidence: dict[str, object] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.detail = detail
        self.recovery_evidence = dict(recovery_evidence or {})
        super().__init__(detail)


_CREATION_SCAFFOLD_OPERATION_DETAILS = {
    "creation_scaffold_stage_create_failed": "creation scaffold stage creation failed",
    "creation_scaffold_stage_write_failed": "creation scaffold stage write failed",
    "creation_scaffold_stage_flush_failed": "creation scaffold stage flush failed",
    "creation_scaffold_stage_verify_failed": "creation scaffold stage verification failed",
    "creation_scaffold_publish_failed": (
        "creation project target already exists or publication failed"
    ),
    "creation_scaffold_published_verify_failed": (
        "published creation scaffold verification failed"
    ),
    "creation_scaffold_parent_flush_failed": "creation scaffold parent flush failed",
    "creation_scaffold_finalize_failed": "creation scaffold finalization failed",
}
CREATION_SCAFFOLD_OPERATION_REASON_CODES = frozenset(_CREATION_SCAFFOLD_OPERATION_DETAILS)


def _operation_failure(reason_code: str, _cause: BaseException) -> CreationScaffoldError:
    if reason_code not in CREATION_SCAFFOLD_OPERATION_REASON_CODES:
        raise AssertionError("unknown creation scaffold operation failure")
    return CreationScaffoldError(
        _CREATION_SCAFFOLD_OPERATION_DETAILS[reason_code],
        reason_code=reason_code,
    )


@dataclass(frozen=True)
class CreationScaffoldFacets:
    """Closed initial facets used by every generic creation entry point."""

    project_kind: str
    gameplay_family: str | None
    initial_core_verb: str | None
    initial_core_loop: str | None
    world_presence: str | None
    narrative_requirement: str | None
    narrative_authorship: str | None
    narrative_topology: str | None
    presentation_mode: str | None
    runtime_support_intent: str | None
    asset_content_mode: str | None


def _invalid_input(detail: str) -> CreationScaffoldError:
    return CreationScaffoldError(
        detail,
        reason_code="creation_scaffold_inputs_invalid",
    )


def normalize_creation_scaffold_facets(
    *,
    project_kind: str = "universe_library",
    gameplay_family: str | None = None,
    initial_core_verb: str | None = None,
    initial_core_loop: str | None = None,
    world_presence: str | None = None,
    narrative_requirement: str | None = None,
    narrative_authorship: str | None = None,
    narrative_topology: str | None = None,
    presentation_mode: str | None = None,
    runtime_support_intent: str | None = None,
    asset_content_mode: str | None = None,
) -> CreationScaffoldFacets:
    """Validate and normalize the closed kind-aware scaffold input contract."""

    if project_kind not in CREATION_PROJECT_KINDS:
        raise _invalid_input(f"project_kind must be one of: {', '.join(CREATION_PROJECT_KINDS)}")
    values = {
        "gameplay_family": gameplay_family,
        "initial_core_verb": initial_core_verb,
        "initial_core_loop": initial_core_loop,
        "world_presence": world_presence,
        "narrative_requirement": narrative_requirement,
        "narrative_authorship": narrative_authorship,
        "narrative_topology": narrative_topology,
        "presentation_mode": presentation_mode,
        "runtime_support_intent": runtime_support_intent,
        "asset_content_mode": asset_content_mode,
    }
    if project_kind != "game":
        supplied = sorted(field for field, value in values.items() if value is not None)
        if supplied:
            raise _invalid_input(
                f"{project_kind} does not accept game facets: {', '.join(supplied)}"
            )
        return CreationScaffoldFacets(
            project_kind=project_kind,
            gameplay_family=None,
            initial_core_verb=None,
            initial_core_loop=None,
            world_presence=None,
            narrative_requirement=None,
            narrative_authorship=None,
            narrative_topology=None,
            presentation_mode=None,
            runtime_support_intent=None,
            asset_content_mode=None,
        )

    required = {
        "gameplay_family": gameplay_family,
        "initial_core_verb": initial_core_verb,
        "initial_core_loop": initial_core_loop,
        "world_presence": world_presence,
        "narrative_requirement": narrative_requirement,
        "presentation_mode": presentation_mode,
        "runtime_support_intent": runtime_support_intent,
    }
    missing = sorted(field for field, value in required.items() if value is None)
    if missing:
        raise _invalid_input(f"game creation requires: {', '.join(missing)}")
    assert gameplay_family is not None
    assert initial_core_verb is not None
    assert initial_core_loop is not None
    assert world_presence is not None
    assert narrative_requirement is not None
    assert presentation_mode is not None
    assert runtime_support_intent is not None
    if gameplay_family not in GAMEPLAY_FAMILIES:
        raise _invalid_input(f"gameplay_family must be one of: {', '.join(GAMEPLAY_FAMILIES)}")
    if not is_creation_identifier(initial_core_verb):
        raise _invalid_input("initial_core_verb must be a portable World Forge identifier")
    normalized_loop = initial_core_loop.strip()
    if not normalized_loop or len(normalized_loop) > 512:
        raise _invalid_input("initial_core_loop must contain 1..512 characters")
    if world_presence not in WORLD_PRESENCES:
        raise _invalid_input(f"world_presence must be one of: {', '.join(WORLD_PRESENCES)}")
    if narrative_requirement not in NARRATIVE_REQUIREMENTS:
        raise _invalid_input(
            "narrative_requirement must be one of: " + ", ".join(NARRATIVE_REQUIREMENTS)
        )
    authorship = narrative_authorship or "none"
    topology = narrative_topology or "none"
    if authorship not in NARRATIVE_AUTHORSHIP_MODES:
        raise _invalid_input(
            "narrative_authorship must be one of: " + ", ".join(NARRATIVE_AUTHORSHIP_MODES)
        )
    if topology not in NARRATIVE_TOPOLOGIES:
        raise _invalid_input(
            "narrative_topology must be one of: " + ", ".join(NARRATIVE_TOPOLOGIES)
        )
    if narrative_requirement == "none":
        if authorship != "none" or topology != "none":
            raise _invalid_input(
                "narrative:none requires narrative_authorship:none and narrative_topology:none"
            )
    elif authorship == "none" or topology == "none":
        raise _invalid_input(
            "optional or required narrative needs explicit non-none authorship and topology"
        )
    if presentation_mode not in PRESENTATION_MODES:
        raise _invalid_input(f"presentation_mode must be one of: {', '.join(PRESENTATION_MODES)}")
    if runtime_support_intent not in RUNTIME_SUPPORT_INTENTS:
        raise _invalid_input(
            "runtime_support_intent must be one of: " + ", ".join(RUNTIME_SUPPORT_INTENTS)
        )
    asset_mode = asset_content_mode or "authored"
    if asset_mode not in CREATION_CONTENT_MODES:
        raise _invalid_input(
            "asset_content_mode must be one of: " + ", ".join(CREATION_CONTENT_MODES)
        )
    return CreationScaffoldFacets(
        project_kind=project_kind,
        gameplay_family=gameplay_family,
        initial_core_verb=initial_core_verb,
        initial_core_loop=normalized_loop,
        world_presence=world_presence,
        narrative_requirement=narrative_requirement,
        narrative_authorship=authorship,
        narrative_topology=topology,
        presentation_mode=presentation_mode,
        runtime_support_intent=runtime_support_intent,
        asset_content_mode=asset_mode,
    )


def _profile_id(project_id: str) -> str:
    candidate = f"{project_id}_profile"
    return candidate if len(candidate) <= 64 else project_id


def _seal(document: dict[str, object]) -> dict[str, object]:
    result = dict(document)
    result["content_hash"] = canonical_creation_hash(result)
    return result


def _profile_document(
    *,
    project_id: str,
    title: str,
    default_locale: str,
    facets: CreationScaffoldFacets | None = None,
) -> dict[str, object]:
    selected = facets or normalize_creation_scaffold_facets()
    document: dict[str, object] = {
        "format": "world-forge.creation_profile",
        "format_version": 1,
        "profile_id": _profile_id(project_id),
        "project_id": project_id,
        "title": f"{title} creation profile",
        "experience": {
            "player_promise": "No player experience has been committed.",
            "audiences": ["authoring_team"],
            "experience_goals": ["establish_reviewed_requirements"],
        },
        "gameplay": {
            "primary_family": "none",
            "secondary_families": [],
            "mechanic_tags": [],
            "player_role": "none",
            "core_verbs": [],
            "core_loop": [],
            "rule_model": "none",
            "goal_model": "none",
            "challenge_model": "none",
            "failure_recovery": "none",
            "progression": "none",
            "teleology": "none",
            "session_structure": "none",
            "social_topology": "none",
            "dependencies": {
                "authored": [],
                "systemic": [],
                "procedural": [],
            },
        },
        "world": {
            "presence": "none",
            "spatial_topology": "none",
            "scale": "none",
            "time_model": "none",
            "simulation_depth": "none",
            "simulated_domains": [],
            "persistence": "none",
            "spatial_structure": "none",
        },
        "narrative": {
            "requirement": "none",
            "authorship_mode": "none",
            "topology": "none",
            "delivery_channels": [],
            "protagonist_model": "none",
            "agency": "none",
            "focalization": "none",
            "canon_variability": "none",
            "pacing": "none",
            "endings": "none",
            "information_model": "none",
        },
        "fiction": {"genres": [], "tones": [], "tags": []},
        "presentation": {
            "mode": "text",
            "camera": "none",
            "perspective": "none",
            "visual_language": "undecided",
            "ui_density": "undecided",
            "audio_role": "none",
            "input_assumptions": ["input:keyboard"],
            "accessibility": {
                "remapping": True,
                "keyboard_only": True,
                "captions": True,
                "text_scaling": True,
                "high_contrast": True,
                "color_independence": True,
                "reduced_motion": True,
                "timing_alternatives": True,
                "screen_reader_structure": True,
            },
            "localization": {
                "source_locale": default_locale,
                "supported_locales": [default_locale],
                "externalized_text": True,
            },
        },
        "production": {
            "content_modes": {
                "gameplay": "not_applicable",
                "world": "not_applicable",
                "narrative": "not_applicable",
                "assets": "not_applicable",
            },
            "seed_policy": "none",
            "reproducibility": "all future canonical inputs must be content addressed",
            "selection_policy": "human review required before canonical promotion",
            "human_review": True,
            "provenance_required": True,
            "licensing_required": True,
            "qa_required": True,
        },
        "runtime_target": {
            "requested_adapter": None,
            "accepted_logic_formats": [],
            "required_features": [],
            "optional_features": [],
            "presentation_mode": "text",
            "platforms": [],
            "renderer": "none",
            "input_capabilities": [],
            "asset_formats": [],
            "save_expected": False,
            "replay_expected": False,
            "packaging_target": "none",
        },
        "extensions": [],
        "content_hash": "",
    }
    if selected.project_kind == "asset_library":
        document["experience"] = {
            "player_promise": "No player experience applies to this asset library.",
            "audiences": ["asset_authoring_team"],
            "experience_goals": ["establish_reviewed_asset_requirements"],
        }
        production = document["production"]
        assert isinstance(production, dict)
        content_modes = production["content_modes"]
        assert isinstance(content_modes, dict)
        content_modes["assets"] = "authored"
    elif selected.project_kind == "game":
        assert selected.gameplay_family is not None
        assert selected.initial_core_verb is not None
        assert selected.initial_core_loop is not None
        assert selected.world_presence is not None
        assert selected.narrative_requirement is not None
        assert selected.narrative_authorship is not None
        assert selected.narrative_topology is not None
        assert selected.presentation_mode is not None
        assert selected.runtime_support_intent is not None
        assert selected.asset_content_mode is not None
        document["experience"] = {
            "player_promise": (
                "Define and review the player experience before implementation handoff."
            ),
            "audiences": ["game_authoring_team"],
            "experience_goals": ["establish_reviewed_gameplay_requirements"],
        }
        document["gameplay"] = {
            "primary_family": selected.gameplay_family,
            "secondary_families": [],
            "mechanic_tags": [],
            "player_role": "undecided",
            "core_verbs": [
                {
                    "id": selected.initial_core_verb,
                    "description": "Initial reviewed core verb selected during project creation.",
                }
            ],
            "core_loop": [selected.initial_core_loop],
            "rule_model": "initial deterministic prototype rule",
            "goal_model": "complete the initial deterministic prototype action",
            "challenge_model": "initial bounded authoring prototype",
            "failure_recovery": "restart the initial prototype state",
            "progression": "one initial reviewed action",
            "teleology": "finite",
            "session_structure": "single bounded prototype session",
            "social_topology": "single_player",
            "dependencies": {
                "authored": [],
                "systemic": ["logic:deterministic_actions"],
                "procedural": [],
            },
        }
        if selected.world_presence == "none":
            document["world"] = {
                "presence": "none",
                "spatial_topology": "none",
                "scale": "none",
                "time_model": "none",
                "simulation_depth": "none",
                "simulated_domains": [],
                "persistence": "none",
                "spatial_structure": "none",
            }
        else:
            document["world"] = {
                "presence": selected.world_presence,
                "spatial_topology": "undecided",
                "scale": "undecided",
                "time_model": "undecided",
                "simulation_depth": "undecided",
                "simulated_domains": [],
                "persistence": "undecided",
                "spatial_structure": "undecided",
            }
        if selected.narrative_requirement == "none":
            document["narrative"] = {
                "requirement": "none",
                "authorship_mode": "none",
                "topology": "none",
                "delivery_channels": [],
                "protagonist_model": "none",
                "agency": "none",
                "focalization": "none",
                "canon_variability": "none",
                "pacing": "none",
                "endings": "none",
                "information_model": "none",
            }
        else:
            document["narrative"] = {
                "requirement": selected.narrative_requirement,
                "authorship_mode": selected.narrative_authorship,
                "topology": selected.narrative_topology,
                "delivery_channels": ["narrative:ui"],
                "protagonist_model": "undecided",
                "agency": "undecided",
                "focalization": "undecided",
                "canon_variability": "undecided",
                "pacing": "undecided",
                "endings": "undecided",
                "information_model": "undecided",
            }
        presentation = document["presentation"]
        assert isinstance(presentation, dict)
        presentation.update(
            {
                "mode": selected.presentation_mode,
                "camera": "none" if selected.presentation_mode == "text" else "undecided",
                "perspective": (
                    "text interface" if selected.presentation_mode == "text" else "undecided"
                ),
                "visual_language": "undecided",
                "ui_density": "undecided",
                "audio_role": "undecided",
            }
        )
        production = document["production"]
        assert isinstance(production, dict)
        production["content_modes"] = {
            "gameplay": "authored",
            "world": ("not_applicable" if selected.world_presence == "none" else "authored"),
            "narrative": (
                "not_applicable" if selected.narrative_requirement == "none" else "authored"
            ),
            "assets": selected.asset_content_mode,
        }
        compatibility_assessment = selected.runtime_support_intent == "compatibility_assessment"
        authoring_only_runtime_absence = selected.runtime_support_intent == "authoring_only"
        document["runtime_target"] = {
            "requested_adapter": None,
            "accepted_logic_formats": (
                []
                if authoring_only_runtime_absence
                else [{"format": "world-forge.gamepack", "versions": [1]}]
            ),
            "required_features": (
                [] if authoring_only_runtime_absence else ["logic:deterministic_actions"]
            ),
            "optional_features": [],
            "presentation_mode": selected.presentation_mode,
            "platforms": (
                ["platform:linux_x86_64", "platform:windows_x86_64"]
                if compatibility_assessment
                else []
            ),
            "renderer": "unresolved" if compatibility_assessment else "none",
            "input_capabilities": [] if authoring_only_runtime_absence else ["input:keyboard"],
            "asset_formats": [],
            "save_expected": compatibility_assessment,
            "replay_expected": compatibility_assessment,
            "packaging_target": (
                "standalone desktop directory" if compatibility_assessment else "none"
            ),
        }
    return _seal(document)


def _project_documents(
    *,
    project_id: str,
    title: str,
    default_locale: str,
    project_version: str,
    facets: CreationScaffoldFacets | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    documents = _scaffold_documents(
        project_id=project_id,
        title=title,
        default_locale=default_locale,
        project_version=project_version,
        facets=facets or normalize_creation_scaffold_facets(),
    )
    return documents[0], documents[1], documents[2]


def _initial_activity_document(*, project_id: str) -> dict[str, object]:
    return _seal(
        {
            "format": "world-forge.activity_module",
            "format_version": 1,
            "module_id": "initial_activity",
            "project_id": project_id,
            "title": "Initial neutral gameplay activity",
            "activities": [
                {
                    "id": "initial_challenge",
                    "activity_type": "challenge",
                    "title": "Initial gameplay challenge",
                    "participant_ids": [],
                    "spatial_context_ids": [],
                    "start_condition_ids": ["action_available"],
                    "end_condition_ids": ["prototype_complete"],
                    "success_condition_ids": ["prototype_complete"],
                    "failure_condition_ids": [],
                    "effect_ids": ["complete_initial_action"],
                    "event_ids": ["initial_action_committed"],
                    "presentation_hook_ids": ["initial_feedback"],
                    "asset_binding_ids": [],
                    "validation_profile": "deterministic_initial_scaffold",
                    "provenance": "explicit initial facets supplied during project creation",
                }
            ],
            "extensions": [],
            "content_hash": "",
        }
    )


def _initial_logic_document(
    *,
    project_id: str,
    core_verb: str,
    branching_narrative: bool,
    required_features: tuple[str, ...] = ("logic:deterministic_actions",),
) -> dict[str, object]:
    feature_ids = list(required_features)
    document: dict[str, object] = {
        "format": "world-forge.logic_module",
        "format_version": 1,
        "module_id": "initial_logic",
        "project_id": project_id,
        "title": "Initial deterministic gameplay prototype",
        "state_variables": [
            {
                "id": "prototype_completed",
                "type": "boolean",
                "initial": False,
                "mutability": "mutable",
                "persistence": "saved",
            }
        ],
        "actions": [
            {
                "id": "initial_action",
                "core_verb_id": core_verb,
                "parameters": [],
                "source_bindings": [{"kind": "activity", "source_id": "initial_challenge"}],
                "rule_ids": ["initial_rule"],
                "presentation_hook_ids": ["initial_feedback"],
                "required_feature_ids": feature_ids,
            }
        ],
        "conditions": [
            {
                "id": "action_available",
                "action_id": "initial_action",
                "operator": "constant",
                "value": True,
            },
            {
                "id": "prototype_complete",
                "action_id": None,
                "operator": "compare",
                "comparison": "equal",
                "left": {"kind": "state", "state_id": "prototype_completed"},
                "right": {
                    "kind": "literal",
                    "value": True,
                    "value_type": "boolean",
                },
            },
        ],
        "effects": [
            {
                "id": "complete_initial_action",
                "action_id": "initial_action",
                "operation": "set",
                "state_id": "prototype_completed",
                "value": {
                    "kind": "literal",
                    "value": True,
                    "value_type": "boolean",
                },
                "invalid_transition_policy": "reject_transition",
            }
        ],
        "events": [{"id": "initial_action_committed"}],
        "rules": [
            {
                "id": "initial_rule",
                "action_id": "initial_action",
                "order": 0,
                "condition_ids": ["action_available"],
                "effect_ids": ["complete_initial_action"],
                "event_ids": ["initial_action_committed"],
            }
        ],
        "presentation_hooks": [
            {"id": "initial_feedback", "kind": "feedback", "asset_binding_ids": []}
        ],
        "goals": [
            {
                "id": "complete_prototype",
                "condition_ids": ["prototype_complete"],
                "success_ending_id": "prototype_complete_ending",
            }
        ],
        "failures": [],
        "endings": [
            {
                "id": "prototype_complete_ending",
                "kind": "success",
                "condition_ids": ["prototype_complete"],
                "event_ids": ["initial_action_committed"],
                "presentation_hook_ids": ["initial_feedback"],
            }
        ],
        "mechanics": [
            {
                "id": "initial_mechanic",
                "core_verb_id": core_verb,
                "action_id": "initial_action",
                "authoritative_state_ids": ["prototype_completed"],
                "condition_ids": ["action_available"],
                "rule_ids": ["initial_rule"],
                "effect_ids": ["complete_initial_action"],
                "event_ids": ["initial_action_committed"],
                "presentation_hook_ids": ["initial_feedback"],
                "asset_binding_ids": [],
                "required_feature_ids": feature_ids,
            }
        ],
        "extensions": [],
        "content_hash": "",
    }
    if branching_narrative:
        actions = document["actions"]
        effects = document["effects"]
        events = document["events"]
        rules = document["rules"]
        mechanics = document["mechanics"]
        assert isinstance(actions, list)
        assert isinstance(effects, list)
        assert isinstance(events, list)
        assert isinstance(rules, list)
        assert isinstance(mechanics, list)
        for suffix, option_id in (("a", "initial_option_a"), ("b", "initial_option_b")):
            action_id = f"choose_option_{suffix}"
            effect_id = f"record_option_{suffix}"
            event_id = f"option_{suffix}_committed"
            rule_id = f"choose_option_{suffix}_rule"
            actions.append(
                {
                    "id": action_id,
                    "core_verb_id": core_verb,
                    "parameters": [],
                    "source_bindings": [
                        {
                            "kind": "narrative_option",
                            "source_id": "initial_choice",
                            "option_id": option_id,
                        }
                    ],
                    "rule_ids": [rule_id],
                    "presentation_hook_ids": ["initial_feedback"],
                    "required_feature_ids": feature_ids,
                }
            )
            effects.append(
                {
                    "id": effect_id,
                    "action_id": action_id,
                    "operation": "set",
                    "state_id": "prototype_completed",
                    "value": {
                        "kind": "literal",
                        "value": True,
                        "value_type": "boolean",
                    },
                    "invalid_transition_policy": "reject_transition",
                }
            )
            events.append({"id": event_id})
            rules.append(
                {
                    "id": rule_id,
                    "action_id": action_id,
                    "order": len(rules),
                    "condition_ids": [],
                    "effect_ids": [effect_id],
                    "event_ids": [event_id],
                }
            )
            mechanics.append(
                {
                    "id": f"choose_option_{suffix}_mechanic",
                    "core_verb_id": core_verb,
                    "action_id": action_id,
                    "authoritative_state_ids": ["prototype_completed"],
                    "condition_ids": [],
                    "rule_ids": [rule_id],
                    "effect_ids": [effect_id],
                    "event_ids": [event_id],
                    "presentation_hook_ids": ["initial_feedback"],
                    "asset_binding_ids": [],
                    "required_feature_ids": feature_ids,
                }
            )
        for records in (actions, effects, events, rules, mechanics):
            records.sort(key=lambda item: str(item["id"]).encode("utf-8"))
        for order, rule in enumerate(rules):
            rule["order"] = order
    return _seal(document)


def _initial_narrative_document(
    *,
    project_id: str,
    topology: str,
) -> dict[str, object]:
    if topology == "branching":
        entry_ids = ["initial_choice"]
        units: list[dict[str, object]] = [
            {
                "id": "initial_choice",
                "unit_type": "choice",
                "title": "Initial neutral choice",
                "prerequisite_ids": [],
                "effect_ids": [],
                "next_unit_ids": ["initial_ending_a", "initial_ending_b"],
                "asset_binding_ids": [],
                "options": [
                    {
                        "id": "initial_option_a",
                        "label": "Option A",
                        "next_unit_id": "initial_ending_a",
                        "condition_ids": [],
                        "effect_ids": ["record_option_a"],
                    },
                    {
                        "id": "initial_option_b",
                        "label": "Option B",
                        "next_unit_id": "initial_ending_b",
                        "condition_ids": [],
                        "effect_ids": ["record_option_b"],
                    },
                ],
            },
            {
                "id": "initial_ending_a",
                "unit_type": "ending",
                "title": "Initial ending A",
                "prerequisite_ids": [],
                "effect_ids": [],
                "next_unit_ids": [],
                "asset_binding_ids": [],
                "ending_kind": "neutral",
            },
            {
                "id": "initial_ending_b",
                "unit_type": "ending",
                "title": "Initial ending B",
                "prerequisite_ids": [],
                "effect_ids": [],
                "next_unit_ids": [],
                "asset_binding_ids": [],
                "ending_kind": "neutral",
            },
        ]
    else:
        entry_ids = ["initial_narrative_unit"]
        units = [
            {
                "id": "initial_narrative_unit",
                "unit_type": "scene",
                "title": "Initial neutral narrative unit",
                "prerequisite_ids": [],
                "effect_ids": [],
                "next_unit_ids": [],
                "asset_binding_ids": [],
            }
        ]
    return _seal(
        {
            "format": "world-forge.narrative_module",
            "format_version": 1,
            "module_id": "initial_narrative",
            "project_id": project_id,
            "title": "Initial neutral narrative structure",
            "entry_unit_ids": entry_ids,
            "units": units,
            "extensions": [],
            "content_hash": "",
        }
    )


def _module_reference(
    document: dict[str, object],
    *,
    path: str,
) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document["module_id"],
        "path": path,
        "content_hash": document["content_hash"],
    }


def _scaffold_documents(
    *,
    project_id: str,
    title: str,
    default_locale: str,
    project_version: str,
    facets: CreationScaffoldFacets,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
]:
    profile = _profile_document(
        project_id=project_id,
        title=title,
        default_locale=default_locale,
        facets=facets,
    )
    world_modules: tuple[dict[str, object], ...] = ()
    activity_modules: tuple[dict[str, object], ...] = ()
    narrative_modules: tuple[dict[str, object], ...] = ()
    system_modules: tuple[dict[str, object], ...] = ()
    logic_modules: tuple[dict[str, object], ...] = ()
    authoring_only_runtime_absence = (
        facets.project_kind == "game" and facets.runtime_support_intent == "authoring_only"
    )
    if facets.project_kind == "game":
        assert facets.initial_core_verb is not None
        activity_modules = (_initial_activity_document(project_id=project_id),)
        logic_modules = (
            _initial_logic_document(
                project_id=project_id,
                core_verb=facets.initial_core_verb,
                branching_narrative=(
                    facets.narrative_requirement == "required"
                    and facets.narrative_topology == "branching"
                ),
                required_features=(
                    () if authoring_only_runtime_absence else ("logic:deterministic_actions",)
                ),
            ),
        )
        if facets.narrative_requirement == "required":
            assert facets.narrative_topology is not None
            narrative_modules = (
                _initial_narrative_document(
                    project_id=project_id,
                    topology=facets.narrative_topology,
                ),
            )
    manifest = _seal(
        {
            "format": "world-forge.creation_source_manifest",
            "format_version": 1,
            "project_id": project_id,
            "profile": {
                "format": profile["format"],
                "format_version": profile["format_version"],
                "id": profile["profile_id"],
                "path": "profile.json",
                "content_hash": profile["content_hash"],
            },
            "modules": {
                "world_modules": [],
                "activity_modules": [
                    _module_reference(
                        document,
                        path="activities/initial.json",
                    )
                    for document in activity_modules
                ],
                "narrative_modules": [
                    _module_reference(
                        document,
                        path="narrative/initial.json",
                    )
                    for document in narrative_modules
                ],
                "system_modules": [],
                "logic_modules": [
                    _module_reference(document, path="logic/initial.json")
                    for document in logic_modules
                ],
            },
            "extensions": [],
            "content_hash": "",
        }
    )
    project = _seal(
        {
            "format": "world-forge.project",
            "format_version": 1,
            "project_kind": facets.project_kind,
            "project_id": project_id,
            "title": title,
            "project_version": project_version,
            "default_locale": default_locale,
            "profile": {
                "format": profile["format"],
                "format_version": profile["format_version"],
                "id": profile["profile_id"],
                "path": "profile.json",
                "content_hash": profile["content_hash"],
            },
            "source_manifest": {
                "format": manifest["format"],
                "format_version": manifest["format_version"],
                "id": project_id,
                "path": "source/manifest.json",
                "content_hash": manifest["content_hash"],
            },
            "extensions": [],
            "content_hash": "",
        }
    )
    try:
        validate_creation_documents(
            project,
            profile,
            manifest,
            world_modules,
            activity_modules,
            narrative_modules,
            system_modules,
            logic_modules,
        )
    except CreationContractError as exc:
        raise CreationScaffoldError(str(exc)) from exc
    return (
        project,
        profile,
        manifest,
        world_modules,
        activity_modules,
        narrative_modules,
        system_modules,
        logic_modules,
    )


def _file_payloads(
    *,
    project: dict[str, object],
    profile: dict[str, object],
    manifest: dict[str, object],
    world_modules: tuple[dict[str, object], ...] = (),
    activity_modules: tuple[dict[str, object], ...] = (),
    narrative_modules: tuple[dict[str, object], ...] = (),
    system_modules: tuple[dict[str, object], ...] = (),
    logic_modules: tuple[dict[str, object], ...] = (),
) -> dict[str, bytes]:
    loaded = validate_creation_documents(
        project,
        profile,
        manifest,
        world_modules,
        activity_modules,
        narrative_modules,
        system_modules,
        logic_modules,
    )
    status = initial_creation_workflow_status(loaded)
    phases = {
        "format": "world-forge.creation_phase_catalog",
        "format_version": 1,
        "phases": phase_catalog(),
    }
    documents = (
        project,
        profile,
        manifest,
        *world_modules,
        *activity_modules,
        *narrative_modules,
        *system_modules,
        *logic_modules,
    )
    initial_history = {
        f".worldforge/artifact_history/{document['content_hash']}.json": encoded_json(document)
        for document in documents
    }
    title = str(project["title"])
    project_kind = str(project["project_kind"])
    module_files: dict[str, bytes] = {}
    module_collections = manifest["modules"]
    assert isinstance(module_collections, dict)
    for collection in module_collections.values():
        assert isinstance(collection, list)
        for reference in collection:
            assert isinstance(reference, dict)
            document = next(
                candidate
                for candidate in documents
                if candidate.get("format") == reference["format"]
                and candidate.get("module_id") == reference["id"]
                and candidate.get("content_hash") == reference["content_hash"]
            )
            module_files[f"source/{reference['path']}"] = encoded_json(document)
    if project_kind == "universe_library":
        agent_guidance = (
            "GPT is the lead authoring agent. This neutral authoring library is not "
            "an executable game. Work follows P00-P14 and keeps authoring validity "
            "separate from compilation, assets, runtime support, native evidence, "
            "packaging, and release. Do not invent world, narrative, actor, asset, "
            "or runtime requirements when the reviewed profile marks them absent.\n"
        )
        readme_guidance = (
            "Generic World Forge neutral authoring library; this is not an executable "
            "game. Start by reviewing `profile.json`, then author only applicable "
            "typed modules and complete P00-P14 with exact phase-report v3 evidence. "
            "Compilation, assets, adapter support, native execution, packaging, and "
            "release remain independent.\n\n"
        )
    elif project_kind == "asset_library":
        agent_guidance = (
            "GPT is the lead authoring agent. This asset library contains no gameplay, "
            "world, narrative, or runtime claim. Work follows P00-P14 and authors only "
            "reviewed asset specifications, provenance, licensing, processing, and QA.\n"
        )
        readme_guidance = (
            "Generic World Forge asset library. It contains no game, world, narrative, "
            "adapter, or executable-runtime claim. Review `profile.json`, then author "
            "only applicable asset contracts and reviewed phase evidence.\n\n"
        )
    else:
        agent_guidance = (
            "GPT is the lead authoring agent. This game project starts from explicit "
            "independent gameplay, world, narrative, presentation, and runtime-support "
            "facets. The initial deterministic activity and logic are authoring seeds, "
            "not release evidence. Never infer a runtime adapter from gameplay or fiction.\n"
        )
        readme_guidance = (
            "Generic World Forge game project. Review the explicit initial facets in "
            "`profile.json` and replace the neutral deterministic seed through reviewed "
            "typed modules. Authoring validity does not certify adapter support, native "
            "execution, packaging, or release readiness.\n\n"
        )
    return {
        "project.json": encoded_json(project),
        "profile.json": encoded_json(profile),
        "source/manifest.json": encoded_json(manifest),
        **module_files,
        ".worldforge/status.json": encoded_json(status),
        **initial_history,
        ".worldforge/phases.json": encoded_json(phases),
        ".worldforge/DECISIONS.md": (
            b"# Decisions\n\nRecord accepted, superseded, and rejected decisions.\n"
        ),
        ".worldforge/TASKS.md": (
            b"# Tasks\n\nMaintain ordered authoring work and explicit blockers.\n"
        ),
        ".worldforge/phase_reports/README.md": (
            b"# Phase reports\n\n"
            b"Immutable content-addressed phase-report v3 documents are stored here.\n"
        ),
        "AGENTS.md": (f"# Agents for {title}\n\n" + agent_guidance).encode(),
        "README.md": (
            f"# {title}\n\n" + readme_guidance + "```bash\nworldforge phase-status .\n```\n"
        ).encode(),
        ".gitignore": b".venv/\n__pycache__/\n*.py[cod]\nbuild/\n",
    }


def _verify_exact_tree(root: Path, files: dict[str, bytes]) -> None:
    observed: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if path.is_symlink():
            raise CreationScaffoldError(f"scaffold tree contains a symbolic link: {relative}")
        if stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                raise CreationScaffoldError(f"scaffold file is hardlinked: {relative}")
            observed.add(relative)
            expected = files.get(relative)
            if expected is None or path.read_bytes() != expected:
                raise CreationScaffoldError(f"scaffold file differs: {relative}")
        elif not stat.S_ISDIR(info.st_mode):
            raise CreationScaffoldError(f"scaffold tree has an unsupported entry: {relative}")
    if observed != set(files):
        missing = sorted(set(files) - observed)
        raise CreationScaffoldError(f"scaffold tree is incomplete: {', '.join(missing)}")


def create_creation_project(
    target: str | Path,
    *,
    project_id: str,
    title: str,
    default_locale: str = "en",
    project_version: str = "0.1.0",
    project_kind: str = "universe_library",
    gameplay_family: str | None = None,
    initial_core_verb: str | None = None,
    initial_core_loop: str | None = None,
    world_presence: str | None = None,
    narrative_requirement: str | None = None,
    narrative_authorship: str | None = None,
    narrative_topology: str | None = None,
    presentation_mode: str | None = None,
    runtime_support_intent: str | None = None,
    asset_content_mode: str | None = None,
) -> Path:
    """Create one valid kind-aware generic creation project without replacement."""

    normalized_title = title.strip()
    if not normalized_title:
        raise _invalid_input("title cannot be empty")
    facets = normalize_creation_scaffold_facets(
        project_kind=project_kind,
        gameplay_family=gameplay_family,
        initial_core_verb=initial_core_verb,
        initial_core_loop=initial_core_loop,
        world_presence=world_presence,
        narrative_requirement=narrative_requirement,
        narrative_authorship=narrative_authorship,
        narrative_topology=narrative_topology,
        presentation_mode=presentation_mode,
        runtime_support_intent=runtime_support_intent,
        asset_content_mode=asset_content_mode,
    )
    (
        project,
        profile,
        manifest,
        world_modules,
        activity_modules,
        narrative_modules,
        system_modules,
        logic_modules,
    ) = _scaffold_documents(
        project_id=project_id,
        title=normalized_title,
        default_locale=default_locale,
        project_version=project_version,
        facets=facets,
    )
    files = _file_payloads(
        project=project,
        profile=profile,
        manifest=manifest,
        world_modules=world_modules,
        activity_modules=activity_modules,
        narrative_modules=narrative_modules,
        system_modules=system_modules,
        logic_modules=logic_modules,
    )
    try:
        destination = assert_new_repository_target(
            target,
            repository_type="creation",
        )
    except RepositoryBoundaryError as exc:
        raise CreationScaffoldError(str(exc)) from exc
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        parent_identity = directory_identity(
            destination.parent,
            context="creation scaffold parent",
        )
    except (DirectoryPublishError, OSError) as exc:
        raise _operation_failure("creation_scaffold_stage_create_failed", exc) from exc
    stage = destination.parent / (f".{destination.name}.creation-stage-{uuid.uuid4().hex}")
    stage_identity: tuple[int, int] | None = None
    published = False
    operation_reason = "creation_scaffold_stage_create_failed"
    try:
        with create_retained_stage(
            stage,
            expected_parent_identity=parent_identity,
        ) as writer:
            stage_identity = writer.identity
            operation_reason = "creation_scaffold_stage_write_failed"
            for relative in sorted(files, key=lambda item: item.encode("utf-8")):
                writer.write_file(relative, files[relative])
            operation_reason = "creation_scaffold_stage_flush_failed"
            writer.fsync()
            operation_reason = "creation_scaffold_stage_verify_failed"
            _verify_exact_tree(stage, files)
            writer.require_binding()
            operation_reason = "creation_scaffold_finalize_failed"
        operation_reason = "creation_scaffold_publish_failed"
        with publish_directory_noreplace(
            stage,
            destination,
            expected_source_identity=stage_identity,
            expected_parent_identity=parent_identity,
        ) as published_identity:
            if published_identity != stage_identity:
                raise CreationScaffoldError("published creation root identity changed")
            operation_reason = "creation_scaffold_published_verify_failed"
            _verify_exact_tree(destination, files)
            operation_reason = "creation_scaffold_parent_flush_failed"
            fsync_directory(destination.parent, context="creation scaffold parent")
            operation_reason = "creation_scaffold_finalize_failed"
        published = True
    except CreationScaffoldError as exc:
        if exc.reason_code in CREATION_SCAFFOLD_OPERATION_REASON_CODES or exc.reason_code in {
            "creation_scaffold_inputs_invalid",
            "creation_scaffold_recovery_required",
        }:
            raise
        raise _operation_failure(operation_reason, exc) from exc
    except (DirectoryPublishError, FileExistsError, OSError) as exc:
        raise _operation_failure(operation_reason, exc) from exc
    finally:
        if not published and stage_identity is not None and stage.exists():
            primary = sys.exception()
            try:
                quarantine_and_remove_verified_directory(
                    stage,
                    stage_identity,
                    verify=lambda root: _verify_exact_tree(root, files),
                )
            except DirectoryPublishRecoveryRequiredError as cleanup_error:
                failure = CreationScaffoldError(
                    "creation scaffold recovery_required: the exact owned stage was "
                    f"retained without namespace deletion: {cleanup_error}",
                    reason_code="creation_scaffold_recovery_required",
                    recovery_evidence={
                        "stage": {
                            "locator": stage.name,
                            "identity": [stage_identity[0], stage_identity[1]],
                            "retention": "active",
                        }
                    },
                )
                if primary is not None:
                    failure.add_note(f"publication failure: {primary}")
                raise failure from cleanup_error
            except (DirectoryPublishError, CreationScaffoldError) as cleanup_error:
                if primary is not None:
                    primary.add_note(
                        f"creation scaffold cleanup retained evidence: {cleanup_error}"
                    )
                else:
                    raise _operation_failure(
                        "creation_scaffold_finalize_failed",
                        cleanup_error,
                    ) from cleanup_error
    try:
        final_identity = directory_identity(destination, context="published creation root")
    except (DirectoryPublishError, OSError) as exc:
        raise _operation_failure("creation_scaffold_finalize_failed", exc) from exc
    if final_identity != stage_identity:
        mismatch = CreationScaffoldError("published creation root identity changed")
        raise _operation_failure("creation_scaffold_finalize_failed", mismatch) from mismatch
    return destination / "project.json"
