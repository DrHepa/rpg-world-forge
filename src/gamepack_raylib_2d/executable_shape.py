"""Code-owned executable-shape checks for the bounded raylib adapters."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

ADAPTER_EXECUTABLE_SHAPE_UNSUPPORTED = "adapter_executable_shape_unsupported"

_PUZZLE_ADAPTER_ID = "gamepack_raylib_2d_puzzle"
_NARRATIVE_ADAPTER_ID = "gamepack_raylib_2d_text"
_CURSOR_ID = "wf_internal_narrative_cursor"


class AdapterExecutableShapeError(ValueError):
    """Raised when authored data exceeds a concrete controller's surface."""

    def __init__(self, detail: str) -> None:
        self.reason_code = ADAPTER_EXECUTABLE_SHAPE_UNSUPPORTED
        self.detail = detail
        super().__init__(f"{self.reason_code}: {detail}")


@dataclass(frozen=True, slots=True)
class AdapterExecutableShape:
    controller_kind: str
    narrative_action_ids: dict[tuple[str, str], str]


def _unsupported(detail: str) -> None:
    raise AdapterExecutableShapeError(detail)


def _mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _unsupported(f"{context} must be an object")
    return value


def _exact_list(value: object, context: str) -> list[object]:
    if type(value) is not list:
        _unsupported(f"{context} must be an exact array")
    return value


def _identifier(value: object, context: str) -> str:
    if type(value) is not str or not value:
        _unsupported(f"{context} must be a non-empty string")
    return value


def _records_by_id(value: object, context: str) -> dict[str, Mapping[str, object]]:
    records = _exact_list(value, context)
    indexed: dict[str, Mapping[str, object]] = {}
    for index, raw in enumerate(records):
        record = _mapping(raw, f"{context}/{index}")
        record_id = _identifier(record.get("id"), f"{context}/{index}.id")
        if record_id in indexed:
            _unsupported(f"{context} contains duplicate identity {record_id}")
        indexed[record_id] = record
    return indexed


def _require_state_contract(
    state: Mapping[str, object],
    *,
    state_type: str,
    mutability: str,
) -> None:
    if (
        state.get("type") != state_type
        or state.get("mutability") != mutability
        or state.get("persistence") != "saved"
    ):
        _unsupported(f"state {state.get('id')} is outside the executable state contract")


def _inspect_puzzle(gamepack: Mapping[str, object]) -> AdapterExecutableShape:
    logic = _mapping(gamepack.get("logic"), "gamepack.logic")
    modules = _mapping(gamepack.get("modules"), "gamepack.modules")
    if logic.get("narrative_cursor") is not None:
        _unsupported("puzzle executable shape cannot contain a narrative cursor")
    if logic.get("narrative_transitions") != []:
        _unsupported("puzzle executable shape cannot contain narrative transitions")
    if modules.get("narrative") != []:
        _unsupported("puzzle executable shape cannot contain narrative modules")

    states = _records_by_id(logic.get("state_schema"), "gamepack.logic.state_schema")
    if set(states) != {"board", "move_count", "target"}:
        _unsupported("puzzle state inventory must be exactly board, move_count, and target")
    _require_state_contract(states["board"], state_type="string_array", mutability="mutable")
    _require_state_contract(states["target"], state_type="string_array", mutability="constant")
    _require_state_contract(states["move_count"], state_type="integer", mutability="mutable")
    for state_id in ("board", "target"):
        state = states[state_id]
        initial = state.get("initial")
        if (
            type(initial) is not list
            or len(initial) != 3
            or any(type(item) is not str for item in initial)
            or state.get("min_items") != 3
            or state.get("max_items") != 3
        ):
            _unsupported(f"puzzle {state_id} must contain exactly three string entries")
    move_count = states["move_count"]
    if (
        type(move_count.get("initial")) is not int
        or move_count.get("initial") != 0
        or type(move_count.get("minimum")) is not int
        or move_count.get("minimum") != 0
        or type(move_count.get("maximum")) is not int
        or move_count["maximum"] < 1
    ):
        _unsupported("puzzle move_count bounds are outside the controller contract")
    initial_state = _mapping(logic.get("initial_state"), "gamepack.logic.initial_state")
    if set(initial_state) != set(states) or any(
        initial_state.get(state_id) != states[state_id].get("initial") for state_id in states
    ):
        _unsupported("puzzle initial state does not match the executable state inventory")

    actions = _records_by_id(logic.get("actions"), "gamepack.logic.actions")
    if set(actions) != {"restart_board", "swap_tiles"}:
        _unsupported("puzzle action inventory must be exactly restart_board and swap_tiles")
    if actions["restart_board"].get("parameters") != []:
        _unsupported("restart_board must be parameterless")
    if actions["swap_tiles"].get("parameters") != [
        {"id": "first_index", "maximum": 2, "minimum": 0, "type": "integer"},
        {"id": "second_index", "maximum": 2, "minimum": 0, "type": "integer"},
    ]:
        _unsupported("swap_tiles must expose exact integer indices from zero through two")
    return AdapterExecutableShape("puzzle", {})


def _inspect_narrative(gamepack: Mapping[str, object]) -> AdapterExecutableShape:
    localization = _mapping(gamepack.get("localization"), "gamepack.localization")
    if localization.get("source_locale") != "en" or localization.get("supported_locales") != ["en"]:
        _unsupported("narrative executable shape supports exact English localization only")

    logic = _mapping(gamepack.get("logic"), "gamepack.logic")
    modules = _mapping(gamepack.get("modules"), "gamepack.modules")
    narrative_modules = _exact_list(modules.get("narrative"), "gamepack.modules.narrative")
    if len(narrative_modules) != 1:
        _unsupported("narrative executable shape requires exactly one narrative module")
    narrative_module = _mapping(narrative_modules[0], "gamepack.modules.narrative/0")

    cursor = _mapping(logic.get("narrative_cursor"), "gamepack.logic.narrative_cursor")
    if (
        cursor.get("id") != _CURSOR_ID
        or cursor.get("compiler_owned") is not True
        or cursor.get("type") != "string"
        or cursor.get("mutability") != "mutable"
        or cursor.get("persistence") != "saved"
    ):
        _unsupported("narrative executable shape requires the exact compiler-owned cursor")
    states = _records_by_id(logic.get("state_schema"), "gamepack.logic.state_schema")
    cursor_state = states.get(_CURSOR_ID)
    if cursor_state is None or dict(cursor_state) != dict(cursor):
        _unsupported("narrative cursor must be present in the executable state schema")
    knowledge = states.get("knowledge")
    if knowledge is None:
        _unsupported("narrative executable shape requires knowledge state")
    _require_state_contract(knowledge, state_type="string_array", mutability="mutable")
    if type(knowledge.get("initial")) is not list:
        _unsupported("narrative knowledge initial value must be a string array")
    initial_state = _mapping(logic.get("initial_state"), "gamepack.logic.initial_state")
    if initial_state.get("knowledge") != knowledge.get("initial"):
        _unsupported("narrative knowledge state is not initialized exactly")

    units = _records_by_id(narrative_module.get("units"), "narrative module units")
    entries = _exact_list(narrative_module.get("entry_unit_ids"), "narrative entry units")
    entry_ids = [_identifier(item, "narrative entry unit") for item in entries]
    if not entry_ids or len(set(entry_ids)) != len(entry_ids):
        _unsupported("narrative entry units must be non-empty and unique")
    option_targets: dict[tuple[str, str], str] = {}
    adjacency: dict[str, set[str]] = {}
    for unit_id, unit in units.items():
        unit_type = unit.get("unit_type")
        if unit_type == "choice":
            options = _exact_list(unit.get("options"), f"narrative unit {unit_id}.options")
            if len(options) != 2:
                _unsupported("every narrative choice must expose exactly two options")
            targets: set[str] = set()
            for option_index, raw_option in enumerate(options):
                option = _mapping(raw_option, f"narrative unit {unit_id}.options/{option_index}")
                option_id = _identifier(option.get("id"), "narrative option.id")
                _identifier(option.get("label"), "narrative option.label")
                target_id = _identifier(option.get("next_unit_id"), "narrative option.next_unit_id")
                key = (unit_id, option_id)
                if key in option_targets:
                    _unsupported("narrative option identity is duplicated")
                option_targets[key] = target_id
                targets.add(target_id)
            adjacency[unit_id] = targets
        elif unit_type == "ending":
            adjacency[unit_id] = set()
        else:
            _unsupported("reachable narrative units must be choices or endings only")

    reachable: set[str] = set()
    pending = list(entry_ids)
    while pending:
        unit_id = pending.pop()
        if unit_id in reachable:
            continue
        if unit_id not in units:
            _unsupported("narrative graph references an unknown unit")
        reachable.add(unit_id)
        pending.extend(adjacency[unit_id])
    if reachable != set(units):
        _unsupported("every narrative unit must be reachable from an authored entry")

    transitions = _exact_list(
        logic.get("narrative_transitions"),
        "gamepack.logic.narrative_transitions",
    )
    action_by_option: dict[tuple[str, str], str] = {}
    for index, raw_transition in enumerate(transitions):
        transition = _mapping(raw_transition, f"narrative transition/{index}")
        source_id = _identifier(transition.get("source_unit_id"), "transition.source_unit_id")
        option_id = _identifier(transition.get("option_id"), "transition.option_id")
        target_id = _identifier(transition.get("target_unit_id"), "transition.target_unit_id")
        action_id = _identifier(transition.get("action_id"), "transition.action_id")
        key = (source_id, option_id)
        if key in action_by_option:
            _unsupported("narrative transition binding is not bijective")
        if option_targets.get(key) != target_id:
            _unsupported("narrative transition does not bind an exact authored option")
        precondition = _mapping(transition.get("precondition"), "transition.precondition")
        effect = _mapping(transition.get("effect"), "transition.effect")
        if (
            transition.get("compiler_owned") is not True
            or precondition.get("compiler_owned") is not True
            or precondition.get("cursor_state_id") != _CURSOR_ID
            or precondition.get("operator") != "cursor_equals"
            or precondition.get("value") != source_id
            or effect.get("compiler_owned") is not True
            or effect.get("cursor_state_id") != _CURSOR_ID
            or effect.get("operation") != "set_cursor"
            or effect.get("value") != target_id
        ):
            _unsupported("narrative transition is not compiler-owned cursor dispatch")
        action_by_option[key] = action_id
    if set(action_by_option) != set(option_targets):
        _unsupported("narrative transitions must bind every authored option exactly once")
    if len(set(action_by_option.values())) != len(action_by_option):
        _unsupported("narrative transition action identities must be bijective")

    actions = _records_by_id(logic.get("actions"), "gamepack.logic.actions")
    if set(actions) != set(action_by_option.values()):
        _unsupported("narrative action inventory must be completely dispatchable")
    if any(action.get("parameters") != [] for action in actions.values()):
        _unsupported("narrative choice actions must be parameterless")
    return AdapterExecutableShape("narrative_text", action_by_option)


def inspect_adapter_executable_shape(
    gamepack: object,
    adapter_id: object,
) -> AdapterExecutableShape:
    """Validate the exact data surface executable by one code-owned controller."""

    checked_gamepack = _mapping(gamepack, "gamepack")
    if adapter_id == _PUZZLE_ADAPTER_ID:
        return _inspect_puzzle(checked_gamepack)
    if adapter_id == _NARRATIVE_ADAPTER_ID:
        return _inspect_narrative(checked_gamepack)
    _unsupported("adapter identity has no bounded executable-shape inspector")


__all__ = [
    "ADAPTER_EXECUTABLE_SHAPE_UNSUPPORTED",
    "AdapterExecutableShape",
    "AdapterExecutableShapeError",
    "inspect_adapter_executable_shape",
]
