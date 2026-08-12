"""Generate the neutral systemic-simulation authoring fixture."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from worldforge.creation_contracts import (
    canonical_creation_hash,
    validate_creation_documents,
)
from worldforge.creation_readiness import (
    build_creation_handoff,
    build_creation_readiness,
)
from worldforge.creation_workflow import initial_creation_workflow_status
from worldforge.game_analysis import analyze_gamepack
from worldforge.gamepack import build_gamepack
from worldforge.integrity import canonical_json_bytes
from worldforge.phase_report_v3 import (
    build_phase_output_evidence_v2,
    build_phase_report_v3,
    document_identity,
)

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "examples/multigenre-contracts/abstract-puzzle"
DESTINATION = ROOT / "examples/multigenre-contracts/systemic-simulation"
AUTHORING_CASES = {
    "action-framing": {
        "project_id": "action_framing",
        "title": "Action Framing",
        "family": "action",
        "player_role": "mission operative",
        "promise": "Complete a bounded action mission framed by authored narrative context.",
        "goal": "complete the authored mission objective",
        "challenge": "timed action planning",
        "progression": "one action mission with framing narrative",
        "state_id": "mission_progress",
        "advance_verb": "engage",
        "advance_action": "engage_objective",
        "activity_id": "action_mission",
        "system_id": "mission_resolution",
        "system_type": "simulation_scenario",
        "mechanic_id": "objective_engagement",
        "asset_id": "action_hud",
        "asset_binding_id": "action_hud_visual",
        "narrative": "framing",
        "authoring_feature": "action:realtime_combat",
        "world_presence": "diegetic",
        "world_title": "Authored mission arena",
    },
    "faction-strategy": {
        "project_id": "faction_strategy",
        "title": "Faction Strategy",
        "family": "strategy",
        "player_role": "faction strategist",
        "promise": "Direct an authored faction toward an explicit reviewed victory condition.",
        "goal": "reach the authored faction influence victory threshold",
        "challenge": "deterministic faction allocation",
        "progression": "authored turns toward a declared faction victory",
        "state_id": "faction_influence",
        "advance_verb": "direct",
        "advance_action": "direct_north_1",
        "activity_id": "faction_campaign",
        "system_id": "faction_influence_system",
        "system_type": "economy",
        "mechanic_id": "faction_direction",
        "asset_id": "strategy_map",
        "asset_binding_id": "strategy_map_visual",
        "narrative": None,
        "authoring_feature": "strategy:turn_order",
        "world_presence": "diegetic",
        "world_title": "North and South faction theater",
    },
    "modular-roguelite": {
        "project_id": "modular_roguelite",
        "title": "Modular Roguelite",
        "family": "roguelite",
        "primary_family": "action",
        "player_role": "expedition leader",
        "promise": (
            "Advance through a bounded expedition assembled from authored modular storylets."
        ),
        "goal": "complete the reviewed expedition depth",
        "challenge": "deterministic expedition sequencing",
        "progression": "bounded run through modular authored storylets",
        "state_id": "expedition_depth",
        "advance_verb": "venture",
        "advance_action": "advance_expedition",
        "activity_id": "roguelite_run",
        "system_id": "storylet_sequence",
        "system_type": "simulation_scenario",
        "mechanic_id": "expedition_advance",
        "asset_id": "storylet_cards",
        "asset_binding_id": "storylet_card_visual",
        "narrative": "storylets",
        "authoring_feature": "roguelite:run_reset",
        "world_presence": "symbolic",
        "world_title": "Modular expedition route",
    },
    "sports-career": {
        "project_id": "sports_career",
        "title": "Sports Career",
        "family": "sports",
        "player_role": "career athlete",
        "promise": (
            "Play a bounded authored season whose results advance a transparent career record."
        ),
        "goal": "reach the authored season points target",
        "challenge": "deterministic match planning",
        "progression": "one reviewed season within an authored sports career",
        "state_id": "season_points",
        "advance_verb": "compete",
        "advance_action": "record_round_1_win",
        "activity_id": "career_season",
        "system_id": "season_standings",
        "system_type": "season",
        "mechanic_id": "career_match",
        "asset_id": "season_dashboard",
        "asset_binding_id": "season_dashboard_visual",
        "narrative": None,
        "authoring_feature": "sports:season",
        "world_presence": "diegetic",
        "world_title": "Authored league season",
    },
}


def _read(relative: str) -> dict[str, Any]:
    value = json.loads((BASE / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{relative} must contain an object")
    return value


def _seal(document: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(document)
    result["content_hash"] = canonical_creation_hash(result)
    return result


def _reference(document: dict[str, Any], *, identifier_field: str, path: str) -> dict[str, Any]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[identifier_field],
        "path": path,
        "content_hash": document["content_hash"],
    }


def _build_documents() -> tuple[object, dict[str, bytes]]:
    project_id = "systemic_simulation"
    profile = _read("profile.json")
    profile.update(
        {
            "profile_id": "systemic_simulation_profile",
            "project_id": project_id,
            "title": "Systemic simulation profile",
        }
    )
    profile["experience"] = {
        "player_promise": (
            "Operate a bounded deterministic system and observe transparent state changes."
        ),
        "audiences": ["systems design reviewers"],
        "experience_goals": [
            "inspect causal state",
            "verify deterministic transitions",
        ],
    }
    profile["gameplay"].update(
        {
            "primary_family": "simulation",
            "mechanic_tags": ["simulation:bounded_system"],
            "player_role": "system operator",
            "core_loop": [
                "inspect current state",
                "apply one legal operation",
                "observe deterministic consequences",
                "evaluate the target state",
            ],
            "rule_model": "deterministic bounded state transition system",
            "goal_model": "reach the reviewed target state",
            "challenge_model": "causal systems reasoning",
            "failure_recovery": "explicit deterministic restart",
            "progression": "one bounded validation scenario",
            "session_structure": "single bounded session",
            "dependencies": {
                "authored": ["simulation:initial_state"],
                "procedural": [],
                "systemic": ["simulation:transition_rules"],
            },
        }
    )
    profile["world"] = {
        "presence": "abstract",
        "spatial_topology": "symbolic state graph",
        "scale": "single bounded system",
        "time_model": "discrete operations",
        "simulation_depth": "bounded deterministic",
        "simulated_domains": ["system:state_transition"],
        "persistence": "session state",
        "spatial_structure": "authored symbolic topology",
    }
    profile["fiction"] = {
        "genres": [],
        "tones": ["analytical"],
        "tags": ["fiction:none"],
    }
    profile["production"]["content_modes"] = {
        "assets": "not_applicable",
        "gameplay": "authored",
        "narrative": "not_applicable",
        "world": "authored",
    }
    profile["production"]["selection_policy"] = (
        "no external asset candidate is required for this authoring-only fixture"
    )
    profile["runtime_target"].update(
        {
            "requested_adapter": None,
            "asset_formats": [],
            "renderer": "raylib",
        }
    )
    profile = _seal(profile)

    activity = _read("source/activities/puzzle.json")
    activity.update(
        {
            "project_id": project_id,
            "module_id": "simulation_activities",
            "title": "Systemic simulation activities",
        }
    )
    activity["activities"][0].update(
        {
            "activity_type": "scenario",
            "title": "Bounded system scenario",
            "provenance": "authored neutral systemic-simulation fixture",
            "asset_binding_ids": [],
        }
    )
    activity = _seal(activity)

    system = _read("source/systems/rules.json")
    system.update(
        {
            "project_id": project_id,
            "module_id": "simulation_rules",
            "title": "Systemic simulation rules",
        }
    )
    system["systems"][0].update(
        {
            "system_type": "simulation_scenario",
            "title": "Apply one deterministic system transition",
            "asset_binding_ids": [],
        }
    )
    system = _seal(system)

    logic = _read("source/logic/puzzle.json")
    logic.update(
        {
            "project_id": project_id,
            "module_id": "simulation_logic",
            "title": "Neutral bounded systemic-simulation logic",
        }
    )
    for hook in logic["presentation_hooks"]:
        hook["asset_binding_ids"] = []
    for mechanic in logic["mechanics"]:
        mechanic["asset_binding_ids"] = []
    logic = _seal(logic)

    world = _seal(
        {
            "format": "world-forge.world_module",
            "format_version": 1,
            "module_id": "simulation_space",
            "project_id": project_id,
            "module_type": "space",
            "title": "Abstract simulation space",
            "spaces": [
                {
                    "id": "system_state_space",
                    "name": "Bounded state space",
                    "topology": "abstract",
                }
            ],
            "extensions": [],
            "content_hash": "",
        }
    )

    manifest = _seal(
        {
            "format": "world-forge.creation_source_manifest",
            "format_version": 1,
            "project_id": project_id,
            "profile": _reference(profile, identifier_field="profile_id", path="profile.json"),
            "modules": {
                "activity_modules": [
                    _reference(
                        activity,
                        identifier_field="module_id",
                        path="activities/simulation.json",
                    )
                ],
                "logic_modules": [
                    _reference(
                        logic,
                        identifier_field="module_id",
                        path="logic/simulation.json",
                    )
                ],
                "narrative_modules": [],
                "system_modules": [
                    _reference(
                        system,
                        identifier_field="module_id",
                        path="systems/simulation.json",
                    )
                ],
                "world_modules": [
                    _reference(
                        world,
                        identifier_field="module_id",
                        path="world/space.json",
                    )
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
            "project_kind": "game",
            "project_id": project_id,
            "title": "Systemic Simulation",
            "project_version": "1.0.0",
            "default_locale": "en",
            "profile": _reference(profile, identifier_field="profile_id", path="profile.json"),
            "source_manifest": _reference(
                manifest,
                identifier_field="project_id",
                path="source/manifest.json",
            ),
            "extensions": [],
            "content_hash": "",
        }
    )
    loaded = validate_creation_documents(
        project,
        profile,
        manifest,
        (world,),
        (activity,),
        (),
        (system,),
        (logic,),
    )
    gamepack = build_gamepack(loaded)
    analysis = analyze_gamepack(gamepack)
    if gamepack["asset_requirements"] != [] or analysis["status"] != "unsupported":
        raise ValueError("systemic simulation must remain asset-free and analyzer-unsupported")
    status = initial_creation_workflow_status(loaded)
    readiness = build_creation_readiness(
        loaded,
        artifacts=(gamepack, analysis),
    )
    handoff = build_creation_handoff(
        loaded,
        status=status,
        readiness=readiness,
        artifacts=(gamepack, analysis),
    )

    reviewer = {"id": "lead_reviewer", "role": "validation_analyst"}

    def report(
        *,
        phase: str,
        status_value: str,
        rationale_code: str,
        role: str | None = None,
        subject: dict[str, Any] | None = None,
        registry: tuple[dict[str, Any], ...] = (),
    ) -> dict[str, Any]:
        output = None
        if status_value == "ready":
            assert role is not None and subject is not None
            output = build_phase_output_evidence_v2(
                evidence_id=f"{phase}_output",
                phase=phase,
                role=role,
                subject=document_identity(subject),
                reviewer_id=reviewer["id"],
                reviewer_role=reviewer["role"],
                source_project=loaded,
                artifact_registry=registry,
            )
        return build_phase_report_v3(
            loaded,
            phase=phase,
            status=status_value,
            rationale_code=rationale_code,
            rationale_message=(
                "The exact reviewed profile and artifact evidence support this phase status."
            ),
            evidence=(
                {
                    "evidence_id": "reviewed_profile",
                    "claim": "The exact creation profile was reviewed.",
                    "subject": document_identity(profile),
                },
            ),
            output_evidence=output,
            reviewer_id=reviewer["id"],
            reviewer_role=reviewer["role"],
            invalidation_dependencies=None,
            artifact_registry=registry,
        )

    p11 = report(
        phase="p11_art_audio",
        status_value="not_applicable",
        rationale_code="assets_not_applicable",
    )
    p12 = report(
        phase="p12_asset_specs",
        status_value="not_applicable",
        rationale_code="assets_not_applicable",
    )
    p13 = report(
        phase="p13_asset_production",
        status_value="ready",
        rationale_code="phase_ready",
        role="runtime_compatibility",
        subject=readiness,
        registry=(gamepack, analysis, readiness),
    )
    p14 = report(
        phase="p14_handoff",
        status_value="ready",
        rationale_code="phase_ready",
        role="implementation_handoff",
        subject=handoff,
        registry=(gamepack, analysis, status, readiness, handoff),
    )
    files = {
        "project.json": canonical_json_bytes(project),
        "profile.json": canonical_json_bytes(profile),
        "source/manifest.json": canonical_json_bytes(manifest),
        "source/world/space.json": canonical_json_bytes(world),
        "source/activities/simulation.json": canonical_json_bytes(activity),
        "source/systems/simulation.json": canonical_json_bytes(system),
        "source/logic/simulation.json": canonical_json_bytes(logic),
        ".worldforge/status.json": canonical_json_bytes(status),
        ".worldforge/phase_reports/README.md": (
            b"# Phase reports\n\n"
            b"Immutable content-addressed phase-report v3 documents are stored here.\n"
        ),
        "artifacts/systemic-simulation.gamepack.json": canonical_json_bytes(gamepack),
        "artifacts/systemic-simulation.game-analysis.json": canonical_json_bytes(analysis),
        "artifacts/systemic-simulation.readiness.json": canonical_json_bytes(readiness),
        "artifacts/systemic-simulation.handoff.json": canonical_json_bytes(handoff),
        "phase-reports/p11_art_audio.json": canonical_json_bytes(p11),
        "phase-reports/p12_asset_specs.json": canonical_json_bytes(p12),
        "phase-reports/p13_asset_production.json": canonical_json_bytes(p13),
        "phase-reports/p14_handoff.json": canonical_json_bytes(p14),
    }
    for document in (
        loaded.project,
        loaded.profile,
        loaded.manifest,
        *loaded.world_modules,
        *loaded.activity_modules,
        *loaded.narrative_modules,
        *loaded.system_modules,
        *loaded.logic_modules,
    ):
        identity = document_identity(document)
        files[f".worldforge/artifact_history/{identity['content_hash']}.json"] = (
            canonical_json_bytes(document)
        )
    return loaded, files


def _authoring_logic(descriptor: dict[str, Any]) -> dict[str, Any]:
    project_id = descriptor["project_id"]
    state_id = descriptor["state_id"]
    action_id = descriptor["advance_action"]
    verb_id = descriptor["advance_verb"]
    activity_id = descriptor["activity_id"]
    system_id = descriptor["system_id"]
    mechanic_id = descriptor["mechanic_id"]
    binding_id = descriptor["asset_binding_id"]
    document = {
        "format": "world-forge.logic_module",
        "format_version": 1,
        "module_id": f"{project_id}_logic",
        "project_id": project_id,
        "title": f"Neutral authored {descriptor['family']} logic",
        "state_variables": [
            {
                "id": state_id,
                "type": "integer",
                "initial": 0,
                "minimum": 0,
                "maximum": 3,
                "mutability": "mutable",
                "persistence": "saved",
            }
        ],
        "conditions": [
            {
                "id": "scenario_ready",
                "action_id": None,
                "operator": "constant",
                "value": True,
            },
            {
                "id": "victory_condition",
                "action_id": None,
                "operator": "compare",
                "comparison": "greater_or_equal",
                "left": {"kind": "state", "state_id": state_id},
                "right": {"kind": "literal", "value": 3, "value_type": "integer"},
            },
        ],
        "effects": [
            {
                "id": "advance_progress",
                "action_id": action_id,
                "operation": "increment",
                "state_id": state_id,
                "amount": {"kind": "literal", "value": 1, "value_type": "integer"},
                "invalid_transition_policy": "reject_transition",
            },
            {
                "id": "reset_progress",
                "action_id": "restart_scenario",
                "operation": "reset",
                "state_id": state_id,
                "invalid_transition_policy": "reject_transition",
            },
        ],
        "rules": [
            {
                "id": "advance_rule",
                "action_id": action_id,
                "condition_ids": [],
                "effect_ids": ["advance_progress"],
                "event_ids": ["progress_advanced"],
                "order": 0,
            },
            {
                "id": "restart_rule",
                "action_id": "restart_scenario",
                "condition_ids": [],
                "effect_ids": ["reset_progress"],
                "event_ids": ["scenario_restarted"],
                "order": 1,
            },
        ],
        "events": [{"id": "progress_advanced"}, {"id": "scenario_restarted"}],
        "actions": [
            {
                "id": action_id,
                "core_verb_id": verb_id,
                "parameters": [],
                "rule_ids": ["advance_rule"],
                "presentation_hook_ids": ["progress_feedback", "progress_view"],
                "required_feature_ids": [
                    "logic:deterministic_actions",
                    "logic:finite_state",
                ],
                "source_bindings": [
                    {"kind": "activity", "source_id": activity_id},
                    {"kind": "system", "source_id": system_id},
                ],
            },
            {
                "id": "restart_scenario",
                "core_verb_id": "restart",
                "parameters": [],
                "rule_ids": ["restart_rule"],
                "presentation_hook_ids": ["progress_feedback"],
                "required_feature_ids": ["logic:finite_state"],
                "source_bindings": [{"kind": "activity", "source_id": activity_id}],
            },
        ],
        "mechanics": [
            {
                "id": mechanic_id,
                "core_verb_id": verb_id,
                "action_id": action_id,
                "authoritative_state_ids": [state_id],
                "condition_ids": [],
                "rule_ids": ["advance_rule"],
                "effect_ids": ["advance_progress"],
                "event_ids": ["progress_advanced"],
                "presentation_hook_ids": ["progress_feedback", "progress_view"],
                "asset_binding_ids": [binding_id],
                "required_feature_ids": [
                    "logic:deterministic_actions",
                    "logic:finite_state",
                ],
            },
            {
                "id": "restart_mechanic",
                "core_verb_id": "restart",
                "action_id": "restart_scenario",
                "authoritative_state_ids": [state_id],
                "condition_ids": [],
                "rule_ids": ["restart_rule"],
                "effect_ids": ["reset_progress"],
                "event_ids": ["scenario_restarted"],
                "presentation_hook_ids": ["progress_feedback"],
                "asset_binding_ids": [binding_id],
                "required_feature_ids": ["logic:finite_state"],
            },
        ],
        "goals": [
            {
                "id": "authored_victory",
                "condition_ids": ["victory_condition"],
                "success_ending_id": "scenario_complete",
            }
        ],
        "failures": [],
        "endings": [
            {
                "id": "scenario_complete",
                "kind": "success",
                "condition_ids": ["victory_condition"],
                "event_ids": [],
                "presentation_hook_ids": ["ending_feedback"],
            }
        ],
        "presentation_hooks": [
            {"id": "ending_feedback", "kind": "ending", "asset_binding_ids": [binding_id]},
            {
                "id": "progress_feedback",
                "kind": "feedback",
                "asset_binding_ids": [binding_id],
            },
            {"id": "progress_view", "kind": "board", "asset_binding_ids": [binding_id]},
        ],
        "extensions": [],
        "content_hash": "",
    }

    if project_id == "action_framing":
        document["state_variables"].append(
            {
                "id": "mission_time_remaining",
                "type": "integer",
                "initial": 3,
                "minimum": 0,
                "maximum": 3,
                "mutability": "mutable",
                "persistence": "saved",
            }
        )
        document["conditions"].append(
            {
                "id": "mission_time_available",
                "action_id": action_id,
                "operator": "compare",
                "comparison": "greater_than",
                "left": {"kind": "state", "state_id": "mission_time_remaining"},
                "right": {"kind": "literal", "value": 0, "value_type": "integer"},
            }
        )
        document["effects"].extend(
            [
                {
                    "id": "consume_mission_time",
                    "action_id": action_id,
                    "operation": "increment",
                    "state_id": "mission_time_remaining",
                    "amount": {"kind": "literal", "value": -1, "value_type": "integer"},
                    "invalid_transition_policy": "reject_transition",
                },
                {
                    "id": "reset_mission_time",
                    "action_id": "restart_scenario",
                    "operation": "reset",
                    "state_id": "mission_time_remaining",
                    "invalid_transition_policy": "reject_transition",
                },
            ]
        )
        document["rules"][0]["condition_ids"] = ["mission_time_available"]
        document["rules"][0]["effect_ids"] = ["advance_progress", "consume_mission_time"]
        document["rules"][1]["effect_ids"] = ["reset_progress", "reset_mission_time"]
        for mechanic in document["mechanics"]:
            mechanic["authoritative_state_ids"] = [state_id, "mission_time_remaining"]
        document["mechanics"][0]["condition_ids"] = ["mission_time_available"]
        document["mechanics"][0]["effect_ids"] = [
            "advance_progress",
            "consume_mission_time",
        ]
        document["mechanics"][1]["effect_ids"] = ["reset_progress", "reset_mission_time"]

    elif project_id == "faction_strategy":
        allocation_actions = (
            ("direct_north_1", "north", 1),
            ("direct_north_2", "north", 2),
            ("direct_south_1", "south", 1),
            ("direct_south_2", "south", 2),
        )
        domain_features = [
            descriptor["authoring_feature"],
            "logic:deterministic_actions",
            "logic:finite_state",
        ]
        document["state_variables"] = [
            {
                "id": "north_influence",
                "type": "integer",
                "initial": 0,
                "minimum": 0,
                "maximum": 4,
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "south_influence",
                "type": "integer",
                "initial": 0,
                "minimum": 0,
                "maximum": 4,
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "command_points",
                "type": "integer",
                "initial": 4,
                "minimum": 0,
                "maximum": 4,
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "turns_remaining",
                "type": "integer",
                "initial": 3,
                "minimum": 0,
                "maximum": 3,
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "last_directed_faction",
                "type": "string",
                "initial": "none",
                "allowed_values": ["none", "north", "south"],
                "mutability": "mutable",
                "persistence": "saved",
            },
        ]
        document["conditions"] = [
            {
                "id": "scenario_ready",
                "action_id": None,
                "operator": "constant",
                "value": True,
            },
            {
                "id": "victory_condition",
                "action_id": None,
                "operator": "compare",
                "comparison": "greater_or_equal",
                "left": {"kind": "state", "state_id": "north_influence"},
                "right": {"kind": "literal", "value": 3, "value_type": "integer"},
            },
        ]
        document["effects"] = []
        document["rules"] = []
        document["actions"] = []
        document["mechanics"] = []
        document["events"] = [{"id": "faction_directed"}, {"id": "scenario_restarted"}]
        for direct_action, faction, amount in allocation_actions:
            allocation_condition = f"allocation_available_for_{direct_action}"
            turn_condition = f"turn_available_for_{direct_action}"
            document["conditions"].extend(
                [
                    {
                        "id": allocation_condition,
                        "action_id": direct_action,
                        "operator": "compare",
                        "comparison": "greater_or_equal",
                        "left": {"kind": "state", "state_id": "command_points"},
                        "right": {
                            "kind": "literal",
                            "value": amount,
                            "value_type": "integer",
                        },
                    },
                    {
                        "id": turn_condition,
                        "action_id": direct_action,
                        "operator": "compare",
                        "comparison": "greater_than",
                        "left": {"kind": "state", "state_id": "turns_remaining"},
                        "right": {"kind": "literal", "value": 0, "value_type": "integer"},
                    },
                ]
            )
            effect_ids = [
                f"{direct_action}_influence",
                f"{direct_action}_record_target",
                f"{direct_action}_spend",
                f"{direct_action}_turn",
            ]
            document["effects"].extend(
                [
                    {
                        "id": effect_ids[0],
                        "action_id": direct_action,
                        "operation": "increment",
                        "state_id": f"{faction}_influence",
                        "amount": {
                            "kind": "literal",
                            "value": amount,
                            "value_type": "integer",
                        },
                        "invalid_transition_policy": "reject_transition",
                    },
                    {
                        "id": effect_ids[1],
                        "action_id": direct_action,
                        "operation": "set",
                        "state_id": "last_directed_faction",
                        "value": {
                            "kind": "literal",
                            "value": faction,
                            "value_type": "string",
                        },
                        "invalid_transition_policy": "reject_transition",
                    },
                    {
                        "id": effect_ids[2],
                        "action_id": direct_action,
                        "operation": "increment",
                        "state_id": "command_points",
                        "amount": {
                            "kind": "literal",
                            "value": -amount,
                            "value_type": "integer",
                        },
                        "invalid_transition_policy": "reject_transition",
                    },
                    {
                        "id": effect_ids[3],
                        "action_id": direct_action,
                        "operation": "increment",
                        "state_id": "turns_remaining",
                        "amount": {"kind": "literal", "value": -1, "value_type": "integer"},
                        "invalid_transition_policy": "reject_transition",
                    },
                ]
            )
            rule_id = f"{direct_action}_rule"
            document["rules"].append(
                {
                    "id": rule_id,
                    "action_id": direct_action,
                    "condition_ids": [allocation_condition, turn_condition],
                    "effect_ids": effect_ids,
                    "event_ids": ["faction_directed"],
                    "order": 0,
                }
            )
            document["actions"].append(
                {
                    "id": direct_action,
                    "core_verb_id": verb_id,
                    "parameters": [],
                    "rule_ids": [rule_id],
                    "presentation_hook_ids": ["progress_feedback", "progress_view"],
                    "required_feature_ids": list(domain_features),
                    "source_bindings": [
                        {"kind": "activity", "source_id": activity_id},
                        {"kind": "system", "source_id": system_id},
                    ],
                }
            )
            document["mechanics"].append(
                {
                    "id": (
                        mechanic_id
                        if direct_action == descriptor["advance_action"]
                        else f"faction_direction_{faction}_{amount}"
                    ),
                    "core_verb_id": verb_id,
                    "action_id": direct_action,
                    "authoritative_state_ids": [
                        "command_points",
                        f"{faction}_influence",
                        "last_directed_faction",
                        "turns_remaining",
                    ],
                    "condition_ids": [allocation_condition, turn_condition],
                    "rule_ids": [rule_id],
                    "effect_ids": effect_ids,
                    "event_ids": ["faction_directed"],
                    "presentation_hook_ids": ["progress_feedback", "progress_view"],
                    "asset_binding_ids": [binding_id],
                    "required_feature_ids": list(domain_features),
                }
            )

        reset_effects = [
            "reset_north_influence",
            "reset_south_influence",
            "reset_command_points",
            "reset_turns_remaining",
            "reset_last_directed_faction",
        ]
        document["effects"].extend(
            {
                "id": f"reset_{state}",
                "action_id": "restart_scenario",
                "operation": "reset",
                "state_id": state,
                "invalid_transition_policy": "reject_transition",
            }
            for state in (
                "north_influence",
                "south_influence",
                "command_points",
                "turns_remaining",
                "last_directed_faction",
            )
        )
        document["rules"].append(
            {
                "id": "restart_rule",
                "action_id": "restart_scenario",
                "condition_ids": [],
                "effect_ids": reset_effects,
                "event_ids": ["scenario_restarted"],
                "order": 0,
            }
        )
        document["actions"].append(
            {
                "id": "restart_scenario",
                "core_verb_id": "restart",
                "parameters": [],
                "rule_ids": ["restart_rule"],
                "presentation_hook_ids": ["progress_feedback"],
                "required_feature_ids": ["logic:finite_state"],
                "source_bindings": [{"kind": "activity", "source_id": activity_id}],
            }
        )
        document["mechanics"].append(
            {
                "id": "restart_mechanic",
                "core_verb_id": "restart",
                "action_id": "restart_scenario",
                "authoritative_state_ids": [state["id"] for state in document["state_variables"]],
                "condition_ids": [],
                "rule_ids": ["restart_rule"],
                "effect_ids": reset_effects,
                "event_ids": ["scenario_restarted"],
                "presentation_hook_ids": ["progress_feedback"],
                "asset_binding_ids": [binding_id],
                "required_feature_ids": ["logic:finite_state"],
            }
        )

    elif project_id == "modular_roguelite":
        recovery_action = "recover_after_death"
        domain_features = [
            descriptor["authoring_feature"],
            "logic:deterministic_actions",
            "logic:finite_state",
        ]
        document["state_variables"] = [
            {
                "id": "run_active",
                "type": "boolean",
                "initial": True,
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "run_depth",
                "type": "integer",
                "initial": 0,
                "minimum": 0,
                "maximum": 3,
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "run_health",
                "type": "integer",
                "initial": 2,
                "minimum": 0,
                "maximum": 2,
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "run_deaths",
                "type": "integer",
                "initial": 0,
                "minimum": 0,
                "maximum": 99,
                "mutability": "mutable",
                "persistence": "saved",
            },
        ]
        document["conditions"] = [
            {
                "id": "advance_depth_available",
                "action_id": action_id,
                "operator": "compare",
                "comparison": "less_than",
                "left": {"kind": "state", "state_id": "run_depth"},
                "right": {"kind": "literal", "value": 3, "value_type": "integer"},
            },
            {
                "id": "advance_run_active",
                "action_id": action_id,
                "operator": "compare",
                "comparison": "equal",
                "left": {"kind": "state", "state_id": "run_active"},
                "right": {"kind": "literal", "value": True, "value_type": "boolean"},
            },
            {
                "id": "death_condition",
                "action_id": None,
                "operator": "compare",
                "comparison": "less_or_equal",
                "left": {"kind": "state", "state_id": "run_health"},
                "right": {"kind": "literal", "value": 0, "value_type": "integer"},
            },
            {
                "id": "endure_health_available",
                "action_id": "endure_storylet_hazard",
                "operator": "compare",
                "comparison": "greater_than",
                "left": {"kind": "state", "state_id": "run_health"},
                "right": {"kind": "literal", "value": 1, "value_type": "integer"},
            },
            {
                "id": "endure_run_active",
                "action_id": "endure_storylet_hazard",
                "operator": "compare",
                "comparison": "equal",
                "left": {"kind": "state", "state_id": "run_active"},
                "right": {"kind": "literal", "value": True, "value_type": "boolean"},
            },
            {
                "id": "fall_health_critical",
                "action_id": "fall_to_storylet_hazard",
                "operator": "compare",
                "comparison": "equal",
                "left": {"kind": "state", "state_id": "run_health"},
                "right": {"kind": "literal", "value": 1, "value_type": "integer"},
            },
            {
                "id": "fall_run_active",
                "action_id": "fall_to_storylet_hazard",
                "operator": "compare",
                "comparison": "equal",
                "left": {"kind": "state", "state_id": "run_active"},
                "right": {"kind": "literal", "value": True, "value_type": "boolean"},
            },
            {
                "id": "recovery_health_depleted",
                "action_id": recovery_action,
                "operator": "compare",
                "comparison": "less_or_equal",
                "left": {"kind": "state", "state_id": "run_health"},
                "right": {"kind": "literal", "value": 0, "value_type": "integer"},
            },
            {
                "id": "recovery_run_inactive",
                "action_id": recovery_action,
                "operator": "compare",
                "comparison": "equal",
                "left": {"kind": "state", "state_id": "run_active"},
                "right": {"kind": "literal", "value": False, "value_type": "boolean"},
            },
            {
                "id": "run_at_entry_depth",
                "action_id": None,
                "operator": "compare",
                "comparison": "equal",
                "left": {"kind": "state", "state_id": "run_depth"},
                "right": {"kind": "literal", "value": 0, "value_type": "integer"},
            },
            {
                "id": "run_is_active",
                "action_id": None,
                "operator": "compare",
                "comparison": "equal",
                "left": {"kind": "state", "state_id": "run_active"},
                "right": {"kind": "literal", "value": True, "value_type": "boolean"},
            },
            {
                "id": "run_is_inactive",
                "action_id": None,
                "operator": "compare",
                "comparison": "equal",
                "left": {"kind": "state", "state_id": "run_active"},
                "right": {"kind": "literal", "value": False, "value_type": "boolean"},
            },
            {
                "id": "scenario_ready",
                "action_id": None,
                "operator": "constant",
                "value": True,
            },
            {
                "id": "storylet_progressed",
                "action_id": None,
                "operator": "compare",
                "comparison": "greater_or_equal",
                "left": {"kind": "state", "state_id": "run_depth"},
                "right": {"kind": "literal", "value": 1, "value_type": "integer"},
            },
            {
                "id": "victory_condition",
                "action_id": None,
                "operator": "compare",
                "comparison": "greater_or_equal",
                "left": {"kind": "state", "state_id": "run_depth"},
                "right": {"kind": "literal", "value": 3, "value_type": "integer"},
            },
        ]
        document["effects"] = [
            {
                "id": "advance_progress",
                "action_id": action_id,
                "operation": "increment",
                "state_id": "run_depth",
                "amount": {"kind": "literal", "value": 1, "value_type": "integer"},
                "invalid_transition_policy": "reject_transition",
            },
            {
                "id": "consume_run_health",
                "action_id": "endure_storylet_hazard",
                "operation": "increment",
                "state_id": "run_health",
                "amount": {"kind": "literal", "value": -1, "value_type": "integer"},
                "invalid_transition_policy": "reject_transition",
            },
            {
                "id": "deactivate_run",
                "action_id": "fall_to_storylet_hazard",
                "operation": "set",
                "state_id": "run_active",
                "value": {"kind": "literal", "value": False, "value_type": "boolean"},
                "invalid_transition_policy": "reject_transition",
            },
            {
                "id": "deplete_run_health",
                "action_id": "fall_to_storylet_hazard",
                "operation": "increment",
                "state_id": "run_health",
                "amount": {"kind": "literal", "value": -1, "value_type": "integer"},
                "invalid_transition_policy": "reject_transition",
            },
            {
                "id": "reactivate_run",
                "action_id": recovery_action,
                "operation": "reset",
                "state_id": "run_active",
                "invalid_transition_policy": "reject_transition",
            },
            {
                "id": "record_run_death",
                "action_id": recovery_action,
                "operation": "increment",
                "state_id": "run_deaths",
                "amount": {"kind": "literal", "value": 1, "value_type": "integer"},
                "invalid_transition_policy": "reject_transition",
            },
            {
                "id": "reset_run_depth",
                "action_id": recovery_action,
                "operation": "reset",
                "state_id": "run_depth",
                "invalid_transition_policy": "reject_transition",
            },
            {
                "id": "restore_run_health",
                "action_id": recovery_action,
                "operation": "reset",
                "state_id": "run_health",
                "invalid_transition_policy": "reject_transition",
            },
        ]
        document["rules"] = [
            {
                "id": "advance_rule",
                "action_id": action_id,
                "condition_ids": ["advance_depth_available", "advance_run_active"],
                "effect_ids": ["advance_progress"],
                "event_ids": ["progress_advanced"],
                "order": 0,
            },
            {
                "id": "endure_hazard_rule",
                "action_id": "endure_storylet_hazard",
                "condition_ids": ["endure_health_available", "endure_run_active"],
                "effect_ids": ["consume_run_health"],
                "event_ids": ["hazard_endured"],
                "order": 0,
            },
            {
                "id": "fall_hazard_rule",
                "action_id": "fall_to_storylet_hazard",
                "condition_ids": ["fall_health_critical", "fall_run_active"],
                "effect_ids": ["deactivate_run", "deplete_run_health"],
                "event_ids": ["run_failed"],
                "order": 0,
            },
            {
                "id": "recovery_rule",
                "action_id": recovery_action,
                "condition_ids": ["recovery_health_depleted", "recovery_run_inactive"],
                "effect_ids": [
                    "reactivate_run",
                    "record_run_death",
                    "reset_run_depth",
                    "restore_run_health",
                ],
                "event_ids": ["run_recovered"],
                "order": 0,
            },
        ]
        document["events"] = [
            {"id": "hazard_endured"},
            {"id": "progress_advanced"},
            {"id": "run_failed"},
            {"id": "run_recovered"},
        ]
        action_specs = (
            (
                action_id,
                verb_id,
                "advance_rule",
                ["advance_depth_available", "advance_run_active"],
                ["advance_progress"],
                ["progress_advanced"],
                mechanic_id,
                ["run_active", "run_depth"],
            ),
            (
                "endure_storylet_hazard",
                verb_id,
                "endure_hazard_rule",
                ["endure_health_available", "endure_run_active"],
                ["consume_run_health"],
                ["hazard_endured"],
                "storylet_hazard_endurance",
                ["run_active", "run_health"],
            ),
            (
                "fall_to_storylet_hazard",
                verb_id,
                "fall_hazard_rule",
                ["fall_health_critical", "fall_run_active"],
                ["deactivate_run", "deplete_run_health"],
                ["run_failed"],
                "storylet_hazard_fall",
                ["run_active", "run_health"],
            ),
            (
                recovery_action,
                "recover",
                "recovery_rule",
                ["recovery_health_depleted", "recovery_run_inactive"],
                [
                    "reactivate_run",
                    "record_run_death",
                    "reset_run_depth",
                    "restore_run_health",
                ],
                ["run_recovered"],
                "death_recovery",
                ["run_active", "run_deaths", "run_depth", "run_health"],
            ),
        )
        document["actions"] = []
        document["mechanics"] = []
        for (
            owned_action,
            core_verb,
            rule_id,
            condition_ids,
            effect_ids,
            event_ids,
            owned_mechanic,
            authoritative_state_ids,
        ) in action_specs:
            features = (
                domain_features if owned_action != recovery_action else ["logic:finite_state"]
            )
            hooks = (
                ["progress_feedback", "progress_view"]
                if owned_action == action_id
                else ["progress_feedback"]
            )
            document["actions"].append(
                {
                    "id": owned_action,
                    "core_verb_id": core_verb,
                    "parameters": [],
                    "rule_ids": [rule_id],
                    "presentation_hook_ids": hooks,
                    "required_feature_ids": list(features),
                    "source_bindings": [
                        {"kind": "activity", "source_id": activity_id},
                        {"kind": "system", "source_id": system_id},
                    ],
                }
            )
            document["mechanics"].append(
                {
                    "id": owned_mechanic,
                    "core_verb_id": core_verb,
                    "action_id": owned_action,
                    "authoritative_state_ids": authoritative_state_ids,
                    "condition_ids": condition_ids,
                    "rule_ids": [rule_id],
                    "effect_ids": effect_ids,
                    "event_ids": event_ids,
                    "presentation_hook_ids": hooks,
                    "asset_binding_ids": [binding_id],
                    "required_feature_ids": list(features),
                }
            )
        document["failures"] = [
            {
                "id": "run_failed",
                "condition_ids": ["death_condition", "run_is_inactive"],
                "recovery_action_ids": [recovery_action],
            }
        ]

    elif project_id == "sports_career":
        opponents = ("mountain_fc", "river_fc", "valley_fc")
        result_points = (("draw", 1), ("loss", 0), ("win", 3))
        domain_features = [
            descriptor["authoring_feature"],
            "logic:deterministic_actions",
            "logic:finite_state",
        ]
        document["state_variables"] = [
            {
                "id": "season_round",
                "type": "integer",
                "initial": 0,
                "minimum": 0,
                "maximum": 3,
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "standings_points",
                "type": "integer",
                "initial": 0,
                "minimum": 0,
                "maximum": 9,
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "career_points",
                "type": "integer",
                "initial": 0,
                "minimum": 0,
                "maximum": 999,
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "player_team",
                "type": "string",
                "initial": "harbor_fc",
                "allowed_values": ["harbor_fc"],
                "mutability": "constant",
                "persistence": "saved",
            },
            {
                "id": "season_opponents",
                "type": "string_array",
                "initial": list(opponents),
                "allowed_values": list(opponents),
                "min_items": 3,
                "max_items": 3,
                "mutability": "constant",
                "persistence": "saved",
            },
            {
                "id": "match_phase",
                "type": "string",
                "initial": "planning",
                "allowed_values": ["planning", "result_pending"],
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "last_match_plan",
                "type": "string",
                "initial": "pending",
                "allowed_values": ["aggressive", "balanced", "pending"],
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "last_opponent",
                "type": "string",
                "initial": "none",
                "allowed_values": sorted(
                    ["none", *opponents],
                    key=lambda item: item.encode("utf-8"),
                ),
                "mutability": "mutable",
                "persistence": "saved",
            },
            {
                "id": "last_match_result",
                "type": "string",
                "initial": "pending",
                "allowed_values": ["draw", "loss", "pending", "win"],
                "mutability": "mutable",
                "persistence": "saved",
            },
        ]
        document["conditions"] = [
            {
                "id": "scenario_ready",
                "action_id": None,
                "operator": "constant",
                "value": True,
            },
            {
                "id": "season_ending_reached",
                "action_id": None,
                "operator": "compare",
                "comparison": "greater_or_equal",
                "left": {"kind": "state", "state_id": "season_round"},
                "right": {"kind": "literal", "value": 3, "value_type": "integer"},
            },
            {
                "id": "season_points_target_reached",
                "action_id": None,
                "operator": "compare",
                "comparison": "greater_or_equal",
                "left": {"kind": "state", "state_id": "standings_points"},
                "right": {"kind": "literal", "value": 5, "value_type": "integer"},
            },
        ]
        document["effects"] = []
        document["rules"] = []
        document["actions"] = []
        document["mechanics"] = []
        document["events"] = [
            {"id": "match_planned"},
            {"id": "match_resolved"},
            {"id": "scenario_restarted"},
        ]

        for plan in ("aggressive", "balanced"):
            plan_action = f"plan_{plan}_match"
            phase_condition = f"planning_phase_for_{plan_action}"
            remaining_condition = f"match_remaining_for_{plan_action}"
            document["conditions"].extend(
                [
                    {
                        "id": phase_condition,
                        "action_id": plan_action,
                        "operator": "compare",
                        "comparison": "equal",
                        "left": {"kind": "state", "state_id": "match_phase"},
                        "right": {
                            "kind": "literal",
                            "value": "planning",
                            "value_type": "string",
                        },
                    },
                    {
                        "id": remaining_condition,
                        "action_id": plan_action,
                        "operator": "compare",
                        "comparison": "less_than",
                        "left": {"kind": "state", "state_id": "season_round"},
                        "right": {
                            "kind": "literal",
                            "value": 3,
                            "value_type": "integer",
                        },
                    },
                ]
            )
            effect_ids = [f"{plan_action}_record", f"{plan_action}_request_result"]
            document["effects"].extend(
                [
                    {
                        "id": effect_ids[0],
                        "action_id": plan_action,
                        "operation": "set",
                        "state_id": "last_match_plan",
                        "value": {"kind": "literal", "value": plan, "value_type": "string"},
                        "invalid_transition_policy": "reject_transition",
                    },
                    {
                        "id": effect_ids[1],
                        "action_id": plan_action,
                        "operation": "set",
                        "state_id": "match_phase",
                        "value": {
                            "kind": "literal",
                            "value": "result_pending",
                            "value_type": "string",
                        },
                        "invalid_transition_policy": "reject_transition",
                    },
                ]
            )
            rule_id = f"{plan_action}_rule"
            condition_ids = [phase_condition, remaining_condition]
            document["rules"].append(
                {
                    "id": rule_id,
                    "action_id": plan_action,
                    "condition_ids": condition_ids,
                    "effect_ids": effect_ids,
                    "event_ids": ["match_planned"],
                    "order": 0,
                }
            )
            document["actions"].append(
                {
                    "id": plan_action,
                    "core_verb_id": "plan",
                    "parameters": [],
                    "rule_ids": [rule_id],
                    "presentation_hook_ids": ["progress_feedback", "progress_view"],
                    "required_feature_ids": list(domain_features),
                    "source_bindings": [
                        {"kind": "activity", "source_id": activity_id},
                        {"kind": "system", "source_id": system_id},
                    ],
                }
            )
            document["mechanics"].append(
                {
                    "id": f"{plan_action}_mechanic",
                    "core_verb_id": "plan",
                    "action_id": plan_action,
                    "authoritative_state_ids": [
                        "last_match_plan",
                        "match_phase",
                        "season_round",
                    ],
                    "condition_ids": condition_ids,
                    "rule_ids": [rule_id],
                    "effect_ids": effect_ids,
                    "event_ids": ["match_planned"],
                    "presentation_hook_ids": ["progress_feedback", "progress_view"],
                    "asset_binding_ids": [binding_id],
                    "required_feature_ids": list(domain_features),
                }
            )

        for round_number, opponent in enumerate(opponents, start=1):
            for result, points in result_points:
                result_action = f"record_round_{round_number}_{result}"
                phase_condition = f"result_phase_for_{result_action}"
                round_condition = f"round_ready_for_{result_action}"
                schedule_condition = f"schedule_valid_for_{result_action}"
                team_condition = f"team_registered_for_{result_action}"
                condition_ids = [
                    phase_condition,
                    round_condition,
                    schedule_condition,
                    team_condition,
                ]
                document["conditions"].extend(
                    [
                        {
                            "id": phase_condition,
                            "action_id": result_action,
                            "operator": "compare",
                            "comparison": "equal",
                            "left": {"kind": "state", "state_id": "match_phase"},
                            "right": {
                                "kind": "literal",
                                "value": "result_pending",
                                "value_type": "string",
                            },
                        },
                        {
                            "id": round_condition,
                            "action_id": result_action,
                            "operator": "compare",
                            "comparison": "equal",
                            "left": {"kind": "state", "state_id": "season_round"},
                            "right": {
                                "kind": "literal",
                                "value": round_number - 1,
                                "value_type": "integer",
                            },
                        },
                        {
                            "id": schedule_condition,
                            "action_id": result_action,
                            "operator": "index_valid",
                            "array_state_id": "season_opponents",
                            "index": {
                                "kind": "literal",
                                "value": round_number - 1,
                                "value_type": "integer",
                            },
                        },
                        {
                            "id": team_condition,
                            "action_id": result_action,
                            "operator": "compare",
                            "comparison": "equal",
                            "left": {"kind": "state", "state_id": "player_team"},
                            "right": {
                                "kind": "literal",
                                "value": "harbor_fc",
                                "value_type": "string",
                            },
                        },
                    ]
                )
                effect_ids = [
                    f"{result_action}_advance_career",
                    f"{result_action}_advance_round",
                    f"{result_action}_record_opponent",
                    f"{result_action}_record_result",
                    f"{result_action}_return_to_planning",
                    f"{result_action}_update_standings",
                ]
                document["effects"].extend(
                    [
                        {
                            "id": effect_ids[0],
                            "action_id": result_action,
                            "operation": "increment",
                            "state_id": "career_points",
                            "amount": {
                                "kind": "literal",
                                "value": points,
                                "value_type": "integer",
                            },
                            "invalid_transition_policy": "reject_transition",
                        },
                        {
                            "id": effect_ids[1],
                            "action_id": result_action,
                            "operation": "increment",
                            "state_id": "season_round",
                            "amount": {"kind": "literal", "value": 1, "value_type": "integer"},
                            "invalid_transition_policy": "reject_transition",
                        },
                        {
                            "id": effect_ids[2],
                            "action_id": result_action,
                            "operation": "set",
                            "state_id": "last_opponent",
                            "value": {
                                "kind": "literal",
                                "value": opponent,
                                "value_type": "string",
                            },
                            "invalid_transition_policy": "reject_transition",
                        },
                        {
                            "id": effect_ids[3],
                            "action_id": result_action,
                            "operation": "set",
                            "state_id": "last_match_result",
                            "value": {
                                "kind": "literal",
                                "value": result,
                                "value_type": "string",
                            },
                            "invalid_transition_policy": "reject_transition",
                        },
                        {
                            "id": effect_ids[4],
                            "action_id": result_action,
                            "operation": "set",
                            "state_id": "match_phase",
                            "value": {
                                "kind": "literal",
                                "value": "planning",
                                "value_type": "string",
                            },
                            "invalid_transition_policy": "reject_transition",
                        },
                        {
                            "id": effect_ids[5],
                            "action_id": result_action,
                            "operation": "increment",
                            "state_id": "standings_points",
                            "amount": {
                                "kind": "literal",
                                "value": points,
                                "value_type": "integer",
                            },
                            "invalid_transition_policy": "reject_transition",
                        },
                    ]
                )
                rule_id = f"{result_action}_rule"
                document["rules"].append(
                    {
                        "id": rule_id,
                        "action_id": result_action,
                        "condition_ids": condition_ids,
                        "effect_ids": effect_ids,
                        "event_ids": ["match_resolved"],
                        "order": 0,
                    }
                )
                document["actions"].append(
                    {
                        "id": result_action,
                        "core_verb_id": verb_id,
                        "parameters": [],
                        "rule_ids": [rule_id],
                        "presentation_hook_ids": ["progress_feedback", "progress_view"],
                        "required_feature_ids": list(domain_features),
                        "source_bindings": [
                            {"kind": "activity", "source_id": activity_id},
                            {"kind": "system", "source_id": system_id},
                        ],
                    }
                )
                document["mechanics"].append(
                    {
                        "id": (
                            mechanic_id
                            if result_action == descriptor["advance_action"]
                            else f"{result_action}_mechanic"
                        ),
                        "core_verb_id": verb_id,
                        "action_id": result_action,
                        "authoritative_state_ids": [
                            "career_points",
                            "last_match_result",
                            "last_opponent",
                            "match_phase",
                            "player_team",
                            "season_opponents",
                            "season_round",
                            "standings_points",
                        ],
                        "condition_ids": condition_ids,
                        "rule_ids": [rule_id],
                        "effect_ids": effect_ids,
                        "event_ids": ["match_resolved"],
                        "presentation_hook_ids": ["progress_feedback", "progress_view"],
                        "asset_binding_ids": [binding_id],
                        "required_feature_ids": list(domain_features),
                    }
                )

        reset_states = (
            "last_match_plan",
            "last_match_result",
            "last_opponent",
            "match_phase",
            "season_round",
            "standings_points",
        )
        reset_effects = [f"reset_{state}" for state in reset_states]
        document["effects"].extend(
            {
                "id": f"reset_{state}",
                "action_id": "restart_scenario",
                "operation": "reset",
                "state_id": state,
                "invalid_transition_policy": "reject_transition",
            }
            for state in reset_states
        )
        document["rules"].append(
            {
                "id": "restart_rule",
                "action_id": "restart_scenario",
                "condition_ids": [],
                "effect_ids": reset_effects,
                "event_ids": ["scenario_restarted"],
                "order": 0,
            }
        )
        document["actions"].append(
            {
                "id": "restart_scenario",
                "core_verb_id": "restart",
                "parameters": [],
                "rule_ids": ["restart_rule"],
                "presentation_hook_ids": ["progress_feedback"],
                "required_feature_ids": ["logic:finite_state"],
                "source_bindings": [{"kind": "activity", "source_id": activity_id}],
            }
        )
        document["mechanics"].append(
            {
                "id": "restart_mechanic",
                "core_verb_id": "restart",
                "action_id": "restart_scenario",
                "authoritative_state_ids": list(reset_states),
                "condition_ids": [],
                "rule_ids": ["restart_rule"],
                "effect_ids": reset_effects,
                "event_ids": ["scenario_restarted"],
                "presentation_hook_ids": ["progress_feedback"],
                "asset_binding_ids": [binding_id],
                "required_feature_ids": ["logic:finite_state"],
            }
        )
        document["goals"] = [
            {
                "id": "complete_authored_season",
                "condition_ids": ["season_ending_reached", "season_points_target_reached"],
                "success_ending_id": "season_complete",
            }
        ]
        document["endings"] = [
            {
                "id": "season_complete",
                "kind": "success",
                "condition_ids": ["season_ending_reached", "season_points_target_reached"],
                "event_ids": [],
                "presentation_hook_ids": ["ending_feedback"],
            }
        ]

    primary_action = next(
        item for item in document["actions"] if item["id"] == descriptor["advance_action"]
    )
    primary_mechanic = next(
        item for item in document["mechanics"] if item["id"] == descriptor["mechanic_id"]
    )
    if descriptor["authoring_feature"] not in primary_action["required_feature_ids"]:
        primary_action["required_feature_ids"].append(descriptor["authoring_feature"])
    if descriptor["authoring_feature"] not in primary_mechanic["required_feature_ids"]:
        primary_mechanic["required_feature_ids"].append(descriptor["authoring_feature"])
    for collection in (
        "actions",
        "conditions",
        "effects",
        "endings",
        "events",
        "failures",
        "goals",
        "mechanics",
        "presentation_hooks",
        "rules",
        "state_variables",
    ):
        document[collection].sort(key=lambda item: item["id"].encode("utf-8"))
        if collection == "rules":
            for order, record in enumerate(document[collection]):
                record["order"] = order
        for record in document[collection]:
            for field, value in record.items():
                if field.endswith("_ids") and isinstance(value, list):
                    value.sort(key=lambda item: item.encode("utf-8"))
            if "parameters" in record:
                record["parameters"].sort(key=lambda item: item["id"].encode("utf-8"))
            if "source_bindings" in record:
                record["source_bindings"].sort(key=lambda item: canonical_json_bytes(item))
    return _seal(document)


def _authoring_narrative(descriptor: dict[str, Any]) -> dict[str, Any] | None:
    narrative_kind = descriptor["narrative"]
    if narrative_kind is None:
        return None
    binding_id = descriptor["asset_binding_id"]
    if narrative_kind == "framing":
        units = [
            {
                "id": "mission_framing",
                "unit_type": "scene",
                "title": "Authored framing narrative for the action mission",
                "prerequisite_ids": [],
                "effect_ids": [],
                "next_unit_ids": ["mission_conclusion"],
                "asset_binding_ids": [binding_id],
            },
            {
                "id": "mission_conclusion",
                "unit_type": "ending",
                "title": "Authored conclusion for the bounded action mission",
                "prerequisite_ids": ["victory_condition"],
                "effect_ids": [],
                "next_unit_ids": [],
                "asset_binding_ids": [binding_id],
                "ending_kind": "success",
            },
        ]
    else:
        units = [
            {
                "id": "storylet_entry",
                "unit_type": "storylet",
                "title": "Modular expedition storylet entry",
                "prerequisite_ids": ["run_at_entry_depth", "run_is_active"],
                "effect_ids": [],
                "next_unit_ids": ["storylet_resolution"],
                "asset_binding_ids": [binding_id],
            },
            {
                "id": "storylet_resolution",
                "unit_type": "storylet",
                "title": "Modular expedition storylet resolution",
                "prerequisite_ids": ["run_is_active", "storylet_progressed"],
                "effect_ids": [],
                "next_unit_ids": ["run_conclusion"],
                "asset_binding_ids": [binding_id],
            },
            {
                "id": "run_conclusion",
                "unit_type": "ending",
                "title": "Authored conclusion for the bounded roguelite run",
                "prerequisite_ids": ["victory_condition"],
                "effect_ids": [],
                "next_unit_ids": [],
                "asset_binding_ids": [binding_id],
                "ending_kind": "success",
            },
        ]
    return _seal(
        {
            "format": "world-forge.narrative_module",
            "format_version": 1,
            "module_id": f"{descriptor['project_id']}_narrative",
            "project_id": descriptor["project_id"],
            "title": (
                "Authored action framing narrative"
                if narrative_kind == "framing"
                else "Authored modular roguelite storylets"
            ),
            "entry_unit_ids": [units[0]["id"]],
            "units": units,
            "extensions": [],
            "content_hash": "",
        }
    )


def _build_authoring_case_documents(case: str) -> tuple[object, dict[str, bytes]]:
    descriptor = AUTHORING_CASES[case]
    project_id = descriptor["project_id"]
    binding_id = descriptor["asset_binding_id"]
    system_id = descriptor["system_id"]
    profile = _read("profile.json")
    profile.update(
        {
            "profile_id": f"{project_id}_profile",
            "project_id": project_id,
            "title": f"{descriptor['title']} authoring profile",
        }
    )
    profile["experience"] = {
        "player_promise": descriptor["promise"],
        "audiences": ["multi-genre authoring reviewers"],
        "experience_goals": [descriptor["goal"], descriptor["challenge"]],
    }
    profile["gameplay"] = {
        "primary_family": descriptor.get("primary_family", descriptor["family"]),
        "secondary_families": [],
        "mechanic_tags": [f"{descriptor['family']}:authored_progression"],
        "player_role": descriptor["player_role"],
        "core_verbs": [
            {
                "id": descriptor["advance_verb"],
                "description": f"Advance the authored {descriptor['family']} scenario.",
            },
            {
                "id": "recover" if descriptor["narrative"] == "storylets" else "restart",
                "description": (
                    "Recover from authored run death and begin a new bounded run."
                    if descriptor["narrative"] == "storylets"
                    else "Restart the bounded authored scenario."
                ),
            },
        ],
        "core_loop": [
            "inspect authored state",
            f"{descriptor['advance_verb']} within the reviewed rules",
            "observe deterministic progress",
            "evaluate the authored victory condition",
        ],
        "rule_model": "deterministic bounded authored progression",
        "goal_model": descriptor["goal"],
        "challenge_model": descriptor["challenge"],
        "failure_recovery": "explicit deterministic restart",
        "progression": descriptor["progression"],
        "session_structure": "single bounded authoring fixture",
        "social_topology": "single_player",
        "teleology": "finite",
        "dependencies": {
            "authored": [f"{descriptor['family']}:reviewed_content"],
            "procedural": [],
            "systemic": [f"{descriptor['family']}:deterministic_rules"],
        },
    }
    if project_id == "sports_career":
        profile["gameplay"]["core_verbs"].append(
            {
                "id": "plan",
                "description": "Commit the authored match plan before resolving a result.",
            }
        )
        profile["gameplay"]["core_verbs"].sort(key=lambda item: item["id"].encode("utf-8"))
    profile["world"] = {
        "presence": descriptor["world_presence"],
        "spatial_topology": "authored bounded topology",
        "scale": "single reviewed scenario",
        "time_model": "discrete authored turns",
        "simulation_depth": "bounded deterministic",
        "simulated_domains": [f"{descriptor['family']}:progression"],
        "persistence": "session and authored progression state",
        "spatial_structure": descriptor["world_title"],
    }
    profile["fiction"] = {
        "genres": [descriptor["family"]],
        "tones": ["neutral"],
        "tags": [
            (
                "narrative:framing"
                if descriptor["narrative"] == "framing"
                else "narrative:modular_storylets"
                if descriptor["narrative"] == "storylets"
                else "fiction:minimal"
            )
        ],
    }
    if descriptor["narrative"] == "framing":
        profile["narrative"] = {
            "requirement": "optional",
            "topology": "linear",
            "agency": "gameplay agency with authored narrative framing",
            "authorship_mode": "authored",
            "canon_variability": "fixed mission framing",
            "delivery_channels": ["narrative:prose", "narrative:ui"],
            "endings": "one authored mission conclusion",
            "focalization": "player focalized",
            "information_model": "framing context is explicit",
            "pacing": "framing scene around the bounded mission",
            "protagonist_model": "authored action operative",
        }
    elif descriptor["narrative"] == "storylets":
        profile["narrative"] = {
            "requirement": "required",
            "topology": "modular",
            "agency": "bounded expedition progression",
            "authorship_mode": "authored",
            "canon_variability": "fixed reviewed storylet sequence",
            "delivery_channels": ["narrative:prose", "narrative:ui"],
            "endings": "one authored run conclusion",
            "focalization": "player focalized",
            "information_model": "storylet order and run depth are explicit",
            "pacing": "short modular storylet beats",
            "protagonist_model": "authored expedition leader",
        }
    profile["production"]["content_modes"] = {
        "assets": "authored",
        "gameplay": "authored",
        "narrative": "authored" if descriptor["narrative"] is not None else "not_applicable",
        "world": "authored",
    }
    profile["production"]["selection_policy"] = (
        "select the exact deterministic project-authored fixture asset after human review"
    )
    profile["runtime_target"].update(
        {
            "requested_adapter": None,
            "asset_formats": ["asset:png"],
            "renderer": "raylib",
            "required_features": sorted(
                [
                    descriptor["authoring_feature"],
                    "logic:deterministic_actions",
                    "logic:finite_state",
                ],
                key=lambda item: item.encode("utf-8"),
            ),
        }
    )
    profile = _seal(profile)
    logic = _authoring_logic(descriptor)
    system_action_ids = {
        action["id"]
        for action in logic["actions"]
        if {
            "kind": "system",
            "source_id": system_id,
        }
        in action["source_bindings"]
    }
    system_rules = [rule for rule in logic["rules"] if rule["action_id"] in system_action_ids]

    def system_closure(field: str) -> list[str]:
        return sorted(
            {identifier for rule in system_rules for identifier in rule[field]},
            key=lambda item: item.encode("utf-8"),
        )

    activity = _seal(
        {
            "format": "world-forge.activity_module",
            "format_version": 1,
            "module_id": f"{project_id}_activities",
            "project_id": project_id,
            "title": f"{descriptor['title']} authored activity",
            "activities": [
                {
                    "id": descriptor["activity_id"],
                    "activity_type": "scenario",
                    "title": descriptor["progression"],
                    "participant_ids": [],
                    "spatial_context_ids": [],
                    "start_condition_ids": ["scenario_ready"],
                    "success_condition_ids": list(logic["goals"][0]["condition_ids"]),
                    "failure_condition_ids": sorted(
                        (
                            condition_id
                            for failure in logic["failures"]
                            for condition_id in failure["condition_ids"]
                        ),
                        key=lambda item: item.encode("utf-8"),
                    ),
                    "end_condition_ids": list(logic["goals"][0]["condition_ids"]),
                    "effect_ids": sorted(
                        (effect["id"] for effect in logic["effects"]),
                        key=lambda item: item.encode("utf-8"),
                    ),
                    "event_ids": sorted(
                        (event["id"] for event in logic["events"]),
                        key=lambda item: item.encode("utf-8"),
                    ),
                    "presentation_hook_ids": [
                        "progress_feedback",
                        "progress_view",
                    ],
                    "asset_binding_ids": [binding_id],
                    "provenance": f"authored neutral {descriptor['family']} fixture",
                    "validation_profile": "authoring_only",
                }
            ],
            "extensions": [],
            "content_hash": "",
        }
    )
    system = _seal(
        {
            "format": "world-forge.system_module",
            "format_version": 1,
            "module_id": f"{project_id}_systems",
            "project_id": project_id,
            "title": descriptor["world_title"],
            "systems": [
                {
                    "id": descriptor["system_id"],
                    "system_type": descriptor["system_type"],
                    "title": descriptor["goal"],
                    "precondition_ids": system_closure("condition_ids"),
                    "effect_ids": system_closure("effect_ids"),
                    "event_ids": system_closure("event_ids"),
                    "asset_binding_ids": [binding_id],
                }
            ],
            "extensions": [],
            "content_hash": "",
        }
    )
    world = _seal(
        {
            "format": "world-forge.world_module",
            "format_version": 1,
            "module_id": f"{project_id}_space",
            "project_id": project_id,
            "module_type": "space",
            "title": descriptor["world_title"],
            "spaces": [
                {
                    "id": f"{project_id}_space",
                    "name": descriptor["world_title"],
                    "topology": descriptor["world_presence"],
                }
            ],
            "extensions": [],
            "content_hash": "",
        }
    )
    narrative = _authoring_narrative(descriptor)
    narrative_modules = () if narrative is None else (narrative,)
    manifest = _seal(
        {
            "format": "world-forge.creation_source_manifest",
            "format_version": 1,
            "project_id": project_id,
            "profile": _reference(profile, identifier_field="profile_id", path="profile.json"),
            "modules": {
                "activity_modules": [
                    _reference(
                        activity,
                        identifier_field="module_id",
                        path=f"activities/{project_id}.json",
                    )
                ],
                "logic_modules": [
                    _reference(
                        logic,
                        identifier_field="module_id",
                        path=f"logic/{project_id}.json",
                    )
                ],
                "narrative_modules": (
                    []
                    if narrative is None
                    else [
                        _reference(
                            narrative,
                            identifier_field="module_id",
                            path=f"narrative/{project_id}.json",
                        )
                    ]
                ),
                "system_modules": [
                    _reference(
                        system,
                        identifier_field="module_id",
                        path=f"systems/{project_id}.json",
                    )
                ],
                "world_modules": [
                    _reference(
                        world,
                        identifier_field="module_id",
                        path=f"world/{project_id}.json",
                    )
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
            "project_kind": "game",
            "project_id": project_id,
            "title": descriptor["title"],
            "project_version": "1.0.0",
            "default_locale": "en",
            "profile": _reference(profile, identifier_field="profile_id", path="profile.json"),
            "source_manifest": _reference(
                manifest,
                identifier_field="project_id",
                path="source/manifest.json",
            ),
            "extensions": [],
            "content_hash": "",
        }
    )
    loaded = validate_creation_documents(
        project,
        profile,
        manifest,
        (world,),
        (activity,),
        narrative_modules,
        (system,),
        (logic,),
    )
    gamepack = build_gamepack(loaded)
    analysis = analyze_gamepack(gamepack)
    if not gamepack["asset_requirements"]:
        raise ValueError(f"{case} must compile non-empty asset requirements")
    if gamepack["runtime_requirements"]["requested_adapter"] is not None:
        raise ValueError(f"{case} must remain authoring-only")
    readiness_artifacts = (gamepack, analysis)
    status = initial_creation_workflow_status(loaded)
    readiness = build_creation_readiness(loaded, artifacts=readiness_artifacts)
    handoff = build_creation_handoff(
        loaded,
        status=status,
        readiness=readiness,
        artifacts=readiness_artifacts,
    )
    files = {
        "project.json": canonical_json_bytes(project),
        "profile.json": canonical_json_bytes(profile),
        "source/manifest.json": canonical_json_bytes(manifest),
        f"source/world/{project_id}.json": canonical_json_bytes(world),
        f"source/activities/{project_id}.json": canonical_json_bytes(activity),
        f"source/systems/{project_id}.json": canonical_json_bytes(system),
        f"source/logic/{project_id}.json": canonical_json_bytes(logic),
        ".worldforge/status.json": canonical_json_bytes(status),
        ".worldforge/phase_reports/README.md": (
            b"# Phase reports\n\n"
            b"Immutable content-addressed phase-report v3 documents are stored here.\n"
        ),
        f"artifacts/{case}.gamepack.json": canonical_json_bytes(gamepack),
        f"artifacts/{case}.game-analysis.json": canonical_json_bytes(analysis),
        f"artifacts/{case}.readiness.json": canonical_json_bytes(readiness),
        f"artifacts/{case}.handoff.json": canonical_json_bytes(handoff),
    }
    if narrative is not None:
        files[f"source/narrative/{project_id}.json"] = canonical_json_bytes(narrative)
    for document in (
        loaded.project,
        loaded.profile,
        loaded.manifest,
        *loaded.world_modules,
        *loaded.activity_modules,
        *loaded.narrative_modules,
        *loaded.system_modules,
        *loaded.logic_modules,
    ):
        identity = document_identity(document)
        files[f".worldforge/artifact_history/{identity['content_hash']}.json"] = (
            canonical_json_bytes(document)
        )
    return loaded, files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate or verify the systemic-simulation creation fixture",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    generated = {"systemic-simulation": _build_documents()[1]}
    generated.update({case: _build_authoring_case_documents(case)[1] for case in AUTHORING_CASES})
    mismatches: list[str] = []
    total = 0
    for case, files in generated.items():
        destination = ROOT / "examples" / "multigenre-contracts" / case
        for relative, expected in sorted(files.items()):
            total += 1
            path = destination / relative
            if args.write:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(expected)
            elif not path.is_file() or path.read_bytes() != expected:
                mismatches.append(f"{case}/{relative}")
    if mismatches:
        for relative in mismatches:
            print(f"ERROR fixture differs: examples/multigenre-contracts/{relative}")
        return 1
    print(f"OK creation_workflow_fixtures={total} mode={'write' if args.write else 'check'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
