from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

Color = tuple[int, int, int, int]

RUNTIME_API_VERSION = "0.5.0"
SUPPORTED_RUNTIME_FEATURES = frozenset(
    {
        "action_replay",
        "actor_needs",
        "conditional_dialogue",
        "construction",
        "contextual_interactions",
        "costed_abilities",
        "delayed_consequences",
        "directed_relationships",
        "grid_movement",
        "hierarchical_goals",
        "locales",
        "path_navigation",
        "personal_campaigns",
        "playable_actor_switching",
        "reactive_quests",
        "resource_economy",
        "schedules",
        "timed_scenes",
        "typed_knowledge",
        "versioned_persistence",
        "world_clock",
    }
)


@dataclass(frozen=True, slots=True)
class ClockDefinition:
    start_day: int = 1
    start_minute: int = 8 * 60
    ticks_per_minute: int = 20
    movement_interval_ticks: int = 4


@dataclass(frozen=True, slots=True)
class TileType:
    id: str
    display_name: str
    color: Color
    walkable: bool
    arable: bool
    height: int = 0


@dataclass(frozen=True, slots=True)
class MapDefinition:
    id: str
    display_name: str
    width: int
    height: int
    rows: tuple[str, ...]
    legend: dict[str, str]

    def tile_id_at(self, x: int, y: int) -> str:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            raise IndexError((x, y))
        return self.legend[self.rows[y][x]]


@dataclass(frozen=True, slots=True)
class Location:
    map_id: str
    x: int
    y: int


Spawn = Location


@dataclass(frozen=True, slots=True)
class EffectDefinition:
    kind: str
    target: str = "self"
    resource: str | None = None
    amount: int = 0
    flag: str | None = None
    fact_id: str | None = None
    knowledge_status: str | None = None
    target_actor_id: str | None = None
    dimension: str | None = None
    faction_id: str | None = None
    stockpile_id: str | None = None
    need_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConditionDefinition:
    kind: str
    negate: bool = False
    flag: str | None = None
    fact_id: str | None = None
    knowledge_status: str | None = None
    actor_id: str | None = None
    target_actor_id: str | None = None
    dimension: str | None = None
    faction_id: str | None = None
    value: int = 0
    quest_id: str | None = None
    quest_status: str | None = None
    event_kind: str | None = None
    subject_id: str | None = None
    map_id: str | None = None
    x: int | None = None
    y: int | None = None
    start_minute: int | None = None
    end_minute: int | None = None
    need_id: str | None = None
    resource_id: str | None = None
    stockpile_id: str | None = None
    construction_id: str | None = None
    construction_status: str | None = None


@dataclass(frozen=True, slots=True)
class ResourceDefinition:
    id: str
    display_name: str
    base_value: int
    scarcity_target: int


@dataclass(frozen=True, slots=True)
class NeedDefinition:
    id: str
    display_name: str
    decay_interval_minutes: int
    decay_amount: int
    critical_below: int
    resource_id: str
    consume_amount: int
    restore_amount: int


@dataclass(frozen=True, slots=True)
class GoalActionDefinition:
    kind: str
    need_id: str | None = None
    stockpile_id: str | None = None
    blueprint_id: str | None = None
    recipe_id: str | None = None
    location: Location | None = None


@dataclass(frozen=True, slots=True)
class GoalDefinition:
    id: str
    display_name: str
    parent_id: str | None
    priority: int
    conditions: tuple[ConditionDefinition, ...]
    action: GoalActionDefinition | None


@dataclass(frozen=True, slots=True)
class StockpileDefinition:
    id: str
    display_name: str
    location: Location
    capacity: int
    resources: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ConstructionDefinition:
    id: str
    display_name: str
    footprint: tuple[tuple[int, int], ...]
    costs: tuple[tuple[str, int], ...]
    build_minutes: int
    blocks_movement: bool
    stockpile_id: str | None


@dataclass(frozen=True, slots=True)
class ProductionRecipeDefinition:
    id: str
    display_name: str
    required_construction_id: str
    inputs: tuple[tuple[str, int], ...]
    outputs: tuple[tuple[str, int], ...]
    duration_minutes: int


@dataclass(frozen=True, slots=True)
class ConsequenceDefinition:
    id: str
    delay_minutes: int
    trigger_event: str
    subject_id: str | None
    conditions: tuple[ConditionDefinition, ...]
    effects: tuple[EffectDefinition, ...]
    once: bool


@dataclass(frozen=True, slots=True)
class FactDefinition:
    id: str
    statement: str
    kind: str
    truth: str


@dataclass(frozen=True, slots=True)
class FactionDefinition:
    id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class AbilityDefinition:
    id: str
    display_name: str
    target: str
    range: int
    costs: dict[str, int]
    cooldown_minutes: int
    effects: tuple[EffectDefinition, ...]


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    start_minute: int
    end_minute: int
    activity: str
    destinations: tuple[Location, ...]

    def contains(self, minute: int) -> bool:
        if self.start_minute < self.end_minute:
            return self.start_minute <= minute < self.end_minute
        return minute >= self.start_minute or minute < self.end_minute


@dataclass(frozen=True, slots=True)
class ScheduleDefinition:
    id: str
    entries: tuple[ScheduleEntry, ...]

    def entry_at(self, minute: int) -> ScheduleEntry | None:
        return next((entry for entry in self.entries if entry.contains(minute)), None)


@dataclass(frozen=True, slots=True)
class InteractionDefinition:
    id: str
    display_name: str
    prompt: str
    location: Location
    range: int
    required_flags: frozenset[str]
    forbidden_flags: frozenset[str]
    repeatable: bool
    effects: tuple[EffectDefinition, ...]


@dataclass(frozen=True, slots=True)
class DialogueChoiceDefinition:
    id: str
    text: str
    next_node_id: str | None
    conditions: tuple[ConditionDefinition, ...]
    effects: tuple[EffectDefinition, ...]


@dataclass(frozen=True, slots=True)
class DialogueNodeDefinition:
    id: str
    speaker_id: str
    text: str
    fact_refs: tuple[str, ...]
    choices: tuple[DialogueChoiceDefinition, ...]
    on_enter: tuple[EffectDefinition, ...]
    allow_exit: bool


@dataclass(frozen=True, slots=True)
class DialogueDefinition:
    id: str
    display_name: str
    actor_id: str
    range: int
    start_node_id: str
    conditions: tuple[ConditionDefinition, ...]
    nodes: dict[str, DialogueNodeDefinition]


@dataclass(frozen=True, slots=True)
class QuestStageDefinition:
    id: str
    description: str
    completion_conditions: tuple[ConditionDefinition, ...]
    failure_conditions: tuple[ConditionDefinition, ...]
    on_complete: tuple[EffectDefinition, ...]
    on_fail: tuple[EffectDefinition, ...]
    next_stage_id: str | None


@dataclass(frozen=True, slots=True)
class QuestDefinition:
    id: str
    title: str
    start_stage_id: str
    auto_start_conditions: tuple[ConditionDefinition, ...]
    stages: dict[str, QuestStageDefinition]


@dataclass(frozen=True, slots=True)
class SceneDefinition:
    id: str
    title: str
    text: str
    start_minute: int
    end_minute: int
    conditions: tuple[ConditionDefinition, ...]
    effects: tuple[EffectDefinition, ...]
    once: bool
    priority: int


@dataclass(frozen=True, slots=True)
class PersonalCampaignActDefinition:
    id: str
    quest_ids: tuple[str, ...]
    scene_ids: tuple[str, ...]
    next_act_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PersonalCampaignDefinition:
    id: str
    actor_id: str
    start_act_id: str
    acts: dict[str, PersonalCampaignActDefinition]


@dataclass(frozen=True, slots=True)
class LocaleDefinition:
    id: str
    language_tag: str
    strings: dict[str, str]


_RUNTIME_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def _runtime_version_key(version: str) -> tuple[int, int, int]:
    match = _RUNTIME_VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError("runtime API versions must use major.minor.patch")
    return tuple(int(value) for value in match.groups())  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class RuntimeApiRange:
    minimum: str
    maximum_exclusive: str

    def contains(self, version: str) -> bool:
        value = _runtime_version_key(version)
        return (
            _runtime_version_key(self.minimum)
            <= value
            < _runtime_version_key(self.maximum_exclusive)
        )


@dataclass(frozen=True, slots=True)
class RuntimeRequirements:
    runtime_api: RuntimeApiRange
    required_features: tuple[str, ...]
    optional_features: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeCompatibilityReport:
    compatible: bool
    api_compatible: bool
    runtime_version: str
    missing_required_features: tuple[str, ...]
    missing_optional_features: tuple[str, ...]


def check_runtime_compatibility(
    requirements: RuntimeRequirements,
    runtime_version: str,
    runtime_features: Iterable[str],
) -> RuntimeCompatibilityReport:
    """Compare immutable content requirements with caller-owned runtime capabilities."""

    available = frozenset(runtime_features)
    missing_required = tuple(
        feature for feature in requirements.required_features if feature not in available
    )
    missing_optional = tuple(
        feature for feature in requirements.optional_features if feature not in available
    )
    api_compatible = requirements.runtime_api.contains(runtime_version)
    return RuntimeCompatibilityReport(
        compatible=api_compatible and not missing_required,
        api_compatible=api_compatible,
        runtime_version=runtime_version,
        missing_required_features=missing_required,
        missing_optional_features=missing_optional,
    )


@dataclass(frozen=True, slots=True)
class ActorDefinition:
    id: str
    display_name: str
    playable: bool
    spawn: Spawn
    color: Color
    personal_arc_id: str | None = None
    schedule_id: str | None = None
    schedule_mode: str = "never"
    ability_ids: tuple[str, ...] = ()
    resources: tuple[tuple[str, int], ...] = ()
    knowledge: tuple[tuple[str, str], ...] = ()
    forbidden_fact_ids: tuple[str, ...] = ()
    relationships: tuple[tuple[str, tuple[tuple[str, int], ...]], ...] = ()
    faction_reputation: tuple[tuple[str, int], ...] = ()
    needs: tuple[tuple[str, int], ...] = ()
    goal_ids: tuple[str, ...] = ()

    @property
    def initial_resources(self) -> dict[str, int]:
        return dict(self.resources)


@dataclass(frozen=True, slots=True)
class WorldPack:
    format_version: int
    world_id: str
    title: str
    language: str
    default_locale: str
    supported_locales: tuple[str, ...]
    start_map_id: str
    content_hash: str
    runtime_requirements: RuntimeRequirements
    ui: dict[str, str]
    clock: ClockDefinition
    tile_types: dict[str, TileType]
    maps: dict[str, MapDefinition]
    actors: dict[str, ActorDefinition]
    abilities: dict[str, AbilityDefinition]
    schedules: dict[str, ScheduleDefinition]
    interactions: dict[str, InteractionDefinition]
    facts: dict[str, FactDefinition]
    factions: dict[str, FactionDefinition]
    dialogues: dict[str, DialogueDefinition]
    quests: dict[str, QuestDefinition]
    scenes: dict[str, SceneDefinition]
    personal_arcs: dict[str, PersonalCampaignDefinition]
    locales: dict[str, LocaleDefinition]
    resources: dict[str, ResourceDefinition]
    needs: dict[str, NeedDefinition]
    goals: dict[str, GoalDefinition]
    stockpiles: dict[str, StockpileDefinition]
    constructions: dict[str, ConstructionDefinition]
    production_recipes: dict[str, ProductionRecipeDefinition]
    consequences: dict[str, ConsequenceDefinition]
    collections: dict[str, tuple[dict[str, Any], ...]]

    @property
    def playable_actor_ids(self) -> tuple[str, ...]:
        return tuple(actor.id for actor in self.actors.values() if actor.playable)

    @property
    def personal_campaigns(self) -> dict[str, PersonalCampaignDefinition]:
        """Typed alias using product terminology while preserving the collection name."""

        return self.personal_arcs

    def personal_campaign_for_actor(self, actor_id: str) -> PersonalCampaignDefinition | None:
        return next(
            (campaign for campaign in self.personal_arcs.values() if campaign.actor_id == actor_id),
            None,
        )

    def locale_for_language_tag(self, language_tag: str) -> LocaleDefinition | None:
        normalized = language_tag.casefold()
        return next(
            (
                locale
                for locale in self.locales.values()
                if locale.language_tag.casefold() == normalized
            ),
            None,
        )

    def compatibility_with(
        self, runtime_version: str, runtime_features: Iterable[str]
    ) -> RuntimeCompatibilityReport:
        return check_runtime_compatibility(
            self.runtime_requirements,
            runtime_version,
            runtime_features,
        )

    def is_walkable(self, map_id: str, x: int, y: int) -> bool:
        world_map = self.maps[map_id]
        if x < 0 or y < 0 or x >= world_map.width or y >= world_map.height:
            return False
        return self.tile_types[world_map.tile_id_at(x, y)].walkable
