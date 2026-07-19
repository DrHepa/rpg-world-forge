from __future__ import annotations

from dataclasses import replace

from isoworld.content.models import (
    ConditionDefinition,
    DialogueChoiceDefinition,
    DialogueNodeDefinition,
    WorldPack,
)
from isoworld.world.state import (
    DialogueState,
    DomainEvent,
    GameAction,
    QuestState,
    WorldState,
    _apply_effects,
)


def _window_contains(minute: int, start: int, end: int) -> bool:
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def condition_met(
    state: WorldState,
    condition: ConditionDefinition,
    *,
    source_actor_id: str,
    events: tuple[DomainEvent, ...] = (),
    pack: WorldPack | None = None,
) -> bool:
    actor_id = condition.actor_id or source_actor_id
    result = False
    if condition.kind == "flag_set":
        result = condition.flag in state.flags
    elif condition.kind == "flag_unset":
        result = condition.flag not in state.flags
    elif condition.kind == "fact_status" and condition.fact_id:
        result = state.actor(actor_id).knowledge_status(condition.fact_id) == (
            condition.knowledge_status or "unknown"
        )
    elif (
        condition.kind == "relationship_at_least"
        and condition.target_actor_id
        and condition.dimension
    ):
        result = (
            state.actor(actor_id).relationship(condition.target_actor_id, condition.dimension)
            >= condition.value
        )
    elif condition.kind == "reputation_at_least" and condition.faction_id:
        result = state.actor(actor_id).reputation(condition.faction_id) >= condition.value
    elif condition.kind == "quest_status" and condition.quest_id:
        result = state.quest(condition.quest_id).status == condition.quest_status
    elif condition.kind == "event" and condition.event_kind:
        result = any(
            event.kind == condition.event_kind
            and (condition.subject_id is None or event.subject_id == condition.subject_id)
            and (condition.actor_id is None or event.actor_id == condition.actor_id)
            for event in events
        )
    elif (
        condition.kind == "time_window"
        and condition.start_minute is not None
        and condition.end_minute is not None
    ):
        result = _window_contains(state.minute_of_day, condition.start_minute, condition.end_minute)
    elif condition.kind == "actor_at" and condition.map_id:
        actor = state.actor(actor_id)
        result = (
            actor.map_id == condition.map_id and actor.x == condition.x and actor.y == condition.y
        )
    elif condition.kind == "need_at_most" and condition.need_id:
        result = state.actor(actor_id).need(condition.need_id) <= condition.value
    elif (
        condition.kind == "stockpile_resource_at_least"
        and condition.stockpile_id
        and condition.resource_id
    ):
        result = state.stockpile(condition.stockpile_id).resource(condition.resource_id) >= (
            condition.value
        )
    elif condition.kind == "construction_status" and condition.construction_id:
        statuses = {
            item.status
            for item in state.constructions
            if item.blueprint_id == condition.construction_id
        }
        requested = condition.construction_status or "absent"
        result = (requested == "absent" and not statuses) or requested in statuses
    elif condition.kind == "scarcity_at_least" and condition.resource_id and pack is not None:
        from isoworld.world.living_world import scarcity_percent

        result = scarcity_percent(state, pack, condition.resource_id) >= condition.value
    return not result if condition.negate else result


def conditions_met(
    state: WorldState,
    conditions: tuple[ConditionDefinition, ...],
    *,
    source_actor_id: str,
    events: tuple[DomainEvent, ...] = (),
    pack: WorldPack | None = None,
) -> bool:
    return all(
        condition_met(
            state,
            condition,
            source_actor_id=source_actor_id,
            events=events,
            pack=pack,
        )
        for condition in conditions
    )


def _replace_quest(state: WorldState, quest: QuestState) -> WorldState:
    return replace(
        state,
        quests=tuple(quest if item.quest_id == quest.quest_id else item for item in state.quests),
    )


def _node_accessible(
    state: WorldState,
    pack: WorldPack,
    node: DialogueNodeDefinition,
) -> bool:
    speaker = state.actor(node.speaker_id)
    forbidden = set(pack.actors[node.speaker_id].forbidden_fact_ids)
    return all(
        fact_id not in forbidden and speaker.knowledge_status(fact_id) != "unknown"
        for fact_id in node.fact_refs
    )


def available_dialogue_choices(
    state: WorldState, pack: WorldPack
) -> tuple[DialogueChoiceDefinition, ...]:
    if state.dialogue is None:
        return ()
    dialogue = pack.dialogues[state.dialogue.dialogue_id]
    node = dialogue.nodes[state.dialogue.node_id]
    result: list[DialogueChoiceDefinition] = []
    for choice in node.choices:
        if not conditions_met(
            state,
            choice.conditions,
            source_actor_id=state.dialogue.initiator_actor_id,
            events=state.recent_events,
            pack=pack,
        ):
            continue
        if choice.next_node_id is not None and not _node_accessible(
            state, pack, dialogue.nodes[choice.next_node_id]
        ):
            continue
        result.append(choice)
    return tuple(result)


def _process_quests(
    state: WorldState,
    pack: WorldPack,
    events: list[DomainEvent],
    source_actor_id: str,
) -> WorldState:
    result = state
    for quest_id in sorted(pack.quests):
        definition = pack.quests[quest_id]
        progress = result.quest(quest_id)
        if progress.status == "inactive" and conditions_met(
            result,
            definition.auto_start_conditions,
            source_actor_id=source_actor_id,
            events=tuple(events),
            pack=pack,
        ):
            progress = QuestState(quest_id, "active", definition.start_stage_id)
            result = _replace_quest(result, progress)
            events.append(DomainEvent("quest_started", source_actor_id, quest_id))
        visited: set[str] = set()
        while progress.status == "active" and progress.stage_id not in visited:
            if progress.stage_id is None:
                break
            visited.add(progress.stage_id)
            stage = definition.stages[progress.stage_id]
            if stage.failure_conditions and conditions_met(
                result,
                stage.failure_conditions,
                source_actor_id=source_actor_id,
                events=tuple(events),
                pack=pack,
            ):
                result = _apply_effects(
                    result,
                    stage.on_fail,
                    source_actor_id=source_actor_id,
                    target_actor_id=source_actor_id,
                    events=events,
                    pack=pack,
                )
                progress = QuestState(quest_id, "failed", progress.stage_id)
                result = _replace_quest(result, progress)
                events.append(DomainEvent("quest_failed", source_actor_id, quest_id))
                break
            if not stage.completion_conditions or not conditions_met(
                result,
                stage.completion_conditions,
                source_actor_id=source_actor_id,
                events=tuple(events),
                pack=pack,
            ):
                break
            result = _apply_effects(
                result,
                stage.on_complete,
                source_actor_id=source_actor_id,
                target_actor_id=source_actor_id,
                events=events,
                pack=pack,
            )
            if stage.next_stage_id is None:
                progress = QuestState(quest_id, "completed", progress.stage_id)
                result = _replace_quest(result, progress)
                events.append(DomainEvent("quest_completed", source_actor_id, quest_id))
                break
            progress = QuestState(quest_id, "active", stage.next_stage_id)
            result = _replace_quest(result, progress)
            events.append(DomainEvent("quest_advanced", source_actor_id, quest_id))
    return result


def _trigger_scene(
    state: WorldState,
    pack: WorldPack,
    events: list[DomainEvent],
    source_actor_id: str,
) -> WorldState:
    if state.dialogue is not None or state.active_scene_id is not None:
        return state
    candidates = []
    for scene in pack.scenes.values():
        occurrence = scene.id if scene.once else f"{scene.id}:{state.day}"
        if occurrence in state.triggered_scenes:
            continue
        if not _window_contains(state.minute_of_day, scene.start_minute, scene.end_minute):
            continue
        if conditions_met(
            state,
            scene.conditions,
            source_actor_id=source_actor_id,
            events=tuple(events),
            pack=pack,
        ):
            candidates.append(scene)
    if not candidates:
        return state
    scene = min(candidates, key=lambda item: (-item.priority, item.id))
    occurrence = scene.id if scene.once else f"{scene.id}:{state.day}"
    events.append(DomainEvent("scene_triggered", source_actor_id, scene.id))
    result = replace(
        state,
        active_scene_id=scene.id,
        triggered_scenes=state.triggered_scenes | {occurrence},
    )
    return _apply_effects(
        result,
        scene.effects,
        source_actor_id=source_actor_id,
        target_actor_id=source_actor_id,
        events=events,
        pack=pack,
    )


def postprocess_narrative(
    state: WorldState,
    pack: WorldPack,
    events: tuple[DomainEvent, ...],
    source_actor_id: str,
) -> WorldState:
    from isoworld.world.living_world import process_consequences

    result = process_consequences(state, pack, events, source_actor_id)
    mutable_events = list(result.recent_events)
    transition_limit = max(
        1, len(pack.quests) + sum(len(quest.stages) for quest in pack.quests.values())
    )
    for _ in range(transition_limit):
        advanced = _process_quests(result, pack, mutable_events, source_actor_id)
        if advanced == result:
            break
        result = advanced
    result = _trigger_scene(result, pack, mutable_events, source_actor_id)
    for _ in range(transition_limit):
        advanced = _process_quests(result, pack, mutable_events, source_actor_id)
        if advanced == result:
            break
        result = advanced
    return replace(result, recent_events=tuple(mutable_events))


def initialize_narrative(state: WorldState, pack: WorldPack) -> WorldState:
    return postprocess_narrative(state, pack, (), state.active_actor_id)


def _start_dialogue(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    actor_id = action.actor_id or state.active_actor_id
    actor = state.actor(actor_id)
    candidates = []
    for dialogue in pack.dialogues.values():
        partner = state.actor(dialogue.actor_id)
        distance = abs(actor.x - partner.x) + abs(actor.y - partner.y)
        if (
            partner.map_id == actor.map_id
            and distance <= dialogue.range
            and conditions_met(
                state,
                dialogue.conditions,
                source_actor_id=actor_id,
                events=(),
                pack=pack,
            )
            and _node_accessible(state, pack, dialogue.nodes[dialogue.start_node_id])
        ):
            candidates.append((distance, dialogue.id, dialogue))
    if action.dialogue_id is not None:
        candidates = [item for item in candidates if item[1] == action.dialogue_id]
    if not candidates:
        return replace(state, last_message="No conversation available")
    dialogue = min(candidates, key=lambda item: (item[0], item[1]))[2]
    events = [DomainEvent("dialogue_started", actor_id, dialogue.id)]
    result = replace(
        state,
        dialogue=DialogueState(dialogue.id, dialogue.start_node_id, actor_id, dialogue.actor_id),
        last_message=dialogue.display_name,
    )
    node = dialogue.nodes[dialogue.start_node_id]
    result = _apply_effects(
        result,
        node.on_enter,
        source_actor_id=actor_id,
        target_actor_id=dialogue.actor_id,
        events=events,
        pack=pack,
    )
    return postprocess_narrative(result, pack, tuple(events), actor_id)


def _choose_dialogue(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    if state.dialogue is None or not action.choice_id:
        return replace(state, last_message="Invalid dialogue choice")
    dialogue_state = state.dialogue
    choice = next(
        (item for item in available_dialogue_choices(state, pack) if item.id == action.choice_id),
        None,
    )
    if choice is None:
        return replace(state, last_message="Dialogue choice unavailable")
    events = [DomainEvent("dialogue_choice", dialogue_state.initiator_actor_id, choice.id)]
    result = _apply_effects(
        state,
        choice.effects,
        source_actor_id=dialogue_state.initiator_actor_id,
        target_actor_id=dialogue_state.partner_actor_id,
        events=events,
        pack=pack,
    )
    if choice.next_node_id is None:
        events.append(
            DomainEvent(
                "dialogue_ended",
                dialogue_state.initiator_actor_id,
                dialogue_state.dialogue_id,
            )
        )
        result = replace(result, dialogue=None, last_message="")
    else:
        next_state = replace(dialogue_state, node_id=choice.next_node_id)
        result = replace(result, dialogue=next_state)
        node = pack.dialogues[next_state.dialogue_id].nodes[next_state.node_id]
        result = _apply_effects(
            result,
            node.on_enter,
            source_actor_id=next_state.initiator_actor_id,
            target_actor_id=next_state.partner_actor_id,
            events=events,
            pack=pack,
        )
    return postprocess_narrative(result, pack, tuple(events), dialogue_state.initiator_actor_id)


def handle_narrative_action(state: WorldState, action: GameAction, pack: WorldPack) -> WorldState:
    if state.active_scene_id is not None:
        if action.kind == "dismiss_scene":
            return replace(state, active_scene_id=None, recent_events=(), last_message="")
        return state
    if state.dialogue is not None:
        if action.kind == "choose_dialogue":
            return _choose_dialogue(state, action, pack)
        if action.kind == "end_dialogue":
            dialogue = pack.dialogues[state.dialogue.dialogue_id]
            node = dialogue.nodes[state.dialogue.node_id]
            if node.allow_exit:
                events = (
                    DomainEvent(
                        "dialogue_ended",
                        state.dialogue.initiator_actor_id,
                        state.dialogue.dialogue_id,
                    ),
                )
                result = replace(state, dialogue=None, last_message="")
                return postprocess_narrative(
                    result, pack, events, state.dialogue.initiator_actor_id
                )
            return replace(state, last_message="This conversation cannot end yet")
        return state
    if action.kind == "start_dialogue":
        return _start_dialogue(state, action, pack)
    return state
