from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.content.models import (
    ConditionDefinition,
    ConsequenceDefinition,
    EffectDefinition,
    SceneDefinition,
)
from isoworld.persistence import load_game, load_replay, save_game, write_replay
from isoworld.render.render_state import build_render_state
from isoworld.world.living_world import is_walkable, scarcity_percent
from isoworld.world.narrative import condition_met
from isoworld.world.simulation import Simulation
from isoworld.world.state import GameAction
from worldforge.integrity import canonical_payload_hash
from worldforge.project import SourceProject, load_source_project
from worldforge.validation import validate_project

ROOT = Path(__file__).resolve().parents[1]
COMPILED = ROOT / "content/compiled/foundation.worldpack.json"
MANIFEST = ROOT / "examples/foundation/source/manifest.json"


def advance_minutes(simulation: Simulation, minutes: int) -> None:
    for _ in range(minutes * simulation.pack.clock.ticks_per_minute):
        simulation.tick()


def start_workshop(simulation: Simulation) -> None:
    simulation.dispatch(
        GameAction(
            "build",
            actor_id="maker",
            blueprint_id="workshop",
            map_id="test_garden",
            x=7,
            y=8,
        )
    )


class M3LivingWorldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pack = load_worldpack(COMPILED)

    def test_current_worldpack_loads_typed_living_world_contracts(self) -> None:
        self.assertEqual(5, self.pack.format_version)
        self.assertEqual("berries", self.pack.needs["hunger"].resource_id)
        self.assertEqual("survive", self.pack.goals["eat_berries"].parent_id)
        self.assertEqual("garden_store", self.pack.constructions["workshop"].stockpile_id)
        self.assertEqual(2, self.pack.production_recipes["make_planks"].duration_minutes)

    def test_v3_worldpacks_still_load_with_empty_living_world_systems(self) -> None:
        raw = json.loads(COMPILED.read_text(encoding="utf-8"))
        raw["format_version"] = 3
        raw.pop("runtime_requirements")
        raw["world"].pop("default_locale")
        raw["world"].pop("supported_locales")
        for collection in (
            "resources",
            "needs",
            "goals",
            "stockpiles",
            "constructions",
            "production_recipes",
            "consequences",
            "personal_arcs",
            "locales",
        ):
            raw["collections"].pop(collection)
        for actor in raw["collections"]["actors"]:
            actor.pop("needs", None)
            actor.pop("goal_ids", None)
        raw["content_hash"] = canonical_payload_hash(raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.worldpack.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            legacy = load_worldpack(path)
        self.assertEqual(3, legacy.format_version)
        self.assertEqual({}, legacy.constructions)
        self.assertEqual({}, legacy.consequences)

    def test_hierarchical_goal_satisfies_a_decaying_need(self) -> None:
        simulation = Simulation(self.pack)
        initial_berries = simulation.state.stockpile("garden_store").resource("berries")
        advance_minutes(simulation, 2)
        guide = simulation.state.actor("guide")
        self.assertGreater(guide.need("hunger"), 40)
        self.assertIsNone(guide.active_goal_id)
        self.assertLess(
            simulation.state.stockpile("garden_store").resource("berries"),
            initial_berries,
        )
        self.assertIn("need_satisfied", {event.kind for event in simulation.state.recent_events})

    def test_construction_changes_navigation_and_completes_on_clock(self) -> None:
        simulation = Simulation(self.pack)
        start_workshop(simulation)
        self.assertEqual(0, simulation.state.actor("maker").resource("timber"))
        self.assertFalse(is_walkable(simulation.state, self.pack, "test_garden", 7, 8))
        self.assertEqual("building", simulation.state.constructions[0].status)
        simulation.dispatch(
            GameAction("navigate", actor_id="explorer", map_id="test_garden", x=7, y=8)
        )
        self.assertEqual("No route", simulation.state.last_message)
        advance_minutes(simulation, 2)
        self.assertEqual("completed", simulation.state.constructions[0].status)
        self.assertEqual("workshop_rumor", simulation.state.pending_consequences[0].consequence_id)
        self.assertEqual("maker", simulation.state.pending_consequences[0].source_actor_id)

    def test_completed_construction_can_trigger_a_narrative_scene(self) -> None:
        scene = SceneDefinition(
            id="workshop_complete",
            title="Workshop complete",
            text="The new structure changes the settlement.",
            start_minute=0,
            end_minute=1440,
            conditions=(
                ConditionDefinition(
                    "construction_status",
                    construction_id="workshop",
                    construction_status="completed",
                ),
            ),
            effects=(),
            once=True,
            priority=100,
        )
        pack = replace(self.pack, scenes={**self.pack.scenes, scene.id: scene})
        simulation = Simulation(pack)
        start_workshop(simulation)
        advance_minutes(simulation, 2)
        self.assertEqual("workshop_complete", simulation.state.active_scene_id)

    def test_production_consumes_inputs_and_delivers_delayed_outputs(self) -> None:
        simulation = Simulation(self.pack)
        start_workshop(simulation)
        advance_minutes(simulation, 2)
        before_timber = simulation.state.stockpile("garden_store").resource("timber")
        simulation.dispatch(
            GameAction(
                "start_production",
                actor_id="maker",
                construction_instance_id="workshop__test_garden__7_8",
                recipe_id="make_planks",
            )
        )
        self.assertEqual(
            before_timber - 1,
            simulation.state.stockpile("garden_store").resource("timber"),
        )
        self.assertEqual(0, simulation.state.stockpile("garden_store").resource("planks"))
        advance_minutes(simulation, 2)
        self.assertEqual(2, simulation.state.stockpile("garden_store").resource("planks"))
        self.assertEqual((), simulation.state.production_jobs)
        self.assertLess(scarcity_percent(simulation.state, self.pack, "planks"), 100)

    def test_resource_transfer_and_scarcity_condition_share_economy_state(self) -> None:
        simulation = Simulation(self.pack)
        simulation.dispatch(
            GameAction(
                "transfer_resource",
                actor_id="guide",
                stockpile_id="garden_store",
                resource_id="berries",
                amount=1,
                direction="withdraw",
            )
        )
        self.assertEqual(1, simulation.state.actor("guide").resource("berries"))
        self.assertEqual(2, simulation.state.stockpile("garden_store").resource("berries"))
        self.assertTrue(
            condition_met(
                simulation.state,
                ConditionDefinition(
                    "scarcity_at_least",
                    resource_id="berries",
                    value=60,
                ),
                source_actor_id="guide",
                pack=self.pack,
            )
        )

    def test_delayed_consequences_form_a_multi_stage_chain(self) -> None:
        simulation = Simulation(self.pack)
        start_workshop(simulation)
        advance_minutes(simulation, 4)
        self.assertIn("market_noticed", simulation.state.flags)
        self.assertIn("workshop_rumor", simulation.state.triggered_consequences)
        self.assertEqual("supply_response", simulation.state.pending_consequences[0].consequence_id)
        advance_minutes(simulation, 2)
        self.assertIn("supply_response", simulation.state.triggered_consequences)
        self.assertEqual((), simulation.state.pending_consequences)

    def test_routed_location_events_can_schedule_consequences(self) -> None:
        arrival = ConsequenceDefinition(
            id="arrival_echo",
            delay_minutes=1,
            trigger_event="location_entered",
            subject_id="test_garden",
            conditions=(),
            effects=(EffectDefinition("set_flag", flag="arrival_remembered"),),
            once=True,
        )
        pack = replace(self.pack, consequences={**self.pack.consequences, arrival.id: arrival})
        simulation = Simulation(pack)
        simulation.dispatch(
            GameAction("navigate", actor_id="explorer", map_id="test_garden", x=1, y=0)
        )
        for _ in range(pack.clock.movement_interval_ticks):
            simulation.tick()
        self.assertEqual("arrival_echo", simulation.state.pending_consequences[0].consequence_id)
        advance_minutes(simulation, 1)
        self.assertIn("arrival_remembered", simulation.state.flags)

    def test_living_world_state_save_and_replay_are_exact(self) -> None:
        simulation = Simulation(self.pack)
        start_workshop(simulation)
        advance_minutes(simulation, 2)
        simulation.dispatch(
            GameAction(
                "start_production",
                actor_id="maker",
                construction_instance_id="workshop__test_garden__7_8",
                recipe_id="make_planks",
            )
        )
        advance_minutes(simulation, 2)
        with tempfile.TemporaryDirectory() as directory:
            save_path = Path(directory) / "living.save.json"
            replay_path = Path(directory) / "living.replay.json"
            save_game(save_path, simulation.state, self.pack)
            self.assertEqual(simulation.state, load_game(save_path, self.pack))
            write_replay(
                replay_path,
                simulation.action_log,
                simulation.state,
                self.pack,
            )
            actions, replayed = load_replay(replay_path, self.pack)
            self.assertEqual(simulation.action_log, actions)
            self.assertEqual(simulation.state, replayed)

    def test_render_snapshot_contains_construction_instances(self) -> None:
        simulation = Simulation(self.pack)
        start_workshop(simulation)
        snapshot = build_render_state(simulation.state, self.pack)
        self.assertEqual("workshop", snapshot.constructions[0].blueprint_id)
        self.assertEqual("building", snapshot.constructions[0].status)

    def test_goal_cycle_and_incomplete_current_pack_fail_closed(self) -> None:
        project = load_source_project(MANIFEST)
        collections = {key: list(value) for key, value in project.collections.items()}
        goals = [dict(value) for value in collections["goals"]]
        goals[0]["parent_id"] = "eat_berries"
        collections["goals"] = goals
        broken = SourceProject(project.manifest_path, project.world, collections)
        self.assertTrue(any("goal cycle" in issue.message for issue in validate_project(broken)))

        raw = json.loads(COMPILED.read_text(encoding="utf-8"))
        raw["collections"].pop("needs")
        raw["content_hash"] = canonical_payload_hash(raw)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.worldpack.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(WorldPackError, "living-world collections"):
                load_worldpack(path)

    def test_malformed_living_world_values_report_issues_without_crashing(self) -> None:
        project = load_source_project(MANIFEST)
        collections = {key: list(value) for key, value in project.collections.items()}
        actors = [dict(value) for value in collections["actors"]]
        actors[0]["goal_ids"] = [["not", "an", "id"]]
        collections["actors"] = actors
        goals = [dict(value) for value in collections["goals"]]
        goals[0]["parent_id"] = ["not", "an", "id"]
        collections["goals"] = goals
        broken = SourceProject(project.manifest_path, project.world, collections)
        messages = [issue.message for issue in validate_project(broken)]
        self.assertTrue(any("unknown goal" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
