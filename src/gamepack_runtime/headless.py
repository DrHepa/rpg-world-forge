"""Bounded deterministic headless execution for generic gamepack v1."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from gamepack_runtime.contracts import (
    CandidateAction,
    GameLogicError,
    StateClassification,
    TransitionResult,
    canonical_state_hash,
    snapshot_plain_json,
    snapshot_strict_candidate,
)
from gamepack_runtime.persistence import (
    GAMEPACK_RUNTIME_API,
    GamePersistenceContext,
    build_game_persistence_context,
    build_game_replay,
    build_game_save,
    play_game_replay,
    restore_game_save,
    serialize_game_replay,
    serialize_game_save,
)
from gamepack_runtime.semantics_v1 import canonical_trace_step
from gamepack_runtime.session import GameSession

GAME_EXECUTION_SCRIPT_FORMAT = "world-forge.game_execution_script"
HEADLESS_EXECUTION_RECEIPT_FORMAT = "world-forge.headless_execution_receipt"
HEADLESS_CONTRACT_VERSION = 1
MAX_GAME_EXECUTION_SCRIPT_BYTES = 4 * 1024 * 1024
MAX_HEADLESS_EXECUTION_RECEIPT_BYTES = 4 * 1024 * 1024
MAX_HEADLESS_SCENARIOS = 32
MAX_HEADLESS_ACTIONS_PER_SCENARIO = 128
MAX_HEADLESS_TOTAL_ACTIONS = 4096
MAX_HEADLESS_STATE_BYTES = 65536

_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SCRIPT_ID_RE = re.compile(r"^game_execution_script_[0-9a-f]{40}$")
_RECEIPT_ID_RE = re.compile(r"^headless_execution_receipt_[0-9a-f]{40}$")
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_BINDING_FIELDS = frozenset(
    {
        "gamepack",
        "runtime_composition",
        "runtime_bundle",
        "adapter",
        "runtime_snapshot",
    }
)
_CLASSIFICATION_FIELDS = frozenset(
    {
        "goal_ids",
        "ending_ids",
        "ending_kind",
        "failure_ids",
        "recovery_action_ids",
        "terminal",
    }
)
_ACTION_FIELDS = frozenset({"action_id", "parameters"})
_SCENARIO_FIELDS = frozenset(
    {
        "scenario_id",
        "actions",
        "expected_initial_state_hash",
        "expected_final_state_hash",
        "expected_classification",
    }
)
_SCRIPT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "script_id",
        "bindings",
        "scenarios",
        "content_hash",
    }
)
_HOST_FIELDS = frozenset(
    {
        "platform_id",
        "platform_family",
        "architecture",
        "backend",
        "renderer",
    }
)
_POLICY_FIELDS = frozenset({"verifier_policy_hash", "audit_policy_hash"})
_CHECK_FIELDS = frozenset({"check_id", "kind", "status", "evidence_id", "content_hash"})
_SAVE_RESULT_FIELDS = frozenset(
    {
        "id",
        "content_hash",
        "restored_state_hash",
    }
)
_REPLAY_RESULT_FIELDS = frozenset(
    {
        "id",
        "content_hash",
        "replayed_state_hash",
    }
)
_SCENARIO_RESULT_FIELDS = frozenset(
    {
        "scenario_id",
        "action_count",
        "trace_hash",
        "final_state_hash",
        "classification",
        "save",
        "replay",
    }
)
_ACTION_COVERAGE_FIELDS = frozenset({"action_id", "mechanic_ids", "scenario_ids"})
_FEATURE_COVERAGE_FIELDS = frozenset({"feature_id", "mechanic_ids", "scenario_ids"})
_COVERAGE_FIELDS = frozenset({"complete", "actions", "required_features"})
_EXECUTOR_FIELDS = frozenset({"key", "adapter_id", "adapter_version", "adapter_hash"})
_RECEIPT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "receipt_id",
        "bindings",
        "host",
        "executor",
        "runtime_api",
        "execution_semantics",
        "policies",
        "native_execution",
        "scenarios",
        "coverage",
        "checks",
        "status",
        "failure",
        "content_hash",
    }
)

HEADLESS_VERIFIER_POLICY = MappingProxyType(
    {
        "version": 1,
        "scenario_limit": MAX_HEADLESS_SCENARIOS,
        "actions_per_scenario_limit": MAX_HEADLESS_ACTIONS_PER_SCENARIO,
        "total_actions_limit": MAX_HEADLESS_TOTAL_ACTIONS,
        "state_bytes_limit": MAX_HEADLESS_STATE_BYTES,
        "runs_per_scenario": 2,
        "rejected_actions_recorded": False,
        "native_execution": False,
    }
)
HEADLESS_AUDIT_POLICY = MappingProxyType(
    {
        "version": 1,
        "blocked_exact_events": [
            "compile",
            "ctypes.call_function",
            "ctypes.dlopen",
            "ctypes.dlsym",
            "exec",
            "function.__new__",
            "os.posix_spawn",
            "os.posix_spawnp",
            "os.system",
            "sock" + "et.__new__",
            "sub" + "process.Popen",
        ],
        "blocked_event_prefixes": [
            "os.exec",
            "os.spawn",
            "sock" + "et.",
        ],
    }
)

_AUDIT_LOCAL = threading.local()
_AUDIT_INSTALL_LOCK = threading.Lock()
_AUDIT_INSTALLED = False


def _fail(reason_code: str, detail: str) -> None:
    raise GameLogicError(reason_code, detail)


def _own(value: object, *, maximum_bytes: int, context: str) -> Any:
    try:
        return snapshot_plain_json(value, maximum_bytes=maximum_bytes)
    except GameLogicError as exc:
        _fail(exc.reason_code, f"{context}: {exc.detail}")


def _canonical_bytes(value: object, *, maximum_bytes: int) -> bytes:
    owned = _own(value, maximum_bytes=maximum_bytes, context="headless canonical value")
    try:
        payload = json.dumps(
            owned,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, UnicodeError, ValueError, OverflowError) as exc:
        _fail("script_invalid", f"could not encode canonical headless JSON: {exc}")
    if len(payload) > maximum_bytes:
        _fail("headless_bytes_exceeded", "canonical headless JSON exceeds its byte limit")
    return payload


def canonical_headless_hash(value: Mapping[str, object]) -> str:
    owned = _own(
        value,
        maximum_bytes=MAX_HEADLESS_EXECUTION_RECEIPT_BYTES,
        context="headless hash input",
    )
    if type(owned) is not dict:
        _fail("script_invalid", "headless hash input must be an object")
    owned.pop("content_hash", None)
    return hashlib.sha256(
        _canonical_bytes(
            owned,
            maximum_bytes=MAX_HEADLESS_EXECUTION_RECEIPT_BYTES,
        )
    ).hexdigest()


def _value_hash(value: object) -> str:
    return hashlib.sha256(
        _canonical_bytes(
            value,
            maximum_bytes=MAX_HEADLESS_EXECUTION_RECEIPT_BYTES,
        )
    ).hexdigest()


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], context: str) -> None:
    if frozenset(value) != expected:
        missing = sorted(expected - set(value), key=lambda item: item.encode("utf-8"))
        extra = sorted(set(value) - expected, key=lambda item: item.encode("utf-8"))
        _fail(
            "script_invalid",
            f"{context} fields differ; missing={missing!r} extra={extra!r}",
        )


def _object(value: object, context: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("script_invalid", f"{context} must be an exact object")
    return value


def _array(value: object, context: str, *, maximum: int) -> list[Any]:
    if type(value) is not list or len(value) > maximum:
        _fail("script_invalid", f"{context} must be an exact bounded array")
    return value


def _sha256(value: object, context: str) -> str:
    if type(value) is not str or _HEX_RE.fullmatch(value) is None:
        _fail("script_invalid", f"{context} must be lowercase SHA-256")
    return value


def _identifier(value: object, context: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail("script_invalid", f"{context} must be a portable lowercase ASCII ID")
    return value


def _identity(
    document: Mapping[str, object],
    *,
    id_field: str,
) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[id_field],
        "content_hash": document["content_hash"],
    }


def _validate_identity(value: object, context: str) -> dict[str, Any]:
    result = _object(value, context)
    _exact_keys(result, _IDENTITY_FIELDS, context)
    if type(result.get("format")) is not str or not result["format"]:
        _fail("script_invalid", f"{context}.format must be non-empty")
    if result.get("format_version") != 1:
        _fail("script_invalid", f"{context}.format_version must be 1")
    if type(result.get("id")) is not str or not result["id"]:
        _fail("script_invalid", f"{context}.id must be non-empty")
    _sha256(result.get("content_hash"), f"{context}.content_hash")
    return result


def _classification_document(value: StateClassification) -> dict[str, object]:
    return {
        "goal_ids": list(value.goal_ids),
        "ending_ids": list(value.ending_ids),
        "ending_kind": value.ending_kind,
        "failure_ids": list(value.failure_ids),
        "recovery_action_ids": list(value.recovery_action_ids),
        "terminal": value.terminal,
    }


def _validate_classification(value: object, context: str) -> dict[str, Any]:
    classification = _object(value, context)
    _exact_keys(classification, _CLASSIFICATION_FIELDS, context)
    for field in (
        "goal_ids",
        "ending_ids",
        "failure_ids",
        "recovery_action_ids",
    ):
        items = _array(classification.get(field), f"{context}.{field}", maximum=256)
        for index, item in enumerate(items):
            _identifier(item, f"{context}.{field}/{index}")
        if items != sorted(items, key=lambda item: item.encode("utf-8")):
            _fail("script_invalid", f"{context}.{field} is not canonical")
        if len(set(items)) != len(items):
            _fail("script_invalid", f"{context}.{field} contains duplicates")
    ending_kind = classification.get("ending_kind")
    if ending_kind is not None and (type(ending_kind) is not str or not ending_kind):
        _fail("script_invalid", f"{context}.ending_kind must be null or non-empty")
    if type(classification.get("terminal")) is not bool:
        _fail("script_invalid", f"{context}.terminal must be boolean")
    return classification


def _semantics_identity(context: GamePersistenceContext) -> dict[str, object]:
    return copy.deepcopy(context.bindings["execution_semantics"])


def _bundle_inputs(
    runtime_bundle: object,
    *,
    gamepack: object,
    composition: object,
    adapter: object,
    runtime_snapshot: object,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    GamePersistenceContext,
]:
    bundle = _object(
        _own(
            runtime_bundle,
            maximum_bytes=MAX_GAME_EXECUTION_SCRIPT_BYTES,
            context="runtime bundle",
        ),
        "runtime bundle",
    )
    checked_gamepack = _object(
        _own(
            gamepack,
            maximum_bytes=MAX_GAME_EXECUTION_SCRIPT_BYTES,
            context="gamepack",
        ),
        "gamepack",
    )
    checked_composition = _object(
        _own(
            composition,
            maximum_bytes=MAX_GAME_EXECUTION_SCRIPT_BYTES,
            context="runtime composition",
        ),
        "runtime composition",
    )
    checked_adapter = _object(
        _own(
            adapter,
            maximum_bytes=MAX_GAME_EXECUTION_SCRIPT_BYTES,
            context="runtime adapter",
        ),
        "runtime adapter",
    )
    checked_snapshot = _object(
        _own(
            runtime_snapshot,
            maximum_bytes=MAX_GAME_EXECUTION_SCRIPT_BYTES,
            context="runtime snapshot",
        ),
        "runtime snapshot",
    )
    context = build_game_persistence_context(
        checked_gamepack,
        checked_composition,
        bundle,
        checked_adapter,
    )
    expected_snapshot = _identity(checked_snapshot, id_field="snapshot_id")
    if checked_composition.get("runtime_snapshot") != expected_snapshot:
        _fail("binding_mismatch", "composition does not reference the exact runtime snapshot")
    contracts = _object(bundle.get("contracts"), "runtime bundle.contracts")
    bundle_snapshot = _object(
        contracts.get("runtime_snapshot"),
        "runtime bundle.contracts.runtime_snapshot",
    )
    projected_snapshot = {
        key: bundle_snapshot.get(key) for key in ("format", "format_version", "id", "content_hash")
    }
    if projected_snapshot != expected_snapshot:
        _fail("binding_mismatch", "runtime bundle does not reference the exact runtime snapshot")
    return (
        bundle,
        checked_gamepack,
        checked_composition,
        checked_adapter,
        checked_snapshot,
        context,
    )


def _bindings(
    bundle: Mapping[str, object],
    composition: Mapping[str, object],
    adapter: Mapping[str, object],
    snapshot: Mapping[str, object],
    context: GamePersistenceContext,
) -> dict[str, object]:
    return {
        "gamepack": context.gamepack_identity,
        "runtime_composition": _identity(composition, id_field="composition_id"),
        "runtime_bundle": _identity(bundle, id_field="bundle_id"),
        "adapter": _identity(adapter, id_field="adapter_id"),
        "runtime_snapshot": _identity(snapshot, id_field="snapshot_id"),
    }


def _seal_script(document: dict[str, object]) -> dict[str, object]:
    seed = {
        key: value for key, value in document.items() if key not in {"script_id", "content_hash"}
    }
    document["script_id"] = "game_execution_script_" + canonical_headless_hash(seed)[:40]
    document["content_hash"] = canonical_headless_hash(document)
    return document


def _run_actions(
    context: GamePersistenceContext,
    actions: Sequence[Mapping[str, object]],
    *,
    scenario_id: str,
) -> tuple[GameSession, list[TransitionResult], list[dict[str, object]]]:
    session = GameSession(context.gamepack)
    results: list[TransitionResult] = []
    trace: list[dict[str, object]] = []
    for index, action in enumerate(actions):
        result = session.apply(
            action["action_id"],  # type: ignore[arg-type]
            action["parameters"],  # type: ignore[arg-type]
        )
        if not result.accepted:
            _fail(
                "action_rejected",
                f"scenario={scenario_id} action_index={index} reason={result.rejection_reason}",
            )
        results.append(result)
        trace.append({"index": index, **canonical_trace_step(result)})
    return session, results, trace


def build_game_execution_script(
    runtime_bundle: object,
    *,
    gamepack: object,
    composition: object,
    adapter: object,
    runtime_snapshot: object,
    scenarios: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Derive exact deterministic expectations for a bounded scenario set."""

    (
        bundle,
        _checked_gamepack,
        checked_composition,
        checked_adapter,
        checked_snapshot,
        context,
    ) = _bundle_inputs(
        runtime_bundle,
        gamepack=gamepack,
        composition=composition,
        adapter=adapter,
        runtime_snapshot=runtime_snapshot,
    )
    owned_scenarios = _own(
        scenarios,
        maximum_bytes=MAX_GAME_EXECUTION_SCRIPT_BYTES,
        context="execution scenarios",
    )
    if type(owned_scenarios) is not list:
        _fail("script_invalid", "execution scenarios must be an exact array")
    built: list[dict[str, object]] = []
    initial_hash = canonical_state_hash(GameSession(context.gamepack).state)
    for index, raw in enumerate(owned_scenarios):
        scenario = _object(raw, f"scenarios/{index}")
        if frozenset(scenario) != frozenset({"scenario_id", "actions"}):
            _fail("script_invalid", f"scenarios/{index} must contain scenario_id and actions")
        scenario_id = _identifier(scenario.get("scenario_id"), f"scenarios/{index}.scenario_id")
        actions = _array(
            scenario.get("actions"),
            f"scenarios/{index}.actions",
            maximum=MAX_HEADLESS_ACTIONS_PER_SCENARIO,
        )
        checked_actions: list[dict[str, object]] = []
        for action_index, raw_action in enumerate(actions):
            action = _object(raw_action, f"scenarios/{index}.actions/{action_index}")
            _exact_keys(action, _ACTION_FIELDS, f"scenarios/{index}.actions/{action_index}")
            candidate = snapshot_strict_candidate(
                CandidateAction(
                    action.get("action_id"),  # type: ignore[arg-type]
                    action.get("parameters"),  # type: ignore[arg-type]
                )
            )
            checked_actions.append(
                {
                    "action_id": candidate.action_id,
                    "parameters": candidate.parameters,
                }
            )
        with execution_audit_guard():
            session, _results, _trace = _run_actions(
                context,
                checked_actions,
                scenario_id=scenario_id,
            )
        built.append(
            {
                "scenario_id": scenario_id,
                "actions": checked_actions,
                "expected_initial_state_hash": initial_hash,
                "expected_final_state_hash": session.state_hash,
                "expected_classification": _classification_document(session.classification),
            }
        )
    built.sort(key=lambda item: item["scenario_id"].encode("utf-8"))
    document: dict[str, object] = {
        "format": GAME_EXECUTION_SCRIPT_FORMAT,
        "format_version": HEADLESS_CONTRACT_VERSION,
        "script_id": "",
        "bindings": _bindings(
            bundle,
            checked_composition,
            checked_adapter,
            checked_snapshot,
            context,
        ),
        "scenarios": built,
        "content_hash": "",
    }
    return validate_game_execution_script(
        bundle,
        _seal_script(document),
        gamepack=context.gamepack,
        composition=checked_composition,
        adapter=checked_adapter,
        runtime_snapshot=checked_snapshot,
    )


def _validate_coverage(
    gamepack: Mapping[str, Any],
    adapter: Mapping[str, Any],
    scenarios: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    mechanics = _array(
        _object(gamepack.get("logic"), "gamepack.logic").get("mechanics"),
        "gamepack.logic.mechanics",
        maximum=256,
    )
    mechanic_by_action: dict[str, list[dict[str, Any]]] = {}
    for index, raw in enumerate(mechanics):
        mechanic = _object(raw, f"gamepack.logic.mechanics/{index}")
        mechanic_by_action.setdefault(mechanic["action_id"], []).append(mechanic)
    scenario_ids_by_action: dict[str, set[str]] = {}
    for scenario in scenarios:
        for action in scenario["actions"]:
            scenario_ids_by_action.setdefault(action["action_id"], set()).add(
                scenario["scenario_id"]
            )
    missing_actions = sorted(
        set(mechanic_by_action) - set(scenario_ids_by_action),
        key=lambda item: item.encode("utf-8"),
    )
    if missing_actions:
        _fail(
            "coverage_violation",
            f"script does not cover executable mechanic actions: {missing_actions!r}",
        )
    required_features = _array(
        _object(
            gamepack.get("runtime_requirements"),
            "gamepack.runtime_requirements",
        ).get("required_features"),
        "gamepack.runtime_requirements.required_features",
        maximum=256,
    )
    supported_features = set(
        _array(
            adapter.get("supported_features"),
            "runtime adapter.supported_features",
            maximum=256,
        )
    )
    action_records: list[dict[str, object]] = []
    feature_records: list[dict[str, object]] = []
    feature_mechanics: dict[str, set[str]] = {feature: set() for feature in required_features}
    feature_scenarios: dict[str, set[str]] = {feature: set() for feature in required_features}
    for action_id in sorted(mechanic_by_action, key=lambda item: item.encode("utf-8")):
        action_mechanics = mechanic_by_action[action_id]
        scenario_ids = sorted(
            scenario_ids_by_action[action_id],
            key=lambda item: item.encode("utf-8"),
        )
        mechanic_ids = sorted(
            {mechanic["id"] for mechanic in action_mechanics},
            key=lambda item: item.encode("utf-8"),
        )
        action_records.append(
            {
                "action_id": action_id,
                "mechanic_ids": mechanic_ids,
                "scenario_ids": scenario_ids,
            }
        )
        for mechanic in action_mechanics:
            for feature in mechanic["required_feature_ids"]:
                if feature in feature_mechanics:
                    feature_mechanics[feature].add(mechanic["id"])
                    feature_scenarios[feature].update(scenario_ids)
    missing_features = [
        feature
        for feature in required_features
        if feature not in supported_features
        or not feature_mechanics[feature]
        or not feature_scenarios[feature]
    ]
    if missing_features:
        _fail(
            "coverage_violation",
            f"required runtime features lack explicit execution coverage: {missing_features!r}",
        )
    for feature in required_features:
        feature_records.append(
            {
                "feature_id": feature,
                "mechanic_ids": sorted(
                    feature_mechanics[feature],
                    key=lambda item: item.encode("utf-8"),
                ),
                "scenario_ids": sorted(
                    feature_scenarios[feature],
                    key=lambda item: item.encode("utf-8"),
                ),
            }
        )
    return {
        "complete": True,
        "actions": action_records,
        "required_features": feature_records,
    }


def validate_game_execution_script(
    runtime_bundle: object,
    value: object,
    *,
    gamepack: object,
    composition: object,
    adapter: object,
    runtime_snapshot: object,
) -> dict[str, object]:
    """Validate a script against one exact immutable pre-execution closure."""

    (
        bundle,
        checked_gamepack,
        checked_composition,
        checked_adapter,
        checked_snapshot,
        context,
    ) = _bundle_inputs(
        runtime_bundle,
        gamepack=gamepack,
        composition=composition,
        adapter=adapter,
        runtime_snapshot=runtime_snapshot,
    )
    document = _object(
        _own(
            value,
            maximum_bytes=MAX_GAME_EXECUTION_SCRIPT_BYTES,
            context="game execution script",
        ),
        "game execution script",
    )
    _exact_keys(document, _SCRIPT_FIELDS, "game execution script")
    if document.get("format") != GAME_EXECUTION_SCRIPT_FORMAT:
        _fail("script_invalid", f"format must be {GAME_EXECUTION_SCRIPT_FORMAT}")
    if document.get("format_version") != HEADLESS_CONTRACT_VERSION:
        _fail("script_invalid", "game execution script format_version must be 1")
    if (
        type(document.get("script_id")) is not str
        or _SCRIPT_ID_RE.fullmatch(document["script_id"]) is None
    ):
        _fail("script_invalid", "script_id is invalid")
    bindings = _object(document.get("bindings"), "game execution script.bindings")
    _exact_keys(bindings, _BINDING_FIELDS, "game execution script.bindings")
    for field in sorted(_BINDING_FIELDS, key=lambda item: item.encode("utf-8")):
        _validate_identity(bindings.get(field), f"game execution script.bindings.{field}")
    expected_bindings = _bindings(
        bundle,
        checked_composition,
        checked_adapter,
        checked_snapshot,
        context,
    )
    if bindings != expected_bindings:
        _fail("binding_mismatch", "game execution script bindings differ from exact inputs")
    scenarios = _array(
        document.get("scenarios"),
        "game execution script.scenarios",
        maximum=MAX_HEADLESS_SCENARIOS,
    )
    if not scenarios:
        _fail("script_invalid", "game execution script requires at least one scenario")
    checked_scenarios: list[dict[str, Any]] = []
    scenario_ids: list[str] = []
    total_actions = 0
    adapter_budget = _object(checked_adapter.get("budgets"), "runtime adapter.budgets").get(
        "max_actions"
    )
    if type(adapter_budget) is not int or isinstance(adapter_budget, bool):
        _fail("script_invalid", "runtime adapter max_actions is invalid")
    scenario_action_limit = min(MAX_HEADLESS_ACTIONS_PER_SCENARIO, adapter_budget)
    initial_hash = canonical_state_hash(GameSession(context.gamepack).state)
    for index, raw in enumerate(scenarios):
        scenario = _object(raw, f"game execution script.scenarios/{index}")
        _exact_keys(scenario, _SCENARIO_FIELDS, f"game execution script.scenarios/{index}")
        scenario_id = _identifier(
            scenario.get("scenario_id"),
            f"game execution script.scenarios/{index}.scenario_id",
        )
        scenario_ids.append(scenario_id)
        actions = scenario.get("actions")
        if type(actions) is not list:
            _fail(
                "script_invalid",
                f"game execution script.scenarios/{index}.actions must be an exact array",
            )
        if len(actions) > scenario_action_limit:
            _fail(
                "action_limit",
                f"scenario={scenario_id} exceeds the adapter action limit",
            )
        total_actions += len(actions)
        if total_actions > MAX_HEADLESS_TOTAL_ACTIONS:
            _fail("action_limit", "game execution script exceeds total action limit")
        checked_actions: list[dict[str, object]] = []
        for action_index, raw_action in enumerate(actions):
            action = _object(
                raw_action,
                f"game execution script.scenarios/{index}.actions/{action_index}",
            )
            _exact_keys(
                action,
                _ACTION_FIELDS,
                f"game execution script.scenarios/{index}.actions/{action_index}",
            )
            candidate = snapshot_strict_candidate(
                CandidateAction(
                    action.get("action_id"),  # type: ignore[arg-type]
                    action.get("parameters"),  # type: ignore[arg-type]
                )
            )
            checked_actions.append(
                {
                    "action_id": candidate.action_id,
                    "parameters": candidate.parameters,
                }
            )
        expected_initial = _sha256(
            scenario.get("expected_initial_state_hash"),
            f"game execution script.scenarios/{index}.expected_initial_state_hash",
        )
        if expected_initial != initial_hash:
            _fail(
                "expected_state_violation",
                f"scenario={scenario_id} expected initial state differs",
            )
        _sha256(
            scenario.get("expected_final_state_hash"),
            f"game execution script.scenarios/{index}.expected_final_state_hash",
        )
        classification = _validate_classification(
            scenario.get("expected_classification"),
            f"game execution script.scenarios/{index}.expected_classification",
        )
        checked_scenarios.append(
            {
                **scenario,
                "actions": checked_actions,
                "expected_classification": classification,
            }
        )
    if scenario_ids != sorted(scenario_ids, key=lambda item: item.encode("utf-8")):
        _fail("script_invalid", "scenario IDs must use canonical sorted order")
    if len({item.casefold() for item in scenario_ids}) != len(scenario_ids):
        _fail("script_invalid", "scenario IDs collide")
    _validate_coverage(checked_gamepack, checked_adapter, checked_scenarios)
    seed = {
        key: value for key, value in document.items() if key not in {"script_id", "content_hash"}
    }
    expected_id = "game_execution_script_" + canonical_headless_hash(seed)[:40]
    if document["script_id"] != expected_id:
        _fail("script_invalid", "script_id is not derived from exact script bytes")
    declared_hash = _sha256(
        document.get("content_hash"),
        "game execution script.content_hash",
    )
    if declared_hash != canonical_headless_hash(document):
        _fail("script_invalid", "game execution script content hash does not match")
    return copy.deepcopy(document)


def serialize_game_execution_script(
    value: object,
    runtime_bundle: object | None = None,
    *,
    gamepack: object | None = None,
    composition: object | None = None,
    adapter: object | None = None,
    runtime_snapshot: object | None = None,
) -> bytes:
    if runtime_bundle is None:
        document = _object(
            _own(
                value,
                maximum_bytes=MAX_GAME_EXECUTION_SCRIPT_BYTES,
                context="game execution script",
            ),
            "game execution script",
        )
        if document.get("content_hash") != canonical_headless_hash(document):
            _fail("script_invalid", "game execution script content hash does not match")
    else:
        document = validate_game_execution_script(
            runtime_bundle,
            value,
            gamepack=gamepack,
            composition=composition,
            adapter=adapter,
            runtime_snapshot=runtime_snapshot,
        )
    return (
        _canonical_bytes(
            document,
            maximum_bytes=MAX_GAME_EXECUTION_SCRIPT_BYTES,
        )
        + b"\n"
    )


def _audit_hook(event: str, _arguments: tuple[object, ...]) -> None:
    if getattr(_AUDIT_LOCAL, "depth", 0) <= 0:
        return
    exact = HEADLESS_AUDIT_POLICY["blocked_exact_events"]
    prefixes = HEADLESS_AUDIT_POLICY["blocked_event_prefixes"]
    if event in exact or any(event.startswith(prefix) for prefix in prefixes):
        _AUDIT_LOCAL.violation = event
        _fail("headless_audit_violation", f"blocked audit event: {event}")


def _install_audit_hook_once() -> None:
    global _AUDIT_INSTALLED
    if _AUDIT_INSTALLED:
        return
    with _AUDIT_INSTALL_LOCK:
        if not _AUDIT_INSTALLED:
            sys.addaudithook(_audit_hook)
            _AUDIT_INSTALLED = True


@contextmanager
def execution_audit_guard() -> Iterator[None]:
    """Activate the process-global audit hook only for the current execution thread."""

    _install_audit_hook_once()
    previous_depth = getattr(_AUDIT_LOCAL, "depth", 0)
    previous_violation = getattr(_AUDIT_LOCAL, "violation", None)
    _AUDIT_LOCAL.depth = previous_depth + 1
    _AUDIT_LOCAL.violation = None
    try:
        yield
    finally:
        _AUDIT_LOCAL.depth = previous_depth
        _AUDIT_LOCAL.violation = previous_violation


def _native_machine() -> str:
    """Return one kernel-derived architecture token without a platform service import."""

    if os.name == "posix":
        try:
            return os.uname().machine.casefold()
        except (AttributeError, OSError) as exc:
            _fail("platform_unsupported", f"could not inspect host architecture: {exc}")
    if os.name == "nt":  # pragma: no cover - native Windows CI
        import ctypes

        class _ProcessorIdentity(ctypes.Structure):
            _fields_ = [
                ("architecture", ctypes.c_ushort),
                ("reserved", ctypes.c_ushort),
            ]

        class _ProcessorUnion(ctypes.Union):
            _anonymous_ = ("processor",)
            _fields_ = [
                ("oem_id", ctypes.c_ulong),
                ("processor", _ProcessorIdentity),
            ]

        class _SystemInfo(ctypes.Structure):
            _anonymous_ = ("identity",)
            _fields_ = [
                ("identity", _ProcessorUnion),
                ("page_size", ctypes.c_ulong),
                ("minimum_application_address", ctypes.c_void_p),
                ("maximum_application_address", ctypes.c_void_p),
                ("active_processor_mask", ctypes.c_size_t),
                ("number_of_processors", ctypes.c_ulong),
                ("processor_type", ctypes.c_ulong),
                ("allocation_granularity", ctypes.c_ulong),
                ("processor_level", ctypes.c_ushort),
                ("processor_revision", ctypes.c_ushort),
            ]

        info = _SystemInfo()
        ctypes.windll.kernel32.GetNativeSystemInfo(ctypes.byref(info))
        return {
            0: "x86",
            5: "arm",
            6: "ia64",
            9: "amd64",
            12: "arm64",
        }.get(int(info.architecture), f"windows_arch_{int(info.architecture)}")
    return "unknown"


def _host_platform(adapter: Mapping[str, Any]) -> dict[str, object]:
    machine = _native_machine()
    if machine not in {"amd64", "x86_64"}:
        _fail("platform_unsupported", f"unsupported host architecture: {machine or 'unknown'}")
    if sys.platform.startswith("linux") and os.name == "posix":
        family = "platform:linux"
        platform_id = "platform:linux_x86_64"
    elif sys.platform == "win32" and os.name == "nt":  # pragma: no cover - native Windows CI
        family = "platform:windows"
        platform_id = "platform:windows_x86_64"
    else:
        _fail("platform_unsupported", f"unsupported host platform: {sys.platform}")
    implementation = _object(adapter.get("implementation"), "runtime adapter.implementation")
    host = {
        "platform_id": platform_id,
        "platform_family": family,
        "architecture": "architecture:x86_64",
        "backend": implementation.get("backend"),
        "renderer": implementation.get("renderer"),
    }
    if host not in adapter.get("platforms", []):
        _fail("platform_unsupported", "actual host is absent from the adapter support matrix")
    return host


def _check_record(
    check_id: str,
    kind: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    content_hash = _value_hash(payload)
    return {
        "check_id": check_id,
        "kind": kind,
        "status": "passed",
        "evidence_id": "headless_check_" + content_hash[:40],
        "content_hash": content_hash,
    }


def _seal_receipt(document: dict[str, object]) -> dict[str, object]:
    seed = {
        key: value for key, value in document.items() if key not in {"receipt_id", "content_hash"}
    }
    document["receipt_id"] = "headless_execution_receipt_" + canonical_headless_hash(seed)[:40]
    document["content_hash"] = canonical_headless_hash(document)
    return document


@dataclass(frozen=True, slots=True)
class HeadlessExecutionResult:
    receipt: dict[str, object]
    saves: Mapping[str, dict[str, object]]
    replays: Mapping[str, dict[str, object]]
    receipt_bytes: bytes
    save_bytes: Mapping[str, bytes]
    replay_bytes: Mapping[str, bytes]


def execute_game_execution_script(
    runtime_bundle: object,
    script: object,
    *,
    gamepack: object,
    composition: object,
    adapter: object,
    runtime_snapshot: object,
) -> HeadlessExecutionResult:
    """Run every script scenario twice, then prove save/restore/replay continuity."""

    (
        bundle,
        checked_gamepack,
        checked_composition,
        checked_adapter,
        checked_snapshot,
        context,
    ) = _bundle_inputs(
        runtime_bundle,
        gamepack=gamepack,
        composition=composition,
        adapter=adapter,
        runtime_snapshot=runtime_snapshot,
    )
    checked_script = validate_game_execution_script(
        bundle,
        script,
        gamepack=checked_gamepack,
        composition=checked_composition,
        adapter=checked_adapter,
        runtime_snapshot=checked_snapshot,
    )
    coverage = _validate_coverage(
        checked_gamepack,
        checked_adapter,
        checked_script["scenarios"],  # type: ignore[arg-type]
    )
    scenario_receipts: list[dict[str, object]] = []
    saves: dict[str, dict[str, object]] = {}
    replays: dict[str, dict[str, object]] = {}
    save_bytes: dict[str, bytes] = {}
    replay_bytes: dict[str, bytes] = {}
    with execution_audit_guard():
        for scenario in checked_script["scenarios"]:  # type: ignore[assignment]
            scenario_id = scenario["scenario_id"]
            first_session, first_results, first_trace = _run_actions(
                context,
                scenario["actions"],
                scenario_id=scenario_id,
            )
            second_session, _second_results, second_trace = _run_actions(
                context,
                scenario["actions"],
                scenario_id=scenario_id,
            )
            if (
                first_trace != second_trace
                or first_session.state != second_session.state
                or first_session.classification != second_session.classification
            ):
                _fail(
                    "determinism_violation",
                    f"scenario={scenario_id} repeated execution differs",
                )
            if first_session.state_hash != scenario["expected_final_state_hash"]:
                _fail(
                    "expected_state_violation",
                    f"scenario={scenario_id} final state differs",
                )
            classification = _classification_document(first_session.classification)
            if classification != scenario["expected_classification"]:
                _fail(
                    "expected_classification_violation",
                    f"scenario={scenario_id} classification differs",
                )
            save = build_game_save(context, first_session.state)
            restored = restore_game_save(context, save)
            if canonical_state_hash(restored) != first_session.state_hash:
                _fail(
                    "save_violation",
                    f"scenario={scenario_id} restored state differs",
                )
            replay = build_game_replay(context, first_results)
            played = play_game_replay(context, replay)
            if (
                played.state_hash != first_session.state_hash
                or played.classification != first_session.classification
            ):
                _fail(
                    "replay_violation",
                    f"scenario={scenario_id} replayed result differs",
                )
            trace_hash = _value_hash({"steps": first_trace})
            saves[scenario_id] = save
            replays[scenario_id] = replay
            save_bytes[scenario_id] = serialize_game_save(save, context)
            replay_bytes[scenario_id] = serialize_game_replay(replay, context)
            scenario_receipts.append(
                {
                    "scenario_id": scenario_id,
                    "action_count": len(first_trace),
                    "trace_hash": trace_hash,
                    "final_state_hash": first_session.state_hash,
                    "classification": classification,
                    "save": {
                        "id": save["save_id"],
                        "content_hash": save["content_hash"],
                        "restored_state_hash": canonical_state_hash(restored),
                    },
                    "replay": {
                        "id": replay["replay_id"],
                        "content_hash": replay["content_hash"],
                        "replayed_state_hash": played.state_hash,
                    },
                }
            )
    headless_payload = {
        "script_hash": checked_script["content_hash"],
        "scenarios": [
            {
                "scenario_id": item["scenario_id"],
                "action_count": item["action_count"],
                "trace_hash": item["trace_hash"],
                "final_state_hash": item["final_state_hash"],
                "classification": item["classification"],
            }
            for item in scenario_receipts
        ],
        "coverage": coverage,
    }
    persistence_payload = {
        "script_hash": checked_script["content_hash"],
        "scenarios": [
            {
                "scenario_id": item["scenario_id"],
                "save": item["save"],
                "replay": item["replay"],
            }
            for item in scenario_receipts
        ],
    }
    checks = [
        _check_record("check:headless_determinism", "headless", headless_payload),
        _check_record("check:save_replay", "save_replay", persistence_payload),
    ]
    checks.sort(key=lambda item: item["check_id"].encode("utf-8"))
    adapter_identity = _identity(checked_adapter, id_field="adapter_id")
    receipt: dict[str, object] = {
        "format": HEADLESS_EXECUTION_RECEIPT_FORMAT,
        "format_version": HEADLESS_CONTRACT_VERSION,
        "receipt_id": "",
        "bindings": {
            **_bindings(
                bundle,
                checked_composition,
                checked_adapter,
                checked_snapshot,
                context,
            ),
            "execution_script": _identity(
                checked_script,
                id_field="script_id",
            ),
        },
        "host": _host_platform(checked_adapter),
        "executor": {
            "key": "gamepack_runtime.headless.v1",
            "adapter_id": checked_adapter["adapter_id"],
            "adapter_version": checked_adapter["adapter_version"],
            "adapter_hash": adapter_identity["content_hash"],
        },
        "runtime_api": dict(GAMEPACK_RUNTIME_API),
        "execution_semantics": _semantics_identity(context),
        "policies": {
            "verifier_policy_hash": _value_hash(dict(HEADLESS_VERIFIER_POLICY)),
            "audit_policy_hash": _value_hash(dict(HEADLESS_AUDIT_POLICY)),
        },
        "native_execution": False,
        "scenarios": scenario_receipts,
        "coverage": coverage,
        "checks": checks,
        "status": "passed",
        "failure": None,
        "content_hash": "",
    }
    checked_receipt = validate_headless_execution_receipt(_seal_receipt(receipt))
    return HeadlessExecutionResult(
        receipt=checked_receipt,
        saves=MappingProxyType(copy.deepcopy(saves)),
        replays=MappingProxyType(copy.deepcopy(replays)),
        receipt_bytes=serialize_headless_execution_receipt(checked_receipt),
        save_bytes=MappingProxyType(dict(save_bytes)),
        replay_bytes=MappingProxyType(dict(replay_bytes)),
    )


def validate_headless_execution_receipt(value: object) -> dict[str, object]:
    document = _object(
        _own(
            value,
            maximum_bytes=MAX_HEADLESS_EXECUTION_RECEIPT_BYTES,
            context="headless execution receipt",
        ),
        "headless execution receipt",
    )
    _exact_keys(document, _RECEIPT_FIELDS, "headless execution receipt")
    if document.get("format") != HEADLESS_EXECUTION_RECEIPT_FORMAT:
        _fail("receipt_invalid", f"format must be {HEADLESS_EXECUTION_RECEIPT_FORMAT}")
    if document.get("format_version") != HEADLESS_CONTRACT_VERSION:
        _fail("receipt_invalid", "headless execution receipt format_version must be 1")
    if (
        type(document.get("receipt_id")) is not str
        or _RECEIPT_ID_RE.fullmatch(document["receipt_id"]) is None
    ):
        _fail("receipt_invalid", "receipt_id is invalid")
    bindings = _object(document.get("bindings"), "headless execution receipt.bindings")
    expected_binding_fields = frozenset({*_BINDING_FIELDS, "execution_script"})
    _exact_keys(bindings, expected_binding_fields, "headless execution receipt.bindings")
    for field in sorted(expected_binding_fields, key=lambda item: item.encode("utf-8")):
        _validate_identity(bindings.get(field), f"headless execution receipt.bindings.{field}")
    host = _object(document.get("host"), "headless execution receipt.host")
    _exact_keys(host, _HOST_FIELDS, "headless execution receipt.host")
    if host.get("platform_id") not in {
        "platform:linux_x86_64",
        "platform:windows_x86_64",
    }:
        _fail("receipt_invalid", "receipt host platform is unsupported")
    if host.get("architecture") != "architecture:x86_64":
        _fail("receipt_invalid", "receipt host architecture is unsupported")
    if host.get("backend") != "backend:raylib" or host.get("renderer") != "raylib":
        _fail("receipt_invalid", "receipt host backend differs from adapter target")
    executor = _object(document.get("executor"), "headless execution receipt.executor")
    _exact_keys(executor, _EXECUTOR_FIELDS, "headless execution receipt.executor")
    if executor.get("key") != "gamepack_runtime.headless.v1":
        _fail("executor_absent", "receipt executor key is not code-owned")
    _identifier(executor.get("adapter_id"), "headless execution receipt.executor.adapter_id")
    if type(executor.get("adapter_version")) is not str or not executor["adapter_version"]:
        _fail("receipt_invalid", "receipt adapter version is invalid")
    _sha256(executor.get("adapter_hash"), "headless execution receipt.executor.adapter_hash")
    runtime_api = _object(document.get("runtime_api"), "headless execution receipt.runtime_api")
    if runtime_api != dict(GAMEPACK_RUNTIME_API):
        _fail("receipt_invalid", "receipt runtime API differs")
    semantics = _object(
        document.get("execution_semantics"),
        "headless execution receipt.execution_semantics",
    )
    if frozenset(semantics) != frozenset({"version", "content_hash"}):
        _fail("receipt_invalid", "receipt execution semantics fields differ")
    if semantics.get("version") != 1:
        _fail("receipt_invalid", "receipt execution semantics version differs")
    _sha256(semantics.get("content_hash"), "headless execution receipt semantics hash")
    policies = _object(document.get("policies"), "headless execution receipt.policies")
    _exact_keys(policies, _POLICY_FIELDS, "headless execution receipt.policies")
    if policies != {
        "verifier_policy_hash": _value_hash(dict(HEADLESS_VERIFIER_POLICY)),
        "audit_policy_hash": _value_hash(dict(HEADLESS_AUDIT_POLICY)),
    }:
        _fail("receipt_invalid", "receipt policy hashes differ from code-owned policies")
    if document.get("native_execution") is not False:
        _fail("receipt_invalid", "headless receipt must not claim native execution")
    scenarios = _array(
        document.get("scenarios"),
        "headless execution receipt.scenarios",
        maximum=MAX_HEADLESS_SCENARIOS,
    )
    scenario_ids: list[str] = []
    for index, raw in enumerate(scenarios):
        scenario = _object(raw, f"headless execution receipt.scenarios/{index}")
        _exact_keys(
            scenario,
            _SCENARIO_RESULT_FIELDS,
            f"headless execution receipt.scenarios/{index}",
        )
        scenario_ids.append(
            _identifier(
                scenario.get("scenario_id"),
                f"headless execution receipt.scenarios/{index}.scenario_id",
            )
        )
        action_count = scenario.get("action_count")
        if (
            type(action_count) is not int
            or isinstance(action_count, bool)
            or not 0 <= action_count <= MAX_HEADLESS_ACTIONS_PER_SCENARIO
        ):
            _fail("receipt_invalid", "receipt action_count is invalid")
        for field in ("trace_hash", "final_state_hash"):
            _sha256(
                scenario.get(field),
                f"headless execution receipt.scenarios/{index}.{field}",
            )
        _validate_classification(
            scenario.get("classification"),
            f"headless execution receipt.scenarios/{index}.classification",
        )
        save = _object(
            scenario.get("save"),
            f"headless execution receipt.scenarios/{index}.save",
        )
        _exact_keys(save, _SAVE_RESULT_FIELDS, f"headless execution receipt.scenarios/{index}.save")
        replay = _object(
            scenario.get("replay"),
            f"headless execution receipt.scenarios/{index}.replay",
        )
        _exact_keys(
            replay,
            _REPLAY_RESULT_FIELDS,
            f"headless execution receipt.scenarios/{index}.replay",
        )
        for result, id_field, hash_field, prefix in (
            (save, "id", "restored_state_hash", "game_save_"),
            (replay, "id", "replayed_state_hash", "game_replay_"),
        ):
            if type(result.get(id_field)) is not str or not result[id_field].startswith(prefix):
                _fail("receipt_invalid", "receipt persistence ID is invalid")
            _sha256(result.get("content_hash"), "receipt persistence content hash")
            _sha256(result.get(hash_field), "receipt persistence state hash")
        if (
            save["restored_state_hash"] != scenario["final_state_hash"]
            or replay["replayed_state_hash"] != scenario["final_state_hash"]
        ):
            _fail("receipt_invalid", "receipt persistence state continuity differs")
    if scenario_ids != sorted(scenario_ids, key=lambda item: item.encode("utf-8")):
        _fail("receipt_invalid", "receipt scenarios are not canonical")
    coverage = _object(document.get("coverage"), "headless execution receipt.coverage")
    _exact_keys(coverage, _COVERAGE_FIELDS, "headless execution receipt.coverage")
    if coverage.get("complete") is not True:
        _fail("coverage_violation", "receipt must record complete execution coverage")
    for field, fields in (
        ("actions", _ACTION_COVERAGE_FIELDS),
        ("required_features", _FEATURE_COVERAGE_FIELDS),
    ):
        records = _array(
            coverage.get(field),
            f"headless execution receipt.coverage.{field}",
            maximum=256,
        )
        previous: bytes | None = None
        for index, raw in enumerate(records):
            record = _object(raw, f"headless execution receipt.coverage.{field}/{index}")
            _exact_keys(
                record,
                fields,
                f"headless execution receipt.coverage.{field}/{index}",
            )
            key_field = "action_id" if field == "actions" else "feature_id"
            key = record.get(key_field)
            if type(key) is not str or not key:
                _fail("receipt_invalid", f"coverage {key_field} is invalid")
            encoded = key.encode("utf-8")
            if previous is not None and encoded <= previous:
                _fail("receipt_invalid", f"coverage {field} is not canonical")
            previous = encoded
            for ids_field in ("mechanic_ids", "scenario_ids"):
                ids = _array(
                    record.get(ids_field),
                    f"headless execution receipt.coverage.{field}/{index}.{ids_field}",
                    maximum=256,
                )
                if not ids:
                    _fail("coverage_violation", f"coverage {ids_field} must be non-empty")
                for id_index, identifier in enumerate(ids):
                    _identifier(identifier, f"coverage {ids_field}/{id_index}")
                if ids != sorted(ids, key=lambda item: item.encode("utf-8")):
                    _fail("receipt_invalid", f"coverage {ids_field} is not canonical")
    checks = _array(
        document.get("checks"),
        "headless execution receipt.checks",
        maximum=2,
    )
    if [item.get("check_id") for item in checks if type(item) is dict] != [
        "check:headless_determinism",
        "check:save_replay",
    ]:
        _fail("receipt_invalid", "receipt checks differ from exact required closure")
    for index, raw in enumerate(checks):
        check = _object(raw, f"headless execution receipt.checks/{index}")
        _exact_keys(check, _CHECK_FIELDS, f"headless execution receipt.checks/{index}")
        if check.get("status") not in {"passed", "failed"}:
            _fail("receipt_invalid", "receipt check status is invalid")
        if type(check.get("evidence_id")) is not str or not check["evidence_id"].startswith(
            "headless_check_"
        ):
            _fail("receipt_invalid", "receipt check evidence ID is invalid")
        _sha256(check.get("content_hash"), "receipt check content hash")
    if document.get("status") != "passed" or document.get("failure") is not None:
        _fail("receipt_invalid", "published receipt must be a passed terminal receipt")
    if any(check["status"] != "passed" for check in checks):
        _fail("receipt_invalid", "passed receipt contains a failed nested check")
    seed = {
        key: value for key, value in document.items() if key not in {"receipt_id", "content_hash"}
    }
    expected_id = "headless_execution_receipt_" + canonical_headless_hash(seed)[:40]
    if document["receipt_id"] != expected_id:
        _fail("receipt_invalid", "receipt_id is not derived from exact receipt bytes")
    if document.get("content_hash") != canonical_headless_hash(document):
        _fail("receipt_invalid", "headless receipt content hash does not match")
    return copy.deepcopy(document)


def serialize_headless_execution_receipt(value: object) -> bytes:
    return (
        _canonical_bytes(
            validate_headless_execution_receipt(value),
            maximum_bytes=MAX_HEADLESS_EXECUTION_RECEIPT_BYTES,
        )
        + b"\n"
    )


__all__ = [
    "GAME_EXECUTION_SCRIPT_FORMAT",
    "HEADLESS_AUDIT_POLICY",
    "HEADLESS_CONTRACT_VERSION",
    "HEADLESS_EXECUTION_RECEIPT_FORMAT",
    "HEADLESS_VERIFIER_POLICY",
    "MAX_GAME_EXECUTION_SCRIPT_BYTES",
    "MAX_HEADLESS_ACTIONS_PER_SCENARIO",
    "MAX_HEADLESS_EXECUTION_RECEIPT_BYTES",
    "MAX_HEADLESS_SCENARIOS",
    "MAX_HEADLESS_STATE_BYTES",
    "MAX_HEADLESS_TOTAL_ACTIONS",
    "HeadlessExecutionResult",
    "build_game_execution_script",
    "canonical_headless_hash",
    "execute_game_execution_script",
    "execution_audit_guard",
    "serialize_game_execution_script",
    "serialize_headless_execution_receipt",
    "validate_game_execution_script",
    "validate_headless_execution_receipt",
]
