"""Bounded deterministic execution semantics for ``world-forge.gamepack`` v1."""

from __future__ import annotations

import copy
import itertools
import math
from collections.abc import Iterable, Mapping
from types import MappingProxyType

from gamepack_runtime.contracts import (
    ANALYSIS_LIMITS,
    EXECUTION_SEMANTICS,
    MAX_SAFE_INTEGER,
    CandidateAction,
    GameLogicError,
    JsonValue,
    StateClassification,
    TransitionResult,
    _snapshot_event_sequence,
    _validate_exact_nfc_string,
    canonical_events_hash,
    canonical_state_hash,
    snapshot_legacy_action_parameters,
    snapshot_plain_json,
    snapshot_strict_candidate,
    snapshot_strict_state,
    validate_runtime_gamepack,
)

_MAPPING_PROXY_TYPE = type(MappingProxyType({}))


def _legacy_state_keys(
    state: Mapping[str, JsonValue],
    expected_keys: Mapping[str, object],
) -> list[str]:
    """Collect bounded legacy keys without hashing or comparing caller objects."""

    expected_count = len(expected_keys)
    try:
        if len(state) != expected_count:
            raise GameLogicError(
                "state_domain_invalid",
                "state keys do not exactly match schema",
            )
        iterator = iter(state)
        collected: list[object] = []
        for _index in range(expected_count + 1):
            try:
                collected.append(next(iterator))
            except StopIteration:
                break
    except GameLogicError:
        raise
    except Exception as exc:
        raise GameLogicError(
            "state_domain_invalid",
            "state keys could not be read safely",
        ) from exc
    if len(collected) != expected_count:
        raise GameLogicError(
            "state_domain_invalid",
            "state keys do not exactly match schema",
        )
    checked: list[str] = []
    for key in collected:
        try:
            checked.append(
                _validate_exact_nfc_string(
                    key,
                    maximum_codepoints=64,
                    reason_code="state_domain_invalid",
                    detail="state keys do not exactly match schema",
                )
            )
        except GameLogicError as exc:
            raise GameLogicError(
                "state_domain_invalid",
                "state keys do not exactly match schema",
            ) from exc
    if sorted(checked) != sorted(expected_keys):
        raise GameLogicError(
            "state_domain_invalid",
            "state keys do not exactly match schema",
        )
    return checked


def _owned_analysis_limits(value: object) -> dict[str, int]:
    if value is ANALYSIS_LIMITS:
        return dict(ANALYSIS_LIMITS)
    if type(value) is not dict:
        raise GameLogicError(
            "analysis_policy_invalid",
            "interpreter limits must be an exact object",
        )
    owned = snapshot_plain_json(
        value,
        maximum_bytes=int(ANALYSIS_LIMITS["state_bytes"]),
    )
    assert type(owned) is dict
    if frozenset(owned) != frozenset(ANALYSIS_LIMITS) or any(
        type(item) is not int or item < 1 for item in owned.values()
    ):
        raise GameLogicError(
            "analysis_policy_invalid",
            "interpreter limits must be the exact positive v1 limit map",
        )
    return owned  # type: ignore[return-value]


class GamepackInterpreter:
    def __init__(
        self,
        gamepack: Mapping[str, object],
        *,
        already_validated: bool = False,
        limits: Mapping[str, int] = ANALYSIS_LIMITS,
    ) -> None:
        if type(already_validated) is not bool:
            raise GameLogicError(
                "validation_policy_invalid",
                "already_validated must be an exact boolean",
            )
        self.limits = _owned_analysis_limits(limits)
        if already_validated:
            checked = snapshot_plain_json(gamepack)
            if type(checked) is not dict:
                raise GameLogicError(
                    "json_root_invalid",
                    "validated gamepack must be an exact object",
                )
            self.gamepack = checked
        else:
            self.gamepack = validate_runtime_gamepack(gamepack)
        logic = self.gamepack.get("logic")
        if type(logic) is not dict:
            raise GameLogicError("gamepack_logic_invalid", "logic must be an object")
        if logic.get("execution_semantics") != EXECUTION_SEMANTICS:
            raise GameLogicError(
                "execution_semantics_unsupported",
                "gamepack does not require the exact v1 execution policy",
            )
        self.logic = logic
        self.state_schema = self._index_records(logic.get("state_schema"), "state_schema")
        self.actions = self._index_records(logic.get("actions"), "actions")
        self.conditions = self._index_records(logic.get("conditions"), "conditions")
        self.effects = self._index_records(logic.get("effects"), "effects")
        self.rules = self._index_records(logic.get("rules"), "rules")
        self.goals = self._records(logic.get("goals"), "goals")
        self.failures = self._records(logic.get("failures"), "failures")
        self.endings = self._records(logic.get("endings"), "endings")
        self.transitions = self._records(
            logic.get("narrative_transitions"),
            "narrative_transitions",
        )
        self.transition_by_action: dict[str, Mapping[str, object]] = {}
        for item in self.transitions:
            action_id = item.get("action_id")
            if not isinstance(action_id, str) or action_id in self.transition_by_action:
                raise GameLogicError(
                    "narrative_transition_unsupported",
                    "each narrative action must own exactly one transition",
                )
            self.transition_by_action[action_id] = item
        self._candidate_cache = self._enumerate_candidates()

    @staticmethod
    def _records(value: object, context: str) -> tuple[Mapping[str, object], ...]:
        if not isinstance(value, list):
            raise GameLogicError("gamepack_logic_invalid", f"{context} must be an array")
        if not all(isinstance(item, Mapping) for item in value):
            raise GameLogicError("gamepack_logic_invalid", f"{context} contains a non-object")
        return tuple(value)

    @classmethod
    def _index_records(
        cls,
        value: object,
        context: str,
    ) -> dict[str, Mapping[str, object]]:
        result: dict[str, Mapping[str, object]] = {}
        for item in cls._records(value, context):
            identifier = item.get("id")
            if not isinstance(identifier, str) or identifier in result:
                raise GameLogicError(
                    "gamepack_logic_invalid",
                    f"{context} contains an invalid or duplicate ID",
                )
            result[identifier] = item
        return result

    def initial_state(self) -> dict[str, JsonValue]:
        value = self.logic.get("initial_state")
        if not isinstance(value, Mapping):
            raise GameLogicError("initial_state_invalid", "initial_state must be an object")
        state = copy.deepcopy(dict(value))
        self._validate_state(state)
        return state

    def _domain_values(self, parameter: Mapping[str, object]) -> tuple[JsonValue, ...]:
        parameter_type = parameter.get("type")
        if parameter_type == "boolean":
            return (False, True)
        if parameter_type == "integer":
            minimum = parameter.get("minimum")
            maximum = parameter.get("maximum")
            if (
                not isinstance(minimum, int)
                or isinstance(minimum, bool)
                or not isinstance(maximum, int)
                or isinstance(maximum, bool)
                or minimum > maximum
            ):
                raise GameLogicError("parameter_domain_invalid", "integer domain is invalid")
            cardinality = maximum - minimum + 1
            if cardinality > self.limits["parameter_combinations_per_action"]:
                raise GameLogicError(
                    "parameter_combinations_exceeded",
                    "an integer parameter exceeds the per-action combination limit",
                )
            return tuple(range(minimum, maximum + 1))
        allowed = parameter.get("allowed_values")
        if parameter_type == "string":
            if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
                raise GameLogicError("parameter_domain_invalid", "string domain is invalid")
            return tuple(allowed)
        if parameter_type == "string_array":
            minimum = parameter.get("min_items")
            maximum = parameter.get("max_items")
            if (
                not isinstance(allowed, list)
                or not all(isinstance(item, str) for item in allowed)
                or not isinstance(minimum, int)
                or isinstance(minimum, bool)
                or not isinstance(maximum, int)
                or isinstance(maximum, bool)
                or minimum < 0
                or minimum > maximum
            ):
                raise GameLogicError("parameter_domain_invalid", "array domain is invalid")
            cardinality = sum(math.perm(len(allowed), size) for size in range(minimum, maximum + 1))
            if cardinality > self.limits["parameter_combinations_per_action"]:
                raise GameLogicError(
                    "parameter_combinations_exceeded",
                    "an array parameter exceeds the per-action combination limit",
                )
            values: list[JsonValue] = []
            for size in range(minimum, maximum + 1):
                values.extend(list(items) for items in itertools.permutations(allowed, size))
            return tuple(values)
        raise GameLogicError(
            "parameter_type_unsupported",
            f"unsupported parameter type {parameter_type!r}",
        )

    def _enumerate_candidates(self) -> tuple[CandidateAction, ...]:
        result: list[CandidateAction] = []
        for action_id in sorted(self.actions, key=lambda item: item.encode("utf-8")):
            action = self.actions[action_id]
            raw_parameters = action.get("parameters")
            if not isinstance(raw_parameters, list):
                raise GameLogicError("parameter_domain_invalid", "parameters must be an array")
            parameter_ids: list[str] = []
            domains: list[tuple[JsonValue, ...]] = []
            cardinality = 1
            for parameter in raw_parameters:
                if not isinstance(parameter, Mapping) or not isinstance(parameter.get("id"), str):
                    raise GameLogicError(
                        "parameter_domain_invalid",
                        "parameter records must have IDs",
                    )
                parameter_id = str(parameter["id"])
                domain = self._domain_values(parameter)
                if not domain:
                    raise GameLogicError(
                        "parameter_domain_invalid",
                        f"parameter {parameter_id} has an empty domain",
                    )
                cardinality *= len(domain)
                if cardinality > self.limits["parameter_combinations_per_action"]:
                    raise GameLogicError(
                        "parameter_combinations_exceeded",
                        f"action {action_id} exceeds the per-action combination limit",
                    )
                parameter_ids.append(parameter_id)
                domains.append(domain)
            combinations: Iterable[tuple[JsonValue, ...]]
            combinations = itertools.product(*domains) if domains else ((),)
            for combination in combinations:
                result.append(
                    CandidateAction(
                        action_id,
                        {
                            parameter_id: copy.deepcopy(value)
                            for parameter_id, value in zip(
                                parameter_ids,
                                combination,
                                strict=True,
                            )
                        },
                    )
                )
        return tuple(result)

    def enumerate_candidates(self) -> tuple[CandidateAction, ...]:
        return tuple(
            CandidateAction(item.action_id, copy.deepcopy(item.parameters))
            for item in self._candidate_cache
        )

    def _validate_parameter_value(
        self,
        parameter: Mapping[str, object],
        value: object,
    ) -> bool:
        kind = parameter.get("type")
        if kind == "boolean":
            return isinstance(value, bool)
        if kind == "integer":
            return (
                isinstance(value, int)
                and not isinstance(value, bool)
                and isinstance(parameter.get("minimum"), int)
                and isinstance(parameter.get("maximum"), int)
                and int(parameter["minimum"]) <= value <= int(parameter["maximum"])
            )
        if kind == "string":
            return isinstance(value, str) and value in parameter.get("allowed_values", ())
        if kind == "string_array":
            return (
                isinstance(value, list)
                and all(isinstance(item, str) for item in value)
                and len(set(value)) == len(value)
                and isinstance(parameter.get("min_items"), int)
                and isinstance(parameter.get("max_items"), int)
                and int(parameter["min_items"]) <= len(value) <= int(parameter["max_items"])
                and set(value).issubset(set(parameter.get("allowed_values", ())))
            )
        return False

    def _validated_parameters(
        self,
        action: Mapping[str, object],
        candidate: CandidateAction,
    ) -> dict[str, JsonValue]:
        parameters = action.get("parameters")
        if not isinstance(parameters, list):
            raise GameLogicError("parameter_domain_invalid", "parameters must be an array")
        expected = [item.get("id") for item in parameters if isinstance(item, Mapping)]
        if len(expected) != len(parameters) or set(candidate.parameters) != set(expected):
            raise GameLogicError(
                "action_parameters_invalid",
                f"action {candidate.action_id} parameters do not exactly match",
            )
        checked: dict[str, JsonValue] = {}
        for parameter in parameters:
            assert isinstance(parameter, Mapping)
            parameter_id = str(parameter["id"])
            value = candidate.parameters[parameter_id]
            if not self._validate_parameter_value(parameter, value):
                raise GameLogicError(
                    "action_parameters_invalid",
                    f"action {candidate.action_id} parameter {parameter_id} is outside its domain",
                )
            checked[parameter_id] = copy.deepcopy(value)
        return checked

    def _validate_state_value(self, schema: Mapping[str, object], value: object) -> bool:
        kind = schema.get("type")
        if kind == "boolean":
            return isinstance(value, bool)
        if kind == "integer":
            return (
                isinstance(value, int)
                and not isinstance(value, bool)
                and -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER
                and isinstance(schema.get("minimum"), int)
                and isinstance(schema.get("maximum"), int)
                and int(schema["minimum"]) <= value <= int(schema["maximum"])
            )
        if kind == "string":
            return isinstance(value, str) and value in schema.get("allowed_values", ())
        if kind == "string_array":
            return (
                isinstance(value, list)
                and all(isinstance(item, str) for item in value)
                and len(set(value)) == len(value)
                and isinstance(schema.get("min_items"), int)
                and isinstance(schema.get("max_items"), int)
                and int(schema["min_items"]) <= len(value) <= int(schema["max_items"])
                and set(value).issubset(set(schema.get("allowed_values", ())))
            )
        return False

    def _validate_state(self, state: Mapping[str, object]) -> None:
        if set(state) != set(self.state_schema):
            raise GameLogicError("state_domain_invalid", "state keys do not exactly match schema")
        for state_id, schema in self.state_schema.items():
            if not self._validate_state_value(schema, state[state_id]):
                raise GameLogicError(
                    "state_domain_invalid",
                    f"state {state_id} is outside its exact domain",
                )

    @staticmethod
    def _operand(
        operand: object,
        state: Mapping[str, JsonValue],
        parameters: Mapping[str, JsonValue],
        *,
        action_id: str | None,
    ) -> JsonValue:
        if not isinstance(operand, Mapping):
            raise GameLogicError("operand_invalid", "operand must be an object")
        kind = operand.get("kind")
        if kind == "literal":
            return copy.deepcopy(operand.get("value"))  # type: ignore[return-value]
        if kind == "state":
            state_id = operand.get("state_id")
            if not isinstance(state_id, str) or state_id not in state:
                raise GameLogicError("operand_invalid", "state operand is unknown")
            return copy.deepcopy(state[state_id])
        if kind == "parameter":
            owned_action = operand.get("action_id")
            parameter_id = operand.get("parameter_id")
            if (
                not isinstance(owned_action, str)
                or owned_action != action_id
                or not isinstance(parameter_id, str)
                or parameter_id not in parameters
            ):
                raise GameLogicError("operand_invalid", "parameter operand is out of scope")
            return copy.deepcopy(parameters[parameter_id])
        raise GameLogicError("operator_unsupported", f"unsupported operand kind {kind!r}")

    def _condition(
        self,
        condition_id: str,
        state: Mapping[str, JsonValue],
        parameters: Mapping[str, JsonValue],
        *,
        action_id: str | None,
        active: frozenset[str] = frozenset(),
    ) -> bool:
        condition = self.conditions.get(condition_id)
        if condition is None:
            raise GameLogicError("condition_unknown", f"unknown condition {condition_id}")
        if condition_id in active:
            raise GameLogicError("condition_cycle", f"condition cycle at {condition_id}")
        scoped_action = condition.get("action_id")
        if scoped_action != action_id:
            raise GameLogicError(
                "condition_scope_invalid",
                f"condition {condition_id} does not belong to action scope",
            )
        operator = condition.get("operator")
        if operator == "constant":
            value = condition.get("value")
            if not isinstance(value, bool):
                raise GameLogicError("condition_invalid", "constant condition is not boolean")
            return value
        if operator == "compare":
            left = self._operand(condition.get("left"), state, parameters, action_id=action_id)
            right = self._operand(condition.get("right"), state, parameters, action_id=action_id)
            comparison = condition.get("comparison")
            if comparison == "equal":
                return left == right and type(left) is type(right)
            if comparison == "not_equal":
                return left != right or type(left) is not type(right)
            if (
                not isinstance(left, int)
                or isinstance(left, bool)
                or not isinstance(right, int)
                or isinstance(right, bool)
            ):
                raise GameLogicError("condition_invalid", "ordered compare requires integers")
            if comparison == "less_than":
                return left < right
            if comparison == "less_or_equal":
                return left <= right
            if comparison == "greater_than":
                return left > right
            if comparison == "greater_or_equal":
                return left >= right
            raise GameLogicError("operator_unsupported", f"unsupported comparison {comparison!r}")
        if operator in {"all", "any"}:
            condition_ids = condition.get("condition_ids")
            if not isinstance(condition_ids, list):
                raise GameLogicError("condition_invalid", "condition IDs must be an array")
            results = [
                self._condition(
                    str(child),
                    state,
                    parameters,
                    action_id=action_id,
                    active=active | {condition_id},
                )
                for child in condition_ids
            ]
            return all(results) if operator == "all" else any(results)
        if operator == "not":
            child = condition.get("condition_id")
            if not isinstance(child, str):
                raise GameLogicError("condition_invalid", "not condition has no child")
            return not self._condition(
                child,
                state,
                parameters,
                action_id=action_id,
                active=active | {condition_id},
            )
        if operator == "index_valid":
            array_state_id = condition.get("array_state_id")
            if not isinstance(array_state_id, str) or not isinstance(
                state.get(array_state_id),
                list,
            ):
                raise GameLogicError("condition_invalid", "index condition array is invalid")
            index = self._operand(
                condition.get("index"),
                state,
                parameters,
                action_id=action_id,
            )
            return (
                isinstance(index, int)
                and not isinstance(index, bool)
                and 0 <= index < len(state[array_state_id])  # type: ignore[arg-type]
            )
        if operator == "integer_distance":
            left = self._operand(condition.get("left"), state, parameters, action_id=action_id)
            right = self._operand(condition.get("right"), state, parameters, action_id=action_id)
            distance = condition.get("distance")
            if (
                not isinstance(left, int)
                or isinstance(left, bool)
                or not isinstance(right, int)
                or isinstance(right, bool)
                or not isinstance(distance, int)
                or isinstance(distance, bool)
            ):
                raise GameLogicError("condition_invalid", "integer distance operands are invalid")
            return abs(left - right) == distance
        raise GameLogicError("operator_unsupported", f"unsupported condition operator {operator!r}")

    def _conditions(
        self,
        condition_ids: object,
        state: Mapping[str, JsonValue],
        parameters: Mapping[str, JsonValue],
        *,
        action_id: str | None,
    ) -> bool:
        if not isinstance(condition_ids, list):
            raise GameLogicError("condition_invalid", "condition collection must be an array")
        return all(
            self._condition(
                str(condition_id),
                state,
                parameters,
                action_id=action_id,
            )
            for condition_id in condition_ids
        )

    def _apply_effect(
        self,
        effect: Mapping[str, object],
        candidate_state: dict[str, JsonValue],
        parameters: Mapping[str, JsonValue],
        *,
        action_id: str,
    ) -> None:
        if effect.get("action_id") != action_id:
            raise GameLogicError("effect_scope_invalid", "effect action scope is invalid")
        if effect.get("invalid_transition_policy") != "reject_transition":
            raise GameLogicError(
                "effect_policy_unsupported",
                "only reject_transition effects are supported",
            )
        operation = effect.get("operation")
        state_id_field = (
            "array_state_id" if operation in {"swap_array_items", "append_unique"} else "state_id"
        )
        state_id = effect.get(state_id_field)
        if not isinstance(state_id, str) or state_id not in candidate_state:
            raise GameLogicError("effect_state_invalid", "effect state target is unknown")
        schema = self.state_schema[state_id]
        if schema.get("mutability") != "mutable":
            raise GameLogicError("effect_state_invalid", "effect target is constant")
        if operation == "set":
            candidate_state[state_id] = self._operand(
                effect.get("value"),
                candidate_state,
                parameters,
                action_id=action_id,
            )
        elif operation == "reset":
            candidate_state[state_id] = copy.deepcopy(schema.get("initial"))  # type: ignore[assignment]
        elif operation == "increment":
            current = candidate_state[state_id]
            amount = self._operand(
                effect.get("amount"),
                candidate_state,
                parameters,
                action_id=action_id,
            )
            if (
                not isinstance(current, int)
                or isinstance(current, bool)
                or not isinstance(amount, int)
                or isinstance(amount, bool)
            ):
                raise GameLogicError("effect_domain_invalid", "increment requires integers")
            result = current + amount
            if not -MAX_SAFE_INTEGER <= result <= MAX_SAFE_INTEGER:
                raise GameLogicError("effect_domain_invalid", "increment exceeds safe integer")
            candidate_state[state_id] = result
        elif operation == "swap_array_items":
            current = candidate_state[state_id]
            first = self._operand(
                effect.get("first_index"),
                candidate_state,
                parameters,
                action_id=action_id,
            )
            second = self._operand(
                effect.get("second_index"),
                candidate_state,
                parameters,
                action_id=action_id,
            )
            if (
                not isinstance(current, list)
                or not isinstance(first, int)
                or isinstance(first, bool)
                or not isinstance(second, int)
                or isinstance(second, bool)
                or not 0 <= first < len(current)
                or not 0 <= second < len(current)
            ):
                raise GameLogicError("effect_domain_invalid", "swap indices are invalid")
            updated = copy.deepcopy(current)
            updated[first], updated[second] = updated[second], updated[first]
            candidate_state[state_id] = updated
        elif operation == "append_unique":
            current = candidate_state[state_id]
            value = self._operand(
                effect.get("value"),
                candidate_state,
                parameters,
                action_id=action_id,
            )
            if not isinstance(current, list) or not isinstance(value, str):
                raise GameLogicError("effect_domain_invalid", "append_unique requires string array")
            if value in current:
                raise GameLogicError(
                    "effect_domain_invalid",
                    "append_unique rejects an existing value",
                )
            updated = copy.deepcopy(current)
            updated.append(value)
            candidate_state[state_id] = updated
        else:
            raise GameLogicError(
                "operator_unsupported",
                f"unsupported effect operation {operation!r}",
            )
        if not self._validate_state_value(schema, candidate_state[state_id]):
            raise GameLogicError(
                "effect_domain_invalid",
                f"effect leaves state {state_id} outside its domain",
            )

    def _narrative_endings(self) -> dict[str, str]:
        result: dict[str, str] = {}
        modules = self.gamepack.get("modules")
        if not isinstance(modules, Mapping):
            return result
        projections = modules.get("narrative")
        if not isinstance(projections, list):
            return result
        for projection in projections:
            if not isinstance(projection, Mapping) or not isinstance(
                projection.get("units"),
                list,
            ):
                continue
            for unit in projection["units"]:
                if (
                    isinstance(unit, Mapping)
                    and unit.get("unit_type") == "ending"
                    and isinstance(unit.get("id"), str)
                    and isinstance(unit.get("ending_kind"), str)
                ):
                    result[str(unit["id"])] = str(unit["ending_kind"])
        return result

    def classify(self, state: Mapping[str, JsonValue]) -> StateClassification:
        checked = snapshot_strict_state(state)
        self._validate_state(checked)
        return self._classify_validated_state(checked)

    def _classify_validated_state(
        self,
        state: Mapping[str, JsonValue],
    ) -> StateClassification:
        goals = tuple(
            str(item["id"])
            for item in self.goals
            if self._conditions(item.get("condition_ids"), state, {}, action_id=None)
        )
        endings = tuple(
            str(item["id"])
            for item in self.endings
            if self._conditions(item.get("condition_ids"), state, {}, action_id=None)
        )
        if len(endings) > 1:
            raise GameLogicError(
                "ambiguous_ending",
                "exactly one ending may match a state",
            )
        ending_kind: str | None = None
        if endings:
            ending = next(item for item in self.endings if item["id"] == endings[0])
            ending_kind = str(ending["kind"])

        narrative_endings = self._narrative_endings()
        cursor = self.logic.get("narrative_cursor")
        if isinstance(cursor, Mapping):
            cursor_id = cursor.get("id")
            if not isinstance(cursor_id, str):
                raise GameLogicError("cursor_divergence", "cursor schema has no ID")
            cursor_value = state.get(cursor_id)
            cursor_terminal = (
                cursor_value
                if isinstance(cursor_value, str) and cursor_value in narrative_endings
                else None
            )
            if cursor_terminal is not None:
                if (
                    endings != (cursor_terminal,)
                    or ending_kind != narrative_endings[cursor_terminal]
                ):
                    raise GameLogicError(
                        "cursor_divergence",
                        "narrative cursor ending and logic ending do not exactly agree",
                    )
            elif endings:
                raise GameLogicError(
                    "cursor_divergence",
                    "logic reached an ending before the narrative cursor",
                )

        if endings:
            return StateClassification(goals, endings, ending_kind, (), ())

        active_failures = tuple(
            str(item["id"])
            for item in self.failures
            if self._conditions(item.get("condition_ids"), state, {}, action_id=None)
        )
        recovery: set[str] | None = None
        for failure_id in active_failures:
            failure = next(item for item in self.failures if item["id"] == failure_id)
            raw_actions = failure.get("recovery_action_ids")
            if not isinstance(raw_actions, list):
                raise GameLogicError("failure_invalid", "recovery actions must be an array")
            owned = {str(item) for item in raw_actions}
            recovery = owned if recovery is None else recovery & owned
        recovery_ids = tuple(sorted(recovery or (), key=lambda item: item.encode("utf-8")))
        return StateClassification(
            goals,
            (),
            None,
            active_failures,
            recovery_ids,
        )

    @staticmethod
    def _rejected(
        candidate: CandidateAction,
        state: Mapping[str, JsonValue],
        reason: str,
    ) -> TransitionResult:
        before = copy.deepcopy(dict(state))
        state_hash = canonical_state_hash(before)
        return TransitionResult(
            False,
            CandidateAction(candidate.action_id, copy.deepcopy(candidate.parameters)),
            copy.deepcopy(before),
            before,
            state_hash,
            state_hash,
            (),
            reason,
        )

    def transition(
        self,
        state: Mapping[str, JsonValue],
        candidate: CandidateAction,
    ) -> TransitionResult:
        return self.transition_strict(state, candidate)

    def transition_strict(
        self,
        state: Mapping[str, JsonValue],
        candidate: CandidateAction,
    ) -> TransitionResult:
        """Apply the explicit strict neutral input policy."""

        checked_candidate = snapshot_strict_candidate(candidate)
        checked_state = snapshot_strict_state(state)
        self._validate_state(checked_state)
        return self._transition_validated_state(checked_state, checked_candidate)

    def transition_legacy(
        self,
        state: Mapping[str, JsonValue],
        candidate: CandidateAction,
    ) -> TransitionResult:
        """Apply the historical Forge state-first compatibility policy."""

        if type(state) not in {dict, _MAPPING_PROXY_TYPE}:
            raise GameLogicError(
                "state_domain_invalid",
                "state must be an exact object",
            )
        _legacy_state_keys(state, self.state_schema)
        try:
            checked_state = snapshot_legacy_action_parameters(state)
        except GameLogicError as exc:
            raise GameLogicError(
                "state_domain_invalid",
                "state contains a non-legacy value",
            ) from exc
        self._validate_state(checked_state)
        if type(candidate) is not CandidateAction or type(candidate.action_id) is not str:
            raise GameLogicError(
                "action_invalid",
                "action must be a CandidateAction with an ID",
            )
        checked_parameters = snapshot_legacy_action_parameters(candidate.parameters)
        checked_candidate = CandidateAction(candidate.action_id, checked_parameters)  # type: ignore[arg-type]
        return self._transition_validated_state(checked_state, checked_candidate)  # type: ignore[arg-type]

    def _transition_validated_state(
        self,
        state: Mapping[str, JsonValue],
        candidate: CandidateAction,
    ) -> TransitionResult:
        before = copy.deepcopy(dict(state))
        action = self.actions.get(candidate.action_id)
        if action is None:
            return self._rejected(candidate, before, "action_unknown")
        try:
            parameters = self._validated_parameters(action, candidate)
        except GameLogicError as exc:
            return self._rejected(candidate, before, exc.reason_code)
        classification = self.classify(before)
        if classification.terminal:
            return self._rejected(candidate, before, "terminal_state")
        if (
            classification.failure_ids
            and candidate.action_id not in classification.recovery_action_ids
        ):
            reason = (
                "failure_recovery_empty_intersection"
                if not classification.recovery_action_ids
                else "failure_recovery_required"
            )
            return self._rejected(candidate, before, reason)
        raw_rule_ids = action.get("rule_ids")
        if not isinstance(raw_rule_ids, list) or not raw_rule_ids:
            return self._rejected(candidate, before, "owned_rules_missing")
        all_owned_rule_ids = {
            rule_id
            for rule_id, rule in self.rules.items()
            if rule.get("action_id") == candidate.action_id
        }
        if set(str(item) for item in raw_rule_ids) != all_owned_rule_ids:
            return self._rejected(candidate, before, "owned_rules_incomplete")
        owned_rules: list[Mapping[str, object]] = []
        for rule_id in raw_rule_ids:
            rule = self.rules.get(str(rule_id))
            if rule is None or rule.get("action_id") != candidate.action_id:
                return self._rejected(candidate, before, "owned_rule_invalid")
            owned_rules.append(rule)
        owned_rules.sort(
            key=lambda item: (
                int(item.get("order", 0)),
                str(item.get("id", "")).encode("utf-8"),
            )
        )
        try:
            if not all(
                self._conditions(
                    rule.get("condition_ids"),
                    before,
                    parameters,
                    action_id=candidate.action_id,
                )
                for rule in owned_rules
            ):
                return self._rejected(candidate, before, "rule_condition_false")
            narrative_transition = self.transition_by_action.get(candidate.action_id)
            if narrative_transition is not None:
                precondition = narrative_transition.get("precondition")
                if not isinstance(precondition, Mapping):
                    raise GameLogicError(
                        "narrative_transition_invalid",
                        "narrative precondition is absent",
                    )
                cursor_state_id = precondition.get("cursor_state_id")
                if (
                    precondition.get("operator") != "cursor_equals"
                    or not isinstance(cursor_state_id, str)
                    or before.get(cursor_state_id) != precondition.get("value")
                ):
                    return self._rejected(
                        candidate,
                        before,
                        "narrative_cursor_precondition_false",
                    )
                expected_conditions = [
                    condition_id
                    for rule in owned_rules
                    for condition_id in rule.get("condition_ids", [])
                ]
                expected_effects = [
                    effect_id for rule in owned_rules for effect_id in rule.get("effect_ids", [])
                ]
                if (
                    narrative_transition.get("atomic_source_condition_ids") != expected_conditions
                    or narrative_transition.get("atomic_source_effect_ids") != expected_effects
                ):
                    raise GameLogicError(
                        "narrative_transition_invalid",
                        "narrative transition does not bind exact source rules",
                    )
            candidate_state = copy.deepcopy(before)
            pending_events: list[str] = []
            for rule in owned_rules:
                effect_ids = rule.get("effect_ids")
                event_ids = rule.get("event_ids")
                if not isinstance(effect_ids, list) or not isinstance(event_ids, list):
                    raise GameLogicError("owned_rule_invalid", "rule references are invalid")
                for effect_id in effect_ids:
                    effect = self.effects.get(str(effect_id))
                    if effect is None:
                        raise GameLogicError("effect_unknown", f"unknown effect {effect_id}")
                    self._apply_effect(
                        effect,
                        candidate_state,
                        parameters,
                        action_id=candidate.action_id,
                    )
                pending_events.extend(str(event_id) for event_id in event_ids)
            if narrative_transition is not None:
                cursor_effect = narrative_transition.get("effect")
                if not isinstance(cursor_effect, Mapping):
                    raise GameLogicError(
                        "narrative_transition_invalid",
                        "narrative cursor effect is absent",
                    )
                cursor_state_id = cursor_effect.get("cursor_state_id")
                if (
                    cursor_effect.get("operation") != "set_cursor"
                    or cursor_effect.get("invalid_transition_policy") != "reject_transition"
                    or not isinstance(cursor_state_id, str)
                    or cursor_state_id not in candidate_state
                ):
                    raise GameLogicError(
                        "operator_unsupported",
                        "unsupported compiler-owned cursor operation",
                    )
                candidate_state[cursor_state_id] = copy.deepcopy(cursor_effect.get("value"))  # type: ignore[assignment]
            self._validate_state(candidate_state)
            self.classify(candidate_state)
        except GameLogicError as exc:
            return self._rejected(candidate, before, exc.reason_code)
        return TransitionResult(
            True,
            CandidateAction(candidate.action_id, copy.deepcopy(parameters)),
            before,
            candidate_state,
            canonical_state_hash(before),
            canonical_state_hash(candidate_state),
            tuple(pending_events),
            None,
        )

    def legal_transitions(
        self,
        state: Mapping[str, JsonValue],
    ) -> tuple[TransitionResult, ...]:
        return tuple(
            result
            for candidate in self._candidate_cache
            if (result := self.transition(state, candidate)).accepted
        )


def _interpreter(gamepack: Mapping[str, object]) -> GamepackInterpreter:
    return GamepackInterpreter(gamepack)


def initial_state(gamepack: Mapping[str, object]) -> dict[str, JsonValue]:
    return _interpreter(gamepack).initial_state()


def enumerate_candidates(
    gamepack: Mapping[str, object],
) -> tuple[CandidateAction, ...]:
    return _interpreter(gamepack).enumerate_candidates()


def classify_state(
    gamepack: Mapping[str, object],
    state: Mapping[str, JsonValue],
) -> StateClassification:
    return _interpreter(gamepack).classify(state)


def transition(
    gamepack: Mapping[str, object],
    state: Mapping[str, JsonValue],
    candidate: CandidateAction,
) -> TransitionResult:
    return _interpreter(gamepack).transition(state, candidate)


def legal_transitions(
    gamepack: Mapping[str, object],
    state: Mapping[str, JsonValue],
) -> tuple[TransitionResult, ...]:
    return _interpreter(gamepack).legal_transitions(state)


def canonical_trace_step(result: TransitionResult) -> dict[str, object]:
    if type(result) is not TransitionResult or type(result.accepted) is not bool:
        raise GameLogicError(
            "trace_step_invalid",
            "trace input must be an exact TransitionResult",
        )
    if not result.accepted:
        raise GameLogicError("trace_step_rejected", "only committed transitions form traces")
    if result.rejection_reason is not None:
        raise GameLogicError(
            "trace_step_invalid",
            "committed transitions cannot carry a rejection reason",
        )
    events = _snapshot_event_sequence(
        result.events,
        exact_tuple=True,
        reason_code="trace_step_invalid",
    )
    action = snapshot_strict_candidate(result.action)
    pre_state = snapshot_strict_state(result.pre_state)
    post_state = snapshot_strict_state(result.post_state)
    if (
        type(result.pre_state_hash) is not str
        or type(result.post_state_hash) is not str
        or len(result.pre_state_hash) != 64
        or len(result.post_state_hash) != 64
        or any(character not in "0123456789abcdef" for character in result.pre_state_hash)
        or any(character not in "0123456789abcdef" for character in result.post_state_hash)
    ):
        raise GameLogicError(
            "trace_step_invalid",
            "trace state hashes must be exact lowercase SHA-256 values",
        )
    if result.pre_state_hash != canonical_state_hash(
        pre_state
    ) or result.post_state_hash != canonical_state_hash(post_state):
        raise GameLogicError(
            "trace_step_invalid",
            "trace state hashes do not match their owned states",
        )
    canonical_events_hash(events)
    return {
        "action_id": action.action_id,
        "parameters": action.parameters,
        "pre_state_hash": result.pre_state_hash,
        "post_state_hash": result.post_state_hash,
        "events": list(events),
    }
