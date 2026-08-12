from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from gamepack_raylib_2d.executable_shape import (
    AdapterExecutableShapeError,
    inspect_adapter_executable_shape,
)

ROOT = Path(__file__).resolve().parents[1]


def _fixture(name: str) -> dict[str, object]:
    return json.loads(
        (
            ROOT
            / "examples"
            / "multigenre-contracts"
            / name
            / "artifacts"
            / f"{name}.gamepack.json"
        ).read_text(encoding="utf-8")
    )


class AdapterExecutableShapeTests(unittest.TestCase):
    def assert_unsupported(self, gamepack: object, adapter_id: str) -> None:
        with self.assertRaises(AdapterExecutableShapeError) as raised:
            inspect_adapter_executable_shape(gamepack, adapter_id)
        self.assertEqual(
            raised.exception.reason_code,
            "adapter_executable_shape_unsupported",
        )

    def test_exact_puzzle_surface_is_accepted_and_four_cells_are_rejected(self) -> None:
        puzzle = _fixture("abstract-puzzle")
        shape = inspect_adapter_executable_shape(puzzle, "gamepack_raylib_2d_puzzle")
        self.assertEqual(shape.controller_kind, "puzzle")
        self.assertEqual(shape.narrative_action_ids, {})

        four_cells = copy.deepcopy(puzzle)
        for state_id in ("board", "target"):
            state = next(
                item for item in four_cells["logic"]["state_schema"] if item["id"] == state_id
            )
            state["allowed_values"].append("D")
            state["initial"].append("D")
            state["min_items"] = 4
            state["max_items"] = 4
            four_cells["logic"]["initial_state"][state_id].append("D")
        self.assert_unsupported(four_cells, "gamepack_raylib_2d_puzzle")

    def test_puzzle_rejects_missing_renamed_or_extra_state_and_action(self) -> None:
        puzzle = _fixture("abstract-puzzle")
        cases: list[dict[str, object]] = []

        missing_state = copy.deepcopy(puzzle)
        missing_state["logic"]["state_schema"] = [
            item for item in missing_state["logic"]["state_schema"] if item["id"] != "target"
        ]
        cases.append(missing_state)

        renamed_state = copy.deepcopy(puzzle)
        next(item for item in renamed_state["logic"]["state_schema"] if item["id"] == "move_count")[
            "id"
        ] = "moves"
        cases.append(renamed_state)

        extra_state = copy.deepcopy(puzzle)
        extra_state["logic"]["state_schema"].append(
            {
                "id": "score",
                "initial": 0,
                "minimum": 0,
                "maximum": 10,
                "mutability": "mutable",
                "persistence": "saved",
                "type": "integer",
            }
        )
        cases.append(extra_state)

        missing_action = copy.deepcopy(puzzle)
        missing_action["logic"]["actions"] = [
            item for item in missing_action["logic"]["actions"] if item["id"] != "restart_board"
        ]
        cases.append(missing_action)

        renamed_action = copy.deepcopy(puzzle)
        next(item for item in renamed_action["logic"]["actions"] if item["id"] == "swap_tiles")[
            "id"
        ] = "swap_cells"
        cases.append(renamed_action)

        extra_action = copy.deepcopy(puzzle)
        extra_action["logic"]["actions"].append(copy.deepcopy(extra_action["logic"]["actions"][0]))
        extra_action["logic"]["actions"][-1]["id"] = "hidden_restart"
        cases.append(extra_action)

        for index, candidate in enumerate(cases):
            with self.subTest(index=index):
                self.assert_unsupported(candidate, "gamepack_raylib_2d_puzzle")

    def test_puzzle_rejects_parameter_shape_or_bounds_outside_controller_surface(self) -> None:
        puzzle = _fixture("abstract-puzzle")
        cases: list[dict[str, object]] = []

        restart_parameter = copy.deepcopy(puzzle)
        restart_parameter["logic"]["actions"][0]["parameters"] = [
            {"id": "seed", "minimum": 0, "maximum": 1, "type": "integer"}
        ]
        cases.append(restart_parameter)

        widened_swap = copy.deepcopy(puzzle)
        widened_swap["logic"]["actions"][1]["parameters"][1]["maximum"] = 3
        cases.append(widened_swap)

        reordered_swap = copy.deepcopy(puzzle)
        reordered_swap["logic"]["actions"][1]["parameters"].reverse()
        cases.append(reordered_swap)

        for index, candidate in enumerate(cases):
            with self.subTest(index=index):
                self.assert_unsupported(candidate, "gamepack_raylib_2d_puzzle")

    def test_narrative_requires_two_option_reachable_choice_ending_graph(self) -> None:
        narrative = _fixture("branching-narrative")
        shape = inspect_adapter_executable_shape(narrative, "gamepack_raylib_2d_text")
        self.assertEqual(shape.controller_kind, "narrative_text")
        self.assertEqual(
            shape.narrative_action_ids,
            {
                ("central_choice", "choose_left"): "choose_left",
                ("central_choice", "choose_right"): "choose_right",
            },
        )

        one_option = copy.deepcopy(narrative)
        one_option["modules"]["narrative"][0]["units"][0]["options"].pop()
        self.assert_unsupported(one_option, "gamepack_raylib_2d_text")

        three_options = copy.deepcopy(narrative)
        third = copy.deepcopy(three_options["modules"]["narrative"][0]["units"][0]["options"][0])
        third["id"] = "choose_third"
        three_options["modules"]["narrative"][0]["units"][0]["options"].append(third)
        self.assert_unsupported(three_options, "gamepack_raylib_2d_text")

        unreachable = copy.deepcopy(narrative)
        unreachable["modules"]["narrative"][0]["units"].append(
            copy.deepcopy(unreachable["modules"]["narrative"][0]["units"][-1])
        )
        unreachable["modules"]["narrative"][0]["units"][-1]["id"] = "hidden_ending"
        self.assert_unsupported(unreachable, "gamepack_raylib_2d_text")

    def test_narrative_rejects_multiple_modules_missing_knowledge_and_unbound_actions(self) -> None:
        narrative = _fixture("branching-narrative")
        cases: list[dict[str, object]] = []

        multiple_modules = copy.deepcopy(narrative)
        multiple_modules["modules"]["narrative"].append(
            copy.deepcopy(multiple_modules["modules"]["narrative"][0])
        )
        cases.append(multiple_modules)

        missing_knowledge = copy.deepcopy(narrative)
        missing_knowledge["logic"]["state_schema"] = [
            item for item in missing_knowledge["logic"]["state_schema"] if item["id"] != "knowledge"
        ]
        cases.append(missing_knowledge)

        unsaved_knowledge = copy.deepcopy(narrative)
        next(
            item for item in unsaved_knowledge["logic"]["state_schema"] if item["id"] == "knowledge"
        )["persistence"] = "session"
        cases.append(unsaved_knowledge)

        extra_action = copy.deepcopy(narrative)
        action = copy.deepcopy(extra_action["logic"]["actions"][0])
        action["id"] = "unbound_choice"
        extra_action["logic"]["actions"].append(action)
        cases.append(extra_action)

        parameterized_action = copy.deepcopy(narrative)
        parameterized_action["logic"]["actions"][0]["parameters"] = [
            {"id": "index", "minimum": 0, "maximum": 1, "type": "integer"}
        ]
        cases.append(parameterized_action)

        missing_transition = copy.deepcopy(narrative)
        missing_transition["logic"]["narrative_transitions"].pop()
        cases.append(missing_transition)

        duplicate_dispatch = copy.deepcopy(narrative)
        duplicate_dispatch["logic"]["narrative_transitions"][1]["action_id"] = "choose_left"
        duplicate_dispatch["logic"]["actions"] = [
            action
            for action in duplicate_dispatch["logic"]["actions"]
            if action["id"] == "choose_left"
        ]
        cases.append(duplicate_dispatch)

        for index, candidate in enumerate(cases):
            with self.subTest(index=index):
                self.assert_unsupported(candidate, "gamepack_raylib_2d_text")

    def test_narrative_rejects_non_exact_locale_or_compiler_cursor(self) -> None:
        narrative = _fixture("branching-narrative")
        cases: list[dict[str, object]] = []

        extra_locale = copy.deepcopy(narrative)
        extra_locale["localization"]["supported_locales"].append("es")
        cases.append(extra_locale)

        wrong_source_locale = copy.deepcopy(narrative)
        wrong_source_locale["localization"]["source_locale"] = "es"
        cases.append(wrong_source_locale)

        authored_cursor = copy.deepcopy(narrative)
        authored_cursor["logic"]["narrative_cursor"]["compiler_owned"] = False
        cases.append(authored_cursor)

        renamed_cursor = copy.deepcopy(narrative)
        renamed_cursor["logic"]["narrative_cursor"]["id"] = "story_cursor"
        cases.append(renamed_cursor)

        for index, candidate in enumerate(cases):
            with self.subTest(index=index):
                self.assert_unsupported(candidate, "gamepack_raylib_2d_text")

    def test_narrative_option_identity_may_differ_from_dispatch_action_identity(self) -> None:
        narrative = _fixture("branching-narrative")
        options = narrative["modules"]["narrative"][0]["units"][0]["options"]
        transitions = narrative["logic"]["narrative_transitions"]
        for index, option in enumerate(options):
            option_id = f"visible_option_{index + 1}"
            old_option_id = option["id"]
            option["id"] = option_id
            next(
                item
                for item in narrative["logic"]["actions"]
                if item["id"] == transitions[index]["action_id"]
            )["source_bindings"][0]["option_id"] = option_id
            self.assertEqual(transitions[index]["option_id"], old_option_id)
            transitions[index]["option_id"] = option_id

        shape = inspect_adapter_executable_shape(narrative, "gamepack_raylib_2d_text")
        self.assertEqual(
            shape.narrative_action_ids,
            {
                ("central_choice", "visible_option_1"): "choose_left",
                ("central_choice", "visible_option_2"): "choose_right",
            },
        )


if __name__ == "__main__":
    unittest.main()
