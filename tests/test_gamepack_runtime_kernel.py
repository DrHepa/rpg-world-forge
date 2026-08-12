from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

from gamepack_runtime import (
    ANALYSIS_LIMITS,
    EXECUTION_SEMANTICS,
    MAX_GAMEPACK_BYTES,
    CandidateAction,
    GameLogicError,
    GamepackInterpreter,
    GameSession,
    StateClassification,
    TransitionResult,
    analysis_requirements_for,
    canonical_action_hash,
    canonical_events_hash,
    canonical_gamepack_hash,
    canonical_state_bytes,
    canonical_state_hash,
    canonical_trace_step,
    classify_state,
    enumerate_candidates,
    initial_state,
    legal_transitions,
    load_gamepack_bytes,
    snapshot_plain_json,
    snapshot_strict_candidate,
    snapshot_strict_state,
    transition,
    validate_runtime_gamepack,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "multigenre-contracts"
PUZZLE_PATH = EXAMPLES / "abstract-puzzle" / "artifacts" / "abstract-puzzle.gamepack.json"
NARRATIVE_PATH = (
    EXAMPLES / "branching-narrative" / "artifacts" / "branching-narrative.gamepack.json"
)

PUZZLE_GAMEPACK_HASH = "0510d69d0f78d3e80810aa26dd4b76752416809f7733e731274ac8d7f35dac09"
PUZZLE_INITIAL_HASH = "0e45dbe418fea6b992d47cc9099d83a733c57ea64ae2c994d2d1e225f9a14bad"
PUZZLE_FINAL_HASH = "aebcc840113a83fcdceafc2ddab957ecd42198d1cd5c76812065fe720aa906ca"
NARRATIVE_GAMEPACK_HASH = "56b8a5393615603ca3a6bbc1a55cf557cadee2e05cf03a8b4714b4536e6cb7b7"
NARRATIVE_INITIAL_HASH = "d36c421fcde1dac5f68bc059902364d5216106f1057e891ab1984aea96204093"
NARRATIVE_LEFT_HASH = "1083d4e41a6bfad92c38beee91b01a267d67ca428c3a2625dc30bac79d2d7f51"
NARRATIVE_RIGHT_HASH = "a91e46da8e98f6b24bc1add282a76463426477e08d5ab7a0cca5d2df27e23a89"


def _load(path: Path) -> dict[str, object]:
    return load_gamepack_bytes(path.read_bytes(), source=os.fspath(path))


def _reseal(document: dict[str, object]) -> dict[str, object]:
    clone = copy.deepcopy(document)
    clone.pop("content_hash", None)
    clone["content_hash"] = canonical_gamepack_hash(clone)
    return clone


class NeutralKernelGoldenTraceTests(unittest.TestCase):
    def test_puzzle_trace_is_frozen_and_session_commits_only_accepted_actions(self) -> None:
        gamepack = _load(PUZZLE_PATH)
        self.assertEqual(gamepack["content_hash"], PUZZLE_GAMEPACK_HASH)
        session = GameSession(gamepack)
        self.assertEqual(session.state_hash, PUZZLE_INITIAL_HASH)

        rejected = session.apply("swap_tiles", {"first_index": 0, "second_index": 2})
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.rejection_reason, "rule_condition_false")
        self.assertEqual(session.state_hash, PUZZLE_INITIAL_HASH)

        committed = session.apply("swap_tiles", {"first_index": 0, "second_index": 1})
        self.assertTrue(committed.accepted)
        self.assertEqual(committed.events, ("tile_swapped",))
        self.assertEqual(committed.post_state_hash, PUZZLE_FINAL_HASH)
        self.assertEqual(session.state_hash, PUZZLE_FINAL_HASH)
        classification = session.classification
        self.assertEqual(classification.ending_ids, ("puzzle_complete",))
        self.assertEqual(classification.ending_kind, "success")

    def test_both_branching_endings_are_frozen_from_independent_sessions(self) -> None:
        gamepack = _load(NARRATIVE_PATH)
        self.assertEqual(gamepack["content_hash"], NARRATIVE_GAMEPACK_HASH)
        left = GameSession(gamepack)
        right = GameSession(copy.deepcopy(gamepack))
        self.assertEqual(left.state_hash, NARRATIVE_INITIAL_HASH)
        self.assertEqual(right.state_hash, NARRATIVE_INITIAL_HASH)

        left_result = left.apply("choose_left", {})
        right_result = right.apply("choose_right", {})
        self.assertEqual(left_result.post_state_hash, NARRATIVE_LEFT_HASH)
        self.assertEqual(right_result.post_state_hash, NARRATIVE_RIGHT_HASH)
        self.assertEqual(left.classification.ending_ids, ("ending_left",))
        self.assertEqual(right.classification.ending_ids, ("ending_right",))
        self.assertEqual(left_result.events, ("choice_left",))
        self.assertEqual(right_result.events, ("choice_right",))

    def test_canonical_state_action_and_event_hashes_are_frozen(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        state = GamepackInterpreter(puzzle).initial_state()
        self.assertEqual(canonical_state_hash(state), PUZZLE_INITIAL_HASH)
        self.assertEqual(
            canonical_action_hash(
                CandidateAction("swap_tiles", {"second_index": 1, "first_index": 0})
            ),
            "58e9a1b8453f0ce0dd1e254f27c2288f7b2033b73cab250aba1fea737e19f2e1",
        )
        self.assertEqual(
            canonical_events_hash(("tile_swapped",)),
            "b338449bd09d279ee2d5827d1c7b271c06c61e4e0ed51de00744f8d0fc092ff7",
        )
        reordered = dict(reversed(list(state.items())))
        self.assertEqual(canonical_state_bytes(reordered), canonical_state_bytes(state))

    def test_repeated_and_mapping_order_runs_are_byte_identical(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        reordered = dict(reversed(list(puzzle.items())))
        traces: list[bytes] = []
        for source in (puzzle, copy.deepcopy(puzzle), reordered):
            session = GameSession(source)
            result = session.apply(
                "swap_tiles",
                dict(reversed([("first_index", 0), ("second_index", 1)])),
            )
            traces.append(
                json.dumps(
                    {
                        "action": {
                            "action_id": result.action.action_id,
                            "parameters": result.action.parameters,
                        },
                        "events": result.events,
                        "pre": result.pre_state_hash,
                        "post": result.post_state_hash,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        self.assertEqual(traces, [traces[0], traces[0], traces[0]])


class NeutralKernelContractTests(unittest.TestCase):
    def test_runtime_accepts_current_optional_metadata_but_rejects_required_extensions(
        self,
    ) -> None:
        puzzle = _load(PUZZLE_PATH)
        self.assertEqual(
            puzzle["registered_extensions"][0]["id"],  # type: ignore[index]
            "example.optional-metadata",
        )
        required = copy.deepcopy(puzzle)
        required["registered_extensions"][0]["required"] = True  # type: ignore[index]
        required = _reseal(required)
        with self.assertRaises(GameLogicError) as caught:
            validate_runtime_gamepack(required)
        self.assertEqual(caught.exception.reason_code, "required_extension_unsupported")

    def test_unknown_format_version_semantics_and_fields_fail_closed(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        mutations = (
            ("gamepack_format_unsupported", lambda value: value.__setitem__("format", "other")),
            ("gamepack_version_unsupported", lambda value: value.__setitem__("format_version", 2)),
            (
                "execution_semantics_unsupported",
                lambda value: value["logic"]["execution_semantics"].__setitem__(  # type: ignore[index]
                    "semantics_version", 2
                ),
            ),
            ("gamepack_fields_invalid", lambda value: value.__setitem__("unknown_field", True)),
        )
        for expected, mutation in mutations:
            with self.subTest(expected=expected):
                changed = copy.deepcopy(puzzle)
                mutation(changed)
                changed = _reseal(changed)
                with self.assertRaises(GameLogicError) as caught:
                    validate_runtime_gamepack(changed)
                self.assertEqual(caught.exception.reason_code, expected)

    def test_unsafe_runtime_ai_script_and_code_fields_fail_closed_at_any_depth(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        for field in (
            "runtime_ai",
            "runtimeAI",
            "Runtime-AI",
            "runtime.ai",
            "RUNTIME_AI",
            "script",
            "native_code",
            "provider_credentials",
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(puzzle)
                changed["logic"]["actions"][0][field] = "forbidden"  # type: ignore[index]
                changed = _reseal(changed)
                with self.assertRaises(GameLogicError) as caught:
                    validate_runtime_gamepack(changed)
                self.assertEqual(caught.exception.reason_code, "unsafe_runtime_field")

    def test_noncanonical_collections_and_unsupported_effects_fail_closed(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        for collection in ("actions", "state_schema"):
            with self.subTest(collection=collection):
                noncanonical = copy.deepcopy(puzzle)
                noncanonical["logic"][collection].reverse()  # type: ignore[index]
                noncanonical = _reseal(noncanonical)
                with self.assertRaises(GameLogicError) as caught:
                    validate_runtime_gamepack(noncanonical)
                self.assertEqual(caught.exception.reason_code, "logic_order_invalid")

        unsupported = copy.deepcopy(puzzle)
        unsupported["logic"]["effects"][0]["operation"] = "execute_script"  # type: ignore[index]
        unsupported = _reseal(unsupported)
        with self.assertRaises(GameLogicError) as caught:
            GamepackInterpreter(unsupported)
        self.assertEqual(caught.exception.reason_code, "operator_unsupported")

    def test_nested_operands_and_runtime_platforms_are_closed_and_canonical(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        nested = copy.deepcopy(puzzle)
        condition = next(
            item
            for item in nested["logic"]["conditions"]  # type: ignore[index]
            if item["id"] == "adjacent_tile"
        )
        condition["left"]["unknown_semantics"] = "ignored"
        nested = _reseal(nested)
        with self.assertRaises(GameLogicError) as caught:
            validate_runtime_gamepack(nested)
        self.assertEqual(caught.exception.reason_code, "gamepack_fields_invalid")

        platform_order = copy.deepcopy(puzzle)
        platform_order["runtime_requirements"]["platform_matrix"].reverse()  # type: ignore[index]
        platform_order = _reseal(platform_order)
        with self.assertRaises(GameLogicError) as caught:
            validate_runtime_gamepack(platform_order)
        self.assertEqual(caught.exception.reason_code, "runtime_order_invalid")

    def test_state_action_and_parameter_inputs_are_bounded_and_exact(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        interpreter = GamepackInterpreter(puzzle)
        state = interpreter.initial_state()
        malformed = copy.deepcopy(state)
        malformed["extra"] = True
        with self.assertRaises(GameLogicError) as caught:
            interpreter.transition(
                malformed,
                CandidateAction("restart_board", {}),
            )
        self.assertEqual(caught.exception.reason_code, "state_domain_invalid")

        unknown = interpreter.transition(state, CandidateAction("unknown", {}))
        self.assertFalse(unknown.accepted)
        self.assertEqual(unknown.rejection_reason, "action_unknown")
        outside = interpreter.transition(
            state,
            CandidateAction("swap_tiles", {"first_index": 0, "second_index": 999}),
        )
        self.assertFalse(outside.accepted)
        self.assertEqual(outside.rejection_reason, "action_parameters_invalid")

        oversized = copy.deepcopy(puzzle)
        action = next(
            item
            for item in oversized["logic"]["actions"]  # type: ignore[index]
            if item["id"] == "swap_tiles"
        )
        action["parameters"][0]["maximum"] = 4096
        action["parameters"][1]["maximum"] = 4096
        oversized = _reseal(oversized)
        with self.assertRaises(GameLogicError) as caught:
            GamepackInterpreter(oversized)
        self.assertEqual(caught.exception.reason_code, "parameter_combinations_exceeded")

        with self.assertRaises(GameLogicError) as caught:
            interpreter.transition(
                state,
                CandidateAction([], {}),  # type: ignore[arg-type]
            )
        self.assertEqual(caught.exception.reason_code, "action_invalid")

        cyclic_parameters: dict[str, object] = {}
        cyclic_parameters["cycle"] = cyclic_parameters
        with self.assertRaises(GameLogicError) as caught:
            interpreter.transition(
                state,
                CandidateAction("swap_tiles", cyclic_parameters),  # type: ignore[arg-type]
            )
        self.assertEqual(caught.exception.reason_code, "json_cycle")

        session = GameSession(puzzle)
        with self.assertRaises(GameLogicError) as caught:
            session.apply("restart_board", [])  # type: ignore[arg-type]
        self.assertEqual(caught.exception.reason_code, "action_invalid")

    def test_declared_state_and_parameter_domains_have_closed_resource_bounds(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        oversized_state = copy.deepcopy(puzzle)
        oversized_state["logic"]["state_schema"][0]["max_items"] = 257  # type: ignore[index]
        oversized_state = _reseal(oversized_state)
        with self.assertRaises(GameLogicError) as caught:
            validate_runtime_gamepack(oversized_state)
        self.assertEqual(caught.exception.reason_code, "state_domain_invalid")

        oversized_parameter = copy.deepcopy(puzzle)
        action = next(
            item
            for item in oversized_parameter["logic"]["actions"]  # type: ignore[index]
            if item["id"] == "swap_tiles"
        )
        action["parameters"][0] = {
            "id": "first_index",
            "type": "string_array",
            "allowed_values": ["only"],
            "min_items": 0,
            "max_items": 257,
        }
        oversized_parameter = _reseal(oversized_parameter)
        with self.assertRaises(GameLogicError) as caught:
            validate_runtime_gamepack(oversized_parameter)
        self.assertEqual(caught.exception.reason_code, "parameter_domain_invalid")

        long_string = copy.deepcopy(puzzle)
        long_string["logic"]["state_schema"][0]["allowed_values"][0] = "x" * 257  # type: ignore[index]
        long_string = _reseal(long_string)
        with self.assertRaises(GameLogicError) as caught:
            validate_runtime_gamepack(long_string)
        self.assertEqual(caught.exception.reason_code, "state_domain_invalid")

    def test_strict_json_rejects_duplicates_floats_nonfinite_utf8_and_nonobjects(self) -> None:
        invalid_cases = (
            (b'{"format":"a","format":"b"}', "json_duplicate_key"),
            (b'{"value":1.0}', "json_float_unsupported"),
            (b'{"value":1e999}', "json_float_unsupported"),
            (b'{"value":NaN}', "json_non_finite"),
            (b"\xff", "json_utf8_invalid"),
            (b"[]", "json_root_invalid"),
        )
        for payload, expected in invalid_cases:
            with self.subTest(payload=payload):
                with self.assertRaises(GameLogicError) as caught:
                    load_gamepack_bytes(payload)
                self.assertEqual(caught.exception.reason_code, expected)

    def test_json_size_depth_nodes_and_safe_integer_bounds_preflight_before_hashing(self) -> None:
        with self.assertRaises(GameLogicError) as caught:
            load_gamepack_bytes(b'{"value":"' + (b"x" * (16 * 1024 * 1024)) + b'"}')
        self.assertEqual(caught.exception.reason_code, "gamepack_bytes_exceeded")

        puzzle = _load(PUZZLE_PATH)
        unsafe_integer = copy.deepcopy(puzzle)
        unsafe_integer["logic"]["initial_state"]["move_count"] = 9_007_199_254_740_992  # type: ignore[index]
        with self.assertRaises(GameLogicError) as caught:
            canonical_gamepack_hash(unsafe_integer)
        self.assertEqual(caught.exception.reason_code, "json_integer_unsupported")

        cyclic: dict[str, object] = {}
        cyclic["cycle"] = cyclic
        with self.assertRaises(GameLogicError) as caught:
            canonical_gamepack_hash(cyclic)
        self.assertEqual(caught.exception.reason_code, "json_cycle")


class NeutralExecutableClosureTests(unittest.TestCase):
    def assert_neutral_and_forge_reject(
        self,
        document: dict[str, object],
        *,
        label: str,
    ) -> None:
        from worldforge.gamepack import GamepackError, validate_gamepack_document

        resealed = _reseal(document)
        with self.subTest(label=label, validator="neutral"):
            with self.assertRaises(GameLogicError):
                validate_runtime_gamepack(resealed)
        with self.subTest(label=label, validator="forge"):
            with self.assertRaises(GamepackError):
                validate_gamepack_document(resealed)

    def test_resealed_reviewer_mutation_corpus_fails_like_forge(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        narrative = _load(NARRATIVE_PATH)
        mutations: list[tuple[str, dict[str, object]]] = []

        duplicate_action_rule = copy.deepcopy(puzzle)
        action_rule_ids = duplicate_action_rule["logic"]["actions"][0]["rule_ids"]  # type: ignore[index]
        action_rule_ids.append(action_rule_ids[0])
        mutations.append(("duplicate_action_rule", duplicate_action_rule))

        duplicate_rule_effect = copy.deepcopy(puzzle)
        rule_effect_ids = duplicate_rule_effect["logic"]["rules"][0]["effect_ids"]  # type: ignore[index]
        rule_effect_ids.append(rule_effect_ids[0])
        mutations.append(("duplicate_rule_effect", duplicate_rule_effect))

        duplicate_emitted_event = copy.deepcopy(puzzle)
        rule_event_ids = duplicate_emitted_event["logic"]["rules"][0]["event_ids"]  # type: ignore[index]
        rule_event_ids.append(rule_event_ids[0])
        mutations.append(("duplicate_emitted_event", duplicate_emitted_event))

        ghost_event = copy.deepcopy(puzzle)
        ghost_event["logic"]["rules"][0]["event_ids"][0] = "ghost_event"  # type: ignore[index]
        mutations.append(("undeclared_event", ghost_event))

        contradictory_literal = copy.deepcopy(puzzle)
        move_limit = next(
            condition
            for condition in contradictory_literal["logic"]["conditions"]  # type: ignore[index,union-attr]
            if condition["id"] == "move_limit_reached"
        )
        move_limit["right"]["value_type"] = "string"
        mutations.append(("contradictory_literal_type", contradictory_literal))

        unknown_ending = copy.deepcopy(puzzle)
        unknown_ending["logic"]["endings"][0]["kind"] = "victory_plus"  # type: ignore[index]
        mutations.append(("unknown_ending_kind", unknown_ending))

        unknown_comparison = copy.deepcopy(puzzle)
        comparison = next(
            condition
            for condition in unknown_comparison["logic"]["conditions"]  # type: ignore[index,union-attr]
            if condition["operator"] == "compare"
        )
        comparison["comparison"] = "approximately_equal"
        mutations.append(("unknown_comparison", unknown_comparison))

        unknown_operator = copy.deepcopy(puzzle)
        unknown_operator["logic"]["conditions"][0]["operator"] = "execute"  # type: ignore[index]
        mutations.append(("unknown_condition_operator", unknown_operator))

        crossed_modules = copy.deepcopy(puzzle)
        crossed_modules["modules"]["narrative"] = copy.deepcopy(  # type: ignore[index]
            narrative["modules"]["narrative"]  # type: ignore[index]
        )
        mutations.append(("cross_correlated_modules", crossed_modules))

        camel_case_runtime_ai = copy.deepcopy(puzzle)
        camel_case_runtime_ai["provenance"][0]["runtimeAI"] = {  # type: ignore[index,union-attr]
            "model": "forbidden"
        }
        mutations.append(("camel_case_runtime_ai", camel_case_runtime_ai))

        unknown_required_feature = copy.deepcopy(puzzle)
        feature = "runtime:execute_python"
        unknown_required_feature["runtime_requirements"]["required_features"] = sorted(  # type: ignore[index]
            [
                *unknown_required_feature["runtime_requirements"]["required_features"],  # type: ignore[index]
                feature,
            ],
            key=lambda item: item.encode("utf-8"),
        )
        mutations.append(("unknown_required_feature", unknown_required_feature))

        for label, document in mutations:
            self.assert_neutral_and_forge_reject(document, label=label)

    def test_self_consistent_unknown_required_feature_is_still_unsupported(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        feature = "runtime:execute_python"
        puzzle["runtime_requirements"]["required_features"].append(feature)  # type: ignore[index]
        puzzle["runtime_requirements"]["required_features"].sort(  # type: ignore[index]
            key=lambda item: item.encode("utf-8")
        )
        for mechanic, requirement in zip(
            puzzle["logic"]["mechanics"],  # type: ignore[index]
            puzzle["mechanic_requirements"],  # type: ignore[index]
            strict=True,
        ):
            mechanic["required_feature_ids"].append(feature)
            mechanic["required_feature_ids"].sort(key=lambda item: item.encode("utf-8"))
            requirement["required_feature_ids"] = copy.deepcopy(mechanic["required_feature_ids"])

        with self.assertRaises(GameLogicError) as caught:
            validate_runtime_gamepack(_reseal(puzzle))
        self.assertEqual(caught.exception.reason_code, "required_feature_unsupported")

    def test_authored_narrative_capability_fails_closed_with_exact_feature_id(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        feature = "action:realtime_combat"
        action = puzzle["logic"]["actions"][0]  # type: ignore[index]
        mechanic = next(
            item
            for item in puzzle["logic"]["mechanics"]  # type: ignore[index]
            if item["action_id"] == action["id"]
        )
        requirement = next(
            item
            for item in puzzle["mechanic_requirements"]  # type: ignore[index]
            if item["mechanic_id"] == mechanic["id"]
        )
        action["required_feature_ids"].append(feature)
        action["required_feature_ids"].sort(key=lambda item: item.encode("utf-8"))
        mechanic["required_feature_ids"] = copy.deepcopy(action["required_feature_ids"])
        requirement["required_feature_ids"] = copy.deepcopy(action["required_feature_ids"])
        puzzle["runtime_requirements"]["required_features"] = sorted(  # type: ignore[index]
            {
                required_feature
                for item in puzzle["logic"]["mechanics"]  # type: ignore[index]
                for required_feature in item["required_feature_ids"]
            },
            key=lambda item: item.encode("utf-8"),
        )

        with self.assertRaises(GameLogicError) as caught:
            validate_runtime_gamepack(_reseal(puzzle))

        self.assertEqual(caught.exception.reason_code, "required_feature_unsupported")
        self.assertIn(feature, caught.exception.detail)

    def test_runtime_target_tokens_match_the_published_forge_contract(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        mutations: list[tuple[str, dict[str, object]]] = []
        for field in (
            "optional_features",
            "input_capabilities",
            "asset_formats",
        ):
            changed = copy.deepcopy(puzzle)
            changed["runtime_requirements"][field] = ["not a token"]  # type: ignore[index]
            mutations.append((f"invalid_{field}", changed))
        invalid_adapter = copy.deepcopy(puzzle)
        invalid_adapter["runtime_requirements"]["requested_adapter"] = "not portable"  # type: ignore[index]
        mutations.append(("invalid_requested_adapter", invalid_adapter))

        for label, document in mutations:
            self.assert_neutral_and_forge_reject(document, label=label)

    def test_runtime_string_domains_preserve_the_published_nfc_contract(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        allowed = puzzle["logic"]["state_schema"][0]["allowed_values"]  # type: ignore[index]
        allowed.append("e\u0301")
        allowed.sort(key=lambda item: item.encode("utf-8"))
        self.assert_neutral_and_forge_reject(puzzle, label="non_nfc_allowed_value")

    def test_crossed_missing_duplicate_and_unknown_references_fail_closed(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        mutations: list[tuple[str, dict[str, object]]] = []

        missing_owned_rule = copy.deepcopy(puzzle)
        missing_owned_rule["logic"]["actions"][0]["rule_ids"] = []  # type: ignore[index]
        mutations.append(("missing_owned_rule", missing_owned_rule))

        crossed_condition = copy.deepcopy(puzzle)
        restart_rule = next(
            rule
            for rule in crossed_condition["logic"]["rules"]  # type: ignore[index,union-attr]
            if rule["action_id"] == "restart_board"
        )
        restart_rule["condition_ids"] = ["first_index_valid"]
        mutations.append(("crossed_condition_scope", crossed_condition))

        unknown_effect = copy.deepcopy(puzzle)
        unknown_effect["logic"]["rules"][0]["effect_ids"][0] = "ghost_effect"  # type: ignore[index]
        mutations.append(("unknown_effect", unknown_effect))

        duplicate_parameter = copy.deepcopy(puzzle)
        swap_action = next(
            action
            for action in duplicate_parameter["logic"]["actions"]  # type: ignore[index,union-attr]
            if action["id"] == "swap_tiles"
        )
        swap_action["parameters"].append(copy.deepcopy(swap_action["parameters"][0]))
        mutations.append(("duplicate_parameter", duplicate_parameter))

        unknown_recovery_action = copy.deepcopy(puzzle)
        unknown_recovery_action["logic"]["failures"][0]["recovery_action_ids"] = [  # type: ignore[index]
            "ghost_action"
        ]
        mutations.append(("unknown_recovery_action", unknown_recovery_action))

        unknown_ending_event = copy.deepcopy(puzzle)
        unknown_ending_event["logic"]["endings"][0]["event_ids"] = ["ghost_event"]  # type: ignore[index]
        mutations.append(("unknown_ending_event", unknown_ending_event))

        crossed_mechanic = copy.deepcopy(puzzle)
        crossed_mechanic["logic"]["mechanics"][0]["effect_ids"] = ["swap_tiles"]  # type: ignore[index]
        mutations.append(("crossed_mechanic_closure", crossed_mechanic))

        for label, document in mutations:
            self.assert_neutral_and_forge_reject(document, label=label)

    def test_loader_rejects_the_complete_forge_executable_oracle_corpus(self) -> None:
        from worldforge.gamepack import GamepackError, validate_gamepack_document

        puzzle = _load(PUZZLE_PATH)
        narrative = _load(NARRATIVE_PATH)
        mutations: list[tuple[str, dict[str, object]]] = []

        for distance in (0, 2, 257, 4097):
            changed = copy.deepcopy(puzzle)
            adjacent = next(
                condition
                for condition in changed["logic"]["conditions"]  # type: ignore[index,union-attr]
                if condition["operator"] == "integer_distance"
            )
            adjacent["distance"] = distance
            mutations.append((f"integer_distance_{distance}", changed))

        goals_empty = copy.deepcopy(puzzle)
        goals_empty["logic"]["goals"] = []  # type: ignore[index]
        mutations.append(("goals_empty", goals_empty))

        cursor_owner_missing = copy.deepcopy(narrative)
        cursor_owner_missing["logic"]["state_schema"][-1].pop("compiler_owned")  # type: ignore[index]
        mutations.append(("cursor_state_compiler_owner_missing", cursor_owner_missing))

        cursor_domain_diverges = copy.deepcopy(narrative)
        cursor_domain_diverges["logic"]["state_schema"][-1]["allowed_values"].append(  # type: ignore[index]
            "ghost"
        )
        mutations.append(("cursor_state_domain_diverges", cursor_domain_diverges))

        module_source_diverges = copy.deepcopy(puzzle)
        module_source_diverges["modules"]["activities"][0]["source"]["content_hash"] = (  # type: ignore[index]
            "0" * 64
        )
        mutations.append(("module_source_hash_diverges_provenance", module_source_diverges))

        narrative_choice_next_empty = copy.deepcopy(narrative)
        narrative_choice_next_empty["modules"]["narrative"][0]["units"][0][  # type: ignore[index]
            "next_unit_ids"
        ] = []
        mutations.append(("narrative_choice_next_empty", narrative_choice_next_empty))

        narrative_choice_next_unknown = copy.deepcopy(narrative)
        narrative_choice_next_unknown["modules"]["narrative"][0]["units"][0][  # type: ignore[index]
            "next_unit_ids"
        ] = ["ghost"]
        mutations.append(("narrative_choice_next_unknown", narrative_choice_next_unknown))

        platform_matrix_empty = copy.deepcopy(puzzle)
        platform_matrix_empty["runtime_requirements"]["platform_matrix"] = []  # type: ignore[index]
        mutations.append(("runtime_platform_matrix_empty", platform_matrix_empty))

        camera_unknown = copy.deepcopy(puzzle)
        camera_unknown["runtime_requirements"]["presentation"]["camera"] = "unknown"  # type: ignore[index]
        mutations.append(("runtime_camera_unknown", camera_unknown))

        perspective_unknown = copy.deepcopy(puzzle)
        perspective_unknown["runtime_requirements"]["presentation"]["perspective"] = (  # type: ignore[index]
            "unknown"
        )
        mutations.append(("runtime_perspective_unknown", perspective_unknown))

        asset_format_uncorrelated = copy.deepcopy(puzzle)
        asset_format_uncorrelated["runtime_requirements"]["asset_formats"].append(  # type: ignore[index]
            "asset:wav"
        )
        mutations.append(("runtime_asset_format_uncorrelated", asset_format_uncorrelated))

        hook_binding_unknown = copy.deepcopy(puzzle)
        hook_binding_unknown["logic"]["presentation_hooks"][0]["asset_binding_ids"] = [  # type: ignore[index]
            "ghost_binding"
        ]
        mutations.append(("hook_asset_binding_unknown", hook_binding_unknown))

        optional_extension_unknown = copy.deepcopy(puzzle)
        optional_extension_unknown["registered_extensions"][0]["id"] = "unknown"  # type: ignore[index]
        mutations.append(("optional_extension_id_unknown", optional_extension_unknown))

        optional_extension_non_nfc = copy.deepcopy(puzzle)
        optional_extension_non_nfc["registered_extensions"][0]["id"] = "example.e\u0301"  # type: ignore[index]
        mutations.append(("optional_extension_id_non_nfc", optional_extension_non_nfc))

        parameter_product_unbounded = copy.deepcopy(puzzle)
        swap_action = next(
            action
            for action in parameter_product_unbounded["logic"]["actions"]  # type: ignore[index,union-attr]
            if action["id"] == "swap_tiles"
        )
        swap_action["parameters"][0]["maximum"] = 64
        swap_action["parameters"][1]["maximum"] = 64
        mutations.append(("parameter_cross_product_unbounded_at_load", parameter_product_unbounded))

        self.assertEqual(len(mutations), 18)
        for label, document in mutations:
            with self.subTest(label=label, validator="forge"):
                resealed = _reseal(document)
                try:
                    checked = validate_gamepack_document(resealed)
                except GamepackError:
                    pass
                else:
                    with self.assertRaises(GameLogicError):
                        GamepackInterpreter(checked, already_validated=True)
            with self.subTest(label=label, validator="neutral_loader"):
                payload = json.dumps(
                    _reseal(document),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                with self.assertRaises(GameLogicError):
                    load_gamepack_bytes(payload)

    def test_top_level_projection_correlations_are_exact_and_closed(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        narrative = _load(NARRATIVE_PATH)
        mutations: list[tuple[str, dict[str, object]]] = []

        for path, value in (
            (("game", "title"), "Renamed game"),
            (("modules", "activities", 0, "title"), "Renamed module"),
            (("modules", "activities", 0, "activities", 0, "title"), "Renamed activity"),
        ):
            changed = copy.deepcopy(puzzle)
            target: object = changed
            for segment in path[:-1]:
                target = target[segment]  # type: ignore[index]
            target[path[-1]] = value  # type: ignore[index]
            mutations.append(("localization_" + "_".join(map(str, path)), changed))

        option_label = copy.deepcopy(narrative)
        option_label["modules"]["narrative"][0]["units"][0]["options"][0]["label"] = (  # type: ignore[index]
            "Renamed option"
        )
        mutations.append(("localization_option_label", option_label))

        provenance_missing = copy.deepcopy(puzzle)
        provenance_missing["provenance"].pop()  # type: ignore[index]
        mutations.append(("provenance_missing", provenance_missing))

        asset_roles_diverge = copy.deepcopy(puzzle)
        asset_roles_diverge["asset_requirements"][0]["roles"] = ["activity_visual"]  # type: ignore[index]
        mutations.append(("asset_roles_diverge", asset_roles_diverge))

        presentation_diverges = copy.deepcopy(puzzle)
        presentation_diverges["presentation"]["camera"] = "unknown"  # type: ignore[index]
        presentation_diverges["runtime_requirements"]["presentation"]["camera"] = "unknown"  # type: ignore[index]
        mutations.append(("unsupported_runtime_presentation", presentation_diverges))

        source_format_diverges = copy.deepcopy(puzzle)
        source_format_diverges["source"]["project"]["format"] = "world-forge.unknown"  # type: ignore[index]
        mutations.append(("source_format_diverges", source_format_diverges))

        for label, document in mutations:
            with self.subTest(label=label):
                with self.assertRaises(GameLogicError):
                    validate_runtime_gamepack(_reseal(document))

    def test_optional_extensions_require_an_explicit_runtime_registration(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        puzzle["registered_extensions"][0]["id"] = "unknown.optional-metadata"  # type: ignore[index]
        with self.assertRaises(GameLogicError) as caught:
            validate_runtime_gamepack(_reseal(puzzle))
        self.assertEqual(caught.exception.reason_code, "optional_extension_unsupported")


class StrictPlainJsonBoundaryTests(unittest.TestCase):
    def test_owned_snapshot_is_iterative_bounded_plain_and_alias_free(self) -> None:
        from gamepack_runtime.contracts import (
            MAX_GAMEPACK_BYTES,
            MAX_JSON_NODES,
            snapshot_plain_json,
        )

        source = {"alpha": [1, "two", True, None], "beta": {"value": 3}}
        snapshot = snapshot_plain_json(source)
        self.assertEqual(snapshot, source)
        self.assertIsNot(snapshot, source)
        self.assertIsNot(snapshot["alpha"], source["alpha"])  # type: ignore[index]
        self.assertIsNot(snapshot["beta"], source["beta"])  # type: ignore[index]
        source["alpha"].append("changed")
        self.assertEqual(snapshot["alpha"], [1, "two", True, None])  # type: ignore[index]

        class DictSubclass(dict[str, object]):
            pass

        class ListSubclass(list[object]):
            pass

        class IntSubclass(int):
            pass

        cyclic: dict[str, object] = {}
        cyclic["cycle"] = cyclic
        shared: list[object] = []
        aliased = {"first": shared, "second": shared}
        deep: object = None
        for _ in range(65):
            deep = [deep]
        cases = (
            (MappingProxyType({"value": 1}), "json_type_unsupported"),
            (DictSubclass({"value": 1}), "json_type_unsupported"),
            ({"value": ListSubclass([1])}, "json_type_unsupported"),
            ({"value": IntSubclass(1)}, "json_type_unsupported"),
            (cyclic, "json_cycle"),
            (aliased, "json_alias"),
            (deep, "json_depth_exceeded"),
            ({"wide": [None] * MAX_JSON_NODES}, "json_nodes_exceeded"),
            ({"large": "x" * MAX_GAMEPACK_BYTES}, "gamepack_bytes_exceeded"),
            ({"surrogate": "\ud800"}, "json_unicode_invalid"),
        )
        for value, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                with self.assertRaises(GameLogicError) as caught:
                    snapshot_plain_json(value)
                self.assertEqual(caught.exception.reason_code, expected_reason)

    def test_every_strict_public_entry_snapshots_before_copy_or_hash(self) -> None:
        from gamepack_runtime.contracts import snapshot_plain_json

        puzzle = _load(PUZZLE_PATH)
        interpreter = GamepackInterpreter(puzzle)
        state = interpreter.initial_state()

        class ExplosiveDict(dict[str, object]):
            def items(self):  # type: ignore[override]
                raise AssertionError("custom mapping methods must not run")

            def __deepcopy__(self, _memo: object) -> object:
                raise AssertionError("deepcopy must not run")

        strict_calls = (
            lambda: snapshot_plain_json(ExplosiveDict({"value": 1})),
            lambda: canonical_gamepack_hash(MappingProxyType(puzzle)),
            lambda: GamepackInterpreter(MappingProxyType(puzzle)),
            lambda: GamepackInterpreter(
                {"logic": MappingProxyType({})},
                already_validated=True,
            ),
            lambda: canonical_state_hash(MappingProxyType(state)),
            lambda: interpreter.classify(MappingProxyType(state)),
            lambda: interpreter.transition(
                state,
                CandidateAction(
                    "swap_tiles",
                    MappingProxyType({"first_index": 0, "second_index": 1}),  # type: ignore[arg-type]
                ),
            ),
            lambda: GameSession(MappingProxyType(puzzle)),
            lambda: GameSession(puzzle).apply(
                "swap_tiles",
                MappingProxyType({"first_index": 0, "second_index": 1}),
            ),
        )
        for call in strict_calls:
            with self.subTest(call=repr(call)):
                with self.assertRaises(GameLogicError) as caught:
                    call()
                self.assertEqual(caught.exception.reason_code, "json_type_unsupported")

        cycle: dict[str, object] = {}
        cycle["cycle"] = cycle
        with self.assertRaises(GameLogicError) as caught:
            interpreter.transition(
                state,
                CandidateAction(
                    "swap_tiles",
                    {"first_index": cycle, "second_index": 1},  # type: ignore[dict-item]
                ),
            )
        self.assertEqual(caught.exception.reason_code, "json_cycle")

    def test_strict_byte_loader_preflights_deep_json_without_raw_errors(self) -> None:
        payload = b'{"value":' + (b"[" * 65) + b"null" + (b"]" * 65) + b"}"
        with self.assertRaises(GameLogicError) as caught:
            load_gamepack_bytes(payload)
        self.assertEqual(caught.exception.reason_code, "json_depth_exceeded")

    def test_strict_byte_loader_rejects_subclasses_before_decode(self) -> None:
        class ExplosiveBytes(bytes):
            def decode(self, *args: object, **kwargs: object) -> str:
                raise AssertionError("custom bytes methods must not run")

        with self.assertRaises(GameLogicError) as caught:
            load_gamepack_bytes(ExplosiveBytes(b"{}"))
        self.assertEqual(caught.exception.reason_code, "json_bytes_invalid")

    def test_hostile_public_api_inputs_never_execute_custom_container_methods(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        interpreter = GamepackInterpreter(puzzle)
        state = interpreter.initial_state()

        class HostileList(list[object]):
            def __iter__(self):  # type: ignore[override]
                raise AssertionError("hostile list iterator reached")

        class HostileDict(dict[str, object]):
            def __iter__(self):  # type: ignore[override]
                raise AssertionError("hostile mapping iterator reached")

            def __getitem__(self, key: str) -> object:
                raise AssertionError(f"hostile mapping getitem reached: {key}")

            def get(self, key: str, default: object = None) -> object:
                raise AssertionError(f"hostile mapping get reached: {key}")

            def items(self):  # type: ignore[override]
                raise AssertionError("hostile mapping items reached")

        calls = (
            lambda: canonical_events_hash(HostileList(["event"])),  # type: ignore[arg-type]
            lambda: analysis_requirements_for(HostileDict(), puzzle["logic"]),  # type: ignore[arg-type,index]
            lambda: analysis_requirements_for(puzzle["modules"], HostileDict()),  # type: ignore[arg-type,index]
            lambda: canonical_trace_step(None),  # type: ignore[arg-type]
            lambda: GamepackInterpreter(puzzle, limits=HostileDict()),  # type: ignore[arg-type]
            lambda: GamepackInterpreter(puzzle, already_validated=HostileList()),  # type: ignore[arg-type]
            lambda: load_gamepack_bytes(PUZZLE_PATH.read_bytes(), source=HostileList()),  # type: ignore[arg-type]
            lambda: interpreter.classify(HostileDict(state)),
            lambda: interpreter.transition(
                state,
                CandidateAction("swap_tiles", HostileDict()),
            ),
            lambda: GameSession(puzzle).apply("swap_tiles", HostileDict()),
        )
        for call in calls:
            with self.subTest(call=repr(call)):
                with self.assertRaises(GameLogicError):
                    call()

    def test_exported_functions_and_public_methods_map_wrong_types_to_stable_errors(
        self,
    ) -> None:
        import gamepack_runtime

        self.assertEqual(
            {
                name
                for name in gamepack_runtime.__all__
                if callable(getattr(gamepack_runtime, name))
            },
            {
                "CandidateAction",
                "GameLogicError",
                "GamePersistenceContext",
                "GameReplayRecorder",
                "GameSession",
                "GamepackInterpreter",
                "HeadlessExecutionResult",
                "RecordingGameSession",
                "StateClassification",
                "TransitionResult",
                "analysis_requirements_for",
                "build_game_execution_script",
                "build_game_persistence_context",
                "build_game_replay",
                "build_game_save",
                "build_persistence_generation",
                "canonical_action_hash",
                "canonical_events_hash",
                "canonical_gamepack_hash",
                "canonical_headless_hash",
                "canonical_persistence_hash",
                "canonical_state_bytes",
                "canonical_state_hash",
                "canonical_trace_step",
                "classify_state",
                "enumerate_candidates",
                "execute_game_execution_script",
                "execution_audit_guard",
                "initial_state",
                "legal_transitions",
                "load_gamepack_bytes",
                "load_game_replay_bytes",
                "load_game_save_bytes",
                "load_persistence_generation_bytes",
                "migrate_legacy_game_replay_slot",
                "migrate_legacy_game_save_slot",
                "play_game_replay",
                "read_game_replay_slot",
                "read_game_save_slot",
                "restore_game_save",
                "resolve_game_replay_slot_conflict",
                "resolve_game_save_slot_conflict",
                "rollback_game_replay_slot",
                "rollback_game_save_slot",
                "serialize_game_replay",
                "serialize_game_save",
                "serialize_game_execution_script",
                "serialize_headless_execution_receipt",
                "serialize_persistence_generation",
                "snapshot_plain_json",
                "snapshot_strict_candidate",
                "snapshot_strict_state",
                "transition",
                "validate_game_replay_document",
                "validate_game_save_document",
                "validate_game_execution_script",
                "validate_headless_execution_receipt",
                "validate_persistence_generation_document",
                "validate_runtime_gamepack",
                "validate_slot_name",
                "write_game_replay_slot",
                "write_game_save_slot",
            },
        )
        self.assertEqual(
            {
                name
                for name, value in GamepackInterpreter.__dict__.items()
                if not name.startswith("_") and callable(value)
            },
            {
                "classify",
                "enumerate_candidates",
                "initial_state",
                "legal_transitions",
                "transition",
                "transition_legacy",
                "transition_strict",
            },
        )
        self.assertEqual(
            {name for name in GameSession.__dict__ if not name.startswith("_")},
            {"apply", "classification", "restore", "state", "state_hash"},
        )

        puzzle = _load(PUZZLE_PATH)
        interpreter = GamepackInterpreter(puzzle)
        session = GameSession(puzzle)
        state = interpreter.initial_state()
        candidate = CandidateAction("restart_board", {})
        accepted = interpreter.transition(state, candidate)
        self.assertTrue(accepted.accepted)

        calls = (
            lambda: canonical_action_hash([]),  # type: ignore[arg-type]
            lambda: canonical_events_hash({}),  # type: ignore[arg-type]
            lambda: canonical_gamepack_hash([]),  # type: ignore[arg-type]
            lambda: canonical_state_bytes([]),  # type: ignore[arg-type]
            lambda: canonical_trace_step(None),  # type: ignore[arg-type]
            lambda: classify_state(puzzle, []),  # type: ignore[arg-type]
            lambda: enumerate_candidates([]),  # type: ignore[arg-type]
            lambda: initial_state({}),
            lambda: legal_transitions(puzzle, {}),  # type: ignore[arg-type]
            lambda: load_gamepack_bytes(bytearray()),  # type: ignore[arg-type]
            lambda: snapshot_strict_candidate({}),  # type: ignore[arg-type]
            lambda: snapshot_strict_state([]),
            lambda: transition(puzzle, {}, candidate),  # type: ignore[arg-type]
            lambda: validate_runtime_gamepack([]),
            lambda: analysis_requirements_for([], {}),  # type: ignore[arg-type]
            lambda: gamepack_runtime.build_persistence_generation(
                {},
                kind="save",
                slot="slot",
                sequence=0,
                parent_hashes=[],
                operation="write",
                context=None,  # type: ignore[arg-type]
            ),
            lambda: gamepack_runtime.validate_persistence_generation_document(
                {},
                context=None,  # type: ignore[arg-type]
            ),
            lambda: gamepack_runtime.load_persistence_generation_bytes(
                bytearray(),
                context=None,  # type: ignore[arg-type]
            ),
            lambda: gamepack_runtime.serialize_persistence_generation(
                {},
                context=None,  # type: ignore[arg-type]
            ),
            lambda: gamepack_runtime.migrate_legacy_game_save_slot(
                "",
                "slot",
                None,  # type: ignore[arg-type]
            ),
            lambda: gamepack_runtime.migrate_legacy_game_replay_slot(
                "",
                "slot",
                None,  # type: ignore[arg-type]
            ),
            lambda: gamepack_runtime.resolve_game_save_slot_conflict(
                "",
                "slot",
                {},
                None,  # type: ignore[arg-type]
            ),
            lambda: gamepack_runtime.resolve_game_replay_slot_conflict(
                "",
                "slot",
                {},
                None,  # type: ignore[arg-type]
            ),
            lambda: gamepack_runtime.rollback_game_save_slot(
                "",
                "slot",
                "",
                None,  # type: ignore[arg-type]
            ),
            lambda: gamepack_runtime.rollback_game_replay_slot(
                "",
                "slot",
                "",
                None,  # type: ignore[arg-type]
            ),
            lambda: interpreter.classify([]),  # type: ignore[arg-type]
            lambda: interpreter.legal_transitions({}),
            lambda: interpreter.transition({}, []),  # type: ignore[arg-type]
            lambda: interpreter.transition_strict({}, []),  # type: ignore[arg-type]
            lambda: interpreter.transition_legacy([], candidate),  # type: ignore[arg-type]
            lambda: session.apply([], {}),  # type: ignore[arg-type]
        )
        for call in calls:
            with self.subTest(call=repr(call)):
                try:
                    call()
                except GameLogicError:
                    pass
                except Exception as exc:  # pragma: no cover - failure diagnostic
                    self.fail(f"raw {type(exc).__name__} escaped: {exc}")
                else:
                    self.fail("malformed public input was accepted")

        self.assertEqual(snapshot_plain_json([]), [])
        self.assertEqual(snapshot_plain_json({}), {})
        self.assertEqual(
            canonical_state_hash({}),
            "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
        )

    def test_trace_step_owns_and_revalidates_every_committed_field(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        interpreter = GamepackInterpreter(puzzle)
        result = interpreter.transition(
            interpreter.initial_state(),
            CandidateAction("restart_board", {}),
        )
        trace = canonical_trace_step(result)
        self.assertEqual(trace["action_id"], "restart_board")

        malformed = TransitionResult(
            True,
            result.action,
            result.pre_state,
            result.post_state,
            result.pre_state_hash,
            result.post_state_hash,
            ("",),
            None,
        )
        with self.assertRaises(GameLogicError):
            canonical_trace_step(malformed)

    def test_analysis_policy_rejects_hostile_keys_before_hash_or_equality(self) -> None:
        puzzle = _load(PUZZLE_PATH)

        class CollidingHostileKey:
            def __hash__(self) -> int:
                return hash("abstract_puzzle")

            def __eq__(self, _other: object) -> bool:
                raise AssertionError("hostile key equality must not run")

        hostile_analyzers = {
            CollidingHostileKey(): ("hostile", 1),
            "branching_narrative": ("worldforge.branching_narrative_exhaustive", 1),
            "unsupported": ("worldforge.unsupported_profile", 1),
        }
        with self.assertRaises(GameLogicError) as caught:
            analysis_requirements_for(
                puzzle["modules"],  # type: ignore[arg-type]
                puzzle["logic"],  # type: ignore[arg-type]
                analyzers=hostile_analyzers,  # type: ignore[arg-type]
            )
        self.assertEqual(caught.exception.reason_code, "analysis_policy_invalid")

        malformed_descriptors = (
            {
                "abstract_puzzle": ("worldforge.abstract_puzzle_exhaustive", True),
                "branching_narrative": (
                    "worldforge.branching_narrative_exhaustive",
                    1,
                ),
                "unsupported": ("worldforge.unsupported_profile", 1),
            },
            {
                "abstract_puzzle": ("x" * 513, 1),
                "branching_narrative": (
                    "worldforge.branching_narrative_exhaustive",
                    1,
                ),
                "unsupported": ("worldforge.unsupported_profile", 1),
            },
        )
        for analyzers in malformed_descriptors:
            with self.subTest(analyzers=analyzers):
                with self.assertRaises(GameLogicError) as caught:
                    analysis_requirements_for(
                        puzzle["modules"],  # type: ignore[arg-type]
                        puzzle["logic"],  # type: ignore[arg-type]
                        analyzers=analyzers,
                    )
                self.assertEqual(caught.exception.reason_code, "analysis_policy_invalid")

    def test_mapping_proxy_access_failures_are_stable_at_transition_boundaries(
        self,
    ) -> None:
        from collections.abc import Iterator, Mapping

        import worldforge.game_logic as facade

        puzzle = _load(PUZZLE_PATH)
        neutral = GamepackInterpreter(puzzle)
        legacy = facade._Interpreter(puzzle)
        state = neutral.initial_state()
        candidate = CandidateAction("restart_board", {})

        class HostileBacking(dict[str, object]):
            def __iter__(self):
                raise AssertionError("hostile proxy iteration reached")

            def __len__(self) -> int:
                raise AssertionError("hostile proxy length reached")

            def items(self):  # type: ignore[override]
                raise AssertionError("hostile proxy items reached")

            def __getitem__(self, _key: object) -> object:
                raise AssertionError("hostile proxy item access reached")

        hostile_state = MappingProxyType(HostileBacking(state))
        hostile_parameters = MappingProxyType(HostileBacking({"first_index": 0, "second_index": 1}))

        class ItemsFailure(dict[str, object]):
            def items(self):  # type: ignore[override]
                raise RuntimeError("hostile proxy items reached")

        class ItemAccessFailure(Mapping[str, object]):
            def __init__(self, values: dict[str, object]) -> None:
                self._values = values

            def __iter__(self) -> Iterator[str]:
                return iter(self._values)

            def __len__(self) -> int:
                return len(self._values)

            def __getitem__(self, _key: str) -> object:
                raise LookupError("hostile proxy item access reached")

        hostile_items = MappingProxyType(ItemsFailure({"first_index": 0, "second_index": 1}))
        hostile_item_access = MappingProxyType(
            ItemAccessFailure({"first_index": 0, "second_index": 1})
        )
        hostile_state_item_access = MappingProxyType(ItemAccessFailure(state))

        strict_cases = (
            lambda: neutral.transition(hostile_state, candidate),
            lambda: neutral.transition(
                state,
                CandidateAction("swap_tiles", hostile_parameters),  # type: ignore[arg-type]
            ),
        )
        for call in strict_cases:
            with self.subTest(boundary="strict"):
                with self.assertRaises(GameLogicError):
                    call()

        legacy_cases = (
            (lambda: neutral.transition_legacy(hostile_state, candidate), "state_domain_invalid"),
            (
                lambda: neutral.transition_legacy(
                    state,
                    CandidateAction("swap_tiles", hostile_parameters),  # type: ignore[arg-type]
                ),
                "action_invalid",
            ),
            (lambda: legacy.transition(hostile_state, candidate), "state_domain_invalid"),
            (
                lambda: legacy.transition(
                    state,
                    CandidateAction("swap_tiles", hostile_parameters),  # type: ignore[arg-type]
                ),
                "action_invalid",
            ),
            (
                lambda: neutral.transition_legacy(
                    state,
                    CandidateAction("swap_tiles", hostile_items),  # type: ignore[arg-type]
                ),
                "action_invalid",
            ),
            (
                lambda: legacy.transition(
                    state,
                    CandidateAction("swap_tiles", hostile_item_access),  # type: ignore[arg-type]
                ),
                "action_invalid",
            ),
            (
                lambda: neutral.transition_legacy(hostile_state_item_access, candidate),
                "state_domain_invalid",
            ),
        )
        for call, expected_reason in legacy_cases:
            with self.subTest(boundary="legacy", expected_reason=expected_reason):
                with self.assertRaises(GameLogicError) as caught:
                    call()
                self.assertEqual(caught.exception.reason_code, expected_reason)

        benign = neutral.transition_legacy(
            MappingProxyType(state),
            CandidateAction(
                "swap_tiles",
                MappingProxyType({"first_index": 0, "second_index": 1}),  # type: ignore[arg-type]
            ),
        )
        self.assertTrue(benign.accepted)
        self.assertEqual(benign.post_state_hash, PUZZLE_FINAL_HASH)

    def test_legacy_state_keys_are_typed_before_hash_or_equality(self) -> None:
        import worldforge.game_logic as facade

        puzzle = _load(PUZZLE_PATH)
        neutral = GamepackInterpreter(puzzle)
        forge = facade._Interpreter(puzzle)
        state = neutral.initial_state()
        candidate = CandidateAction("restart_board", {})
        replaced_key = next(iter(state))

        class HostileKey:
            def __init__(self, *, collision: bool) -> None:
                self.collision = collision
                self.hash_calls = 0
                self.equality_calls = 0

            def __hash__(self) -> int:
                self.hash_calls += 1
                target = replaced_key if self.collision else "not_a_schema_key"
                return hash(target)

            def __eq__(self, _other: object) -> bool:
                self.equality_calls += 1
                raise AssertionError("hostile key equality must not run")

        def malformed_state(key: HostileKey) -> dict[object, object]:
            value = dict(state)
            replaced = value.pop(replaced_key)
            value[key] = replaced
            return value

        colliding_key = HostileKey(collision=True)
        colliding = malformed_state(colliding_key)
        colliding_proxy = MappingProxyType(colliding)
        baseline = (colliding_key.hash_calls, colliding_key.equality_calls)
        six_paths = (
            ("neutral_dict", lambda: neutral.transition_legacy(colliding, candidate)),
            (
                "neutral_proxy",
                lambda: neutral.transition_legacy(colliding_proxy, candidate),
            ),
            ("forge_dict", lambda: forge.transition(colliding, candidate)),
            ("forge_proxy", lambda: forge.transition(colliding_proxy, candidate)),
            (
                "facade_transition",
                lambda: facade.transition(puzzle, colliding, candidate),
            ),
            (
                "facade_legal_transitions",
                lambda: facade.legal_transitions(puzzle, colliding_proxy),
            ),
        )
        for label, call in six_paths:
            with self.subTest(label=label):
                with self.assertRaises(GameLogicError) as caught:
                    call()
                self.assertEqual(caught.exception.reason_code, "state_domain_invalid")
                self.assertEqual(
                    str(caught.exception),
                    "state_domain_invalid: state keys do not exactly match schema",
                )
        self.assertEqual(
            (colliding_key.hash_calls, colliding_key.equality_calls),
            baseline,
        )

        noncolliding_key = HostileKey(collision=False)
        noncolliding = malformed_state(noncolliding_key)
        noncolliding_baseline = (
            noncolliding_key.hash_calls,
            noncolliding_key.equality_calls,
        )
        for candidate_state in (noncolliding, MappingProxyType(noncolliding)):
            with self.subTest(noncolliding=type(candidate_state).__name__):
                with self.assertRaises(GameLogicError) as caught:
                    neutral.transition_legacy(candidate_state, candidate)  # type: ignore[arg-type]
                self.assertEqual(caught.exception.reason_code, "state_domain_invalid")
        self.assertEqual(
            (noncolliding_key.hash_calls, noncolliding_key.equality_calls),
            noncolliding_baseline,
        )

        benign_neutral = neutral.transition_legacy(state, candidate)
        benign_proxy = neutral.transition_legacy(MappingProxyType(state), candidate)
        benign_forge = forge.transition(state, candidate)
        benign_facade = facade.transition(puzzle, state, candidate)
        self.assertEqual(benign_neutral, benign_proxy)
        self.assertEqual(benign_neutral, benign_forge)
        self.assertEqual(benign_neutral, benign_facade)
        self.assertEqual(
            facade.legal_transitions(puzzle, MappingProxyType(state)),
            forge.legal_transitions(state),
        )

    def test_action_parameter_keys_are_typed_before_set_comparisons(self) -> None:
        puzzle = _load(PUZZLE_PATH)
        interpreter = GamepackInterpreter(puzzle)
        state = interpreter.initial_state()

        class HostileParameterKey:
            def __init__(self) -> None:
                self.hash_calls = 0
                self.equality_calls = 0

            def __hash__(self) -> int:
                self.hash_calls += 1
                return hash("first_index")

            def __eq__(self, _other: object) -> bool:
                self.equality_calls += 1
                raise AssertionError("hostile parameter equality must not run")

        key = HostileParameterKey()
        parameters: dict[object, object] = {key: 0, "second_index": 1}
        baseline = (key.hash_calls, key.equality_calls)
        for candidate_parameters in (parameters, MappingProxyType(parameters)):
            candidate = CandidateAction(
                "swap_tiles",
                candidate_parameters,  # type: ignore[arg-type]
            )
            for transition_call in (
                interpreter.transition,
                interpreter.transition_legacy,
            ):
                with self.subTest(
                    parameters=type(candidate_parameters).__name__,
                    transition=transition_call.__name__,
                ):
                    with self.assertRaises(GameLogicError):
                        transition_call(state, candidate)
        self.assertEqual((key.hash_calls, key.equality_calls), baseline)

    def test_loader_source_is_exact_scalar_bounded_and_unicode_safe(self) -> None:
        import gamepack_runtime.runtime_io as runtime_io

        payload = PUZZLE_PATH.read_bytes()
        maximum = runtime_io.MAX_SOURCE_LABEL_CODEPOINTS
        self.assertEqual(
            load_gamepack_bytes(payload, source="s" * maximum)["content_hash"],
            PUZZLE_GAMEPACK_HASH,
        )
        self.assertEqual(
            load_gamepack_bytes(payload, source="é" * maximum)["content_hash"],
            PUZZLE_GAMEPACK_HASH,
        )

        invalid_sources: tuple[object, ...] = (
            "",
            "s" * (maximum + 1),
            "\ud800",
            "line\nbreak",
            "e\u0301",
            ["not", "a", "string"],
        )
        for source in invalid_sources:
            with self.subTest(source_type=type(source).__name__, source_length=len(source)):
                with self.assertRaises(GameLogicError) as caught:
                    load_gamepack_bytes(payload, source=source)  # type: ignore[arg-type]
                self.assertEqual(caught.exception.reason_code, "json_source_invalid")

        oversized = "x" * (MAX_GAMEPACK_BYTES + 1)
        with patch.object(
            runtime_io.unicodedata,
            "normalize",
            side_effect=AssertionError("normalization must follow the O(1) bound"),
        ):
            with self.assertRaises(GameLogicError) as caught:
                load_gamepack_bytes(payload, source=oversized)
        self.assertEqual(caught.exception.reason_code, "json_source_invalid")

    def test_terminal_uses_the_exact_bounded_ending_id_domain(self) -> None:
        from gamepack_runtime.contracts import MAX_TERMINAL_ENDING_IDS

        self.assertFalse(StateClassification((), (), None, (), ()).terminal)
        self.assertTrue(StateClassification((), ("ending_a",), "success", (), ()).terminal)
        maximum_ids = tuple(f"ending_{index:02d}" for index in range(MAX_TERMINAL_ENDING_IDS))
        self.assertTrue(StateClassification((), maximum_ids, "success", (), ()).terminal)

        malformed = (
            ("ending_a", "ending_a"),
            ("\ud800",),
            ("x" * 65,),
        )
        for ending_ids in malformed:
            with self.subTest(ending_ids=ending_ids):
                with self.assertRaises(GameLogicError) as caught:
                    _ = StateClassification((), ending_ids, None, (), ()).terminal
                self.assertEqual(caught.exception.reason_code, "classification_invalid")

        oversized = tuple(f"ending_{index:03d}" for index in range(MAX_TERMINAL_ENDING_IDS + 1))
        import gamepack_runtime.contracts as contracts

        with patch.object(
            contracts.unicodedata,
            "normalize",
            side_effect=AssertionError("normalization must follow the O(1) count bound"),
        ):
            with self.assertRaises(GameLogicError) as caught:
                _ = StateClassification((), oversized, None, (), ()).terminal
        self.assertEqual(caught.exception.reason_code, "classification_invalid")

    def test_public_string_snapshots_reject_lower_bound_before_scanning(self) -> None:
        import gamepack_runtime.contracts as contracts

        oversized = "x" * 1_000_000
        real_string_size = contracts._json_string_size

        def reject_oversized_scan(value: str, *args: object, **kwargs: object) -> int:
            if len(value) == len(oversized):
                raise AssertionError("oversized string scanner was reached")
            return real_string_size(value, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(
            contracts,
            "_json_string_size",
            side_effect=reject_oversized_scan,
        ):
            with self.assertRaises(GameLogicError) as caught:
                snapshot_plain_json({"value": oversized}, maximum_bytes=65_536)
        self.assertEqual(caught.exception.reason_code, "gamepack_bytes_exceeded")

        with patch.object(
            contracts.json,
            "dumps",
            side_effect=AssertionError("encoding must follow scalar validation"),
        ):
            with self.assertRaises(GameLogicError) as caught:
                canonical_state_hash({"value": "\ud800"})
        self.assertEqual(caught.exception.reason_code, "json_unicode_invalid")

    def test_trace_events_preflight_precedes_other_snapshots_and_hashes(self) -> None:
        import gamepack_runtime.semantics_v1 as semantics

        puzzle = _load(PUZZLE_PATH)
        interpreter = GamepackInterpreter(puzzle)
        accepted = interpreter.transition(
            interpreter.initial_state(),
            CandidateAction("restart_board", {}),
        )
        malformed = TransitionResult(
            True,
            accepted.action,
            accepted.pre_state,
            accepted.post_state,
            accepted.pre_state_hash,
            accepted.post_state_hash,
            ("",),
            None,
        )
        with (
            patch.object(
                semantics,
                "snapshot_strict_candidate",
                side_effect=AssertionError("action snapshot reached after invalid events"),
            ),
            patch.object(
                semantics,
                "snapshot_strict_state",
                side_effect=AssertionError("state snapshot reached after invalid events"),
            ),
            patch.object(
                semantics,
                "canonical_state_hash",
                side_effect=AssertionError("state hash reached after invalid events"),
            ),
            patch.object(
                semantics,
                "canonical_events_hash",
                side_effect=AssertionError("event hash reached after invalid events"),
            ),
        ):
            with self.assertRaises(GameLogicError) as caught:
                canonical_trace_step(malformed)
        self.assertEqual(caught.exception.reason_code, "trace_step_invalid")

        original_events = accepted.events
        hashed_event_inputs: list[tuple[str, ...] | list[str]] = []
        real_events_hash = semantics.canonical_events_hash

        def record_events_hash(events: tuple[str, ...] | list[str]) -> str:
            hashed_event_inputs.append(events)
            return real_events_hash(events)

        with patch.object(semantics, "canonical_events_hash", record_events_hash):
            trace = canonical_trace_step(accepted)
        self.assertEqual(trace["events"], list(original_events))
        self.assertEqual(len(hashed_event_inputs), 1)
        self.assertIsNot(hashed_event_inputs[0], original_events)

    def test_legacy_facade_bounds_cycles_before_any_rejected_result_copy(self) -> None:
        import worldforge.game_logic as facade

        puzzle = _load(PUZZLE_PATH)
        interpreter = facade._Interpreter(puzzle)
        state = interpreter.initial_state()
        cycle: dict[str, object] = {}
        cycle["cycle"] = cycle
        with self.assertRaises(GameLogicError) as caught:
            interpreter.transition(
                state,
                facade.CandidateAction(
                    "swap_tiles",
                    {"first_index": cycle, "second_index": 1},  # type: ignore[dict-item]
                ),
            )
        self.assertEqual(caught.exception.reason_code, "json_cycle")


class ForgeCompatibilityFacadeTests(unittest.TestCase):
    def test_worldforge_facade_exports_neutral_shapes_and_exact_results(self) -> None:
        import worldforge.game_logic as facade

        puzzle = _load(PUZZLE_PATH)
        self.assertIs(facade.CandidateAction, CandidateAction)
        self.assertIs(facade.GameLogicError, GameLogicError)
        self.assertEqual(facade.EXECUTION_SEMANTICS, EXECUTION_SEMANTICS)
        self.assertEqual(facade.ANALYSIS_LIMITS, ANALYSIS_LIMITS)
        state = facade.initial_state(puzzle)
        result = facade.transition(
            puzzle,
            state,
            facade.CandidateAction(
                "swap_tiles",
                {"first_index": 0, "second_index": 1},
            ),
        )
        self.assertEqual(result.post_state_hash, PUZZLE_FINAL_HASH)
        self.assertEqual(result.events, ("tile_swapped",))

    def test_legacy_facade_preserves_pre_extraction_malformed_input_precedence(
        self,
    ) -> None:
        import worldforge.game_logic as facade

        puzzle = _load(PUZZLE_PATH)
        interpreter = facade._Interpreter(puzzle)
        state = interpreter.initial_state()
        cases = (
            (
                "empty_action",
                state,
                facade.CandidateAction("", {}),
                ("result", False, "action_unknown", "", {}),
            ),
            (
                "float_parameter",
                state,
                facade.CandidateAction(
                    "swap_tiles",
                    {"first_index": 0.0, "second_index": 1},
                ),
                (
                    "result",
                    False,
                    "action_parameters_invalid",
                    "swap_tiles",
                    {"first_index": 0.0, "second_index": 1},
                ),
            ),
            (
                "mapping_parameter",
                state,
                facade.CandidateAction(
                    "swap_tiles",
                    {"first_index": {}, "second_index": 1},  # type: ignore[dict-item]
                ),
                (
                    "result",
                    False,
                    "action_parameters_invalid",
                    "swap_tiles",
                    {"first_index": {}, "second_index": 1},
                ),
            ),
            (
                "mapping_proxy_parameters",
                state,
                facade.CandidateAction(
                    "swap_tiles",
                    MappingProxyType({"first_index": 0, "second_index": 1}),  # type: ignore[arg-type]
                ),
                (
                    "result",
                    True,
                    None,
                    "swap_tiles",
                    {"first_index": 0, "second_index": 1},
                ),
            ),
            (
                "surrogate_action",
                state,
                facade.CandidateAction("\ud800", {}),
                ("result", False, "action_unknown", "\ud800", {}),
            ),
        )
        for label, candidate_state, candidate, expected in cases:
            with self.subTest(label=label):
                result = interpreter.transition(candidate_state, candidate)
                actual = (
                    "result",
                    result.accepted,
                    result.rejection_reason,
                    result.action.action_id,
                    result.action.parameters,
                )
                self.assertEqual(expected, actual)
                self.assertEqual(result.pre_state_hash, PUZZLE_INITIAL_HASH)
                self.assertEqual(
                    result.post_state_hash,
                    PUZZLE_FINAL_HASH if result.accepted else PUZZLE_INITIAL_HASH,
                )

        for label, candidate in (
            ("invalid_state_and_empty_action", facade.CandidateAction("", {})),
            (
                "invalid_state_and_float_parameter",
                facade.CandidateAction(
                    "swap_tiles",
                    {"first_index": 0.0, "second_index": 1},
                ),
            ),
        ):
            with self.subTest(label=label):
                with self.assertRaises(GameLogicError) as caught:
                    interpreter.transition({"bad": object()}, candidate)  # type: ignore[dict-item]
                self.assertEqual(caught.exception.reason_code, "state_domain_invalid")
                self.assertEqual(
                    str(caught.exception),
                    "state_domain_invalid: state keys do not exactly match schema",
                )

    def test_strict_neutral_action_policy_is_explicitly_not_the_legacy_facade(
        self,
    ) -> None:
        puzzle = _load(PUZZLE_PATH)
        interpreter = GamepackInterpreter(puzzle)
        state = interpreter.initial_state()
        cases = (
            (CandidateAction("", {}), "action_invalid"),
            (
                CandidateAction(
                    "swap_tiles",
                    {"first_index": 0.0, "second_index": 1},  # type: ignore[dict-item]
                ),
                "json_float_unsupported",
            ),
            (CandidateAction("\ud800", {}), "json_unicode_invalid"),
        )
        for candidate, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                with self.assertRaises(GameLogicError) as caught:
                    interpreter.transition(state, candidate)
                self.assertEqual(caught.exception.reason_code, expected_reason)

    def test_kernel_imports_no_forge_legacy_provider_network_clock_or_platform_services(
        self,
    ) -> None:
        package_root = ROOT / "src" / "gamepack_runtime"
        forbidden_roots = {
            "worldforge",
            "isoworld",
            "requests",
            "urllib",
            "http",
            "socket",
            "subprocess",
            "time",
            "datetime",
            "platform",
        }
        imported: set[str] = set()
        for path in sorted(package_root.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".", 1)[0])
        self.assertEqual(imported & forbidden_roots, set())

        code = (
            "import json,sys; import gamepack_runtime; "
            "print(json.dumps(sorted(name for name in sys.modules "
            "if name == 'worldforge' or name.startswith('worldforge.') "
            "or name == 'isoworld' or name.startswith('isoworld.'))))"
        )
        completed = subprocess.run(
            [sys.executable, "-I", "-c", code],
            cwd=ROOT,
            env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
            check=False,
            capture_output=True,
            text=True,
        )
        # Isolated mode cannot discover the source tree. Repeat with only an
        # explicit, process-local source insertion rather than PYTHONPATH.
        if completed.returncode != 0:
            code = f"import sys;sys.path.insert(0,{os.fspath(ROOT / 'src')!r});" + code
            completed = subprocess.run(
                [sys.executable, "-I", "-c", code],
                cwd=ROOT,
                env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "[]")

    def test_game_analysis_imports_the_same_neutral_kernel_path(self) -> None:
        import gamepack_runtime.semantics_v1 as neutral_semantics
        import worldforge.game_analysis as analysis
        import worldforge.game_logic as facade

        self.assertTrue(issubclass(facade._Interpreter, neutral_semantics.GamepackInterpreter))
        self.assertIs(analysis._Interpreter, facade._Interpreter)

    def test_neutral_and_forge_facade_results_match_for_every_initial_candidate(self) -> None:
        import worldforge.game_logic as facade

        for path in (PUZZLE_PATH, NARRATIVE_PATH):
            with self.subTest(path=path.name):
                gamepack = _load(path)
                neutral = GamepackInterpreter(gamepack)
                forge = facade._Interpreter(gamepack)
                neutral_state = neutral.initial_state()
                forge_state = forge.initial_state()
                self.assertEqual(neutral_state, forge_state)
                self.assertEqual(
                    neutral.enumerate_candidates(),
                    forge.enumerate_candidates(),
                )
                for candidate in neutral.enumerate_candidates():
                    neutral_result = neutral.transition(neutral_state, candidate)
                    forge_result = forge.transition(forge_state, candidate)
                    self.assertEqual(neutral_result, forge_result)


class PackagingDiscoveryTests(unittest.TestCase):
    def test_setuptools_package_discovery_includes_neutral_runtime(self) -> None:
        package_root = ROOT / "src" / "gamepack_runtime"
        package_files = sorted(
            path.relative_to(package_root).as_posix() for path in package_root.rglob("*.py")
        )
        self.assertEqual(
            package_files,
            [
                "__init__.py",
                "contracts.py",
                "distribution.py",
                "distribution_names.py",
                "file_stat.py",
                "game_package.py",
                "headless.py",
                "persistence.py",
                "persistence_generation.py",
                "persistence_io.py",
                "runtime_io.py",
                "semantics_v1.py",
                "session.py",
            ],
        )

    def test_setuptools_package_discovery_includes_bounded_raylib_2d_adapters(
        self,
    ) -> None:
        package_files = sorted(
            path.name for path in (ROOT / "src" / "gamepack_raylib_2d").glob("*.py")
        )
        self.assertEqual(
            package_files,
            [
                "__init__.py",
                "app.py",
                "audit.py",
                "backend.py",
                "descriptor_policy.py",
                "executable_shape.py",
                "fixed_step.py",
                "input.py",
                "narrative_text.py",
                "native_smoke.py",
                "puzzle.py",
                "registry.py",
                "resources.py",
                "types.py",
            ],
        )


if __name__ == "__main__":
    unittest.main()
