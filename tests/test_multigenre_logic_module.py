from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worldforge import creation_contracts as contracts
from worldforge.creation_contracts import (
    LOGIC_MODULE_FORMAT,
    CreationContractError,
    canonical_creation_hash,
    load_creation_project,
    validate_creation_document,
    validate_creation_documents,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "multigenre-contracts"
PUZZLE = FIXTURES / "abstract-puzzle" / "project.json"
NARRATIVE = FIXTURES / "branching-narrative" / "project.json"
RUNTIME_STRING_CORPUS = ROOT / "tests" / "fixtures" / "logic-runtime-string-corpus.json"


def _reseal(document: dict[str, object]) -> dict[str, object]:
    value = copy.deepcopy(document)
    value["content_hash"] = canonical_creation_hash(value)
    return value


def _rebind_logic(
    loaded: object,
    logic: dict[str, object],
    *,
    profile: dict[str, object] | None = None,
    activity_modules: tuple[dict[str, object], ...] | None = None,
    narrative_modules: tuple[dict[str, object], ...] | None = None,
    system_modules: tuple[dict[str, object], ...] | None = None,
) -> object:
    checked_profile = copy.deepcopy(profile or loaded.profile)
    checked_activities = tuple(
        copy.deepcopy(item)
        for item in (loaded.activity_modules if activity_modules is None else activity_modules)
    )
    checked_narrative = tuple(
        copy.deepcopy(item)
        for item in (loaded.narrative_modules if narrative_modules is None else narrative_modules)
    )
    checked_systems = tuple(
        copy.deepcopy(item)
        for item in (loaded.system_modules if system_modules is None else system_modules)
    )
    checked_logic = _reseal(logic)
    manifest = copy.deepcopy(loaded.manifest)
    manifest["modules"]["logic_modules"][0]["content_hash"] = checked_logic["content_hash"]
    if profile is not None:
        checked_profile = _reseal(checked_profile)
        manifest["profile"]["content_hash"] = checked_profile["content_hash"]
    if activity_modules is not None:
        for reference, module in zip(
            manifest["modules"]["activity_modules"],
            checked_activities,
            strict=True,
        ):
            reference["content_hash"] = module["content_hash"]
    if narrative_modules is not None:
        for reference, module in zip(
            manifest["modules"]["narrative_modules"],
            checked_narrative,
            strict=True,
        ):
            reference["content_hash"] = module["content_hash"]
    if system_modules is not None:
        for reference, module in zip(
            manifest["modules"]["system_modules"],
            checked_systems,
            strict=True,
        ):
            reference["content_hash"] = module["content_hash"]
    manifest = _reseal(manifest)
    project = copy.deepcopy(loaded.project)
    if profile is not None:
        project["profile"]["content_hash"] = checked_profile["content_hash"]
    project["source_manifest"]["content_hash"] = manifest["content_hash"]
    project = _reseal(project)
    return validate_creation_documents(
        project,
        checked_profile,
        manifest,
        loaded.world_modules,
        checked_activities,
        checked_narrative,
        checked_systems,
        (checked_logic,),
    )


class LogicModuleContractTests(unittest.TestCase):
    def test_neutral_puzzle_logic_loads_as_finite_declarative_state(self) -> None:
        loaded = load_creation_project(PUZZLE)

        self.assertEqual(1, len(loaded.logic_modules))
        logic = loaded.logic_modules[0]
        self.assertEqual(LOGIC_MODULE_FORMAT, logic["format"])
        self.assertEqual(
            {"board", "move_count", "target"},
            {item["id"] for item in logic["state_variables"]},
        )
        self.assertEqual(
            {"restart", "swap"},
            {item["core_verb_id"] for item in logic["actions"]},
        )
        self.assertEqual({"solved"}, {item["id"] for item in logic["goals"]})
        self.assertEqual({"puzzle_complete"}, {item["id"] for item in logic["endings"]})
        self.assertNotIn("actors", json.dumps(logic))
        self.assertNotIn(
            "quest",
            {
                binding["kind"]
                for action in logic["actions"]
                for binding in action["source_bindings"]
            },
        )

    def test_branching_logic_maps_exact_options_and_two_authored_endings(self) -> None:
        loaded = load_creation_project(NARRATIVE)

        logic = loaded.logic_modules[0]
        option_bindings = {
            (binding["source_id"], binding["option_id"])
            for action in logic["actions"]
            for binding in action["source_bindings"]
            if binding["kind"] == "narrative_option"
        }
        self.assertEqual(
            {
                ("central_choice", "choose_left"),
                ("central_choice", "choose_right"),
            },
            option_bindings,
        )
        self.assertEqual(
            {"ending_left", "ending_right"},
            {ending["id"] for ending in logic["endings"]},
        )
        self.assertEqual(
            {"logic:branching_choice", "logic:persistent_variables"},
            {
                feature
                for mechanic in logic["mechanics"]
                for feature in mechanic["required_feature_ids"]
            },
        )

    def test_effect_domains_and_reject_transition_policy_close_state_boundaries(self) -> None:
        puzzle = load_creation_project(PUZZLE)
        narrative = load_creation_project(NARRATIVE)
        self.assertEqual(
            {"reject_transition"},
            {
                effect["invalid_transition_policy"]
                for logic in (puzzle.logic_modules[0], narrative.logic_modules[0])
                for effect in logic["effects"]
            },
        )

        with self.subTest("impossible state array domain"):
            logic = copy.deepcopy(narrative.logic_modules[0])
            knowledge = next(item for item in logic["state_variables"] if item["id"] == "knowledge")
            knowledge["min_items"] = 3
            knowledge["max_items"] = 3
            with self.assertRaisesRegex(CreationContractError, "possible unique|array domain"):
                validate_creation_document(_reseal(logic))

        with self.subTest("impossible parameter array domain"):
            logic = copy.deepcopy(puzzle.logic_modules[0])
            restart = next(item for item in logic["actions"] if item["id"] == "restart_board")
            restart["parameters"] = [
                {
                    "allowed_values": ["left", "right"],
                    "id": "selection",
                    "max_items": 3,
                    "min_items": 3,
                    "type": "string_array",
                }
            ]
            with self.assertRaisesRegex(CreationContractError, "possible unique|array domain"):
                validate_creation_document(_reseal(logic))

        with self.subTest("literal set outside allowed values"):
            logic = copy.deepcopy(narrative.logic_modules[0])
            effect = next(item for item in logic["effects"] if item["id"] == "remember_left")
            effect["value"]["value"] = "outside"
            with self.assertRaisesRegex(CreationContractError, "domain|allowed"):
                validate_creation_document(_reseal(logic))

        with self.subTest("parameter set domain is broader than state"):
            logic = copy.deepcopy(narrative.logic_modules[0])
            action = next(item for item in logic["actions"] if item["id"] == "choose_left")
            action["parameters"] = [
                {
                    "allowed_values": ["left", "outside"],
                    "id": "choice",
                    "type": "string",
                }
            ]
            effect = next(item for item in logic["effects"] if item["id"] == "remember_left")
            effect["value"] = {
                "action_id": "choose_left",
                "kind": "parameter",
                "parameter_id": "choice",
            }
            with self.assertRaisesRegex(CreationContractError, "domain|subset|allowed"):
                validate_creation_document(_reseal(logic))

        with self.subTest("state set domain is broader than target"):
            logic = copy.deepcopy(narrative.logic_modules[0])
            logic["state_variables"].append(
                {
                    "allowed_values": ["left", "outside"],
                    "id": "source_choice",
                    "initial": "left",
                    "mutability": "constant",
                    "persistence": "transient",
                    "type": "string",
                }
            )
            logic["state_variables"].sort(key=lambda item: item["id"].encode("utf-8"))
            effect = next(item for item in logic["effects"] if item["id"] == "remember_left")
            effect["value"] = {"kind": "state", "state_id": "source_choice"}
            with self.assertRaisesRegex(CreationContractError, "domain|subset|allowed"):
                validate_creation_document(_reseal(logic))

        with self.subTest("array set contains a disallowed value"):
            logic = copy.deepcopy(puzzle.logic_modules[0])
            effect = next(item for item in logic["effects"] if item["id"] == "reset_board")
            effect.clear()
            effect.update(
                {
                    "action_id": "restart_board",
                    "id": "reset_board",
                    "invalid_transition_policy": "reject_transition",
                    "operation": "set",
                    "state_id": "board",
                    "value": {
                        "kind": "literal",
                        "value": ["A", "B", "outside"],
                        "value_type": "string_array",
                    },
                }
            )
            with self.assertRaisesRegex(CreationContractError, "domain|allowed"):
                validate_creation_document(_reseal(logic))

        with self.subTest("append value outside allowed values"):
            logic = copy.deepcopy(narrative.logic_modules[0])
            effect = next(item for item in logic["effects"] if item["id"] == "record_left")
            effect["value"]["value"] = "outside"
            with self.assertRaisesRegex(CreationContractError, "domain|allowed"):
                validate_creation_document(_reseal(logic))

        with self.subTest("append parameter domain is broader than state"):
            logic = copy.deepcopy(narrative.logic_modules[0])
            action = next(item for item in logic["actions"] if item["id"] == "choose_left")
            action["parameters"] = [
                {
                    "allowed_values": ["learned_left", "outside"],
                    "id": "knowledge_value",
                    "type": "string",
                }
            ]
            effect = next(item for item in logic["effects"] if item["id"] == "record_left")
            effect["value"] = {
                "action_id": "choose_left",
                "kind": "parameter",
                "parameter_id": "knowledge_value",
            }
            with self.assertRaisesRegex(CreationContractError, "domain|subset|allowed"):
                validate_creation_document(_reseal(logic))

        for name, amount in (("minimum overflow", -1), ("maximum overflow", 1)):
            with self.subTest(name):
                logic = copy.deepcopy(puzzle.logic_modules[0])
                move_count = next(
                    item for item in logic["state_variables"] if item["id"] == "move_count"
                )
                move_count["minimum"] = 0
                move_count["maximum"] = 0
                increment = next(
                    item for item in logic["effects"] if item["id"] == "increment_moves"
                )
                increment["amount"]["value"] = amount
                validate_creation_document(_reseal(logic))

        with self.subTest("append at capacity is a rejected transition"):
            logic = copy.deepcopy(narrative.logic_modules[0])
            knowledge = next(item for item in logic["state_variables"] if item["id"] == "knowledge")
            knowledge["max_items"] = 0
            validate_creation_document(_reseal(logic))

        for name, mutate in (
            (
                "missing boundary policy",
                lambda value: value["effects"][0].pop("invalid_transition_policy"),
            ),
            (
                "unsafe boundary policy",
                lambda value: value["effects"][0].update(
                    {"invalid_transition_policy": "allow_out_of_bounds"}
                ),
            ),
        ):
            with self.subTest(name):
                logic = copy.deepcopy(puzzle.logic_modules[0])
                mutate(logic)
                with self.assertRaisesRegex(
                    CreationContractError,
                    "invalid_transition_policy|missing fields|unknown fields",
                ):
                    validate_creation_document(_reseal(logic))

        reset = next(
            item for item in puzzle.logic_modules[0]["effects"] if item["operation"] == "reset"
        )
        self.assertEqual("reject_transition", reset["invalid_transition_policy"])

    def test_library_projects_cannot_contain_executable_logic(self) -> None:
        loaded = load_creation_project(PUZZLE)
        project = _reseal({**loaded.project, "project_kind": "asset_library"})

        with self.assertRaisesRegex(CreationContractError, "library.*logic"):
            validate_creation_documents(
                project,
                loaded.profile,
                loaded.manifest,
                loaded.world_modules,
                loaded.activity_modules,
                loaded.narrative_modules,
                loaded.system_modules,
                loaded.logic_modules,
            )

    def test_logic_discriminators_and_types_fail_closed(self) -> None:
        loaded = load_creation_project(PUZZLE)
        original = loaded.logic_modules[0]
        mutations = (
            (
                "integer comparison",
                lambda value: value["conditions"][0].update(
                    {"left": {"kind": "state", "state_id": "board"}}
                ),
                "integer|type",
            ),
            (
                "array swap",
                lambda value: value["effects"][0].update({"array_state_id": "move_count"}),
                "string_array|array",
            ),
            (
                "increment",
                lambda value: next(
                    item for item in value["effects"] if item["id"] == "increment_moves"
                ).update({"state_id": "board"}),
                "integer",
            ),
            (
                "parameter scope",
                lambda value: value["conditions"][0]["left"].update(
                    {"action_id": "restart", "parameter_id": "first_index"}
                ),
                "parameter|scope|action",
            ),
            (
                "mixed operand",
                lambda value: value["conditions"][0]["left"].update({"value": 0}),
                "unknown fields|operand",
            ),
        )
        for name, mutate, expected in mutations:
            with self.subTest(name=name):
                logic = copy.deepcopy(original)
                mutate(logic)
                with self.assertRaisesRegex(CreationContractError, expected):
                    validate_creation_document(_reseal(logic))

    def test_conditions_rules_and_execution_order_are_acyclic_and_unambiguous(self) -> None:
        loaded = load_creation_project(PUZZLE)
        original = loaded.logic_modules[0]

        with self.subTest("condition cycle"):
            logic = copy.deepcopy(original)
            logic["conditions"].extend(
                [
                    {
                        "action_id": "swap_tiles",
                        "condition_ids": ["cycle_two"],
                        "id": "cycle_one",
                        "operator": "all",
                    },
                    {
                        "action_id": "swap_tiles",
                        "condition_ids": ["cycle_one"],
                        "id": "cycle_two",
                        "operator": "all",
                    },
                ]
            )
            logic["conditions"].sort(key=lambda item: item["id"].encode("utf-8"))
            with self.assertRaisesRegex(CreationContractError, "condition cycle"):
                validate_creation_document(_reseal(logic))

        with self.subTest("ambiguous rule order"):
            logic = copy.deepcopy(original)
            logic["rules"][1]["order"] = logic["rules"][0]["order"]
            with self.assertRaisesRegex(CreationContractError, "rule order"):
                validate_creation_document(_reseal(logic))

        with self.subTest("cross-action rule"):
            logic = copy.deepcopy(original)
            logic["actions"][0]["rule_ids"] = [logic["rules"][1]["id"]]
            with self.assertRaisesRegex(CreationContractError, "rule.*action|action.*rule"):
                validate_creation_document(_reseal(logic))

        with self.subTest("semantic priorities are independent from lexical IDs"):
            logic = copy.deepcopy(original)
            replacements = {
                "restart_rule": "z_restart_priority",
                "swap_rule": "a_swap_followup",
            }
            for rule in logic["rules"]:
                rule["id"] = replacements[rule["id"]]
            for action in logic["actions"]:
                action["rule_ids"] = [replacements[item] for item in action["rule_ids"]]
            for mechanic in logic["mechanics"]:
                mechanic["rule_ids"] = [replacements[item] for item in mechanic["rule_ids"]]
            validate_creation_document(_reseal(logic))

            logic["rules"].reverse()
            with self.assertRaisesRegex(CreationContractError, "semantic rule order"):
                validate_creation_document(_reseal(logic))

    def test_mechanic_ledgers_equal_the_exact_transitive_action_closure(self) -> None:
        puzzle = load_creation_project(PUZZLE)
        logic = puzzle.logic_modules[0]
        swap = next(item for item in logic["mechanics"] if item["action_id"] == "swap_tiles")
        self.assertEqual(["board", "move_count"], swap["authoritative_state_ids"])
        self.assertEqual(
            ["adjacent_tile", "first_index_valid", "second_index_valid"],
            swap["condition_ids"],
        )
        self.assertEqual(["increment_moves", "swap_tiles"], swap["effect_ids"])
        self.assertEqual(["tile_swapped"], swap["event_ids"])

        for field, remove in (
            ("authoritative_state_ids", "move_count"),
            ("condition_ids", "second_index_valid"),
            ("effect_ids", "increment_moves"),
            ("presentation_hook_ids", "board_view"),
            ("required_feature_ids", "logic:deterministic_actions"),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(logic)
                mechanic = next(
                    item for item in changed["mechanics"] if item["action_id"] == "swap_tiles"
                )
                mechanic[field].remove(remove)
                with self.assertRaisesRegex(CreationContractError, "exact action closure"):
                    validate_creation_document(_reseal(changed))

        with self.subTest("extra unrelated state"):
            changed = copy.deepcopy(logic)
            mechanic = next(
                item for item in changed["mechanics"] if item["action_id"] == "swap_tiles"
            )
            mechanic["authoritative_state_ids"].append("target")
            mechanic["authoritative_state_ids"].sort(key=lambda item: item.encode("utf-8"))
            with self.assertRaisesRegex(CreationContractError, "exact action closure"):
                validate_creation_document(_reseal(changed))

        with self.subTest("extra unrelated event"):
            changed = copy.deepcopy(logic)
            mechanic = next(
                item for item in changed["mechanics"] if item["action_id"] == "swap_tiles"
            )
            mechanic["event_ids"].append("restart_requested")
            mechanic["event_ids"].sort(key=lambda item: item.encode("utf-8"))
            with self.assertRaisesRegex(CreationContractError, "exact action closure"):
                validate_creation_document(_reseal(changed))

        with self.subTest("extra unrelated asset"):
            changed = copy.deepcopy(logic)
            ending_hook = next(
                item for item in changed["presentation_hooks"] if item["id"] == "ending_feedback"
            )
            ending_hook["asset_binding_ids"] = ["board_texture", "ending_texture"]
            mechanic = next(
                item for item in changed["mechanics"] if item["action_id"] == "swap_tiles"
            )
            mechanic["asset_binding_ids"] = ["board_texture", "ending_texture"]
            with self.assertRaisesRegex(CreationContractError, "exact action closure"):
                validate_creation_document(_reseal(changed))

        with self.subTest("orphan rule"):
            changed = copy.deepcopy(logic)
            changed["rules"].append(
                {
                    "action_id": "swap_tiles",
                    "condition_ids": ["adjacent_tile"],
                    "effect_ids": ["swap_tiles"],
                    "event_ids": ["tile_swapped"],
                    "id": "unlisted_swap_rule",
                    "order": 2,
                }
            )
            with self.assertRaisesRegex(CreationContractError, "exact rule closure|orphan rule"):
                validate_creation_document(_reseal(changed))

        with self.subTest("multiple mechanics for one action"):
            changed = copy.deepcopy(logic)
            duplicate = copy.deepcopy(swap)
            duplicate["id"] = "swap_tiles_duplicate"
            changed["mechanics"].append(duplicate)
            changed["mechanics"].sort(key=lambda item: item["id"].encode("utf-8"))
            with self.assertRaisesRegex(CreationContractError, "exactly one mechanic"):
                validate_creation_document(_reseal(changed))

    def test_every_executable_logic_definition_has_inverse_liveness(self) -> None:
        loaded = load_creation_project(PUZZLE)
        original = loaded.logic_modules[0]

        def add_state(value: dict[str, object]) -> None:
            value["state_variables"].append(
                {
                    "id": "unused_state",
                    "initial": False,
                    "mutability": "constant",
                    "persistence": "transient",
                    "type": "boolean",
                }
            )
            value["state_variables"].sort(key=lambda item: item["id"].encode("utf-8"))

        def add_condition(value: dict[str, object]) -> None:
            value["conditions"].append(
                {
                    "action_id": None,
                    "id": "unused_condition",
                    "operator": "constant",
                    "value": True,
                }
            )
            value["conditions"].sort(key=lambda item: item["id"].encode("utf-8"))

        def add_effect(value: dict[str, object]) -> None:
            value["effects"].append(
                {
                    "action_id": "restart_board",
                    "id": "unused_effect",
                    "invalid_transition_policy": "reject_transition",
                    "operation": "reset",
                    "state_id": "board",
                }
            )
            value["effects"].sort(key=lambda item: item["id"].encode("utf-8"))

        def add_event(value: dict[str, object]) -> None:
            value["events"].append({"id": "unused_event"})
            value["events"].sort(key=lambda item: item["id"].encode("utf-8"))

        def add_hook_and_asset(value: dict[str, object]) -> None:
            value["presentation_hooks"].append(
                {
                    "asset_binding_ids": ["unused_asset"],
                    "id": "unused_hook",
                    "kind": "feedback",
                }
            )
            value["presentation_hooks"].sort(key=lambda item: item["id"].encode("utf-8"))

        def add_parameter(value: dict[str, object]) -> None:
            restart = next(item for item in value["actions"] if item["id"] == "restart_board")
            restart["parameters"].append({"id": "unused_parameter", "type": "boolean"})

        mutations = (
            ("state", add_state, "orphan state"),
            ("condition", add_condition, "orphan condition"),
            ("effect", add_effect, "orphan effect"),
            ("event", add_event, "orphan event"),
            (
                "presentation hook and asset binding",
                add_hook_and_asset,
                "orphan presentation hook|orphan asset binding",
            ),
            ("action parameter", add_parameter, "orphan action parameter"),
        )
        for name, mutate, expected in mutations:
            with self.subTest(name=name):
                logic = copy.deepcopy(original)
                mutate(logic)
                with self.assertRaisesRegex(CreationContractError, expected):
                    _rebind_logic(loaded, logic)

        with self.subTest("success ending must be owned by a goal"):
            logic = copy.deepcopy(original)
            logic["endings"].append(
                {
                    "condition_ids": ["board_solved"],
                    "event_ids": [],
                    "id": "unused_success_ending",
                    "kind": "success",
                    "presentation_hook_ids": ["ending_feedback"],
                }
            )
            logic["endings"].sort(key=lambda item: item["id"].encode("utf-8"))
            with self.assertRaisesRegex(
                CreationContractError,
                "success ending.*goal|orphan.*ending",
            ):
                _rebind_logic(loaded, logic)

        with self.subTest("source condition and constant target state remain live"):
            checked = _rebind_logic(loaded, copy.deepcopy(original))
            self.assertEqual("board_ready", checked.activities[0]["start_condition_ids"][0])
            board_solved = next(
                item
                for item in checked.logic_modules[0]["conditions"]
                if item["id"] == "board_solved"
            )
            self.assertEqual("target", board_solved["right"]["state_id"])

    def test_swap_paths_require_exact_index_and_adjacency_guards(self) -> None:
        loaded = load_creation_project(PUZZLE)
        original = loaded.logic_modules[0]

        for name, mutate, expected in (
            (
                "missing first index guard",
                lambda value: next(item for item in value["rules"] if item["id"] == "swap_rule")[
                    "condition_ids"
                ].remove("first_index_valid"),
                "first.*index_valid|index guard",
            ),
            (
                "same index guarded twice",
                lambda value: next(
                    item for item in value["conditions"] if item["id"] == "first_index_valid"
                )["index"].update({"parameter_id": "second_index"}),
                "first.*index_valid|index guard",
            ),
            (
                "missing adjacency guard",
                lambda value: next(item for item in value["rules"] if item["id"] == "swap_rule")[
                    "condition_ids"
                ].remove("adjacent_tile"),
                "adjacency|distance",
            ),
            (
                "non-adjacent distance",
                lambda value: next(
                    item for item in value["conditions"] if item["id"] == "adjacent_tile"
                ).update({"distance": 2}),
                "adjacency|distance",
            ),
        ):
            with self.subTest(name=name):
                logic = copy.deepcopy(original)
                mutate(logic)
                with self.assertRaisesRegex(CreationContractError, expected):
                    validate_creation_document(_reseal(logic))

    def test_required_feature_ids_accept_empty_and_nonempty_canonical_semantics(self) -> None:
        loaded = load_creation_project(PUZZLE)
        logic = loaded.logic_modules[0]
        action = next(item for item in logic["actions"] if item["id"] == "swap_tiles")
        mechanic = next(item for item in logic["mechanics"] if item["action_id"] == "swap_tiles")
        self.assertEqual(
            ["logic:deterministic_actions", "logic:finite_state"],
            action["required_feature_ids"],
        )
        self.assertEqual(action["required_feature_ids"], mechanic["required_feature_ids"])

        without_required_features = copy.deepcopy(logic)
        empty_action = next(
            item for item in without_required_features["actions"] if item["id"] == "swap_tiles"
        )
        empty_mechanic = next(
            item
            for item in without_required_features["mechanics"]
            if item["action_id"] == "swap_tiles"
        )
        empty_action["required_feature_ids"] = []
        empty_mechanic["required_feature_ids"] = []
        checked = validate_creation_document(_reseal(without_required_features))
        checked_action = next(item for item in checked["actions"] if item["id"] == "swap_tiles")
        checked_mechanic = next(
            item for item in checked["mechanics"] if item["action_id"] == "swap_tiles"
        )
        self.assertEqual([], checked_action["required_feature_ids"])
        self.assertEqual([], checked_mechanic["required_feature_ids"])

    def test_unmapped_verbs_orphan_actions_and_required_feature_drift_fail(self) -> None:
        loaded = load_creation_project(PUZZLE)
        original = loaded.logic_modules[0]

        with self.subTest("unmapped verb"):
            profile = copy.deepcopy(loaded.profile)
            profile["gameplay"]["core_verbs"].append(
                {"id": "inspect", "description": "Inspect the board."}
            )
            with self.assertRaisesRegex(CreationContractError, "core verb.*mapped"):
                _rebind_logic(loaded, copy.deepcopy(original), profile=profile)

        with self.subTest("orphan action"):
            logic = copy.deepcopy(original)
            logic["mechanics"] = [
                item for item in logic["mechanics"] if item["action_id"] != "restart_board"
            ]
            with self.assertRaisesRegex(CreationContractError, "action.*mechanic"):
                _rebind_logic(loaded, logic)

        with self.subTest("required feature"):
            logic = copy.deepcopy(original)
            logic["mechanics"][0]["required_feature_ids"] = ["logic:unregistered"]
            with self.assertRaisesRegex(CreationContractError, "required feature"):
                _rebind_logic(loaded, logic)

    def test_cross_document_references_and_source_bindings_resolve_exactly(self) -> None:
        puzzle = load_creation_project(PUZZLE)

        with self.subTest("activity effect"):
            activity = copy.deepcopy(puzzle.activity_modules[0])
            activity["activities"][0]["effect_ids"] = ["missing_effect"]
            activity = _reseal(activity)
            with self.assertRaisesRegex(CreationContractError, "missing_effect"):
                _rebind_logic(
                    puzzle,
                    copy.deepcopy(puzzle.logic_modules[0]),
                    activity_modules=(activity,),
                )

        with self.subTest("source binding"):
            logic = copy.deepcopy(puzzle.logic_modules[0])
            logic["actions"][0]["source_bindings"][0]["source_id"] = "missing_activity"
            with self.assertRaisesRegex(CreationContractError, "source binding"):
                _rebind_logic(puzzle, logic)

        narrative = load_creation_project(NARRATIVE)
        with self.subTest("narrative option"):
            logic = copy.deepcopy(narrative.logic_modules[0])
            logic["actions"][0]["source_bindings"][0]["option_id"] = "missing_option"
            with self.assertRaisesRegex(CreationContractError, "narrative option"):
                _rebind_logic(narrative, logic)

        with self.subTest("narrative effect"):
            narrative_module = copy.deepcopy(narrative.narrative_modules[0])
            narrative_module["units"][0]["options"][0]["effect_ids"] = ["missing_effect"]
            narrative_module = _reseal(narrative_module)
            with self.assertRaisesRegex(CreationContractError, "missing_effect"):
                _rebind_logic(
                    narrative,
                    copy.deepcopy(narrative.logic_modules[0]),
                    narrative_modules=(narrative_module,),
                )

    def test_source_bindings_match_exact_bound_source_semantics(self) -> None:
        puzzle = load_creation_project(PUZZLE)

        with self.subTest("activity aggregate effects"):
            activity = copy.deepcopy(puzzle.activity_modules[0])
            activity["activities"][0]["effect_ids"].remove("reset_board")
            activity = _reseal(activity)
            with self.assertRaisesRegex(
                CreationContractError, "activity.*exact bound action closure"
            ):
                _rebind_logic(
                    puzzle,
                    copy.deepcopy(puzzle.logic_modules[0]),
                    activity_modules=(activity,),
                )

        with self.subTest("activity aggregate events"):
            activity = copy.deepcopy(puzzle.activity_modules[0])
            activity["activities"][0]["event_ids"].remove("restart_requested")
            activity = _reseal(activity)
            with self.assertRaisesRegex(
                CreationContractError, "activity.*exact bound action closure"
            ):
                _rebind_logic(
                    puzzle,
                    copy.deepcopy(puzzle.logic_modules[0]),
                    activity_modules=(activity,),
                )

        with self.subTest("system conditions and effects"):
            system = copy.deepcopy(puzzle.system_modules[0])
            system["systems"][0]["precondition_ids"].remove("first_index_valid")
            system = _reseal(system)
            with self.assertRaisesRegex(
                CreationContractError, "system.*exact bound action closure"
            ):
                _rebind_logic(
                    puzzle,
                    copy.deepcopy(puzzle.logic_modules[0]),
                    system_modules=(system,),
                )

        narrative = load_creation_project(NARRATIVE)
        with self.subTest("narrative option cannot cite another action effect"):
            module = copy.deepcopy(narrative.narrative_modules[0])
            left = module["units"][0]["options"][0]
            left["effect_ids"] = ["record_left", "remember_right"]
            module = _reseal(module)
            with self.assertRaisesRegex(
                CreationContractError,
                "narrative option.*exact bound action closure",
            ):
                _rebind_logic(
                    narrative,
                    copy.deepcopy(narrative.logic_modules[0]),
                    narrative_modules=(module,),
                )

        for collection in ("activity", "system"):
            with self.subTest(global_collision=collection):
                if collection == "activity":
                    original = puzzle.activity_modules[0]
                    duplicate = copy.deepcopy(original)
                    duplicate["module_id"] = "second_activities"
                    duplicate = _reseal(duplicate)
                    activity_modules = (original, duplicate)
                    system_modules = puzzle.system_modules
                    manifest_collection = "activity_modules"
                    path = "activities/second.json"
                else:
                    original = puzzle.system_modules[0]
                    duplicate = copy.deepcopy(original)
                    duplicate["module_id"] = "second_systems"
                    duplicate = _reseal(duplicate)
                    activity_modules = puzzle.activity_modules
                    system_modules = (original, duplicate)
                    manifest_collection = "system_modules"
                    path = "systems/second.json"
                manifest = copy.deepcopy(puzzle.manifest)
                manifest["modules"][manifest_collection].append(
                    {
                        "content_hash": duplicate["content_hash"],
                        "format": duplicate["format"],
                        "format_version": duplicate["format_version"],
                        "id": duplicate["module_id"],
                        "path": path,
                    }
                )
                manifest["modules"][manifest_collection].sort(
                    key=lambda item: item["id"].encode("utf-8")
                )
                manifest = _reseal(manifest)
                project = copy.deepcopy(puzzle.project)
                project["source_manifest"]["content_hash"] = manifest["content_hash"]
                project = _reseal(project)
                with self.assertRaisesRegex(
                    CreationContractError,
                    f"global {collection} ID.*collision",
                ):
                    validate_creation_documents(
                        project,
                        puzzle.profile,
                        manifest,
                        puzzle.world_modules,
                        activity_modules,
                        puzzle.narrative_modules,
                        system_modules,
                        puzzle.logic_modules,
                    )

        with self.subTest("narrative unit bindings fail closed until unambiguous"):
            narrative = load_creation_project(NARRATIVE)
            module = copy.deepcopy(narrative.narrative_modules[0])
            module["entry_unit_ids"] = ["prologue"]
            module["units"].append(
                {
                    "asset_binding_ids": [],
                    "effect_ids": [],
                    "id": "prologue",
                    "next_unit_ids": ["central_choice"],
                    "prerequisite_ids": [],
                    "title": "Prologue",
                    "unit_type": "scene",
                }
            )
            module["units"].sort(key=lambda item: item["id"].encode("utf-8"))
            module = _reseal(module)
            logic = copy.deepcopy(narrative.logic_modules[0])
            choose_left = next(item for item in logic["actions"] if item["id"] == "choose_left")
            choose_left["source_bindings"] = [{"kind": "narrative_unit", "source_id": "prologue"}]
            with self.assertRaisesRegex(
                CreationContractError,
                "narrative_unit.*unsupported|unsupported.*narrative_unit",
            ):
                _rebind_logic(
                    narrative,
                    logic,
                    narrative_modules=(module,),
                )

    def test_goal_and_success_ending_conditions_must_be_identical(self) -> None:
        loaded = load_creation_project(NARRATIVE)
        logic = copy.deepcopy(loaded.logic_modules[0])
        goal = next(item for item in logic["goals"] if item["id"] == "reach_left")
        goal["condition_ids"] = ["ending_left"]
        with self.assertRaisesRegex(
            CreationContractError,
            "goal.*success ending.*identical",
        ):
            validate_creation_document(_reseal(logic))

    def test_reserved_and_runtime_authoring_content_is_rejected_without_prose_false_positive(
        self,
    ) -> None:
        loaded = load_creation_project(NARRATIVE)
        original = loaded.logic_modules[0]
        corpus = json.loads(RUNTIME_STRING_CORPUS.read_text(encoding="utf-8"))
        for prose in corpus["accepted"]:
            with self.subTest(legitimate_prose=prose):
                logic = copy.deepcopy(original)
                logic["title"] = prose
                validate_creation_document(_reseal(logic))

        for text in corpus["rejected"]:
            with self.subTest(unsafe_runtime_string=text):
                logic = copy.deepcopy(original)
                logic["title"] = text
                with self.assertRaisesRegex(
                    CreationContractError,
                    "unsafe runtime string|authoring path|metadata|provider|model|prompt",
                ):
                    validate_creation_document(_reseal(logic))

        with self.subTest("grammar is shared with JSON Schema"):
            schema = json.loads(
                (ROOT / "schemas" / "logic-module.schema.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                contracts.LOGIC_RUNTIME_STRING_PATTERN,
                schema["$defs"]["runtimeString"]["pattern"],
            )

        for name, mutate in (
            (
                "state allowed value",
                lambda value: next(
                    item for item in value["state_variables"] if item["id"] == "choice_state"
                )["allowed_values"].append("assets/My File.png"),
            ),
            (
                "literal operand",
                lambda value: next(
                    item for item in value["effects"] if item["id"] == "remember_left"
                )["value"].update({"value": "provider_id=openai"}),
            ),
        ):
            with self.subTest(runtime_string_position=name):
                changed = copy.deepcopy(original)
                mutate(changed)
                if name == "state allowed value":
                    next(
                        item for item in changed["state_variables"] if item["id"] == "choice_state"
                    )["allowed_values"].sort(key=lambda item: item.encode("utf-8"))
                with self.assertRaisesRegex(
                    CreationContractError,
                    "unsafe runtime string",
                ):
                    validate_creation_document(_reseal(changed))

        for name, mutate, expected in (
            (
                "reserved id",
                lambda value: value["events"][0].update({"id": "wf_internal_transition"}),
                "reserved",
            ),
            (
                "runtime AI field",
                lambda value: value.update({"runtime_ai": True}),
                "unknown fields|runtime",
            ),
            (
                "nested provider credentials",
                lambda value: value["events"][0].update(
                    {"provider_credentials": {"token": "secret"}}
                ),
                "provider_credentials|unsafe runtime",
            ),
        ):
            with self.subTest(name=name):
                changed = copy.deepcopy(original)
                mutate(changed)
                with self.assertRaisesRegex(CreationContractError, expected):
                    validate_creation_document(_reseal(changed))

    def test_unknown_required_extensions_fail_closed_for_logic(self) -> None:
        loaded = load_creation_project(PUZZLE)
        logic = copy.deepcopy(loaded.logic_modules[0])
        logic["extensions"] = [
            {
                "id": "example.logic-extension",
                "version": 1,
                "required": True,
                "content_hash": "0" * 64,
            }
        ]
        logic = _reseal(logic)
        with self.assertRaisesRegex(CreationContractError, "unknown required extension"):
            validate_creation_document(logic)
        validate_creation_document(
            logic,
            registered_extensions={"example.logic-extension": lambda _: None},
        )

        excessive = copy.deepcopy(loaded.logic_modules[0])
        excessive["extensions"] = [
            {
                "content_hash": "0" * 64,
                "id": f"example.ext{index:02d}",
                "required": False,
                "version": 1,
            }
            for index in range(65)
        ]
        with self.assertRaisesRegex(CreationContractError, "64-item extension limit"):
            validate_creation_document(_reseal(excessive))

    def test_direct_logic_objects_preflight_bounds_before_traversal_or_hashing(self) -> None:
        oversized = {
            "actions": [{} for _ in range(129)],
            "content_hash": "0" * 64,
            "format": LOGIC_MODULE_FORMAT,
            "format_version": 1,
        }
        with (
            mock.patch.object(
                contracts,
                "_validate_json_structure",
                side_effect=AssertionError("full traversal ran before logic preflight"),
            ),
            mock.patch.object(
                contracts,
                "canonical_creation_hash",
                side_effect=AssertionError("hashing ran before logic preflight"),
            ),
        ):
            with self.assertRaisesRegex(CreationContractError, "actions.*128-item|preflight"):
                validate_creation_document(oversized)

        oversized_mapping = {
            "content_hash": "0" * 64,
            "format": LOGIC_MODULE_FORMAT,
            "format_version": 1,
            "state_variables": [{f"field_{index}": index for index in range(65)}],
        }
        with (
            mock.patch.object(
                contracts,
                "_validate_json_structure",
                side_effect=AssertionError("full traversal ran before logic preflight"),
            ),
            mock.patch.object(
                contracts,
                "canonical_creation_hash",
                side_effect=AssertionError("hashing ran before logic preflight"),
            ),
        ):
            with self.assertRaisesRegex(CreationContractError, "object field|preflight"):
                validate_creation_document(oversized_mapping)

        with self.subTest("oversized object key aborts before canonical hashing"):
            oversized_key = "k" * (contracts.MAX_CREATION_CONTRACT_BYTES + 1)
            oversized_key_document = {
                oversized_key: None,
                "content_hash": "0" * 64,
                "format": LOGIC_MODULE_FORMAT,
                "format_version": 1,
            }
            with mock.patch.object(
                contracts,
                "canonical_creation_hash",
                side_effect=AssertionError("hashing ran before logic size preflight"),
            ) as canonical_hash:
                with self.assertRaisesRegex(
                    CreationContractError,
                    "encoded JSON size|preflight",
                ):
                    validate_creation_document(oversized_key_document)
            canonical_hash.assert_not_called()

        with self.subTest("incremental accounting matches canonical JSON boundaries"):
            boundary = {
                'clé"\\': ["mañana", True, None, -12],
                "content_hash": "0" * 64,
                "format": LOGIC_MODULE_FORMAT,
                "format_version": 1,
            }
            exact_size = len(
                json.dumps(
                    boundary,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
            with mock.patch.object(
                contracts,
                "MAX_CREATION_CONTRACT_BYTES",
                exact_size,
            ):
                contracts._preflight_logic_object(boundary)
            with mock.patch.object(
                contracts,
                "MAX_CREATION_CONTRACT_BYTES",
                exact_size - 1,
            ):
                with self.assertRaisesRegex(
                    CreationContractError,
                    "encoded JSON size",
                ):
                    contracts._preflight_logic_object(boundary)

    def test_logic_module_files_use_the_existing_snapshot_security_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "puzzle"
            shutil.copytree(PUZZLE.parent, copied)
            manifest_path = copied / "source" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            logic_path = copied / "source" / manifest["modules"]["logic_modules"][0]["path"]

            with self.subTest("hardlink"):
                replacement = logic_path.with_suffix(".replacement")
                logic_path.rename(replacement)
                try:
                    os.link(replacement, logic_path)
                except (OSError, NotImplementedError):
                    replacement.rename(logic_path)
                else:
                    try:
                        with self.assertRaisesRegex(
                            CreationContractError,
                            "hard link|standalone regular file",
                        ):
                            load_creation_project(copied / "project.json")
                    finally:
                        logic_path.unlink()
                        replacement.rename(logic_path)

            with self.subTest("mid-read mutation"):
                original = contracts.read_workspace_file_snapshot
                mutated = False

                def changing_snapshot(*args: object, **kwargs: object) -> bytes:
                    nonlocal mutated
                    payload = original(*args, **kwargs)
                    if (
                        not mutated
                        and str(args[1]).replace("\\", "/")
                        == f"source/{manifest['modules']['logic_modules'][0]['path']}"
                    ):
                        logic_path.write_bytes(payload + b" ")
                        mutated = True
                    return payload

                with mock.patch.object(
                    contracts,
                    "read_workspace_file_snapshot",
                    side_effect=changing_snapshot,
                ):
                    with self.assertRaisesRegex(CreationContractError, "hash|changed|JSON"):
                        load_creation_project(copied / "project.json")

            with self.subTest("file bound"):
                with mock.patch.object(contracts, "MAX_CREATION_CONTRACT_BYTES", 100):
                    with self.assertRaisesRegex(CreationContractError, "100-byte|100 bytes"):
                        load_creation_project(copied / "project.json")

    def test_schema_catalog_and_generated_types_include_closed_logic_contract(self) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "logic-module.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(LOGIC_MODULE_FORMAT, schema["properties"]["format"]["const"])
        self.assertFalse(schema["additionalProperties"])

        catalog = json.loads((ROOT / "contracts" / "catalog.json").read_text(encoding="utf-8"))
        entry = next(item for item in catalog["contracts"] if item["id"] == "logic-module")
        self.assertEqual(LOGIC_MODULE_FORMAT, entry["format"])

        generated = (
            ROOT / "apps" / "studio" / "src" / "generated" / "world-forge-contracts.d.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("WorldForgeDeclarativeLogicModuleV1", generated)
        self.assertIn('operator: "integer_distance"', generated)
        self.assertIn('kind: "parameter"', generated)
        self.assertIn("runtime_ai?: never", generated)


if __name__ == "__main__":
    unittest.main()
