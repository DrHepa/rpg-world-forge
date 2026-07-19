from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.persistence import load_game, load_replay, save_game, write_replay
from isoworld.world.narrative import available_dialogue_choices
from isoworld.world.state import GameAction, initial_world_state, reduce_world
from worldforge.narrative_analysis import analyze_project
from worldforge.project import SourceProject, load_source_project

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples/foundation/source/manifest.json"
PACK_PATH = ROOT / "content/compiled/foundation.worldpack.json"


def _adjacent_to_guide(state):
    actors = tuple(
        replace(actor, x=1, y=2, route=()) if actor.actor_id == "guide" else actor
        for actor in state.actors
    )
    return replace(state, actors=actors)


class M2NarrativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pack = load_worldpack(PACK_PATH)

    def test_initial_state_contains_typed_epistemic_and_social_state(self) -> None:
        state = initial_world_state(self.pack)
        explorer = state.actor("explorer")
        guide = state.actor("guide")
        self.assertEqual("suspected", explorer.knowledge_status("garden_rumor"))
        self.assertEqual("secret", guide.knowledge_status("stone_song"))
        self.assertEqual(10, explorer.relationship("guide", "trust"))
        self.assertEqual(0, explorer.reputation("garden_keepers"))
        self.assertEqual("active", state.quest("resonance_trial").status)
        self.assertEqual("awaken_stone", state.quest("resonance_trial").stage_id)

    def test_event_reactive_quest_and_timed_scene_fire_after_interaction(self) -> None:
        state = initial_world_state(self.pack)
        result = reduce_world(state, GameAction(kind="interact"), self.pack)
        self.assertEqual("learn_song", result.quest("resonance_trial").stage_id)
        self.assertEqual("morning_resonance", result.active_scene_id)
        self.assertIn("morning_resonance", result.triggered_scenes)
        self.assertIn("interaction_completed", {event.kind for event in result.recent_events})
        self.assertIn("quest_advanced", {event.kind for event in result.recent_events})
        self.assertIn("scene_triggered", {event.kind for event in result.recent_events})

    def test_scene_time_window_and_once_semantics_are_deterministic(self) -> None:
        state = replace(initial_world_state(self.pack), minute_of_day=600)
        result = reduce_world(state, GameAction(kind="interact"), self.pack)
        self.assertIsNone(result.active_scene_id)
        morning = replace(state, minute_of_day=500)
        first = reduce_world(morning, GameAction(kind="interact"), self.pack)
        dismissed = reduce_world(first, GameAction(kind="dismiss_scene"), self.pack)
        ticked = reduce_world(dismissed, GameAction(kind="tick"), self.pack)
        self.assertIsNone(ticked.active_scene_id)

    def test_conditional_dialogue_updates_knowledge_relationship_and_quest(self) -> None:
        state = _adjacent_to_guide(initial_world_state(self.pack))
        unavailable = reduce_world(state, GameAction(kind="start_dialogue"), self.pack)
        self.assertIsNone(unavailable.dialogue)

        state = reduce_world(state, GameAction(kind="interact"), self.pack)
        state = reduce_world(state, GameAction(kind="dismiss_scene"), self.pack)
        state = reduce_world(state, GameAction(kind="start_dialogue"), self.pack)
        self.assertEqual("welcome", state.dialogue.node_id if state.dialogue else None)
        self.assertEqual(
            ("ask_song", "leave"),
            tuple(choice.id for choice in available_dialogue_choices(state, self.pack)),
        )
        state = reduce_world(
            state,
            GameAction(kind="choose_dialogue", choice_id="ask_song"),
            self.pack,
        )
        self.assertEqual("explanation", state.dialogue.node_id if state.dialogue else None)
        state = reduce_world(
            state,
            GameAction(kind="choose_dialogue", choice_id="remember_song"),
            self.pack,
        )
        explorer = state.actor("explorer")
        self.assertIsNone(state.dialogue)
        self.assertEqual("known", explorer.knowledge_status("stone_song"))
        self.assertEqual(20, explorer.relationship("guide", "trust"))
        self.assertEqual(5, explorer.reputation("garden_keepers"))
        self.assertEqual("completed", state.quest("resonance_trial").status)

    def test_dialogue_overlay_pauses_world_clock(self) -> None:
        state = _adjacent_to_guide(initial_world_state(self.pack))
        state = reduce_world(state, GameAction(kind="interact"), self.pack)
        state = reduce_world(state, GameAction(kind="dismiss_scene"), self.pack)
        state = reduce_world(state, GameAction(kind="start_dialogue"), self.pack)
        paused = reduce_world(state, GameAction(kind="tick"), self.pack)
        self.assertEqual(state.tick, paused.tick)
        self.assertEqual(state.minute_of_day, paused.minute_of_day)

    def test_forbidden_fact_cannot_be_learned_by_dialogue_effect(self) -> None:
        actor_definitions = dict(self.pack.actors)
        explorer = actor_definitions["explorer"]
        actor_definitions["explorer"] = replace(
            explorer,
            forbidden_fact_ids=explorer.forbidden_fact_ids + ("stone_song",),
        )
        guarded_pack = replace(self.pack, actors=actor_definitions)
        state = _adjacent_to_guide(initial_world_state(guarded_pack))
        for action in (
            GameAction(kind="interact"),
            GameAction(kind="dismiss_scene"),
            GameAction(kind="start_dialogue"),
            GameAction(kind="choose_dialogue", choice_id="ask_song"),
            GameAction(kind="choose_dialogue", choice_id="remember_song"),
        ):
            state = reduce_world(state, action, guarded_pack)
        self.assertEqual("unknown", state.actor("explorer").knowledge_status("stone_song"))
        self.assertEqual("active", state.quest("resonance_trial").status)

    def test_narrative_state_save_round_trip_is_exact(self) -> None:
        state = reduce_world(initial_world_state(self.pack), GameAction(kind="interact"), self.pack)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "m2-save.json"
            save_game(path, state, self.pack)
            loaded = load_game(path, self.pack)
        self.assertEqual(state, loaded)

    def test_m2_actions_replay_to_the_same_narrative_state(self) -> None:
        actor_definitions = dict(self.pack.actors)
        guide = actor_definitions["guide"]
        actor_definitions["guide"] = replace(
            guide, spawn=replace(guide.spawn, x=1, y=2), schedule_id=None
        )
        replay_pack = replace(self.pack, actors=actor_definitions)
        actions = [
            GameAction(kind="interact"),
            GameAction(kind="dismiss_scene"),
            GameAction(kind="start_dialogue"),
            GameAction(kind="choose_dialogue", choice_id="ask_song"),
            GameAction(kind="choose_dialogue", choice_id="remember_song"),
        ]
        state = initial_world_state(replay_pack)
        for action in actions:
            state = reduce_world(state, action, replay_pack)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "m2.replay.json"
            write_replay(path, actions, state, replay_pack)
            replayed_actions, replayed_state = load_replay(path, replay_pack)
        self.assertEqual(actions, replayed_actions)
        self.assertEqual(state, replayed_state)

    def test_loader_rejects_broken_dialogue_graph_after_rehash(self) -> None:
        raw = json.loads(PACK_PATH.read_text(encoding="utf-8"))
        raw["collections"]["dialogues"][0]["start_node_id"] = "missing_node"
        raw.pop("content_hash")
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        raw["content_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.worldpack.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(WorldPackError):
                load_worldpack(path)

    def test_static_analysis_detects_unreachable_content_and_softlocks(self) -> None:
        original = load_source_project(MANIFEST)
        collections = copy.deepcopy(original.collections)
        dialogue = collections["dialogues"][0]
        dialogue["nodes"].append(
            {
                "id": "dead_end",
                "speaker_id": "guide",
                "text": "No exit.",
                "fact_refs": [],
                "choices": [],
                "on_enter": [],
                "allow_exit": False,
            }
        )
        project = SourceProject(original.manifest_path, original.world, collections)
        report = analyze_project(project)
        codes = {finding["code"] for finding in report["findings"]}
        self.assertIn("unreachable_dialogue_node", codes)
        self.assertIn("dialogue_hard_softlock", codes)
        self.assertEqual(1, report["summary"]["error"])

    def test_static_analysis_detects_forbidden_knowledge_leak(self) -> None:
        original = load_source_project(MANIFEST)
        collections = copy.deepcopy(original.collections)
        guide = next(actor for actor in collections["actors"] if actor["id"] == "guide")
        guide["knowledge"]["forbidden"] = ["stone_song"]
        project = SourceProject(original.manifest_path, original.world, collections)
        codes = {finding["code"] for finding in analyze_project(project)["findings"]}
        self.assertIn("forbidden_knowledge_leak", codes)


if __name__ == "__main__":
    unittest.main()
