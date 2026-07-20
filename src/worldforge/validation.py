from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from isoworld.content.portability import is_portable_path_component
from worldforge.project import SourceProject

ID_PATTERN = re.compile(r"^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$")
BCP47_PATTERN = re.compile(
    r"^(?:"
    r"(?:[A-Za-z]{2,3}(?:-[A-Za-z]{3}){0,3}|[A-Za-z]{4}|[A-Za-z]{5,8})"
    r"(?:-[A-Za-z]{4})?"
    r"(?:-(?:[A-Za-z]{2}|[0-9]{3}))?"
    r"(?:-(?:[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}))*"
    r"(?:-[0-9A-WY-Za-wy-z](?:-[A-Za-z0-9]{2,8})+)*"
    r"(?:-x(?:-[A-Za-z0-9]{1,8})+)?"
    r"|x(?:-[A-Za-z0-9]{1,8})+"
    r")$"
)
RUNTIME_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
PLACEHOLDER_PATTERN = re.compile(
    r"(\{\{[^}]+\}\}|\bTODO\b|\bTBD\b|<\s*(?:fill|replace|pending)[^>]*>)",
    re.IGNORECASE,
)
REQUIRED_COLLECTIONS = ("tile_types", "maps", "actors")
KNOWN_COLLECTIONS = (
    "tile_types",
    "maps",
    "actors",
    "facts",
    "factions",
    "abilities",
    "schedules",
    "dialogues",
    "interactions",
    "quests",
    "scenes",
    "personal_arcs",
    "locales",
    "resources",
    "needs",
    "goals",
    "stockpiles",
    "constructions",
    "production_recipes",
    "consequences",
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def _walk_strings(value: Any, path: str):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, f"{path}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}/{index}")


def _require(item: dict[str, Any], fields: tuple[str, ...], path: str) -> list[ValidationIssue]:
    return [
        ValidationIssue(f"{path}/{field}", "required field is missing")
        for field in fields
        if field not in item
    ]


def _valid_id(value: Any, path: str) -> list[ValidationIssue]:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        return [ValidationIssue(path, "invalid ID; use 2..64-character ASCII snake_case")]
    return []


def _valid_repository_id(value: Any, path: str) -> list[ValidationIssue]:
    issues = _valid_id(value, path)
    if not issues and not is_portable_path_component(value):
        issues.append(ValidationIssue(path, "ID is reserved or unsafe as a portable path"))
    return issues


def _valid_language_tag(value: Any, path: str) -> list[ValidationIssue]:
    if not isinstance(value, str) or BCP47_PATTERN.fullmatch(value) is None:
        return [ValidationIssue(path, "must be a BCP47 language tag")]
    return []


def _runtime_version_key(
    value: Any, path: str
) -> tuple[tuple[int, int, int] | None, list[ValidationIssue]]:
    if not isinstance(value, str):
        return None, [ValidationIssue(path, "must use major.minor.patch")]
    match = RUNTIME_VERSION_PATTERN.fullmatch(value)
    if match is None:
        return None, [ValidationIssue(path, "must use major.minor.patch")]
    return tuple(int(part) for part in match.groups()), []  # type: ignore[return-value]


def _validate_color(value: Any, path: str) -> list[ValidationIssue]:
    if not isinstance(value, list) or len(value) not in (3, 4):
        return [ValidationIssue(path, "color must contain 3 or 4 channels")]
    if any(not isinstance(channel, int) or channel < 0 or channel > 255 for channel in value):
        return [ValidationIssue(path, "each channel must be an integer in 0..255")]
    return []


def _validate_location(
    value: Any,
    path: str,
    maps: dict[str, dict[str, Any]],
    tile_types: dict[str, dict[str, Any]],
    *,
    require_walkable: bool,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(value, dict):
        return [ValidationIssue(path, "must be a location object")]
    issues.extend(_require(value, ("map_id", "x", "y"), path))
    map_id = value.get("map_id")
    if not isinstance(map_id, str) or map_id not in maps:
        issues.append(ValidationIssue(f"{path}/map_id", f"unknown map: {map_id}"))
        return issues
    if not all(
        isinstance(value.get(field), int) and not isinstance(value.get(field), bool)
        for field in ("x", "y")
    ):
        issues.append(ValidationIssue(path, "x and y must be integers"))
        return issues
    world_map = maps[map_id]
    x, y = value["x"], value["y"]
    width = world_map.get("width")
    height = world_map.get("height")
    rows = world_map.get("rows")
    legend = world_map.get("legend")
    if not isinstance(width, int) or not isinstance(height, int):
        return issues
    if x < 0 or y < 0 or x >= width or y >= height:
        issues.append(ValidationIssue(path, "position is outside the map"))
        return issues
    if require_walkable and isinstance(rows, list) and isinstance(legend, dict):
        try:
            tile_id = legend[rows[y][x]]
        except (IndexError, KeyError, TypeError):
            return issues
        if tile_id in tile_types and tile_types[tile_id].get("walkable") is False:
            issues.append(ValidationIssue(path, "position is on a non-walkable tile"))
    return issues


def _validate_effects(
    value: Any,
    path: str,
    *,
    facts: dict[str, dict[str, Any]] | None = None,
    actors: dict[str, dict[str, Any]] | None = None,
    factions: dict[str, dict[str, Any]] | None = None,
    resources: dict[str, dict[str, Any]] | None = None,
    needs: dict[str, dict[str, Any]] | None = None,
    stockpiles: dict[str, dict[str, Any]] | None = None,
    allow_empty: bool = False,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(value, list) or (not value and not allow_empty):
        message = "must be a list" if allow_empty else "must contain at least one effect"
        return [ValidationIssue(path, message)]
    facts = facts or {}
    actors = actors or {}
    factions = factions or {}
    resources = resources or {}
    needs = needs or {}
    stockpiles = stockpiles or {}
    for index, effect in enumerate(value):
        effect_path = f"{path}/{index}"
        if not isinstance(effect, dict):
            issues.append(ValidationIssue(effect_path, "must be an object"))
            continue
        kind = effect.get("kind")
        if kind not in {
            "set_flag",
            "clear_flag",
            "change_resource",
            "learn_fact",
            "change_relationship",
            "change_reputation",
            "change_stockpile_resource",
            "change_need",
        }:
            issues.append(ValidationIssue(f"{effect_path}/kind", f"unsupported effect: {kind}"))
        if effect.get("target", "self") not in {"self", "target"}:
            issues.append(ValidationIssue(f"{effect_path}/target", "must be self or target"))
        if kind in {"set_flag", "clear_flag"}:
            issues.extend(_valid_id(effect.get("flag"), f"{effect_path}/flag"))
        if kind == "change_resource":
            issues.extend(_valid_id(effect.get("resource"), f"{effect_path}/resource"))
            if resources and effect.get("resource") not in resources:
                issues.append(
                    ValidationIssue(
                        f"{effect_path}/resource",
                        f"unknown resource: {effect.get('resource')}",
                    )
                )
            amount = effect.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0:
                issues.append(
                    ValidationIssue(f"{effect_path}/amount", "must be a non-zero integer")
                )
        if kind == "learn_fact":
            fact_id = effect.get("fact_id")
            if not isinstance(fact_id, str) or fact_id not in facts:
                issues.append(ValidationIssue(f"{effect_path}/fact_id", f"unknown fact: {fact_id}"))
            if effect.get("knowledge_status") not in {"suspected", "known", "secret"}:
                issues.append(
                    ValidationIssue(
                        f"{effect_path}/knowledge_status",
                        "must be suspected, known, or secret",
                    )
                )
        if kind == "change_relationship":
            target_actor_id = effect.get("target_actor_id")
            if not isinstance(target_actor_id, str) or target_actor_id not in actors:
                issues.append(
                    ValidationIssue(
                        f"{effect_path}/target_actor_id",
                        f"unknown actor: {target_actor_id}",
                    )
                )
            issues.extend(_valid_id(effect.get("dimension"), f"{effect_path}/dimension"))
            amount = effect.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0:
                issues.append(
                    ValidationIssue(f"{effect_path}/amount", "must be a non-zero integer")
                )
        if kind == "change_reputation":
            faction_id = effect.get("faction_id")
            if not isinstance(faction_id, str) or faction_id not in factions:
                issues.append(
                    ValidationIssue(f"{effect_path}/faction_id", f"unknown faction: {faction_id}")
                )
            amount = effect.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0:
                issues.append(
                    ValidationIssue(f"{effect_path}/amount", "must be a non-zero integer")
                )
        if kind == "change_stockpile_resource":
            stockpile_id = effect.get("stockpile_id")
            if not isinstance(stockpile_id, str) or stockpile_id not in stockpiles:
                issues.append(
                    ValidationIssue(
                        f"{effect_path}/stockpile_id", f"unknown stockpile: {stockpile_id}"
                    )
                )
            resource_id = effect.get("resource")
            if not isinstance(resource_id, str) or resource_id not in resources:
                issues.append(
                    ValidationIssue(f"{effect_path}/resource", f"unknown resource: {resource_id}")
                )
            amount = effect.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0:
                issues.append(
                    ValidationIssue(f"{effect_path}/amount", "must be a non-zero integer")
                )
        if kind == "change_need":
            need_id = effect.get("need_id")
            if not isinstance(need_id, str) or need_id not in needs:
                issues.append(ValidationIssue(f"{effect_path}/need_id", f"unknown need: {need_id}"))
            amount = effect.get("amount")
            if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0:
                issues.append(
                    ValidationIssue(f"{effect_path}/amount", "must be a non-zero integer")
                )
    return issues


def _validate_conditions(
    value: Any,
    path: str,
    *,
    facts: dict[str, dict[str, Any]],
    actors: dict[str, dict[str, Any]],
    factions: dict[str, dict[str, Any]],
    quests: dict[str, dict[str, Any]],
    maps: dict[str, dict[str, Any]],
    resources: dict[str, dict[str, Any]] | None = None,
    needs: dict[str, dict[str, Any]] | None = None,
    stockpiles: dict[str, dict[str, Any]] | None = None,
    constructions: dict[str, dict[str, Any]] | None = None,
) -> list[ValidationIssue]:
    if not isinstance(value, list):
        return [ValidationIssue(path, "must be a list")]
    issues: list[ValidationIssue] = []
    resources = resources or {}
    needs = needs or {}
    stockpiles = stockpiles or {}
    constructions = constructions or {}
    supported = {
        "flag_set",
        "flag_unset",
        "fact_status",
        "relationship_at_least",
        "reputation_at_least",
        "quest_status",
        "event",
        "time_window",
        "actor_at",
        "need_at_most",
        "stockpile_resource_at_least",
        "construction_status",
        "scarcity_at_least",
    }
    for index, condition in enumerate(value):
        condition_path = f"{path}/{index}"
        if not isinstance(condition, dict):
            issues.append(ValidationIssue(condition_path, "must be an object"))
            continue
        kind = condition.get("kind")
        if kind not in supported:
            issues.append(
                ValidationIssue(f"{condition_path}/kind", f"unsupported condition: {kind}")
            )
            continue
        if "negate" in condition and not isinstance(condition["negate"], bool):
            issues.append(ValidationIssue(f"{condition_path}/negate", "must be a boolean"))
        actor_id = condition.get("actor_id")
        if actor_id is not None and actor_id not in actors:
            issues.append(
                ValidationIssue(f"{condition_path}/actor_id", f"unknown actor: {actor_id}")
            )
        if kind in {"flag_set", "flag_unset"}:
            issues.extend(_valid_id(condition.get("flag"), f"{condition_path}/flag"))
        elif kind == "fact_status":
            fact_id = condition.get("fact_id")
            if not isinstance(fact_id, str) or fact_id not in facts:
                issues.append(
                    ValidationIssue(f"{condition_path}/fact_id", f"unknown fact: {fact_id}")
                )
            if condition.get("knowledge_status") not in {
                "unknown",
                "suspected",
                "known",
                "secret",
            }:
                issues.append(
                    ValidationIssue(
                        f"{condition_path}/knowledge_status",
                        "must be unknown, suspected, known, or secret",
                    )
                )
        elif kind == "relationship_at_least":
            target = condition.get("target_actor_id")
            if not isinstance(target, str) or target not in actors:
                issues.append(
                    ValidationIssue(f"{condition_path}/target_actor_id", f"unknown actor: {target}")
                )
            issues.extend(_valid_id(condition.get("dimension"), f"{condition_path}/dimension"))
        elif kind == "reputation_at_least":
            faction_id = condition.get("faction_id")
            if not isinstance(faction_id, str) or faction_id not in factions:
                issues.append(
                    ValidationIssue(
                        f"{condition_path}/faction_id", f"unknown faction: {faction_id}"
                    )
                )
        elif kind == "quest_status":
            quest_id = condition.get("quest_id")
            if not isinstance(quest_id, str) or quest_id not in quests:
                issues.append(
                    ValidationIssue(f"{condition_path}/quest_id", f"unknown quest: {quest_id}")
                )
            if condition.get("quest_status") not in {
                "inactive",
                "active",
                "completed",
                "failed",
            }:
                issues.append(
                    ValidationIssue(f"{condition_path}/quest_status", "invalid quest status")
                )
        elif kind == "event":
            issues.extend(_valid_id(condition.get("event_kind"), f"{condition_path}/event_kind"))
            if condition.get("subject_id") is not None:
                issues.extend(
                    _valid_id(condition.get("subject_id"), f"{condition_path}/subject_id")
                )
        elif kind == "time_window":
            for field, maximum in (("start_minute", 1439), ("end_minute", 1440)):
                minute = condition.get(field)
                if (
                    not isinstance(minute, int)
                    or isinstance(minute, bool)
                    or not 0 <= minute <= maximum
                ):
                    issues.append(
                        ValidationIssue(f"{condition_path}/{field}", f"must be in 0..{maximum}")
                    )
            if condition.get("start_minute") == condition.get("end_minute"):
                issues.append(ValidationIssue(condition_path, "time window cannot be empty"))
        elif kind == "actor_at":
            location = {
                "map_id": condition.get("map_id"),
                "x": condition.get("x"),
                "y": condition.get("y"),
            }
            issues.extend(
                _validate_location(
                    location,
                    condition_path,
                    maps,
                    {},
                    require_walkable=False,
                )
            )
        elif kind == "need_at_most":
            if (
                not isinstance(condition.get("need_id"), str)
                or condition.get("need_id") not in needs
            ):
                issues.append(
                    ValidationIssue(
                        f"{condition_path}/need_id",
                        f"unknown need: {condition.get('need_id')}",
                    )
                )
        elif kind == "stockpile_resource_at_least":
            if (
                not isinstance(condition.get("stockpile_id"), str)
                or condition.get("stockpile_id") not in stockpiles
            ):
                issues.append(
                    ValidationIssue(
                        f"{condition_path}/stockpile_id",
                        f"unknown stockpile: {condition.get('stockpile_id')}",
                    )
                )
            if (
                not isinstance(condition.get("resource_id"), str)
                or condition.get("resource_id") not in resources
            ):
                issues.append(
                    ValidationIssue(
                        f"{condition_path}/resource_id",
                        f"unknown resource: {condition.get('resource_id')}",
                    )
                )
        elif kind == "construction_status":
            if (
                not isinstance(condition.get("construction_id"), str)
                or condition.get("construction_id") not in constructions
            ):
                issues.append(
                    ValidationIssue(
                        f"{condition_path}/construction_id",
                        f"unknown construction: {condition.get('construction_id')}",
                    )
                )
            if condition.get("construction_status") not in {"absent", "building", "completed"}:
                issues.append(
                    ValidationIssue(
                        f"{condition_path}/construction_status", "invalid construction status"
                    )
                )
        elif kind == "scarcity_at_least":
            if (
                not isinstance(condition.get("resource_id"), str)
                or condition.get("resource_id") not in resources
            ):
                issues.append(
                    ValidationIssue(
                        f"{condition_path}/resource_id",
                        f"unknown resource: {condition.get('resource_id')}",
                    )
                )
        if kind in {
            "relationship_at_least",
            "reputation_at_least",
            "need_at_most",
            "stockpile_resource_at_least",
            "scarcity_at_least",
        }:
            value = condition.get("value")
            if not isinstance(value, int) or isinstance(value, bool):
                issues.append(ValidationIssue(f"{condition_path}/value", "must be an integer"))
    return issues


def validate_project(
    project: SourceProject,
    *,
    profile: str = "release",
) -> list[ValidationIssue]:
    if profile not in {"draft", "release"}:
        raise ValueError("profile must be draft or release")
    issues: list[ValidationIssue] = []
    world = project.world
    issues.extend(_require(world, ("id", "title", "language", "start_map_id", "ui"), "world"))
    if "id" in world:
        issues.extend(_valid_repository_id(world["id"], "world/id"))
    if not isinstance(world.get("title"), str) or not world.get("title", "").strip():
        issues.append(ValidationIssue("world/title", "must contain a title"))
    issues.extend(_valid_language_tag(world.get("language"), "world/language"))
    ui = world.get("ui")
    required_ui = {"move_help", "switch_help", "active_actor"}
    capabilities = world.get("capabilities", [])
    if not isinstance(capabilities, list) or not all(
        isinstance(capability, str) for capability in capabilities
    ):
        issues.append(ValidationIssue("world/capabilities", "must be a list of strings"))
    else:
        capability_ui = {
            "path_navigation": {"navigate_help"},
            "contextual_interactions": {"interact_help"},
            "costed_abilities": {"ability_help"},
            "world_clock": {"clock_label"},
            "conditional_dialogue": {"dialogue_help"},
            "reactive_quests": {"quest_label"},
            "timed_scenes": {"scene_help"},
            "actor_needs": {"needs_label"},
            "hierarchical_goals": {"goal_label"},
        }
        for capability in capabilities:
            required_ui.update(capability_ui.get(capability, set()))
            issues.extend(_valid_id(capability, "world/capabilities"))
        if len(set(capabilities)) != len(capabilities):
            issues.append(ValidationIssue("world/capabilities", "contains duplicate feature IDs"))

    configured_requirements = world.get("runtime_requirements")
    if configured_requirements is not None:
        requirements_path = "world/runtime_requirements"
        if not isinstance(configured_requirements, dict):
            issues.append(ValidationIssue(requirements_path, "must be an object"))
        else:
            issues.extend(
                _require(
                    configured_requirements,
                    ("runtime_api", "required_features", "optional_features"),
                    requirements_path,
                )
            )
            runtime_api = configured_requirements.get("runtime_api")
            if not isinstance(runtime_api, dict):
                issues.append(
                    ValidationIssue(f"{requirements_path}/runtime_api", "must be an object")
                )
            else:
                issues.extend(
                    _require(
                        runtime_api,
                        ("minimum", "maximum_exclusive"),
                        f"{requirements_path}/runtime_api",
                    )
                )
                minimum, minimum_issues = _runtime_version_key(
                    runtime_api.get("minimum"), f"{requirements_path}/runtime_api/minimum"
                )
                maximum, maximum_issues = _runtime_version_key(
                    runtime_api.get("maximum_exclusive"),
                    f"{requirements_path}/runtime_api/maximum_exclusive",
                )
                issues.extend(minimum_issues)
                issues.extend(maximum_issues)
                if minimum is not None and maximum is not None and minimum >= maximum:
                    issues.append(
                        ValidationIssue(
                            f"{requirements_path}/runtime_api", "must define a non-empty range"
                        )
                    )
            requirement_features: dict[str, list[str]] = {}
            for field in ("required_features", "optional_features"):
                feature_values = configured_requirements.get(field)
                if not isinstance(feature_values, list):
                    issues.append(ValidationIssue(f"{requirements_path}/{field}", "must be a list"))
                    continue
                valid_features: list[str] = []
                for position, feature in enumerate(feature_values):
                    feature_issues = _valid_id(feature, f"{requirements_path}/{field}/{position}")
                    issues.extend(feature_issues)
                    if not feature_issues:
                        valid_features.append(feature)
                if len(set(valid_features)) != len(valid_features):
                    issues.append(
                        ValidationIssue(
                            f"{requirements_path}/{field}", "contains duplicate feature IDs"
                        )
                    )
                requirement_features[field] = valid_features
            required_features = set(requirement_features.get("required_features", []))
            optional_features = set(requirement_features.get("optional_features", []))
            if required_features & optional_features:
                issues.append(
                    ValidationIssue(
                        requirements_path,
                        "a feature cannot be both required and optional",
                    )
                )
            if isinstance(capabilities, list):
                content_features = {
                    feature
                    for collection, feature in (
                        ("personal_arcs", "personal_campaigns"),
                        ("locales", "locales"),
                    )
                    if project.collections.get(collection)
                }
                for capability in [*capabilities, *sorted(content_features)]:
                    if isinstance(capability, str) and capability not in required_features:
                        issues.append(
                            ValidationIssue(
                                f"{requirements_path}/required_features",
                                f"content feature must be required: {capability}",
                            )
                        )
    if not isinstance(ui, dict):
        issues.append(ValidationIssue("world/ui", "must be an object of localized strings"))
    else:
        for key in sorted(required_ui - set(ui)):
            issues.append(ValidationIssue(f"world/ui/{key}", "UI string is missing"))

    simulation = world.get("simulation", {})
    if not isinstance(simulation, dict):
        issues.append(ValidationIssue("world/simulation", "must be an object"))
        simulation = {}
    for field, default, minimum, maximum in (
        ("start_day", 1, 1, None),
        ("start_minute", 480, 0, 1439),
        ("ticks_per_minute", 20, 1, None),
        ("movement_interval_ticks", 4, 1, None),
    ):
        value = simulation.get(field, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            issues.append(
                ValidationIssue(f"world/simulation/{field}", f"must be an integer >= {minimum}")
            )
        elif maximum is not None and value > maximum:
            issues.append(ValidationIssue(f"world/simulation/{field}", f"must be <= {maximum}"))

    for collection in REQUIRED_COLLECTIONS:
        if collection not in project.collections:
            issues.append(
                ValidationIssue(f"collections/{collection}", "required collection is missing")
            )
    for collection in project.collections:
        if collection not in KNOWN_COLLECTIONS:
            issues.append(ValidationIssue(f"collections/{collection}", "unknown collection"))

    indexes: dict[str, dict[str, dict[str, Any]]] = {}
    for collection, items in project.collections.items():
        index: dict[str, dict[str, Any]] = {}
        for position, item in enumerate(items):
            path = f"collections/{collection}/{position}"
            if "id" not in item:
                issues.append(ValidationIssue(f"{path}/id", "required field is missing"))
                continue
            identifier = item["id"]
            id_issues = _valid_id(identifier, f"{path}/id")
            issues.extend(id_issues)
            if id_issues:
                continue
            if identifier in index:
                issues.append(ValidationIssue(f"{path}/id", f"duplicate ID: {identifier}"))
            index[identifier] = item
        indexes[collection] = index

    tile_types = indexes.get("tile_types", {})
    maps = indexes.get("maps", {})
    actors = indexes.get("actors", {})
    facts = indexes.get("facts", {})
    factions = indexes.get("factions", {})
    abilities = indexes.get("abilities", {})
    schedules = indexes.get("schedules", {})
    interactions = indexes.get("interactions", {})
    dialogues = indexes.get("dialogues", {})
    quests = indexes.get("quests", {})
    scenes = indexes.get("scenes", {})
    arcs = indexes.get("personal_arcs", {})
    locales = indexes.get("locales", {})
    resource_definitions = indexes.get("resources", {})
    needs = indexes.get("needs", {})
    goals = indexes.get("goals", {})
    stockpiles = indexes.get("stockpiles", {})
    constructions = indexes.get("constructions", {})
    recipes = indexes.get("production_recipes", {})
    consequences = indexes.get("consequences", {})

    policy = world.get("content_policy", {})
    if not isinstance(policy, dict):
        issues.append(ValidationIssue("world/content_policy", "must be an object"))
        policy = {}

    language = world.get("language")
    default_locale = world.get("default_locale", language)
    issues.extend(_valid_language_tag(default_locale, "world/default_locale"))
    locale_tags: dict[str, str] = {}
    locale_key_sets: set[frozenset[str]] = set()
    for locale_id, locale in locales.items():
        path = f"locales/{locale_id}"
        issues.extend(_require(locale, ("language_tag", "strings"), path))
        language_tag = locale.get("language_tag")
        tag_issues = _valid_language_tag(language_tag, f"{path}/language_tag")
        issues.extend(tag_issues)
        if not tag_issues:
            normalized_tag = language_tag.casefold()
            if normalized_tag in locale_tags:
                issues.append(
                    ValidationIssue(
                        f"{path}/language_tag",
                        f"duplicate language tag used by locale: {locale_tags[normalized_tag]}",
                    )
                )
            locale_tags[normalized_tag] = locale_id
        strings = locale.get("strings")
        if not isinstance(strings, dict) or not strings:
            issues.append(
                ValidationIssue(f"{path}/strings", "must be a non-empty object of strings")
            )
            continue
        valid_keys: set[str] = set()
        for key, value in strings.items():
            if not isinstance(key, str) or not key:
                issues.append(ValidationIssue(f"{path}/strings", "keys must be non-empty strings"))
                continue
            valid_keys.add(key)
            if not isinstance(value, str) or not value:
                issues.append(
                    ValidationIssue(f"{path}/strings/{key}", "must be a non-empty string")
                )
        locale_key_sets.add(frozenset(valid_keys))
    if len(locale_key_sets) > 1:
        issues.append(ValidationIssue("locales", "locale string maps must contain identical keys"))

    declared_supported = world.get("supported_locales")
    if declared_supported is None:
        supported_locales = (
            sorted(
                (locale.get("language_tag") for locale in locales.values()),
                key=lambda value: value.casefold() if isinstance(value, str) else "",
            )
            if locales
            else [default_locale]
        )
    elif not isinstance(declared_supported, list) or not declared_supported:
        issues.append(
            ValidationIssue("world/supported_locales", "must be a non-empty list of BCP47 tags")
        )
        supported_locales = []
    else:
        supported_locales = declared_supported
    normalized_supported: list[str] = []
    for position, language_tag in enumerate(supported_locales):
        tag_issues = _valid_language_tag(language_tag, f"world/supported_locales/{position}")
        issues.extend(tag_issues)
        if not tag_issues:
            normalized_supported.append(language_tag.casefold())
    if len(set(normalized_supported)) != len(normalized_supported):
        issues.append(ValidationIssue("world/supported_locales", "contains duplicate tags"))
    if isinstance(default_locale, str) and default_locale.casefold() not in normalized_supported:
        issues.append(
            ValidationIssue("world/default_locale", "must be included in supported_locales")
        )
    if (
        isinstance(language, str)
        and isinstance(default_locale, str)
        and language.casefold() != default_locale.casefold()
    ):
        issues.append(
            ValidationIssue("world/language", "legacy language must match default_locale")
        )
    if locales and set(normalized_supported) != set(locale_tags):
        issues.append(
            ValidationIssue("world/supported_locales", "does not match locale language tags")
        )
    if not locales and isinstance(language, str) and normalized_supported != [language.casefold()]:
        issues.append(
            ValidationIssue(
                "world/supported_locales", "declares locales without locale definitions"
            )
        )
    if locales and isinstance(default_locale, str):
        default_locale_id = locale_tags.get(default_locale.casefold())
        if default_locale_id is not None:
            default_strings = locales[default_locale_id].get("strings")
            if isinstance(default_strings, dict) and isinstance(ui, dict):
                for key, value in ui.items():
                    if default_strings.get(key) != value:
                        issues.append(
                            ValidationIssue(
                                f"locales/{default_locale_id}/strings/{key}",
                                "must match the legacy world UI string",
                            )
                        )

    for fact_id, fact in facts.items():
        path = f"facts/{fact_id}"
        issues.extend(_require(fact, ("statement", "kind", "truth"), path))
        if not isinstance(fact.get("statement"), str) or not fact.get("statement", "").strip():
            issues.append(ValidationIssue(f"{path}/statement", "must contain a statement"))
        if fact.get("kind") not in {"truth", "secret", "rumor"}:
            issues.append(ValidationIssue(f"{path}/kind", "must be truth, secret, or rumor"))
        if fact.get("truth") not in {"true", "false", "unknown"}:
            issues.append(ValidationIssue(f"{path}/truth", "must be true, false, or unknown"))

    for faction_id, faction in factions.items():
        if (
            not isinstance(faction.get("display_name"), str)
            or not faction.get("display_name", "").strip()
        ):
            issues.append(
                ValidationIssue(
                    f"factions/{faction_id}/display_name", "must contain a display name"
                )
            )

    for tile_id, tile in tile_types.items():
        path = f"tile_types/{tile_id}"
        issues.extend(_require(tile, ("display_name", "color", "walkable", "arable"), path))
        if "color" in tile:
            issues.extend(_validate_color(tile["color"], f"{path}/color"))
        for field in ("walkable", "arable"):
            if field in tile and not isinstance(tile[field], bool):
                issues.append(ValidationIssue(f"{path}/{field}", "must be a boolean"))

    for map_id, world_map in maps.items():
        path = f"maps/{map_id}"
        issues.extend(
            _require(world_map, ("display_name", "width", "height", "rows", "legend"), path)
        )
        width = world_map.get("width")
        height = world_map.get("height")
        rows = world_map.get("rows")
        legend = world_map.get("legend")
        if not isinstance(width, int) or isinstance(width, bool) or width <= 0:
            issues.append(ValidationIssue(f"{path}/width", "must be a positive integer"))
        if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
            issues.append(ValidationIssue(f"{path}/height", "must be a positive integer"))
        if not isinstance(rows, list) or not all(isinstance(row, str) for row in rows):
            issues.append(ValidationIssue(f"{path}/rows", "must be a list of strings"))
            rows = []
        if isinstance(height, int) and len(rows) != height:
            issues.append(ValidationIssue(f"{path}/rows", "row count differs from height"))
        for index, row in enumerate(rows):
            if isinstance(width, int) and len(row) != width:
                issues.append(
                    ValidationIssue(f"{path}/rows/{index}", "row length differs from width")
                )
        if not isinstance(legend, dict):
            issues.append(
                ValidationIssue(f"{path}/legend", "must be a character -> tile_id object")
            )
            legend = {}
        for symbol, tile_id in legend.items():
            if not isinstance(symbol, str) or len(symbol) != 1:
                issues.append(
                    ValidationIssue(f"{path}/legend", "each symbol must be one character")
                )
            if not isinstance(tile_id, str) or tile_id not in tile_types:
                issues.append(
                    ValidationIssue(f"{path}/legend/{symbol}", f"unknown tile: {tile_id}")
                )
        for row_index, row in enumerate(rows):
            for column, symbol in enumerate(row):
                if symbol not in legend:
                    issues.append(
                        ValidationIssue(
                            f"{path}/rows/{row_index}/{column}",
                            f"symbol is missing from legend: {symbol}",
                        )
                    )

    start_map = world.get("start_map_id")
    if not isinstance(start_map, str) or start_map not in maps:
        issues.append(ValidationIssue("world/start_map_id", f"unknown map: {start_map}"))

    spawn_cells: dict[tuple[str, int, int], str] = {}
    for actor_id, actor in actors.items():
        path = f"actors/{actor_id}"
        issues.extend(_require(actor, ("display_name", "playable", "spawn", "color"), path))
        if "playable" in actor and not isinstance(actor["playable"], bool):
            issues.append(ValidationIssue(f"{path}/playable", "must be a boolean"))
        if "color" in actor:
            issues.extend(_validate_color(actor["color"], f"{path}/color"))
        spawn = actor.get("spawn")
        issues.extend(
            _validate_location(
                spawn,
                f"{path}/spawn",
                maps,
                tile_types,
                require_walkable=True,
            )
        )
        if (
            isinstance(spawn, dict)
            and isinstance(spawn.get("map_id"), str)
            and isinstance(spawn.get("x"), int)
            and not isinstance(spawn.get("x"), bool)
            and isinstance(spawn.get("y"), int)
            and not isinstance(spawn.get("y"), bool)
        ):
            cell = (spawn["map_id"], spawn["x"], spawn["y"])
            if cell in spawn_cells:
                issues.append(
                    ValidationIssue(
                        f"{path}/spawn",
                        f"cell is already occupied by actor: {spawn_cells[cell]}",
                    )
                )
            spawn_cells[cell] = actor_id
        arc_id = actor.get("personal_arc_id")
        if arc_id is not None:
            arc_id_issues = _valid_id(arc_id, f"{path}/personal_arc_id")
            issues.extend(arc_id_issues)
            if not arc_id_issues and arc_id not in arcs:
                issues.append(ValidationIssue(f"{path}/personal_arc_id", f"unknown arc: {arc_id}"))
        schedule_id = actor.get("schedule_id")
        if schedule_id is not None and (
            not isinstance(schedule_id, str) or schedule_id not in schedules
        ):
            issues.append(
                ValidationIssue(f"{path}/schedule_id", f"unknown schedule: {schedule_id}")
            )
        schedule_mode = actor.get(
            "schedule_mode", "when_inactive" if actor.get("playable") is True else "always"
        )
        if schedule_mode not in {"always", "when_inactive", "never"}:
            issues.append(
                ValidationIssue(
                    f"{path}/schedule_mode",
                    "must be always, when_inactive, or never",
                )
            )
        resources = actor.get("resources", {})
        if not isinstance(resources, dict):
            issues.append(ValidationIssue(f"{path}/resources", "must be an object"))
        else:
            for resource_id, amount in resources.items():
                issues.extend(_valid_id(resource_id, f"{path}/resources/{resource_id}"))
                if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
                    issues.append(
                        ValidationIssue(
                            f"{path}/resources/{resource_id}",
                            "must be a non-negative integer",
                        )
                    )
        for field, index in (("ability_ids", abilities), ("faction_ids", factions)):
            refs = actor.get(field, [])
            if not isinstance(refs, list):
                issues.append(ValidationIssue(f"{path}/{field}", "must be a list"))
            else:
                for ref in refs:
                    if not isinstance(ref, str) or ref not in index:
                        issues.append(
                            ValidationIssue(f"{path}/{field}", f"unknown reference: {ref}")
                        )
        knowledge = actor.get("knowledge", {})
        if not isinstance(knowledge, dict):
            issues.append(ValidationIssue(f"{path}/knowledge", "must be an object"))
        else:
            groups: dict[str, set[str]] = {}
            for group in ("knows", "suspects", "secrets", "forbidden"):
                refs = knowledge.get(group, [])
                if not isinstance(refs, list):
                    issues.append(ValidationIssue(f"{path}/knowledge/{group}", "must be a list"))
                    continue
                groups[group] = {ref for ref in refs if isinstance(ref, str)}
                for ref in refs:
                    if not isinstance(ref, str) or ref not in facts:
                        issues.append(
                            ValidationIssue(f"{path}/knowledge/{group}", f"unknown fact: {ref}")
                        )
            forbidden = groups.get("forbidden", set())
            known = (
                groups.get("knows", set())
                | groups.get("suspects", set())
                | groups.get("secrets", set())
            )
            for conflict in sorted(forbidden & known):
                issues.append(
                    ValidationIssue(
                        f"{path}/knowledge",
                        f"fact {conflict} is both known and forbidden",
                    )
                )
        relationships = actor.get("relationships", {})
        if not isinstance(relationships, dict):
            issues.append(ValidationIssue(f"{path}/relationships", "must be an object"))
        else:
            for target_actor_id, dimensions in relationships.items():
                relationship_path = f"{path}/relationships/{target_actor_id}"
                if target_actor_id not in actors:
                    issues.append(
                        ValidationIssue(relationship_path, f"unknown actor: {target_actor_id}")
                    )
                if not isinstance(dimensions, dict) or not dimensions:
                    issues.append(
                        ValidationIssue(relationship_path, "must contain relationship dimensions")
                    )
                    continue
                for dimension, value in dimensions.items():
                    issues.extend(_valid_id(dimension, f"{relationship_path}/{dimension}"))
                    if (
                        not isinstance(value, int)
                        or isinstance(value, bool)
                        or not -100 <= value <= 100
                    ):
                        issues.append(
                            ValidationIssue(
                                f"{relationship_path}/{dimension}", "must be in -100..100"
                            )
                        )
        reputation = actor.get("faction_reputation", {})
        if not isinstance(reputation, dict):
            issues.append(ValidationIssue(f"{path}/faction_reputation", "must be an object"))
        else:
            for faction_id, value in reputation.items():
                reputation_path = f"{path}/faction_reputation/{faction_id}"
                if faction_id not in factions:
                    issues.append(
                        ValidationIssue(reputation_path, f"unknown faction: {faction_id}")
                    )
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not -100 <= value <= 100
                ):
                    issues.append(ValidationIssue(reputation_path, "must be in -100..100"))
        actor_needs = actor.get("needs", {})
        if not isinstance(actor_needs, dict):
            issues.append(ValidationIssue(f"{path}/needs", "must be an object"))
        else:
            for need_id, value in actor_needs.items():
                if need_id not in needs:
                    issues.append(ValidationIssue(f"{path}/needs/{need_id}", "unknown need"))
                if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
                    issues.append(ValidationIssue(f"{path}/needs/{need_id}", "must be in 0..100"))
        goal_ids = actor.get("goal_ids", [])
        if not isinstance(goal_ids, list):
            issues.append(ValidationIssue(f"{path}/goal_ids", "must be a list"))
        else:
            for goal_id in goal_ids:
                if not isinstance(goal_id, str) or goal_id not in goals:
                    issues.append(ValidationIssue(f"{path}/goal_ids", f"unknown goal: {goal_id}"))
                elif goals[goal_id].get("parent_id") is not None:
                    issues.append(
                        ValidationIssue(
                            f"{path}/goal_ids",
                            f"goal is not a hierarchy root: {goal_id}",
                        )
                    )

    playable_actors = [actor for actor in actors.values() if actor.get("playable") is True]
    if profile == "release" and not playable_actors:
        issues.append(ValidationIssue("actors", "at least one playable actor is required"))
    expected_playable = policy.get("exact_playable_actor_count")
    if expected_playable is not None and profile == "release":
        if not isinstance(expected_playable, int) or expected_playable < 1:
            issues.append(
                ValidationIssue(
                    "world/content_policy/exact_playable_actor_count",
                    "must be a positive integer",
                )
            )
        elif len(playable_actors) != expected_playable:
            issues.append(
                ValidationIssue(
                    "actors",
                    f"expected {expected_playable} playable actors, found {len(playable_actors)}",
                )
            )
    if profile == "release" and policy.get("playable_requires_personal_arc") is True:
        for actor in playable_actors:
            if "personal_arc_id" not in actor:
                issues.append(
                    ValidationIssue(
                        f"actors/{actor['id']}/personal_arc_id",
                        "project requires a personal arc for every playable actor",
                    )
                )

    for ability_id, ability in abilities.items():
        path = f"abilities/{ability_id}"
        issues.extend(
            _require(
                ability,
                ("display_name", "target", "range", "costs", "cooldown_minutes", "effects"),
                path,
            )
        )
        if ability.get("target") not in {"self", "actor"}:
            issues.append(ValidationIssue(f"{path}/target", "must be self or actor"))
        ability_range = ability.get("range")
        if (
            not isinstance(ability_range, int)
            or isinstance(ability_range, bool)
            or ability_range < 0
        ):
            issues.append(ValidationIssue(f"{path}/range", "must be a non-negative integer"))
        cooldown = ability.get("cooldown_minutes")
        if not isinstance(cooldown, int) or isinstance(cooldown, bool) or cooldown < 0:
            issues.append(
                ValidationIssue(f"{path}/cooldown_minutes", "must be a non-negative integer")
            )
        costs = ability.get("costs")
        if not isinstance(costs, dict) or not costs:
            issues.append(
                ValidationIssue(f"{path}/costs", "must contain at least one resource cost")
            )
        else:
            for resource_id, amount in costs.items():
                issues.extend(_valid_id(resource_id, f"{path}/costs/{resource_id}"))
                if resource_definitions and resource_id not in resource_definitions:
                    issues.append(
                        ValidationIssue(
                            f"{path}/costs/{resource_id}",
                            f"unknown resource: {resource_id}",
                        )
                    )
                if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                    issues.append(
                        ValidationIssue(f"{path}/costs/{resource_id}", "must be a positive integer")
                    )
        issues.extend(
            _validate_effects(
                ability.get("effects"),
                f"{path}/effects",
                facts=facts,
                actors=actors,
                factions=factions,
                resources=resource_definitions,
                needs=needs,
                stockpiles=stockpiles,
            )
        )

    for actor_id, actor in actors.items():
        resources = actor.get("resources", {})
        if not isinstance(resources, dict):
            continue
        ability_ids = actor.get("ability_ids", [])
        if not isinstance(ability_ids, list):
            continue
        for ability_id in ability_ids:
            ability = abilities.get(ability_id) if isinstance(ability_id, str) else None
            if ability is None or not isinstance(ability.get("costs"), dict):
                continue
            for resource_id in ability["costs"]:
                if resource_id not in resources:
                    issues.append(
                        ValidationIssue(
                            f"actors/{actor_id}/resources/{resource_id}",
                            f"resource required by ability: {ability_id}",
                        )
                    )

    campaign_owners: dict[str, str] = {}
    for arc_id, arc in arcs.items():
        path = f"personal_arcs/{arc_id}"
        issues.extend(_require(arc, ("actor_id", "start_act_id", "acts"), path))
        actor_id = arc.get("actor_id")
        actor_id_issues = _valid_id(actor_id, f"{path}/actor_id")
        issues.extend(actor_id_issues)
        if actor_id_issues or actor_id not in actors:
            issues.append(ValidationIssue(f"{path}/actor_id", f"unknown actor: {actor_id}"))
        else:
            if actors[actor_id].get("playable") is not True:
                issues.append(
                    ValidationIssue(f"{path}/actor_id", "campaign owner must be playable")
                )
            if actors[actor_id].get("personal_arc_id") != arc_id:
                issues.append(
                    ValidationIssue(f"{path}/actor_id", "actor does not reference this arc")
                )
            if actor_id in campaign_owners:
                issues.append(
                    ValidationIssue(
                        f"{path}/actor_id",
                        f"actor already owns campaign: {campaign_owners[actor_id]}",
                    )
                )
            campaign_owners[actor_id] = arc_id
        start_act_id = arc.get("start_act_id")
        issues.extend(_valid_id(start_act_id, f"{path}/start_act_id"))
        acts = arc.get("acts")
        if not isinstance(acts, list) or not acts:
            issues.append(ValidationIssue(f"{path}/acts", "must contain at least one act"))
            continue
        act_index: dict[str, dict[str, Any]] = {}
        for position, act in enumerate(acts):
            act_path = f"{path}/acts/{position}"
            if not isinstance(act, dict):
                issues.append(ValidationIssue(act_path, "must be an object"))
                continue
            issues.extend(_require(act, ("id",), act_path))
            act_id = act.get("id")
            act_id_issues = _valid_id(act_id, f"{act_path}/id")
            issues.extend(act_id_issues)
            if act_id_issues:
                continue
            if act_id in act_index:
                issues.append(ValidationIssue(f"{act_path}/id", f"duplicate act ID: {act_id}"))
            act_index[act_id] = act
            for field, references in (
                ("quest_ids", quests),
                ("scene_ids", scenes),
                ("next_act_ids", None),
            ):
                values = act.get(field, [])
                if not isinstance(values, list):
                    issues.append(ValidationIssue(f"{act_path}/{field}", "must be a list"))
                    continue
                valid_values: list[str] = []
                for ref_position, reference in enumerate(values):
                    reference_issues = _valid_id(reference, f"{act_path}/{field}/{ref_position}")
                    issues.extend(reference_issues)
                    if reference_issues:
                        continue
                    valid_values.append(reference)
                    if references is not None and reference not in references:
                        issues.append(
                            ValidationIssue(
                                f"{act_path}/{field}/{ref_position}",
                                f"unknown reference: {reference}",
                            )
                        )
                if len(set(valid_values)) != len(valid_values):
                    issues.append(
                        ValidationIssue(f"{act_path}/{field}", "contains duplicate references")
                    )
        if isinstance(start_act_id, str) and start_act_id not in act_index:
            issues.append(ValidationIssue(f"{path}/start_act_id", "unknown start act"))
        for act_id, act in act_index.items():
            next_act_ids = act.get("next_act_ids", [])
            if not isinstance(next_act_ids, list):
                continue
            for position, next_act_id in enumerate(next_act_ids):
                if isinstance(next_act_id, str) and next_act_id not in act_index:
                    issues.append(
                        ValidationIssue(
                            f"{path}/acts/{act_id}/next_act_ids/{position}",
                            f"unknown act: {next_act_id}",
                        )
                    )
        if isinstance(start_act_id, str) and start_act_id in act_index:
            reachable_act_ids: set[str] = set()
            pending_act_ids = [start_act_id]
            while pending_act_ids:
                current_act_id = pending_act_ids.pop()
                if current_act_id in reachable_act_ids:
                    continue
                reachable_act_ids.add(current_act_id)
                next_act_ids = act_index[current_act_id].get("next_act_ids", [])
                if not isinstance(next_act_ids, list):
                    continue
                pending_act_ids.extend(
                    next_act_id
                    for next_act_id in next_act_ids
                    if isinstance(next_act_id, str)
                    and next_act_id in act_index
                    and next_act_id not in reachable_act_ids
                )
            for unreachable_act_id in sorted(set(act_index) - reachable_act_ids):
                issues.append(
                    ValidationIssue(
                        f"{path}/acts/{unreachable_act_id}",
                        f"act is unreachable from start act: {start_act_id}",
                    )
                )

    for schedule_id, schedule in schedules.items():
        path = f"schedules/{schedule_id}"
        entries = schedule.get("entries")
        if not isinstance(entries, list) or not entries:
            issues.append(ValidationIssue(f"{path}/entries", "must contain schedule segments"))
            continue
        covered_minutes: set[int] = set()
        for index, entry in enumerate(entries):
            entry_path = f"{path}/entries/{index}"
            if not isinstance(entry, dict):
                issues.append(ValidationIssue(entry_path, "must be an object"))
                continue
            issues.extend(
                _require(
                    entry,
                    ("start_minute", "end_minute", "map_id", "x", "y", "activity"),
                    entry_path,
                )
            )
            for field in ("start_minute", "end_minute"):
                value = entry.get(field)
                maximum = 1439 if field == "start_minute" else 1440
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                    or value > maximum
                ):
                    issues.append(
                        ValidationIssue(f"{entry_path}/{field}", f"must be in 0..{maximum}")
                    )
            if entry.get("start_minute") == entry.get("end_minute"):
                issues.append(
                    ValidationIssue(entry_path, "schedule segment cannot have zero duration")
                )
            start = entry.get("start_minute")
            end = entry.get("end_minute")
            if (
                isinstance(start, int)
                and not isinstance(start, bool)
                and isinstance(end, int)
                and not isinstance(end, bool)
                and 0 <= start <= 1439
                and 0 <= end <= 1440
                and start != end
            ):
                minutes = (
                    set(range(start, end))
                    if start < end
                    else set(range(start, 1440)) | set(range(0, end))
                )
                if covered_minutes & minutes:
                    issues.append(ValidationIssue(entry_path, "schedule segments overlap"))
                covered_minutes.update(minutes)
            issues.extend(
                _validate_location(
                    entry,
                    entry_path,
                    maps,
                    tile_types,
                    require_walkable=True,
                )
            )
            fallbacks = entry.get("fallbacks", [])
            if not isinstance(fallbacks, list):
                issues.append(ValidationIssue(f"{entry_path}/fallbacks", "must be a list"))
            else:
                for fallback_index, fallback in enumerate(fallbacks):
                    issues.extend(
                        _validate_location(
                            fallback,
                            f"{entry_path}/fallbacks/{fallback_index}",
                            maps,
                            tile_types,
                            require_walkable=True,
                        )
                    )

    for actor_id, actor in actors.items():
        schedule_id = actor.get("schedule_id")
        schedule = schedules.get(schedule_id) if isinstance(schedule_id, str) else None
        spawn = actor.get("spawn")
        if schedule is None or not isinstance(spawn, dict):
            continue
        for entry in schedule.get("entries", []):
            if not isinstance(entry, dict):
                continue
            fallbacks = entry.get("fallbacks", [])
            destinations = [entry] + (list(fallbacks) if isinstance(fallbacks, list) else [])
            for destination in destinations:
                if isinstance(destination, dict) and destination.get("map_id") != spawn.get(
                    "map_id"
                ):
                    issues.append(
                        ValidationIssue(
                            f"actors/{actor_id}/schedule_id",
                            "M1 schedules cannot route an actor between maps",
                        )
                    )

    for interaction_id, interaction in interactions.items():
        path = f"interactions/{interaction_id}"
        issues.extend(
            _require(
                interaction,
                ("display_name", "prompt", "map_id", "x", "y", "range", "effects"),
                path,
            )
        )
        issues.extend(
            _validate_location(
                interaction,
                path,
                maps,
                tile_types,
                require_walkable=False,
            )
        )
        interaction_range = interaction.get("range")
        if (
            not isinstance(interaction_range, int)
            or isinstance(interaction_range, bool)
            or interaction_range < 0
        ):
            issues.append(ValidationIssue(f"{path}/range", "must be a non-negative integer"))
        for field in ("required_flags", "forbidden_flags"):
            values = interaction.get(field, [])
            if not isinstance(values, list):
                issues.append(ValidationIssue(f"{path}/{field}", "must be a list"))
            else:
                for index, flag in enumerate(values):
                    issues.extend(_valid_id(flag, f"{path}/{field}/{index}"))
        if "repeatable" in interaction and not isinstance(interaction["repeatable"], bool):
            issues.append(ValidationIssue(f"{path}/repeatable", "must be a boolean"))
        issues.extend(
            _validate_effects(
                interaction.get("effects"),
                f"{path}/effects",
                facts=facts,
                actors=actors,
                factions=factions,
                resources=resource_definitions,
                needs=needs,
                stockpiles=stockpiles,
            )
        )

    condition_indexes = {
        "facts": facts,
        "actors": actors,
        "factions": factions,
        "quests": quests,
        "maps": maps,
        "resources": resource_definitions,
        "needs": needs,
        "stockpiles": stockpiles,
        "constructions": constructions,
    }
    effect_indexes = {
        "facts": facts,
        "actors": actors,
        "factions": factions,
        "resources": resource_definitions,
        "needs": needs,
        "stockpiles": stockpiles,
    }

    for dialogue_id, dialogue in dialogues.items():
        path = f"dialogues/{dialogue_id}"
        issues.extend(
            _require(
                dialogue,
                ("display_name", "actor_id", "range", "start_node_id", "nodes"),
                path,
            )
        )
        if (
            not isinstance(dialogue.get("display_name"), str)
            or not dialogue.get("display_name", "").strip()
        ):
            issues.append(ValidationIssue(f"{path}/display_name", "must contain a display name"))
        if dialogue.get("actor_id") not in actors:
            issues.append(
                ValidationIssue(f"{path}/actor_id", f"unknown actor: {dialogue.get('actor_id')}")
            )
        dialogue_range = dialogue.get("range")
        if (
            not isinstance(dialogue_range, int)
            or isinstance(dialogue_range, bool)
            or dialogue_range < 0
        ):
            issues.append(ValidationIssue(f"{path}/range", "must be a non-negative integer"))
        issues.extend(
            _validate_conditions(
                dialogue.get("conditions", []), f"{path}/conditions", **condition_indexes
            )
        )
        nodes = dialogue.get("nodes")
        if not isinstance(nodes, list) or not nodes:
            issues.append(ValidationIssue(f"{path}/nodes", "must contain nodes"))
            continue
        node_ids: set[str] = set()
        for position, node in enumerate(nodes):
            node_path = f"{path}/nodes/{position}"
            if not isinstance(node, dict):
                issues.append(ValidationIssue(node_path, "must be an object"))
                continue
            node_id = node.get("id")
            issues.extend(_valid_id(node_id, f"{node_path}/id"))
            if isinstance(node_id, str):
                if node_id in node_ids:
                    issues.append(ValidationIssue(f"{node_path}/id", f"duplicate ID: {node_id}"))
                node_ids.add(node_id)
        if dialogue.get("start_node_id") not in node_ids:
            issues.append(
                ValidationIssue(
                    f"{path}/start_node_id",
                    f"unknown node: {dialogue.get('start_node_id')}",
                )
            )
        for position, node in enumerate(nodes):
            if not isinstance(node, dict):
                continue
            node_path = f"{path}/nodes/{position}"
            issues.extend(_require(node, ("id", "speaker_id", "text", "choices"), node_path))
            if node.get("speaker_id") not in actors:
                issues.append(
                    ValidationIssue(
                        f"{node_path}/speaker_id",
                        f"unknown actor: {node.get('speaker_id')}",
                    )
                )
            if not isinstance(node.get("text"), str) or not node.get("text", "").strip():
                issues.append(ValidationIssue(f"{node_path}/text", "must contain text"))
            if "allow_exit" in node and not isinstance(node["allow_exit"], bool):
                issues.append(ValidationIssue(f"{node_path}/allow_exit", "must be a boolean"))
            fact_refs = node.get("fact_refs", [])
            if not isinstance(fact_refs, list):
                issues.append(ValidationIssue(f"{node_path}/fact_refs", "must be a list"))
            else:
                for fact_id in fact_refs:
                    if fact_id not in facts:
                        issues.append(
                            ValidationIssue(f"{node_path}/fact_refs", f"unknown fact: {fact_id}")
                        )
            issues.extend(
                _validate_effects(
                    node.get("on_enter", []),
                    f"{node_path}/on_enter",
                    allow_empty=True,
                    **effect_indexes,
                )
            )
            choices = node.get("choices")
            if not isinstance(choices, list):
                issues.append(ValidationIssue(f"{node_path}/choices", "must be a list"))
                continue
            choice_ids: set[str] = set()
            for choice_position, choice in enumerate(choices):
                choice_path = f"{node_path}/choices/{choice_position}"
                if not isinstance(choice, dict):
                    issues.append(ValidationIssue(choice_path, "must be an object"))
                    continue
                issues.extend(_require(choice, ("id", "text"), choice_path))
                if not isinstance(choice.get("text"), str) or not choice.get("text", "").strip():
                    issues.append(ValidationIssue(f"{choice_path}/text", "must contain text"))
                choice_id = choice.get("id")
                issues.extend(_valid_id(choice_id, f"{choice_path}/id"))
                if isinstance(choice_id, str):
                    if choice_id in choice_ids:
                        issues.append(
                            ValidationIssue(f"{choice_path}/id", f"duplicate ID: {choice_id}")
                        )
                    choice_ids.add(choice_id)
                target = choice.get("next_node_id")
                if target is not None and target not in node_ids:
                    issues.append(
                        ValidationIssue(f"{choice_path}/next_node_id", f"unknown node: {target}")
                    )
                issues.extend(
                    _validate_conditions(
                        choice.get("conditions", []),
                        f"{choice_path}/conditions",
                        **condition_indexes,
                    )
                )
                issues.extend(
                    _validate_effects(
                        choice.get("effects", []),
                        f"{choice_path}/effects",
                        allow_empty=True,
                        **effect_indexes,
                    )
                )

    for quest_id, quest in quests.items():
        path = f"quests/{quest_id}"
        issues.extend(_require(quest, ("title", "start_stage_id", "stages"), path))
        if not isinstance(quest.get("title"), str) or not quest.get("title", "").strip():
            issues.append(ValidationIssue(f"{path}/title", "must contain a title"))
        issues.extend(
            _validate_conditions(
                quest.get("auto_start_conditions", []),
                f"{path}/auto_start_conditions",
                **condition_indexes,
            )
        )
        stages = quest.get("stages")
        if not isinstance(stages, list) or not stages:
            issues.append(ValidationIssue(f"{path}/stages", "must contain stages"))
            continue
        stage_ids: set[str] = set()
        for position, stage in enumerate(stages):
            if not isinstance(stage, dict) or not isinstance(stage.get("id"), str):
                continue
            stage_id = stage["id"]
            if stage_id in stage_ids:
                issues.append(
                    ValidationIssue(f"{path}/stages/{position}/id", f"duplicate ID: {stage_id}")
                )
            stage_ids.add(stage_id)
        if quest.get("start_stage_id") not in stage_ids:
            issues.append(
                ValidationIssue(
                    f"{path}/start_stage_id",
                    f"unknown stage: {quest.get('start_stage_id')}",
                )
            )
        for position, stage in enumerate(stages):
            stage_path = f"{path}/stages/{position}"
            if not isinstance(stage, dict):
                issues.append(ValidationIssue(stage_path, "must be an object"))
                continue
            issues.extend(
                _require(
                    stage,
                    ("id", "description", "completion_conditions"),
                    stage_path,
                )
            )
            if (
                not isinstance(stage.get("description"), str)
                or not stage.get("description", "").strip()
            ):
                issues.append(
                    ValidationIssue(f"{stage_path}/description", "must contain a description")
                )
            issues.extend(_valid_id(stage.get("id"), f"{stage_path}/id"))
            target = stage.get("next_stage_id")
            if target is not None and target not in stage_ids:
                issues.append(
                    ValidationIssue(f"{stage_path}/next_stage_id", f"unknown stage: {target}")
                )
            for field in ("completion_conditions", "failure_conditions"):
                issues.extend(
                    _validate_conditions(
                        stage.get(field, []), f"{stage_path}/{field}", **condition_indexes
                    )
                )
            for field in ("on_complete", "on_fail"):
                issues.extend(
                    _validate_effects(
                        stage.get(field, []),
                        f"{stage_path}/{field}",
                        allow_empty=True,
                        **effect_indexes,
                    )
                )

    for scene_id, scene in scenes.items():
        path = f"scenes/{scene_id}"
        issues.extend(_require(scene, ("title", "text", "start_minute", "end_minute"), path))
        for field in ("title", "text"):
            if not isinstance(scene.get(field), str) or not scene.get(field, "").strip():
                issues.append(ValidationIssue(f"{path}/{field}", "must contain text"))
        for field, maximum in (("start_minute", 1439), ("end_minute", 1440)):
            value = scene.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
                issues.append(ValidationIssue(f"{path}/{field}", f"must be in 0..{maximum}"))
        if scene.get("start_minute") == scene.get("end_minute"):
            issues.append(ValidationIssue(path, "scene time window cannot be empty"))
        if "once" in scene and not isinstance(scene["once"], bool):
            issues.append(ValidationIssue(f"{path}/once", "must be a boolean"))
        priority = scene.get("priority", 0)
        if not isinstance(priority, int) or isinstance(priority, bool):
            issues.append(ValidationIssue(f"{path}/priority", "must be an integer"))
        issues.extend(
            _validate_conditions(
                scene.get("conditions", []), f"{path}/conditions", **condition_indexes
            )
        )
        issues.extend(
            _validate_effects(
                scene.get("effects", []),
                f"{path}/effects",
                allow_empty=True,
                **effect_indexes,
            )
        )

    def validate_resource_amounts(
        value: Any, path: str, *, allow_empty: bool
    ) -> list[ValidationIssue]:
        result: list[ValidationIssue] = []
        if not isinstance(value, dict) or (not value and not allow_empty):
            return [ValidationIssue(path, "must be an object with resource amounts")]
        for resource_id, amount in value.items():
            if resource_id not in resource_definitions:
                result.append(
                    ValidationIssue(f"{path}/{resource_id}", f"unknown resource: {resource_id}")
                )
            if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
                result.append(
                    ValidationIssue(f"{path}/{resource_id}", "must be a non-negative integer")
                )
        return result

    for resource_id, resource in resource_definitions.items():
        path = f"resources/{resource_id}"
        issues.extend(_require(resource, ("display_name", "base_value", "scarcity_target"), path))
        if (
            not isinstance(resource.get("display_name"), str)
            or not resource.get("display_name", "").strip()
        ):
            issues.append(ValidationIssue(f"{path}/display_name", "must contain a display name"))
        for field in ("base_value", "scarcity_target"):
            value = resource.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                issues.append(ValidationIssue(f"{path}/{field}", "must be a non-negative integer"))

    for need_id, need in needs.items():
        path = f"needs/{need_id}"
        issues.extend(
            _require(
                need,
                (
                    "display_name",
                    "decay_interval_minutes",
                    "decay_amount",
                    "critical_below",
                    "resource_id",
                    "consume_amount",
                    "restore_amount",
                ),
                path,
            )
        )
        if (
            not isinstance(need.get("display_name"), str)
            or not need.get("display_name", "").strip()
        ):
            issues.append(ValidationIssue(f"{path}/display_name", "must contain a display name"))
        if (
            not isinstance(need.get("resource_id"), str)
            or need.get("resource_id") not in resource_definitions
        ):
            issues.append(
                ValidationIssue(
                    f"{path}/resource_id", f"unknown resource: {need.get('resource_id')}"
                )
            )
        for field in (
            "decay_interval_minutes",
            "decay_amount",
            "consume_amount",
            "restore_amount",
        ):
            value = need.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                issues.append(ValidationIssue(f"{path}/{field}", "must be a positive integer"))
        critical = need.get("critical_below")
        if not isinstance(critical, int) or isinstance(critical, bool) or not 0 <= critical <= 100:
            issues.append(ValidationIssue(f"{path}/critical_below", "must be in 0..100"))

    for stockpile_id, stockpile in stockpiles.items():
        path = f"stockpiles/{stockpile_id}"
        issues.extend(_require(stockpile, ("display_name", "location", "capacity"), path))
        if (
            not isinstance(stockpile.get("display_name"), str)
            or not stockpile.get("display_name", "").strip()
        ):
            issues.append(ValidationIssue(f"{path}/display_name", "must contain a display name"))
        issues.extend(
            _validate_location(
                stockpile.get("location"),
                f"{path}/location",
                maps,
                tile_types,
                require_walkable=True,
            )
        )
        capacity = stockpile.get("capacity")
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            issues.append(ValidationIssue(f"{path}/capacity", "must be a positive integer"))
        issues.extend(
            validate_resource_amounts(
                stockpile.get("resources", {}),
                f"{path}/resources",
                allow_empty=True,
            )
        )
        contents = stockpile.get("resources", {})
        if (
            isinstance(capacity, int)
            and isinstance(contents, dict)
            and all(isinstance(value, int) for value in contents.values())
            and sum(contents.values()) > capacity
        ):
            issues.append(ValidationIssue(f"{path}/resources", "exceeds stockpile capacity"))

    for construction_id, construction in constructions.items():
        path = f"constructions/{construction_id}"
        issues.extend(
            _require(
                construction,
                ("display_name", "footprint", "costs", "build_minutes", "blocks_movement"),
                path,
            )
        )
        if (
            not isinstance(construction.get("display_name"), str)
            or not construction.get("display_name", "").strip()
        ):
            issues.append(ValidationIssue(f"{path}/display_name", "must contain a display name"))
        footprint = construction.get("footprint")
        if (
            not isinstance(footprint, list)
            or not footprint
            or any(
                not isinstance(cell, list)
                or len(cell) != 2
                or any(not isinstance(value, int) or isinstance(value, bool) for value in cell)
                for cell in footprint
            )
        ):
            issues.append(ValidationIssue(f"{path}/footprint", "must contain [x, y] offsets"))
        elif [0, 0] not in footprint or len({tuple(cell) for cell in footprint}) != len(footprint):
            issues.append(
                ValidationIssue(f"{path}/footprint", "must include [0, 0] without duplicates")
            )
        issues.extend(
            validate_resource_amounts(construction.get("costs"), f"{path}/costs", allow_empty=False)
        )
        if isinstance(construction.get("costs"), dict) and any(
            value == 0 for value in construction["costs"].values()
        ):
            issues.append(ValidationIssue(f"{path}/costs", "construction costs must be positive"))
        build_minutes = construction.get("build_minutes")
        if (
            not isinstance(build_minutes, int)
            or isinstance(build_minutes, bool)
            or build_minutes < 1
        ):
            issues.append(ValidationIssue(f"{path}/build_minutes", "must be a positive integer"))
        if not isinstance(construction.get("blocks_movement"), bool):
            issues.append(ValidationIssue(f"{path}/blocks_movement", "must be a boolean"))
        stockpile_id = construction.get("stockpile_id")
        if stockpile_id is not None and (
            not isinstance(stockpile_id, str) or stockpile_id not in stockpiles
        ):
            issues.append(
                ValidationIssue(f"{path}/stockpile_id", f"unknown stockpile: {stockpile_id}")
            )

    for recipe_id, recipe in recipes.items():
        path = f"production_recipes/{recipe_id}"
        issues.extend(
            _require(
                recipe,
                (
                    "display_name",
                    "required_construction_id",
                    "inputs",
                    "outputs",
                    "duration_minutes",
                ),
                path,
            )
        )
        if (
            not isinstance(recipe.get("display_name"), str)
            or not recipe.get("display_name", "").strip()
        ):
            issues.append(ValidationIssue(f"{path}/display_name", "must contain a display name"))
        if (
            not isinstance(recipe.get("required_construction_id"), str)
            or recipe.get("required_construction_id") not in constructions
        ):
            issues.append(
                ValidationIssue(
                    f"{path}/required_construction_id",
                    f"unknown construction: {recipe.get('required_construction_id')}",
                )
            )
        for field in ("inputs", "outputs"):
            issues.extend(
                validate_resource_amounts(recipe.get(field), f"{path}/{field}", allow_empty=False)
            )
            if isinstance(recipe.get(field), dict) and any(
                value == 0 for value in recipe[field].values()
            ):
                issues.append(ValidationIssue(f"{path}/{field}", "amounts must be positive"))
        duration = recipe.get("duration_minutes")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 1:
            issues.append(ValidationIssue(f"{path}/duration_minutes", "must be positive"))

    for goal_id, goal in goals.items():
        path = f"goals/{goal_id}"
        issues.extend(_require(goal, ("display_name", "priority", "conditions", "action"), path))
        if (
            not isinstance(goal.get("display_name"), str)
            or not goal.get("display_name", "").strip()
        ):
            issues.append(ValidationIssue(f"{path}/display_name", "must contain a display name"))
        parent_id = goal.get("parent_id")
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in goals):
            issues.append(ValidationIssue(f"{path}/parent_id", f"unknown goal: {parent_id}"))
        priority = goal.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool):
            issues.append(ValidationIssue(f"{path}/priority", "must be an integer"))
        issues.extend(
            _validate_conditions(goal.get("conditions"), f"{path}/conditions", **condition_indexes)
        )
        action = goal.get("action")
        if action is None:
            continue
        if not isinstance(action, dict):
            issues.append(ValidationIssue(f"{path}/action", "must be an object or null"))
            continue
        kind = action.get("kind")
        if kind not in {"satisfy_need", "produce", "build", "travel"}:
            issues.append(ValidationIssue(f"{path}/action/kind", f"unsupported action: {kind}"))
        if kind == "satisfy_need" and (
            not isinstance(action.get("need_id"), str) or action.get("need_id") not in needs
        ):
            issues.append(ValidationIssue(f"{path}/action/need_id", "unknown need"))
        if kind == "produce" and (
            not isinstance(action.get("recipe_id"), str) or action.get("recipe_id") not in recipes
        ):
            issues.append(ValidationIssue(f"{path}/action/recipe_id", "unknown recipe"))
        if kind == "build" and (
            not isinstance(action.get("blueprint_id"), str)
            or action.get("blueprint_id") not in constructions
        ):
            issues.append(ValidationIssue(f"{path}/action/blueprint_id", "unknown construction"))
        if action.get("stockpile_id") is not None and (
            not isinstance(action.get("stockpile_id"), str)
            or action.get("stockpile_id") not in stockpiles
        ):
            issues.append(ValidationIssue(f"{path}/action/stockpile_id", "unknown stockpile"))
        if kind in {"build", "travel"}:
            issues.extend(
                _validate_location(
                    action.get("location"),
                    f"{path}/action/location",
                    maps,
                    tile_types,
                    require_walkable=True,
                )
            )

    for goal_id in goals:
        seen: set[str] = set()
        cursor: str | None = goal_id
        while cursor is not None and cursor in goals:
            if cursor in seen:
                issues.append(ValidationIssue(f"goals/{goal_id}/parent_id", "goal cycle"))
                break
            seen.add(cursor)
            parent = goals[cursor].get("parent_id")
            cursor = parent if isinstance(parent, str) else None

    for consequence_id, consequence in consequences.items():
        path = f"consequences/{consequence_id}"
        issues.extend(_require(consequence, ("delay_minutes", "trigger_event", "effects"), path))
        delay = consequence.get("delay_minutes")
        if not isinstance(delay, int) or isinstance(delay, bool) or delay < 1:
            issues.append(ValidationIssue(f"{path}/delay_minutes", "must be positive"))
        issues.extend(_valid_id(consequence.get("trigger_event"), f"{path}/trigger_event"))
        if consequence.get("subject_id") is not None:
            issues.extend(_valid_id(consequence.get("subject_id"), f"{path}/subject_id"))
        if "once" in consequence and not isinstance(consequence["once"], bool):
            issues.append(ValidationIssue(f"{path}/once", "must be a boolean"))
        issues.extend(
            _validate_conditions(
                consequence.get("conditions", []), f"{path}/conditions", **condition_indexes
            )
        )
        issues.extend(
            _validate_effects(consequence.get("effects"), f"{path}/effects", **effect_indexes)
        )

    if resource_definitions:
        for actor_id, actor in actors.items():
            resources = actor.get("resources", {})
            if isinstance(resources, dict):
                for resource_id in resources:
                    if resource_id not in resource_definitions:
                        issues.append(
                            ValidationIssue(
                                f"actors/{actor_id}/resources/{resource_id}",
                                f"unknown resource: {resource_id}",
                            )
                        )

    for path, value in _walk_strings(world, "world"):
        if PLACEHOLDER_PATTERN.search(value):
            issues.append(ValidationIssue(path, "unresolved placeholder"))
    for collection, items in project.collections.items():
        for index, item in enumerate(items):
            for path, value in _walk_strings(item, f"collections/{collection}/{index}"):
                if PLACEHOLDER_PATTERN.search(value):
                    issues.append(ValidationIssue(path, "unresolved placeholder"))

    return issues
