from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from functools import partial
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from gamepack_runtime import GameLogicError as RuntimeGameLogicError
from gamepack_runtime import validate_runtime_gamepack
from scripts import (
    generate_creation_workflow_fixtures,
    generate_gamepack_fixtures,
    generate_generic_asset_fixtures,
)
from worldforge.creation_readiness import validate_creation_readiness
from worldforge.game_logic import (
    CandidateAction,
    classify_state,
    initial_state,
    transition,
)
from worldforge.gamepack import (
    GamepackError,
    build_authoring_capability_ledger,
    build_gamepack,
    load_game_source_project,
    load_gamepack,
    serialize_gamepack,
    validate_capability_ledger_document,
)
from worldforge.integrity import canonical_payload_hash

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "multigenre-contracts"
CASES = tuple(generate_creation_workflow_fixtures.AUTHORING_CASES)
SEMANTIC_PNG_CASES = generate_generic_asset_fixtures.SEMANTIC_PNG_CASES
EXPECTED_SEMANTIC_PNG_SHA256 = {
    "action-framing": "ff437da92b6c201db4784f72daf706fc059ca25962161843e246596aa1de8784",
    "faction-strategy": "6a9a67b732876b41a9588d39f80262a13baebf5cd7c6c64450fa4342d69e3d0d",
    "modular-roguelite": "db353596d401280d7ac6034dda345b067b39a80d99516d755256e48c7af0bdc8",
    "sports-career": "6ce813f1705c2c84b3578142cd68cec4c38a3d3c1183362a47a21176637833f6",
}
UNSUPPORTED_FEATURES = {
    "action-framing": "action:realtime_combat",
    "faction-strategy": "strategy:turn_order",
    "modular-roguelite": "roguelite:run_reset",
    "sports-career": "sports:season",
}


def _generated(case: str) -> tuple[object, dict[str, bytes]]:
    return generate_creation_workflow_fixtures._build_authoring_case_documents(case)


def _generated_json(files: dict[str, bytes], relative: str) -> dict[str, object]:
    value = json.loads(files[relative])
    if not isinstance(value, dict):
        raise AssertionError(f"{relative} is not an object")
    return value


def _logic_records(loaded: object) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    logic = loaded.logic_modules[0]
    states = {item["id"]: item for item in logic["state_variables"]}
    return logic, states


def _gamepack(files: dict[str, bytes], case: str) -> dict[str, object]:
    return _generated_json(files, f"artifacts/{case}.gamepack.json")


def _generic_asset_cli_documents(
    temporary_root: Path,
    build_order: list[str],
    case: str,
) -> tuple[tuple[Path, None, bytes], ...]:
    build_order.append(case)
    return tuple(
        (
            temporary_root / "fixtures" / case / f"{index:02d}.bin",
            None,
            f"{case}:{index}".encode(),
        )
        for index in range(16)
    )


def _accepted_action(
    gamepack: dict[str, object],
    state: dict[str, object],
    action_id: str,
) -> dict[str, object]:
    result = transition(gamepack, state, CandidateAction(action_id, {}))
    if not result.accepted:
        raise AssertionError(f"{action_id} was rejected: {result.rejection_reason}")
    return result.post_state


def _expected_authored_narrative(module: dict[str, object]) -> dict[str, object]:
    fields = (
        "id",
        "unit_type",
        "title",
        "prerequisite_ids",
        "effect_ids",
        "next_unit_ids",
        "asset_binding_ids",
    )
    units: list[dict[str, object]] = []
    for source_unit in module["units"]:
        unit = {field: copy.deepcopy(source_unit[field]) for field in fields}
        if source_unit["unit_type"] == "ending":
            unit["ending_kind"] = source_unit["ending_kind"]
        units.append(unit)
    return {
        "source": {
            "format": module["format"],
            "format_version": module["format_version"],
            "id": module["module_id"],
            "content_hash": module["content_hash"],
        },
        "title": module["title"],
        "entry_unit_ids": copy.deepcopy(module["entry_unit_ids"]),
        "units": units,
    }


class AuthoringOnlyMultigenreFixtureTests(unittest.TestCase):
    def test_action_framing_has_typed_narrative_and_bounded_action_rules(self) -> None:
        loaded, files = _generated("action-framing")
        logic, states = _logic_records(loaded)

        self.assertEqual("linear", loaded.profile["narrative"]["topology"])
        self.assertEqual("authored", loaded.profile["narrative"]["authorship_mode"])
        self.assertEqual(1, len(loaded.narrative_modules))
        self.assertEqual(
            ["scene", "ending"],
            [unit["unit_type"] for unit in loaded.narrative_modules[0]["units"]],
        )
        self.assertEqual({"mission_progress", "mission_time_remaining"}, set(states))
        engage = next(item for item in logic["actions"] if item["id"] == "engage_objective")
        self.assertEqual(["advance_rule"], engage["rule_ids"])
        rule = next(item for item in logic["rules"] if item["id"] == "advance_rule")
        self.assertEqual(["mission_time_available"], rule["condition_ids"])
        self.assertEqual(["advance_progress", "consume_mission_time"], rule["effect_ids"])

        gamepack = _gamepack(files, "action-framing")
        state = initial_state(gamepack)
        forged = transition(
            gamepack,
            state,
            CandidateAction("engage_objective", {"unexpected": 1}),
        )
        self.assertFalse(forged.accepted)
        self.assertEqual("action_parameters_invalid", forged.rejection_reason)
        self.assertEqual(state, forged.pre_state)
        self.assertEqual(state, forged.post_state)

        for _ in range(3):
            state = _accepted_action(gamepack, state, "engage_objective")
        classification = classify_state(gamepack, state)
        self.assertEqual(("authored_victory",), classification.goal_ids)
        self.assertEqual(("scenario_complete",), classification.ending_ids)
        self.assertEqual("success", classification.ending_kind)

    def test_faction_strategy_models_factions_allocation_turns_resources_and_victory(self) -> None:
        loaded, files = _generated("faction-strategy")
        logic, states = _logic_records(loaded)

        self.assertEqual(
            {
                "command_points",
                "last_directed_faction",
                "north_influence",
                "south_influence",
                "turns_remaining",
            },
            set(states),
        )
        actions = {item["id"]: item for item in logic["actions"]}
        allocation_actions = {
            "direct_north_1": ("north", 1),
            "direct_north_2": ("north", 2),
            "direct_south_1": ("south", 1),
            "direct_south_2": ("south", 2),
        }
        self.assertEqual(set(allocation_actions) | {"restart_scenario"}, set(actions))
        self.assertTrue(
            all(not actions[action_id]["parameters"] for action_id in allocation_actions)
        )
        effects = {item["id"]: item for item in logic["effects"]}
        rules = {item["id"]: item for item in logic["rules"]}
        for action_id, (faction, amount) in allocation_actions.items():
            rule = rules[f"{action_id}_rule"]
            owned = [effects[effect_id] for effect_id in rule["effect_ids"]]
            influence = next(
                item for item in owned if item.get("state_id") == f"{faction}_influence"
            )
            spend = next(item for item in owned if item.get("state_id") == "command_points")
            record = next(item for item in owned if item.get("state_id") == "last_directed_faction")
            consume = next(item for item in owned if item.get("state_id") == "turns_remaining")
            self.assertEqual(amount, influence["amount"]["value"])
            self.assertEqual(-amount, spend["amount"]["value"])
            self.assertEqual(faction, record["value"]["value"])
            self.assertEqual(-1, consume["amount"]["value"])

        victory = next(item for item in logic["conditions"] if item["id"] == "victory_condition")
        self.assertEqual({"kind": "state", "state_id": "north_influence"}, victory["left"])
        self.assertEqual(3, victory["right"]["value"])

        gamepack = _gamepack(files, "faction-strategy")
        for action_id, (faction, amount) in allocation_actions.items():
            state = _accepted_action(gamepack, initial_state(gamepack), action_id)
            self.assertEqual(amount, state[f"{faction}_influence"])
            other = "south" if faction == "north" else "north"
            self.assertEqual(0, state[f"{other}_influence"])
            self.assertEqual(4 - amount, state["command_points"])
            self.assertEqual(2, state["turns_remaining"])

        winning = initial_state(gamepack)
        winning = _accepted_action(gamepack, winning, "direct_north_2")
        winning = _accepted_action(gamepack, winning, "direct_north_1")
        classification = classify_state(gamepack, winning)
        self.assertEqual(("authored_victory",), classification.goal_ids)
        self.assertEqual(("scenario_complete",), classification.ending_ids)

    def test_modular_roguelite_models_storylet_eligibility_run_and_death_recovery(self) -> None:
        loaded, files = _generated("modular-roguelite")
        logic, states = _logic_records(loaded)

        self.assertEqual("modular", loaded.profile["narrative"]["topology"])
        units = loaded.narrative_modules[0]["units"]
        self.assertEqual(["storylet", "storylet", "ending"], [u["unit_type"] for u in units])
        self.assertEqual(["run_at_entry_depth", "run_is_active"], units[0]["prerequisite_ids"])
        self.assertEqual(
            ["run_is_active", "storylet_progressed"],
            units[1]["prerequisite_ids"],
        )
        self.assertEqual(["storylet_resolution"], units[0]["next_unit_ids"])
        self.assertEqual({"run_active", "run_depth", "run_health", "run_deaths"}, set(states))
        self.assertEqual(
            [
                {
                    "id": "run_failed",
                    "condition_ids": ["death_condition", "run_is_inactive"],
                    "recovery_action_ids": ["recover_after_death"],
                }
            ],
            logic["failures"],
        )
        recovery = next(item for item in logic["actions"] if item["id"] == "recover_after_death")
        self.assertEqual(["recovery_rule"], recovery["rule_ids"])
        recovery_rule = next(item for item in logic["rules"] if item["id"] == "recovery_rule")
        self.assertEqual(
            [
                "reactivate_run",
                "record_run_death",
                "reset_run_depth",
                "restore_run_health",
            ],
            recovery_rule["effect_ids"],
        )

        gamepack = _gamepack(files, "modular-roguelite")
        successful = initial_state(gamepack)
        for _ in range(3):
            successful = _accepted_action(gamepack, successful, "advance_expedition")
        success = classify_state(gamepack, successful)
        self.assertEqual(("authored_victory",), success.goal_ids)
        self.assertEqual(("scenario_complete",), success.ending_ids)

        recovered = initial_state(gamepack)
        recovered = _accepted_action(gamepack, recovered, "endure_storylet_hazard")
        recovered = _accepted_action(gamepack, recovered, "fall_to_storylet_hazard")
        failed = classify_state(gamepack, recovered)
        self.assertEqual(("run_failed",), failed.failure_ids)
        blocked = transition(
            gamepack,
            recovered,
            CandidateAction("advance_expedition", {}),
        )
        self.assertFalse(blocked.accepted)
        self.assertEqual("failure_recovery_required", blocked.rejection_reason)
        recovered = _accepted_action(gamepack, recovered, "recover_after_death")
        self.assertEqual(
            {"run_active": True, "run_deaths": 1, "run_depth": 0, "run_health": 2},
            {
                key: recovered[key]
                for key in ("run_active", "run_deaths", "run_depth", "run_health")
            },
        )
        recovered = _accepted_action(gamepack, recovered, "advance_expedition")
        self.assertEqual(1, recovered["run_depth"])

    def test_sports_career_models_teams_schedule_results_standings_and_season_end(self) -> None:
        loaded, files = _generated("sports-career")
        logic, states = _logic_records(loaded)

        self.assertEqual(
            {
                "career_points",
                "last_opponent",
                "last_match_plan",
                "last_match_result",
                "match_phase",
                "player_team",
                "season_opponents",
                "season_round",
                "standings_points",
            },
            set(states),
        )
        self.assertEqual("harbor_fc", states["player_team"]["initial"])
        self.assertEqual(
            ["mountain_fc", "river_fc", "valley_fc"],
            states["season_opponents"]["initial"],
        )
        actions = {item["id"]: item for item in logic["actions"]}
        result_points = {"draw": 1, "loss": 0, "win": 3}
        opponents = {1: "mountain_fc", 2: "river_fc", 3: "valley_fc"}
        effects = {item["id"]: item for item in logic["effects"]}
        rules = {item["id"]: item for item in logic["rules"]}
        for round_number, opponent in opponents.items():
            for result, points in result_points.items():
                action_id = f"record_round_{round_number}_{result}"
                self.assertEqual([], actions[action_id]["parameters"])
                owned = [effects[item] for item in rules[f"{action_id}_rule"]["effect_ids"]]
                recorded_opponent = next(
                    item for item in owned if item.get("state_id") == "last_opponent"
                )
                recorded_result = next(
                    item for item in owned if item.get("state_id") == "last_match_result"
                )
                standings = next(
                    item for item in owned if item.get("state_id") == "standings_points"
                )
                career = next(item for item in owned if item.get("state_id") == "career_points")
                self.assertEqual(opponent, recorded_opponent["value"]["value"])
                self.assertEqual(result, recorded_result["value"]["value"])
                self.assertEqual(points, standings["amount"]["value"])
                self.assertEqual(points, career["amount"]["value"])

        ending = next(item for item in logic["endings"] if item["id"] == "season_complete")
        self.assertEqual(
            ["season_ending_reached", "season_points_target_reached"],
            ending["condition_ids"],
        )

        gamepack = _gamepack(files, "sports-career")
        state = initial_state(gamepack)
        unplanned = transition(
            gamepack,
            state,
            CandidateAction("record_round_1_win", {}),
        )
        self.assertFalse(unplanned.accepted)
        self.assertEqual("rule_condition_false", unplanned.rejection_reason)
        state = _accepted_action(gamepack, state, "plan_balanced_match")
        self.assertEqual("result_pending", state["match_phase"])
        self.assertEqual("balanced", state["last_match_plan"])
        state = _accepted_action(gamepack, state, "record_round_1_win")
        self.assertEqual(
            (1, 3, 3, "mountain_fc", "win", "planning"),
            (
                state["season_round"],
                state["standings_points"],
                state["career_points"],
                state["last_opponent"],
                state["last_match_result"],
                state["match_phase"],
            ),
        )
        state = _accepted_action(gamepack, state, "plan_aggressive_match")
        wrong_round = transition(
            gamepack,
            state,
            CandidateAction("record_round_1_win", {}),
        )
        self.assertFalse(wrong_round.accepted)
        self.assertEqual("rule_condition_false", wrong_round.rejection_reason)
        state = _accepted_action(gamepack, state, "record_round_2_draw")
        state = _accepted_action(gamepack, state, "plan_balanced_match")
        state = _accepted_action(gamepack, state, "record_round_3_win")
        self.assertEqual(
            (3, 7, 7), (state["season_round"], state["standings_points"], state["career_points"])
        )
        self.assertEqual(("season_complete",), classify_state(gamepack, state).ending_ids)

        losing = initial_state(gamepack)
        for round_number in range(1, 4):
            losing = _accepted_action(gamepack, losing, "plan_balanced_match")
            losing = _accepted_action(gamepack, losing, f"record_round_{round_number}_loss")
        self.assertEqual((3, 0), (losing["season_round"], losing["standings_points"]))
        self.assertEqual((), classify_state(gamepack, losing).ending_ids)

    def test_source_contracts_validate_and_gamepacks_compile_deterministically(self) -> None:
        for case in CASES:
            with self.subTest(case=case):
                first_loaded, first_files = _generated(case)
                second_loaded, second_files = _generated(case)
                relative = f"artifacts/{case}.gamepack.json"
                self.assertEqual(first_files[relative], second_files[relative])
                self.assertEqual(first_loaded.project, second_loaded.project)
                with tempfile.TemporaryDirectory() as root:
                    path = Path(root) / f"{case}.gamepack.json"
                    path.write_bytes(first_files[relative])
                    gamepack = load_gamepack(path)

                first_artifacts = generate_gamepack_fixtures._artifacts(case)
                second_artifacts = generate_gamepack_fixtures._artifacts(case)
                self.assertEqual(
                    [(path.name, payload) for path, _document, payload in first_artifacts],
                    [(path.name, payload) for path, _document, payload in second_artifacts],
                )
                generated_gamepack = next(
                    payload
                    for path, _document, payload in first_artifacts
                    if path.name == f"{case}.gamepack.json"
                )
                self.assertEqual(first_files[relative], generated_gamepack)

                self.assertIsNone(gamepack["runtime_requirements"]["requested_adapter"])
                ledger = build_authoring_capability_ledger(gamepack)
                entries = [*ledger["mechanics"], *ledger["features"]]
                self.assertGreater(len(entries), 1)
                self.assertEqual({"authoring_only"}, {item["status"] for item in entries})
                self.assertEqual(
                    {"adapter_not_evaluated"}, {item["reason_code"] for item in entries}
                )
                self.assertTrue(
                    all(
                        not item["test_evidence"] and not item["native_evidence"]
                        for item in entries
                    )
                )
                if case in {"action-framing", "modular-roguelite"}:
                    self.assertGreater(len(first_loaded.narrative_modules), 0)
                    self.assertEqual(
                        [_expected_authored_narrative(first_loaded.narrative_modules[0])],
                        gamepack["modules"]["narrative"],
                    )
                    self.assertIsNone(gamepack["logic"]["narrative_cursor"])
                    self.assertEqual([], gamepack["logic"]["narrative_transitions"])
                    self.assertEqual(
                        first_loaded.profile["content_hash"],
                        gamepack["source"]["profile"]["content_hash"],
                    )
                    profile_history = [
                        json.loads(payload)
                        for relative, payload in first_files.items()
                        if relative.startswith(".worldforge/artifact_history/")
                        and json.loads(payload).get("format") == "world-forge.creation_profile"
                    ]
                    self.assertEqual([first_loaded.profile], profile_history)

    def test_emitted_authoring_sources_round_trip_through_the_canonical_loader(self) -> None:
        for case in CASES:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                in_memory_loaded, files = _generated(case)
                project_root = Path(root) / case
                for relative, payload in files.items():
                    if relative not in {"project.json", "profile.json"} and not (
                        relative.startswith("source/") and relative.endswith(".json")
                    ):
                        continue
                    path = project_root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)

                disk_loaded = load_game_source_project(project_root)
                disk_gamepack = build_gamepack(disk_loaded)
                in_memory_gamepack = _gamepack(files, case)

                self.assertEqual(in_memory_loaded, disk_loaded)
                self.assertEqual(
                    files[f"artifacts/{case}.gamepack.json"],
                    serialize_gamepack(disk_gamepack),
                )
                self.assertEqual(
                    in_memory_gamepack["content_hash"],
                    disk_gamepack["content_hash"],
                )

    def test_asset_plans_are_truthful_and_legacy_planning_bytes_are_unchanged(self) -> None:
        self.assertEqual(
            "ui_readability",
            generate_generic_asset_fixtures.ASSET_FIXTURES["abstract-puzzle"]["qa_profile"],
        )
        for case in CASES:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                _, generated_files = _generated(case)
                source_root = Path(root)
                gamepack_path = source_root / case / "artifacts" / f"{case}.gamepack.json"
                gamepack_path.parent.mkdir(parents=True)
                gamepack_path.write_bytes(generated_files[f"artifacts/{case}.gamepack.json"])
                planning = generate_generic_asset_fixtures.build_fixture_planning_documents(
                    case,
                    source_root=source_root,
                )
                documents = [document for _, document, _ in planning]
                gamepack = _generated_json(generated_files, f"artifacts/{case}.gamepack.json")
                inventory = next(
                    item for item in documents if item["format"] == "world-forge.asset_inventory"
                )
                self.assertEqual(1, len(inventory["assets"]))
                self.assertEqual(1, len(gamepack["asset_requirements"]))
                self.assertEqual(
                    generate_generic_asset_fixtures.ASSET_FIXTURES[case]["asset_id"],
                    inventory["assets"][0]["asset_id"],
                )
                self.assertTrue(gamepack["asset_requirements"][0]["required"])
                self.assertEqual(
                    generate_generic_asset_fixtures.ASSET_FIXTURES[case]["binding_ids"][0],
                    gamepack["asset_requirements"][0]["binding_id"],
                )

        for case in ("abstract-puzzle", "branching-narrative"):
            with self.subTest(legacy=case):
                for (
                    path,
                    _document,
                    expected,
                ) in generate_generic_asset_fixtures.build_fixture_planning_documents(case):
                    self.assertEqual(expected, path.read_bytes(), path)
                for path, _document, expected in generate_gamepack_fixtures._artifacts(case):
                    self.assertEqual(expected, path.read_bytes(), path)

        _, systemic_files = generate_creation_workflow_fixtures._build_documents()
        for relative, expected in systemic_files.items():
            self.assertEqual(
                expected,
                (generate_creation_workflow_fixtures.DESTINATION / relative).read_bytes(),
                relative,
            )

    def test_semantic_png_golden_outputs_and_processed_payload_rejection(self) -> None:
        self.assertEqual(
            tuple(
                case
                for case, descriptor in generate_generic_asset_fixtures.ASSET_FIXTURES.items()
                if "png_layout" in descriptor
            ),
            SEMANTIC_PNG_CASES,
        )
        payloads = {
            case: generate_generic_asset_fixtures._authoring_png(case)
            for case in SEMANTIC_PNG_CASES
        }
        payload_hashes = {
            case: hashlib.sha256(payload).hexdigest() for case, payload in payloads.items()
        }
        puzzle_hash = hashlib.sha256(generate_generic_asset_fixtures._puzzle_png()).hexdigest()
        self.assertEqual(EXPECTED_SEMANTIC_PNG_SHA256, payload_hashes)
        self.assertEqual(len(SEMANTIC_PNG_CASES), len(set(payload_hashes.values())))
        self.assertNotIn(puzzle_hash, set(payload_hashes.values()))

        for case, payload in payloads.items():
            with self.subTest(case=case), Image.open(BytesIO(payload)) as image:
                self.assertEqual(
                    payload,
                    generate_generic_asset_fixtures._authoring_png(case),
                )
                image.load()
                self.assertEqual("PNG", image.format)
                self.assertEqual((256, 256), image.size)
                self.assertEqual("RGBA", image.mode)
                self.assertLess(len(payload), 16_384)
                acceptance = generate_generic_asset_fixtures._evaluate_processed_png_acceptance(
                    case,
                    payload,
                    self._processing_output(payload),
                )
                self.assertEqual(
                    len(
                        generate_generic_asset_fixtures.ASSET_FIXTURES[case]["acceptance_criteria"]
                    ),
                    len(acceptance),
                )
                self.assertEqual({"passed"}, {result["status"] for result in acceptance})
                self.assertEqual(
                    "release_ready",
                    generate_generic_asset_fixtures._manifest_state_for_acceptance(acceptance),
                )

        invalid_payloads = {
            "malformed": b"not-a-png",
            "corrupted": payloads["action-framing"][: len(payloads["action-framing"]) // 2],
            "blank": self._blank_png(),
            "wrong-dimensions": self._blank_png(size=(128, 128)),
            "checkerboard": generate_generic_asset_fixtures._puzzle_png(),
        }
        invalid_payloads.update(
            {f"swapped-{source_case}": payload for source_case, payload in payloads.items()}
        )
        for expected_case in SEMANTIC_PNG_CASES:
            for payload_name, payload in invalid_payloads.items():
                if payload_name == f"swapped-{expected_case}":
                    continue
                with self.subTest(expected_case=expected_case, payload=payload_name):
                    acceptance = generate_generic_asset_fixtures._evaluate_processed_png_acceptance(
                        expected_case,
                        payload,
                        self._processing_output(payload),
                    )
                    self.assertIn("failed", {result["status"] for result in acceptance})
                    self.assertNotEqual(
                        "release_ready",
                        generate_generic_asset_fixtures._manifest_state_for_acceptance(acceptance),
                    )

        for case, payload in payloads.items():
            with self.subTest(case=case, payload="processing-receipt-hash-mismatch"):
                processing_output = self._processing_output(payload)
                processing_output["sha256"] = "0" * 64
                acceptance = generate_generic_asset_fixtures._evaluate_processed_png_acceptance(
                    case,
                    payload,
                    processing_output,
                )
                self.assertEqual({"failed"}, {result["status"] for result in acceptance})
                self.assertEqual(
                    "processed",
                    generate_generic_asset_fixtures._manifest_state_for_acceptance(acceptance),
                )

    def test_generated_png_qa_evidence_is_derived_from_processed_runtime_pixels(self) -> None:
        for case in SEMANTIC_PNG_CASES:
            with (
                self.subTest(case=case),
                tempfile.TemporaryDirectory() as source_root,
                tempfile.TemporaryDirectory() as artifact_root,
            ):
                examples = Path(source_root)
                _, generated_files = _generated(case)
                gamepack_path = examples / case / "artifacts" / f"{case}.gamepack.json"
                gamepack_path.parent.mkdir(parents=True)
                gamepack_path.write_bytes(generated_files[f"artifacts/{case}.gamepack.json"])
                documents = generate_generic_asset_fixtures.build_fixture_documents(
                    case,
                    artifact_root=Path(artifact_root),
                    source_root=examples,
                )
                processed_payload = next(
                    payload
                    for path, document, payload in documents
                    if document is None and "/processed/" in path.as_posix()
                )
                qa_report = next(
                    document
                    for path, document, _payload in documents
                    if path.name == "qa-report.json"
                )
                processing_receipt = next(
                    document
                    for path, document, _payload in documents
                    if path.name == "processing-receipt.json"
                )
                manifest = next(
                    document
                    for path, document, _payload in documents
                    if path == examples / case / "assets" / "manifest.json"
                )
                processing_output = processing_receipt["outputs"][0]
                self.assertEqual(
                    hashlib.sha256(processed_payload).hexdigest(),
                    processing_output["sha256"],
                )
                expected_acceptance = (
                    generate_generic_asset_fixtures._evaluate_processed_png_acceptance(
                        case,
                        processed_payload,
                        processing_output,
                    )
                )
                self.assertEqual(expected_acceptance, qa_report["acceptance_criteria"])
                self.assertEqual(
                    processing_output["sha256"],
                    qa_report["outputs"][0]["sha256"],
                )
                self.assertEqual(
                    processing_output["sha256"],
                    manifest["assets"][0]["outputs"][0]["sha256"],
                )
                self.assertEqual(
                    qa_report["content_hash"],
                    manifest["assets"][0]["qa_report"]["content_hash"],
                )
                self.assertEqual("passed", qa_report["status"])
                self.assertEqual("release_ready", manifest["state"])

    @staticmethod
    def _blank_png(*, size: tuple[int, int] = (256, 256)) -> bytes:
        output = BytesIO()
        Image.new("RGBA", size, (0, 0, 0, 255)).save(
            output,
            format="PNG",
            compress_level=9,
            optimize=False,
        )
        return output.getvalue()

    @staticmethod
    def _processing_output(payload: bytes) -> dict[str, object]:
        return {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }

    def test_creation_bytes_do_not_depend_on_existing_asset_evidence(self) -> None:
        for case in CASES:
            with (
                self.subTest(case=case),
                tempfile.TemporaryDirectory() as clean,
                tempfile.TemporaryDirectory() as populated,
            ):
                with patch.object(generate_creation_workflow_fixtures, "ROOT", Path(clean)):
                    _, clean_files = _generated(case)

                populated_root = Path(populated)
                populated_examples = populated_root / "examples" / "multigenre-contracts"
                gamepack_path = populated_examples / case / "artifacts" / f"{case}.gamepack.json"
                gamepack_path.parent.mkdir(parents=True)
                gamepack_path.write_bytes(clean_files[f"artifacts/{case}.gamepack.json"])
                planning = generate_generic_asset_fixtures.build_fixture_planning_documents(
                    case,
                    source_root=populated_examples,
                )
                for path, _document, payload in planning:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)

                with patch.object(generate_creation_workflow_fixtures, "ROOT", populated_root):
                    _, populated_files = _generated(case)
                self.assertEqual(clean_files, populated_files)

    def test_readiness_keeps_authoring_independent_from_release(self) -> None:
        for case in CASES:
            with self.subTest(case=case):
                loaded, files = _generated(case)
                readiness = _generated_json(files, f"artifacts/{case}.readiness.json")
                gamepack = _generated_json(files, f"artifacts/{case}.gamepack.json")
                analysis = _generated_json(files, f"artifacts/{case}.game-analysis.json")
                evidence = [gamepack, analysis]
                checked = validate_creation_readiness(readiness, loaded, artifacts=evidence)
                self.assertEqual("valid", checked["dimensions"]["authoring"])
                self.assertEqual("compiled", checked["dimensions"]["compilation"])
                self.assertEqual("unplanned", checked["dimensions"]["assets"])
                self.assertEqual("absent", checked["dimensions"]["adapter"])
                self.assertEqual(
                    [
                        {
                            "platform": "platform:linux_x86_64",
                            "status": "untested",
                            "evidence_ids": [],
                        },
                        {
                            "platform": "platform:windows_x86_64",
                            "status": "untested",
                            "evidence_ids": [],
                        },
                    ],
                    checked["dimensions"]["execution"],
                )
                self.assertEqual("unverified", checked["dimensions"]["packaging"])
                self.assertEqual("blocked", checked["dimensions"]["release"])
                self.assertEqual("unsupported", analysis["status"])
                self.assertEqual(["analysis_profile_unsupported"], analysis["reason_codes"])

                ledger = build_authoring_capability_ledger(gamepack)
                self.assertEqual(
                    gamepack["runtime_requirements"]["required_features"],
                    [item["feature_id"] for item in ledger["features"]],
                )
                self.assertIn(
                    UNSUPPORTED_FEATURES[case],
                    [item["feature_id"] for item in ledger["features"]],
                )
                with self.assertRaises(RuntimeGameLogicError) as caught:
                    validate_runtime_gamepack(gamepack)
                self.assertEqual("required_feature_unsupported", caught.exception.reason_code)
                self.assertIn(UNSUPPORTED_FEATURES[case], caught.exception.detail)

    def test_malformed_supported_claim_without_evidence_fails_at_claim_boundary(self) -> None:
        _, files = _generated("action-framing")
        gamepack = _generated_json(files, "artifacts/action-framing.gamepack.json")
        ledger = build_authoring_capability_ledger(gamepack)
        malformed = copy.deepcopy(ledger)
        malformed["mechanics"][0]["status"] = "supported_current"
        malformed["mechanics"][0]["reason_code"] = "adapter_verified"
        malformed["content_hash"] = canonical_payload_hash(malformed)
        with self.assertRaisesRegex(GamepackError, "evidence|adapter|status"):
            validate_capability_ledger_document(malformed, gamepack=gamepack)

    def test_generators_have_explicit_check_and_preserve_default_check(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            temporary_root = Path(root)
            examples = temporary_root / "examples" / "multigenre-contracts"
            selected_authoring_cases = {
                "action-framing": generate_creation_workflow_fixtures.AUTHORING_CASES[
                    "action-framing"
                ]
            }
            with (
                patch.object(generate_creation_workflow_fixtures, "ROOT", temporary_root),
                patch.object(
                    generate_creation_workflow_fixtures,
                    "AUTHORING_CASES",
                    selected_authoring_cases,
                ),
                patch.object(generate_gamepack_fixtures, "ROOT", temporary_root),
                patch.object(generate_gamepack_fixtures, "EXAMPLES", examples),
                patch.object(generate_gamepack_fixtures, "CASES", ("action-framing",)),
                patch.object(generate_generic_asset_fixtures, "ROOT", temporary_root),
                patch.object(generate_generic_asset_fixtures, "EXAMPLES", examples),
                patch.object(generate_generic_asset_fixtures, "CASES", ("action-framing",)),
            ):
                creation_write = StringIO()
                with redirect_stdout(creation_write):
                    self.assertEqual(0, generate_creation_workflow_fixtures.main(["--write"]))
                self.assertIn("creation_workflow_fixtures=", creation_write.getvalue())
                self.assertIn("mode=write", creation_write.getvalue())

                gamepack_write = StringIO()
                with redirect_stdout(gamepack_write):
                    self.assertEqual(0, generate_gamepack_fixtures.main(["--write"]))
                self.assertIn("gamepack_fixtures=3 mode=write", gamepack_write.getvalue())

                write_output = StringIO()
                with redirect_stdout(write_output):
                    self.assertEqual(0, generate_generic_asset_fixtures.main(["--write"]))
                self.assertIn("generic_asset_fixtures=18 mode=write", write_output.getvalue())

                for generator in (
                    generate_creation_workflow_fixtures,
                    generate_gamepack_fixtures,
                    generate_generic_asset_fixtures,
                ):
                    default_output = StringIO()
                    with redirect_stdout(default_output):
                        self.assertEqual(0, generator.main([]))
                    explicit_output = StringIO()
                    with redirect_stdout(explicit_output):
                        self.assertEqual(0, generator.main(["--check"]))
                    self.assertEqual(default_output.getvalue(), explicit_output.getvalue())
                    with self.assertRaises(SystemExit):
                        generator.main(["--check", "--write"])

                project_path = examples / "action-framing" / "project.json"
                expected_project = project_path.read_bytes()
                project_path.write_bytes(b"stale\n")
                with redirect_stdout(StringIO()):
                    self.assertEqual(1, generate_creation_workflow_fixtures.main([]))
                    self.assertEqual(0, generate_creation_workflow_fixtures.main(["--write"]))
                    self.assertEqual(0, generate_creation_workflow_fixtures.main(["--check"]))
                self.assertEqual(expected_project, project_path.read_bytes())

                ledger_path = (
                    examples
                    / "action-framing"
                    / "artifacts"
                    / "action-framing.authoring-ledger.json"
                )
                expected_ledger = ledger_path.read_bytes()
                stale_ledger = json.loads(expected_ledger)
                stale_ledger["content_hash"] = "0" * 64
                ledger_path.write_text(json.dumps(stale_ledger), encoding="utf-8")
                with redirect_stdout(StringIO()):
                    self.assertEqual(1, generate_gamepack_fixtures.main([]))
                    self.assertEqual(0, generate_gamepack_fixtures.main(["--write"]))
                    self.assertEqual(0, generate_gamepack_fixtures.main(["--check"]))
                self.assertEqual(expected_ledger, ledger_path.read_bytes())

                subject_path = examples / "action-framing" / "assets" / "subject.json"
                expected_subject = subject_path.read_bytes()
                stale_subject = json.loads(expected_subject)
                stale_subject["content_hash"] = "0" * 64
                subject_path.write_text(json.dumps(stale_subject), encoding="utf-8")
                with redirect_stdout(StringIO()):
                    self.assertEqual(1, generate_generic_asset_fixtures.main([]))
                    self.assertEqual(0, generate_generic_asset_fixtures.main(["--write"]))
                    self.assertEqual(0, generate_generic_asset_fixtures.main(["--check"]))
                self.assertEqual(expected_subject, subject_path.read_bytes())

    def test_generic_asset_case_selector_writes_and_checks_only_selected_case(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            temporary_root = Path(root)
            build_order: list[str] = []
            fixture_documents = partial(
                _generic_asset_cli_documents,
                temporary_root,
                build_order,
            )

            untouched = temporary_root / "fixtures" / "branching-narrative" / "00.bin"
            untouched.parent.mkdir(parents=True)
            untouched.write_bytes(b"unselected-sentinel")

            with (
                patch.object(generate_generic_asset_fixtures, "ROOT", temporary_root),
                patch.object(
                    generate_generic_asset_fixtures,
                    "build_fixture_documents",
                    fixture_documents,
                ),
            ):
                write_output = StringIO()
                with redirect_stdout(write_output):
                    self.assertEqual(
                        0,
                        generate_generic_asset_fixtures.main(
                            ["--case", "action-framing", "--write"]
                        ),
                    )
                check_output = StringIO()
                with redirect_stdout(check_output):
                    self.assertEqual(
                        0,
                        generate_generic_asset_fixtures.main(["--case", "action-framing"]),
                    )

            self.assertEqual(
                "OK generic_asset_fixtures=16 mode=write\n",
                write_output.getvalue(),
            )
            self.assertEqual(
                "OK generic_asset_fixtures=16 mode=check\n",
                check_output.getvalue(),
            )
            self.assertEqual(["action-framing", "action-framing"], build_order)
            self.assertEqual(b"unselected-sentinel", untouched.read_bytes())
            self.assertEqual(
                b"action-framing:15",
                (temporary_root / "fixtures" / "action-framing" / "15.bin").read_bytes(),
            )

    def test_generic_asset_case_selector_uses_canonical_order_when_repeated(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            temporary_root = Path(root)
            build_order: list[str] = []
            fixture_documents = partial(
                _generic_asset_cli_documents,
                temporary_root,
                build_order,
            )

            untouched = temporary_root / "fixtures" / "modular-roguelite" / "00.bin"
            untouched.parent.mkdir(parents=True)
            untouched.write_bytes(b"unselected-sentinel")
            caller_order = [
                "--case",
                "sports-career",
                "--case",
                "action-framing",
            ]

            with (
                patch.object(generate_generic_asset_fixtures, "ROOT", temporary_root),
                patch.object(
                    generate_generic_asset_fixtures,
                    "build_fixture_documents",
                    fixture_documents,
                ),
            ):
                write_output = StringIO()
                with redirect_stdout(write_output):
                    self.assertEqual(
                        0,
                        generate_generic_asset_fixtures.main(caller_order + ["--write"]),
                    )
                check_output = StringIO()
                with redirect_stdout(check_output):
                    self.assertEqual(
                        0,
                        generate_generic_asset_fixtures.main(caller_order + ["--check"]),
                    )

            self.assertEqual(
                [
                    "action-framing",
                    "sports-career",
                    "action-framing",
                    "sports-career",
                ],
                build_order,
            )
            self.assertEqual(
                "OK generic_asset_fixtures=32 mode=write\n",
                write_output.getvalue(),
            )
            self.assertEqual(
                "OK generic_asset_fixtures=32 mode=check\n",
                check_output.getvalue(),
            )
            self.assertEqual(b"unselected-sentinel", untouched.read_bytes())

    def test_generic_asset_case_selector_preserves_all_case_default(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            temporary_root = Path(root)
            build_order: list[str] = []
            fixture_documents = partial(
                _generic_asset_cli_documents,
                temporary_root,
                build_order,
            )

            with (
                patch.object(generate_generic_asset_fixtures, "ROOT", temporary_root),
                patch.object(
                    generate_generic_asset_fixtures,
                    "build_fixture_documents",
                    fixture_documents,
                ),
            ):
                write_output = StringIO()
                with redirect_stdout(write_output):
                    self.assertEqual(0, generate_generic_asset_fixtures.main(["--write"]))
                default_output = StringIO()
                with redirect_stdout(default_output):
                    self.assertEqual(0, generate_generic_asset_fixtures.main([]))
                explicit_output = StringIO()
                with redirect_stdout(explicit_output):
                    self.assertEqual(0, generate_generic_asset_fixtures.main(["--check"]))

            self.assertEqual(list(generate_generic_asset_fixtures.CASES) * 3, build_order)
            self.assertEqual(
                "OK generic_asset_fixtures=96 mode=write\n",
                write_output.getvalue(),
            )
            self.assertEqual(
                "OK generic_asset_fixtures=96 mode=check\n",
                default_output.getvalue(),
            )
            self.assertEqual(default_output.getvalue(), explicit_output.getvalue())
            self.assertEqual(
                {case: 16 for case in generate_generic_asset_fixtures.CASES},
                {
                    case: len(tuple((temporary_root / "fixtures" / case).iterdir()))
                    for case in generate_generic_asset_fixtures.CASES
                },
            )

    def test_generic_asset_case_selector_rejects_invalid_selections(self) -> None:
        invalid_inputs = (
            (["--case", "unknown-case"], "unknown --case CASE_ID 'unknown-case'"),
            (["--case", ""], "--case CASE_ID cannot be empty"),
            (
                ["--case", "action-framing", "--case", "action-framing"],
                "duplicate --case CASE_ID 'action-framing'",
            ),
        )
        with patch.object(
            generate_generic_asset_fixtures,
            "build_fixture_documents",
        ) as fixture_builder:
            for arguments, expected_error in invalid_inputs:
                with self.subTest(arguments=arguments):
                    stderr = StringIO()
                    with self.assertRaises(SystemExit) as caught, redirect_stderr(stderr):
                        generate_generic_asset_fixtures.main(arguments)
                    self.assertEqual(2, caught.exception.code)
                    self.assertIn(expected_error, stderr.getvalue())
            fixture_builder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
