from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import worldforge.game_analysis as game_analysis_module
import worldforge.game_logic as game_logic_module
import worldforge.gamepack as gamepack_module
from worldforge.creation_contracts import (
    LoadedCreationProject,
    canonical_creation_hash,
)
from worldforge.game_analysis import (
    ANALYSIS_FORMAT,
    ANALYSIS_VERSION,
    GameAnalysisError,
    analyze_gamepack,
    load_game_analysis,
    publish_game_analysis,
    serialize_game_analysis,
    validate_game_analysis,
    validate_game_analysis_structure,
)
from worldforge.game_logic import (
    ANALYSIS_LIMITS,
    EXECUTION_SEMANTICS,
    CandidateAction,
    GameLogicError,
    _Interpreter,
    analysis_requirements_for,
    canonical_state_hash,
    classify_state,
    enumerate_candidates,
    initial_state,
    legal_transitions,
    transition,
)
from worldforge.gamepack import (
    GamepackError,
    build_gamepack,
    load_game_source_project,
    validate_gamepack_document,
)
from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "multigenre-contracts"


def _gamepack(case: str) -> dict[str, object]:
    return build_gamepack(load_game_source_project(EXAMPLES / case))


def _reseal(document: dict[str, object]) -> dict[str, object]:
    clone = copy.deepcopy(document)
    clone.pop("content_hash", None)
    clone["content_hash"] = canonical_creation_hash(clone)
    return clone


def _with_logic_mutation(
    case: str,
    mutate: Callable[[dict[str, object]], None],
    *,
    mutate_narrative: Callable[[dict[str, object]], None] | None = None,
) -> LoadedCreationProject:
    loaded = load_game_source_project(EXAMPLES / case)
    logic = copy.deepcopy(loaded.logic_modules[0])
    mutate(logic)
    logic["content_hash"] = canonical_creation_hash(logic)
    narrative_modules = [copy.deepcopy(item) for item in loaded.narrative_modules]
    if mutate_narrative is not None:
        mutate_narrative(narrative_modules[0])
        narrative_modules[0]["content_hash"] = canonical_creation_hash(narrative_modules[0])
    manifest = copy.deepcopy(loaded.manifest)
    manifest["modules"]["logic_modules"][0]["content_hash"] = logic["content_hash"]
    if mutate_narrative is not None:
        manifest["modules"]["narrative_modules"][0]["content_hash"] = narrative_modules[0][
            "content_hash"
        ]
    manifest["content_hash"] = canonical_creation_hash(manifest)
    project = copy.deepcopy(loaded.project)
    project["source_manifest"]["content_hash"] = manifest["content_hash"]
    project["content_hash"] = canonical_creation_hash(project)
    return LoadedCreationProject(
        project=project,
        profile=copy.deepcopy(loaded.profile),
        manifest=manifest,
        world_modules=tuple(copy.deepcopy(item) for item in loaded.world_modules),
        activity_modules=tuple(copy.deepcopy(item) for item in loaded.activity_modules),
        narrative_modules=tuple(narrative_modules),
        system_modules=tuple(copy.deepcopy(item) for item in loaded.system_modules),
        logic_modules=(logic,),
    )


class CompiledExecutionPolicyTests(unittest.TestCase):
    def test_compiler_pins_execution_policy_and_analysis_profile(self) -> None:
        puzzle = _gamepack("abstract-puzzle")
        narrative = _gamepack("branching-narrative")
        self.assertEqual(puzzle["logic"]["execution_semantics"], EXECUTION_SEMANTICS)
        self.assertEqual(narrative["logic"]["execution_semantics"], EXECUTION_SEMANTICS)
        self.assertEqual(puzzle["analysis_requirements"]["profile"], "abstract_puzzle")
        self.assertEqual(
            narrative["analysis_requirements"]["profile"],
            "branching_narrative",
        )
        self.assertEqual(
            puzzle["analysis_requirements"]["limits"],
            ANALYSIS_LIMITS,
        )

    def test_analysis_selection_is_structural_and_unknown_shapes_are_unsupported(self) -> None:
        puzzle = _gamepack("abstract-puzzle")
        renamed = copy.deepcopy(puzzle)
        renamed["game"]["id"] = "not_a_genre_label"
        self.assertEqual(
            analysis_requirements_for(renamed["modules"], renamed["logic"])["profile"],
            "abstract_puzzle",
        )
        mixed = copy.deepcopy(puzzle["modules"])
        mixed["activities"][0]["activities"].append(
            {
                **copy.deepcopy(mixed["activities"][0]["activities"][0]),
                "activity_type": "match",
                "id": "not_a_puzzle",
            }
        )
        requirement = analysis_requirements_for(mixed, puzzle["logic"])
        self.assertEqual(requirement["profile"], "unsupported")
        self.assertEqual(
            requirement["reason_code"],
            "analysis_profile_unsupported",
        )

    def test_python_and_schema_pin_the_same_limits(self) -> None:
        gamepack_schema = json.loads(
            (ROOT / "schemas" / "gamepack.schema.json").read_text(encoding="utf-8")
        )
        schema_limits = {
            key: value["const"]
            for key, value in gamepack_schema["$defs"]["analysisLimits"]["properties"].items()
        }
        self.assertEqual(schema_limits, dict(ANALYSIS_LIMITS))
        analysis_schema = json.loads(
            (ROOT / "schemas" / "game-analysis.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            analysis_schema["$defs"]["metrics"]["properties"]["largest_state_bytes"]["maximum"],
            ANALYSIS_LIMITS["total_state_bytes"],
        )
        generated = (
            ROOT / "apps" / "studio" / "src" / "generated" / "world-forge-contracts.d.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("WorldForgeDeterministicGameAnalysisV1", generated)
        self.assertIn("depth: 512;", generated)

    def test_tampered_policy_or_requirement_is_not_integrally_valid(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        for mutate in (
            lambda value: value["logic"]["execution_semantics"].__setitem__("semantics_version", 2),
            lambda value: value["analysis_requirements"].__setitem__(
                "profile", "branching_narrative"
            ),
        ):
            tampered = copy.deepcopy(gamepack)
            mutate(tampered)
            tampered = _reseal(tampered)
            with self.assertRaises(GamepackError):
                validate_gamepack_document(tampered)


class PureInterpreterTests(unittest.TestCase):
    def test_candidate_order_and_puzzle_shortest_solution(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        candidates = enumerate_candidates(gamepack)
        self.assertEqual(candidates[0], CandidateAction("restart_board", {}))
        self.assertEqual(
            candidates[1],
            CandidateAction("swap_tiles", {"first_index": 0, "second_index": 0}),
        )
        state = initial_state(gamepack)
        result = transition(
            gamepack,
            state,
            CandidateAction("swap_tiles", {"first_index": 0, "second_index": 1}),
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.post_state["board"], ["A", "B", "C"])
        self.assertEqual(result.post_state["move_count"], 1)
        self.assertEqual(result.events, ("tile_swapped",))
        self.assertEqual(
            classify_state(gamepack, result.post_state).ending_ids,
            ("puzzle_complete",),
        )

    def test_rejected_transition_is_atomic(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        state = initial_state(gamepack)
        before = copy.deepcopy(state)
        result = transition(
            gamepack,
            state,
            CandidateAction("swap_tiles", {"first_index": 0, "second_index": 2}),
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.post_state, before)
        self.assertEqual(result.events, ())
        self.assertEqual(state, before)

    def test_narrative_source_effects_and_cursor_commit_atomically(self) -> None:
        gamepack = _gamepack("branching-narrative")
        state = initial_state(gamepack)
        result = transition(gamepack, state, CandidateAction("choose_left", {}))
        self.assertTrue(result.accepted)
        self.assertEqual(result.post_state["choice_state"], "left")
        self.assertEqual(result.post_state["knowledge"], ["learned_left"])
        self.assertEqual(
            result.post_state["wf_internal_narrative_cursor"],
            "ending_left",
        )
        self.assertEqual(result.events, ("choice_left",))
        classification = classify_state(gamepack, result.post_state)
        self.assertEqual(classification.ending_ids, ("ending_left",))

    def test_compiled_multi_rule_narrative_order_matches_interpreter_order(self) -> None:
        def add_late_rule(logic: dict[str, object]) -> None:
            logic["conditions"].append(
                {
                    "action_id": "choose_left",
                    "id": "late_guard",
                    "operator": "constant",
                    "value": True,
                }
            )
            logic["effects"].append(
                {
                    "action_id": "choose_left",
                    "id": "aaa_late_effect",
                    "invalid_transition_policy": "reject_transition",
                    "operation": "set",
                    "state_id": "choice_state",
                    "value": {
                        "kind": "literal",
                        "value": "left",
                        "value_type": "string",
                    },
                }
            )
            logic["rules"].append(
                {
                    "action_id": "choose_left",
                    "condition_ids": ["late_guard"],
                    "effect_ids": ["aaa_late_effect"],
                    "event_ids": [],
                    "id": "choose_left_late_rule",
                    "order": 2,
                }
            )
            action = next(item for item in logic["actions"] if item["id"] == "choose_left")
            action["rule_ids"].append("choose_left_late_rule")
            action["rule_ids"].sort(key=lambda item: item.encode("utf-8"))
            mechanic = next(
                item for item in logic["mechanics"] if item["action_id"] == "choose_left"
            )
            mechanic["condition_ids"].append("late_guard")
            mechanic["effect_ids"].append("aaa_late_effect")
            mechanic["rule_ids"].append("choose_left_late_rule")
            for field in ("condition_ids", "effect_ids", "rule_ids"):
                mechanic[field].sort(key=lambda item: item.encode("utf-8"))
            for field in ("conditions", "effects"):
                logic[field].sort(key=lambda item: item["id"].encode("utf-8"))
            logic["rules"].sort(
                key=lambda item: (
                    item["order"],
                    item["id"].encode("utf-8"),
                )
            )

        def bind_late_rule(narrative: dict[str, object]) -> None:
            unit = next(item for item in narrative["units"] if item["id"] == "central_choice")
            option = next(item for item in unit["options"] if item["id"] == "choose_left")
            option["condition_ids"].append("late_guard")
            option["effect_ids"].append("aaa_late_effect")
            for field in ("condition_ids", "effect_ids"):
                option[field].sort(key=lambda item: item.encode("utf-8"))

        gamepack = build_gamepack(
            _with_logic_mutation(
                "branching-narrative",
                add_late_rule,
                mutate_narrative=bind_late_rule,
            )
        )
        transition_contract = next(
            item
            for item in gamepack["logic"]["narrative_transitions"]
            if item["action_id"] == "choose_left"
        )
        self.assertEqual(
            transition_contract["atomic_source_effect_ids"],
            ["record_left", "remember_left", "aaa_late_effect"],
        )
        result = transition(
            gamepack,
            initial_state(gamepack),
            CandidateAction("choose_left", {}),
        )
        self.assertTrue(result.accepted, result.rejection_reason)

    def test_every_condition_operator_and_effect_operator_is_interpreted(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        state = initial_state(gamepack)
        legal = legal_transitions(gamepack, state)
        self.assertTrue(any(item.action.action_id == "restart_board" for item in legal))
        self.assertTrue(any(item.action.action_id == "swap_tiles" for item in legal))
        self.assertEqual(
            canonical_state_hash(state),
            canonical_state_hash(copy.deepcopy(state)),
        )

    def test_nested_conditions_multi_rule_order_and_candidate_state_operands(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        logic = gamepack["logic"]
        logic["conditions"].extend(
            [
                {
                    "action_id": "swap_tiles",
                    "id": "always_false",
                    "operator": "constant",
                    "value": False,
                },
                {
                    "action_id": "swap_tiles",
                    "condition_id": "always_false",
                    "id": "not_false",
                    "operator": "not",
                },
                {
                    "action_id": "swap_tiles",
                    "condition_ids": ["always_false", "first_index_valid"],
                    "id": "either_guard",
                    "operator": "any",
                },
                {
                    "action_id": "swap_tiles",
                    "condition_ids": ["either_guard", "not_false"],
                    "id": "combined_guard",
                    "operator": "all",
                },
            ]
        )
        logic["effects"].append(
            {
                "action_id": "swap_tiles",
                "amount": {"kind": "state", "state_id": "move_count"},
                "id": "increment_by_candidate_count",
                "invalid_transition_policy": "reject_transition",
                "operation": "increment",
                "state_id": "move_count",
            }
        )
        logic["rules"].append(
            {
                "action_id": "swap_tiles",
                "condition_ids": ["combined_guard"],
                "effect_ids": ["increment_by_candidate_count"],
                "event_ids": ["restart_requested"],
                "id": "second_swap_rule",
                "order": 2,
            }
        )
        action = next(item for item in logic["actions"] if item["id"] == "swap_tiles")
        action["rule_ids"].append("second_swap_rule")
        interpreter = _Interpreter(gamepack, already_validated=True)
        result = interpreter.transition(
            interpreter.initial_state(),
            CandidateAction("swap_tiles", {"first_index": 0, "second_index": 1}),
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.post_state["move_count"], 2)
        self.assertEqual(result.events, ("tile_swapped", "restart_requested"))

    def test_partial_effect_failure_rolls_back_state_events_and_cursor(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        rule = next(item for item in gamepack["logic"]["rules"] if item["id"] == "swap_rule")
        gamepack["logic"]["effects"].append(
            {
                "action_id": "swap_tiles",
                "amount": {"kind": "literal", "value": 8, "value_type": "integer"},
                "id": "overflow_moves",
                "invalid_transition_policy": "reject_transition",
                "operation": "increment",
                "state_id": "move_count",
            }
        )
        rule["effect_ids"].append("overflow_moves")
        interpreter = _Interpreter(gamepack, already_validated=True)
        state = interpreter.initial_state()
        result = interpreter.transition(
            state,
            CandidateAction("swap_tiles", {"first_index": 0, "second_index": 1}),
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_reason, "effect_domain_invalid")
        self.assertEqual(result.pre_state, result.post_state)
        self.assertEqual(result.events, ())

    def test_append_unique_rejects_duplicate_values_atomically(self) -> None:
        gamepack = _gamepack("branching-narrative")
        interpreter = _Interpreter(gamepack, already_validated=True)
        state = interpreter.initial_state()
        state["knowledge"] = ["learned_left"]
        result = interpreter.transition(state, CandidateAction("choose_left", {}))
        self.assertFalse(result.accepted)
        self.assertEqual(result.rejection_reason, "effect_domain_invalid")
        self.assertEqual(result.pre_state, result.post_state)
        self.assertEqual(result.events, ())
        self.assertEqual(
            result.post_state["wf_internal_narrative_cursor"],
            "central_choice",
        )

    def test_endings_precede_failures_and_failure_recovery_is_intersected(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        interpreter = _Interpreter(gamepack, already_validated=True)
        solved_at_limit = interpreter.initial_state()
        solved_at_limit["board"] = ["A", "B", "C"]
        solved_at_limit["move_count"] = 8
        terminal = interpreter.classify(solved_at_limit)
        self.assertEqual(terminal.ending_ids, ("puzzle_complete",))
        self.assertEqual(terminal.failure_ids, ())

        gamepack["logic"]["failures"].append(
            {
                "condition_ids": ["move_limit_reached"],
                "id": "second_limit",
                "recovery_action_ids": ["swap_tiles"],
            }
        )
        intersected = _Interpreter(gamepack, already_validated=True)
        failed = intersected.initial_state()
        failed["move_count"] = 8
        classification = intersected.classify(failed)
        self.assertEqual(
            classification.failure_ids,
            ("move_limit", "second_limit"),
        )
        self.assertEqual(classification.recovery_action_ids, ())
        rejected = intersected.transition(
            failed,
            CandidateAction("restart_board", {}),
        )
        self.assertEqual(
            rejected.rejection_reason,
            "failure_recovery_empty_intersection",
        )

    def test_ambiguous_ending_is_an_integrity_error(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        duplicate = copy.deepcopy(gamepack["logic"]["endings"][0])
        duplicate["id"] = "second_complete"
        gamepack["logic"]["endings"].append(duplicate)
        interpreter = _Interpreter(gamepack, already_validated=True)
        state = interpreter.initial_state()
        state["board"] = ["A", "B", "C"]
        with self.assertRaises(GameLogicError) as caught:
            interpreter.classify(state)
        self.assertEqual(caught.exception.reason_code, "ambiguous_ending")

    def test_parameter_preflight_fails_before_large_cartesian_allocation(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        oversized = copy.deepcopy(gamepack)
        action = next(item for item in oversized["logic"]["actions"] if item["id"] == "swap_tiles")
        action["parameters"][0]["maximum"] = 4096
        action["parameters"][1]["maximum"] = 4096
        oversized = _reseal(oversized)
        with self.assertRaises(GameLogicError) as caught:
            enumerate_candidates(oversized)
        self.assertEqual(caught.exception.reason_code, "parameter_combinations_exceeded")


class BoundedAnalysisTests(unittest.TestCase):
    def test_puzzle_and_branching_fixtures_pass_with_deterministic_witnesses(self) -> None:
        for case, profile in (
            ("abstract-puzzle", "abstract_puzzle"),
            ("branching-narrative", "branching_narrative"),
        ):
            gamepack = _gamepack(case)
            first = analyze_gamepack(gamepack)
            second = analyze_gamepack(copy.deepcopy(gamepack))
            self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
            self.assertEqual(first["format"], ANALYSIS_FORMAT)
            self.assertEqual(first["format_version"], ANALYSIS_VERSION)
            self.assertEqual(first["analyzer"]["profile"], profile)
            self.assertEqual(first["status"], "passed")
            self.assertTrue(first["witnesses"])
            if case == "abstract-puzzle":
                self.assertEqual(len(first["witnesses"][0]["steps"]), 1)
            fixture = EXAMPLES / case / "artifacts" / f"{case}.game-analysis.json"
            self.assertEqual(fixture.read_bytes(), serialize_game_analysis(first))
            self.assertEqual(first, validate_game_analysis(first, gamepack))

    def test_report_drift_and_source_drift_are_rejected(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        report = analyze_gamepack(gamepack)
        drifted = copy.deepcopy(report)
        drifted["summary"]["findings"] += 1
        drifted = _reseal(drifted)
        with self.assertRaises(GameAnalysisError):
            validate_game_analysis(drifted, gamepack)
        other = _gamepack("branching-narrative")
        with self.assertRaises(GameAnalysisError):
            validate_game_analysis(report, other)

    def test_structure_validation_is_separate_from_integral_rerun(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        report = analyze_gamepack(gamepack)
        self.assertEqual(report, validate_game_analysis_structure(report))

    def test_unsolvable_recovery_loop_and_branch_softlock_fail_closed(self) -> None:
        def trap_initial(logic: dict[str, object]) -> None:
            state = next(item for item in logic["state_variables"] if item["id"] == "move_count")
            state["initial"] = 8

        trapped = build_gamepack(_with_logic_mutation("abstract-puzzle", trap_initial))
        trapped_report = analyze_gamepack(trapped)
        self.assertEqual(trapped_report["status"], "failed")
        self.assertIn(
            "authored_ending_unreachable",
            trapped_report["reason_codes"],
        )
        self.assertIn(
            "nonterminal_terminal_trap",
            trapped_report["reason_codes"],
        )

        def disable_choices_safely(logic: dict[str, object]) -> None:
            for condition in logic["conditions"]:
                identifier = condition["id"]
                if identifier in {
                    "choice_available_left",
                    "choice_available_right",
                }:
                    action_id = condition["action_id"]
                    condition.clear()
                    condition.update(
                        {
                            "action_id": action_id,
                            "id": identifier,
                            "operator": "constant",
                            "value": False,
                        }
                    )

        softlocked = build_gamepack(
            _with_logic_mutation("branching-narrative", disable_choices_safely)
        )
        softlocked_report = analyze_gamepack(softlocked)
        self.assertEqual(softlocked_report["status"], "failed")
        self.assertIn("nonterminal_softlock", softlocked_report["reason_codes"])
        self.assertIn(
            "narrative_option_unreachable",
            softlocked_report["reason_codes"],
        )

    def test_reaching_a_count_bound_before_closure_is_inconclusive(self) -> None:
        small_limits = dict(ANALYSIS_LIMITS)
        small_limits["candidate_evaluations"] = 1
        with (
            mock.patch.object(game_logic_module, "ANALYSIS_LIMITS", small_limits),
            mock.patch.object(gamepack_module, "ANALYSIS_LIMITS", small_limits),
            mock.patch.object(game_analysis_module, "ANALYSIS_LIMITS", small_limits),
        ):
            gamepack = _gamepack("abstract-puzzle")
            report = analyze_gamepack(gamepack)
            self.assertEqual(report["status"], "inconclusive")
            self.assertFalse(report["metrics"]["frontier_closed"])
            self.assertIn(
                "candidate_evaluations_bound_reached",
                report["reason_codes"],
            )
            self.assertNotIn("authored_goal_unreachable", report["reason_codes"])
            self.assertNotIn("authored_ending_unreachable", report["reason_codes"])
            self.assertNotIn("nonterminal_terminal_trap", report["reason_codes"])
            checks = {item["id"]: item["status"] for item in report["checks"]}
            self.assertEqual(checks["authored_goals_reachable"], "inconclusive")
            self.assertEqual(checks["authored_endings_reachable"], "inconclusive")
            self.assertEqual(checks["initial_solvable"], "inconclusive")
            self.assertEqual(checks["terminal_reachability"], "inconclusive")

    def test_parameter_and_state_size_bounds_return_inconclusive_reports(self) -> None:
        parameter_limits = dict(ANALYSIS_LIMITS)
        parameter_limits["parameter_combinations_per_action"] = 1
        with (
            mock.patch.object(game_logic_module, "ANALYSIS_LIMITS", parameter_limits),
            mock.patch.object(gamepack_module, "ANALYSIS_LIMITS", parameter_limits),
            mock.patch.object(game_analysis_module, "ANALYSIS_LIMITS", parameter_limits),
        ):
            parameter_report = analyze_gamepack(_gamepack("abstract-puzzle"))
        self.assertEqual(parameter_report["status"], "inconclusive")
        self.assertEqual(
            parameter_report["reason_codes"],
            ["parameter_combinations_bound_reached"],
        )

        initial_limits = dict(ANALYSIS_LIMITS)
        initial_limits["state_bytes"] = 1
        with (
            mock.patch.object(game_logic_module, "ANALYSIS_LIMITS", initial_limits),
            mock.patch.object(gamepack_module, "ANALYSIS_LIMITS", initial_limits),
            mock.patch.object(game_analysis_module, "ANALYSIS_LIMITS", initial_limits),
        ):
            initial_report = analyze_gamepack(_gamepack("abstract-puzzle"))
        self.assertEqual(initial_report["status"], "inconclusive")
        self.assertEqual(
            initial_report["reason_codes"],
            ["state_bytes_bound_reached"],
        )

        narrative = _gamepack("branching-narrative")
        start = initial_state(narrative)
        post = transition(
            narrative,
            start,
            CandidateAction("choose_left", {}),
        ).post_state
        start_size = len(game_logic_module.canonical_state_bytes(start))
        self.assertGreater(
            len(game_logic_module.canonical_state_bytes(post)),
            start_size,
        )
        post_limits = dict(ANALYSIS_LIMITS)
        post_limits["state_bytes"] = start_size
        with (
            mock.patch.object(game_logic_module, "ANALYSIS_LIMITS", post_limits),
            mock.patch.object(gamepack_module, "ANALYSIS_LIMITS", post_limits),
            mock.patch.object(game_analysis_module, "ANALYSIS_LIMITS", post_limits),
        ):
            post_report = analyze_gamepack(_gamepack("branching-narrative"))
        self.assertEqual(post_report["status"], "inconclusive")
        self.assertIn("state_bytes_bound_reached", post_report["reason_codes"])

    def test_parameterized_cursor_divergence_is_deduplicated(self) -> None:
        def diverge_with_unused_parameter(logic: dict[str, object]) -> None:
            action = next(item for item in logic["actions"] if item["id"] == "choose_left")
            action["parameters"] = [{"id": "variant", "type": "boolean"}]
            condition = next(
                item for item in logic["conditions"] if item["id"] == "choice_available_left"
            )
            condition.clear()
            condition.update(
                {
                    "action_id": "choose_left",
                    "comparison": "equal",
                    "id": "choice_available_left",
                    "left": {
                        "action_id": "choose_left",
                        "kind": "parameter",
                        "parameter_id": "variant",
                    },
                    "operator": "compare",
                    "right": {
                        "action_id": "choose_left",
                        "kind": "parameter",
                        "parameter_id": "variant",
                    },
                }
            )
            effect = next(item for item in logic["effects"] if item["id"] == "remember_left")
            effect["value"]["value"] = "right"

        gamepack = build_gamepack(
            _with_logic_mutation(
                "branching-narrative",
                diverge_with_unused_parameter,
            )
        )
        report = analyze_gamepack(gamepack)
        self.assertEqual(report["status"], "failed")
        divergence = [
            item for item in report["findings"] if item["reason_code"] == "cursor_divergence"
        ]
        self.assertEqual(len(divergence), 1)
        self.assertIsNotNone(divergence[0]["witness_id"])

    def test_witness_bounds_do_not_rewrite_closed_frontier_evidence(self) -> None:
        witness_limits = dict(ANALYSIS_LIMITS)
        witness_limits["witness_traces"] = 1
        with (
            mock.patch.object(game_logic_module, "ANALYSIS_LIMITS", witness_limits),
            mock.patch.object(gamepack_module, "ANALYSIS_LIMITS", witness_limits),
            mock.patch.object(game_analysis_module, "ANALYSIS_LIMITS", witness_limits),
        ):
            report = analyze_gamepack(_gamepack("branching-narrative"))
        self.assertEqual(report["status"], "inconclusive")
        self.assertTrue(report["metrics"]["frontier_closed"])
        checks = {item["id"]: item for item in report["checks"]}
        self.assertEqual(checks["frontier_closed"]["status"], "passed")
        self.assertEqual(
            checks["witness_evidence_complete"],
            {
                "id": "witness_evidence_complete",
                "status": "inconclusive",
                "reason_codes": ["witness_bound_reached"],
            },
        )

    def test_reports_include_targeted_shortest_proof_and_counterexample_witnesses(
        self,
    ) -> None:
        passing = analyze_gamepack(_gamepack("branching-narrative"))
        witness_targets = {(item["kind"], item["target_id"]) for item in passing["witnesses"]}
        self.assertIn(("narrative_unit", "central_choice"), witness_targets)
        self.assertIn(
            ("narrative_option", "central_choice:choose_left"),
            witness_targets,
        )
        self.assertIn(("ending", "ending_left"), witness_targets)

        def disable_choices(logic: dict[str, object]) -> None:
            for condition in logic["conditions"]:
                if condition["id"] in {
                    "choice_available_left",
                    "choice_available_right",
                }:
                    action_id = condition["action_id"]
                    condition.clear()
                    condition.update(
                        {
                            "action_id": action_id,
                            "id": (
                                "choice_available_left"
                                if action_id == "choose_left"
                                else "choice_available_right"
                            ),
                            "operator": "constant",
                            "value": False,
                        }
                    )

        report = analyze_gamepack(
            build_gamepack(_with_logic_mutation("branching-narrative", disable_choices))
        )
        softlock = next(
            item for item in report["findings"] if item["reason_code"] == "nonterminal_softlock"
        )
        self.assertIsNotNone(softlock["witness_id"])
        witness = next(item for item in report["witnesses"] if item["id"] == softlock["witness_id"])
        self.assertEqual(witness["kind"], "state")
        self.assertEqual(witness["steps"], [])

    def test_python_report_validation_matches_schema_literals(self) -> None:
        report = analyze_gamepack(_gamepack("abstract-puzzle"))
        for field_path in (
            ("gamepack", "format_version"),
            ("requirement", "analyzer_version"),
            ("analyzer", "version"),
        ):
            malformed = copy.deepcopy(report)
            malformed[field_path[0]][field_path[1]] = True
            if field_path[0] == "requirement":
                malformed["requirement"]["content_hash"] = canonical_creation_hash(
                    malformed["requirement"]
                )
            malformed = _reseal(malformed)
            with self.assertRaises(GameAnalysisError):
                validate_game_analysis_structure(malformed)
        malformed = copy.deepcopy(report)
        malformed["witnesses"][0]["kind"] = "arbitrary"
        malformed = _reseal(malformed)
        with self.assertRaises(GameAnalysisError):
            validate_game_analysis_structure(malformed)

        malformed = copy.deepcopy(report)
        malformed["metrics"]["transitions"] = 262_145
        malformed = _reseal(malformed)
        with self.assertRaises(GameAnalysisError):
            validate_game_analysis_structure(malformed)

        malformed = copy.deepcopy(report)
        malformed["witnesses"][0]["steps"][0]["parameters"] = {
            "synthetic_parameter": {"nested": True}
        }
        malformed = _reseal(malformed)
        with self.assertRaises(GameAnalysisError):
            validate_game_analysis_structure(malformed)

    def test_failed_status_reason_codes_and_unsupported_profile_are_correlated(
        self,
    ) -> None:
        report = analyze_gamepack(_gamepack("abstract-puzzle"))
        malformed = copy.deepcopy(report)
        check = next(item for item in malformed["checks"] if item["id"] == "execution_integrity")
        check["status"] = "failed"
        check["reason_codes"] = ["synthetic_failure"]
        malformed["summary"]["passed"] -= 1
        malformed["summary"]["failed"] += 1
        malformed["status"] = "failed"
        malformed["reason_codes"] = []
        malformed = _reseal(malformed)
        with self.assertRaises(GameAnalysisError):
            validate_game_analysis_structure(malformed)

        malformed = copy.deepcopy(report)
        check = next(item for item in malformed["checks"] if item["id"] == "execution_integrity")
        check["status"] = "not_applicable"
        malformed["summary"]["passed"] -= 1
        malformed = _reseal(malformed)
        with self.assertRaisesRegex(GameAnalysisError, "passed status overclaims"):
            validate_game_analysis_structure(malformed)

        unsupported_pack = _gamepack("abstract-puzzle")
        requirement = {
            "profile": "unsupported",
            "analyzer_id": "worldforge.unsupported_profile",
            "analyzer_version": 1,
            "reason_code": "analysis_profile_unsupported",
            "limits": dict(ANALYSIS_LIMITS),
        }
        requirement["content_hash"] = canonical_creation_hash(requirement)
        unsupported_pack["analysis_requirements"] = requirement
        unsupported = game_analysis_module._unsupported_report(unsupported_pack)
        missing_unsupported_reasons = copy.deepcopy(unsupported)
        missing_unsupported_reasons["reason_codes"] = []
        missing_unsupported_reasons["checks"][0]["reason_codes"] = []
        missing_unsupported_reasons = _reseal(missing_unsupported_reasons)
        with self.assertRaisesRegex(GameAnalysisError, "requires reason codes"):
            validate_game_analysis_structure(missing_unsupported_reasons)

        unsupported["status"] = "failed"
        unsupported["checks"][0]["status"] = "failed"
        unsupported["summary"]["failed"] = 1
        unsupported["reason_codes"] = ["analysis_profile_unsupported"]
        unsupported = _reseal(unsupported)
        with self.assertRaisesRegex(
            GameAnalysisError,
            "unsupported status must exactly match",
        ):
            validate_game_analysis_structure(unsupported)

    def test_failure_only_terminal_does_not_prove_initial_puzzle_solvability(
        self,
    ) -> None:
        gamepack = _gamepack("abstract-puzzle")
        gamepack["logic"]["initial_state"]["move_count"] = 8
        state = next(
            item for item in gamepack["logic"]["state_schema"] if item["id"] == "move_count"
        )
        state["initial"] = 8
        gamepack["logic"]["endings"].append(
            {
                "condition_ids": ["move_limit_reached"],
                "event_ids": [],
                "id": "move_limit_failure",
                "kind": "failure",
                "presentation_hook_ids": ["ending_feedback"],
            }
        )
        gamepack["logic"]["endings"].sort(key=lambda item: item["id"].encode("utf-8"))
        with mock.patch.object(
            game_analysis_module,
            "validate_gamepack_document",
            return_value=gamepack,
        ):
            report = analyze_gamepack(gamepack)
        checks = {item["id"]: item for item in report["checks"]}
        self.assertEqual(
            checks["initial_solvable"],
            {
                "id": "initial_solvable",
                "status": "failed",
                "reason_codes": ["initial_state_unsolvable"],
            },
        )
        self.assertEqual(report["status"], "failed")

    def test_noop_narrative_option_witness_includes_the_executed_edge(self) -> None:
        gamepack = _gamepack("branching-narrative")
        rule = next(item for item in gamepack["logic"]["rules"] if item["id"] == "choose_left_rule")
        rule["effect_ids"] = []
        transition_contract = next(
            item
            for item in gamepack["logic"]["narrative_transitions"]
            if item["action_id"] == "choose_left"
        )
        transition_contract["atomic_source_effect_ids"] = []
        transition_contract["target_unit_id"] = "central_choice"
        transition_contract["effect"]["value"] = "central_choice"
        with mock.patch.object(
            game_analysis_module,
            "validate_gamepack_document",
            return_value=gamepack,
        ):
            report = analyze_gamepack(gamepack)
        option_witness = next(
            item
            for item in report["witnesses"]
            if (
                item["kind"],
                item["target_id"],
            )
            == ("narrative_option", "central_choice:choose_left")
        )
        self.assertEqual(len(option_witness["steps"]), 1)
        self.assertEqual(option_witness["steps"][0]["action_id"], "choose_left")
        self.assertEqual(
            option_witness["steps"][0]["pre_state_hash"],
            option_witness["steps"][0]["post_state_hash"],
        )

    def test_secure_create_only_publish_and_load(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        report = analyze_gamepack(gamepack)
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "analysis.json"
            published = publish_game_analysis(output, report, gamepack=gamepack)
            self.assertEqual(published.content_hash, report["content_hash"])
            self.assertEqual(load_game_analysis(output, gamepack=gamepack), report)
            with self.assertRaises(GameAnalysisError):
                publish_game_analysis(output, report, gamepack=gamepack)
            self.assertEqual(output.read_bytes(), serialize_game_analysis(report))

    def test_strict_json_and_snapshot_security_are_inherited(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        report = analyze_gamepack(gamepack)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            duplicate = root / "duplicate.json"
            duplicate.write_text(
                '{"format":"world-forge.game_analysis","format":"world-forge.game_analysis"}',
                encoding="utf-8",
            )
            with self.assertRaises(GameAnalysisError) as caught:
                load_game_analysis(duplicate)
            self.assertEqual(caught.exception.reason_code, "invalid_json")

            real = root / "real.json"
            real.write_bytes(serialize_game_analysis(report))
            alias = root / "alias.json"
            alias.symlink_to(real.name)
            with self.assertRaises(GameAnalysisError) as caught:
                load_game_analysis(alias, gamepack=gamepack)
            self.assertEqual(caught.exception.reason_code, "invalid_json")
            hardlink = root / "hardlink.json"
            try:
                os.link(real, hardlink)
            except OSError:
                pass
            else:
                with self.assertRaises(GameAnalysisError) as caught:
                    load_game_analysis(hardlink, gamepack=gamepack)
                self.assertEqual(caught.exception.reason_code, "invalid_json")


class AnalyzeGameCliTests(unittest.TestCase):
    def test_cli_prints_full_report_or_create_only_receipt(self) -> None:
        gamepack = EXAMPLES / "abstract-puzzle" / "artifacts"
        gamepack /= "abstract-puzzle.gamepack.json"
        env = {"PYTHONPATH": str(ROOT / "src")}
        printed = subprocess.run(
            [sys.executable, "-m", "worldforge", "analyze-game", str(gamepack)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(printed.returncode, 0, printed.stderr)
        self.assertEqual(json.loads(printed.stdout)["status"], "passed")
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "analysis.json"
            written = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "worldforge",
                    "analyze-game",
                    str(gamepack),
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(written.returncode, 0, written.stderr)
            receipt = json.loads(written.stdout)
            self.assertEqual(receipt["status"], "passed")
            self.assertEqual(receipt["output"], str(output))
            self.assertTrue(output.is_file())

    def test_cli_contract_errors_use_stderr_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            malformed = Path(raw) / "bad.json"
            malformed.write_text("{}", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "worldforge",
                    "analyze-game",
                    str(malformed),
                ],
                cwd=ROOT,
                env={"PYTHONPATH": str(ROOT / "src")},
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("ERROR", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
