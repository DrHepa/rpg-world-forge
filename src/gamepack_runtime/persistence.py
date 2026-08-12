"""Deterministic save and replay contracts for the neutral gamepack runtime."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path, PosixPath, WindowsPath
from types import MappingProxyType
from typing import Any

from gamepack_runtime.contracts import (
    ANALYSIS_LIMITS,
    EXECUTION_SEMANTICS,
    CandidateAction,
    GameLogicError,
    JsonValue,
    StateClassification,
    TransitionResult,
    canonical_state_bytes,
    canonical_state_hash,
    snapshot_plain_json,
    snapshot_strict_candidate,
    snapshot_strict_state,
    validate_runtime_gamepack,
)
from gamepack_runtime.persistence_io import (
    decode_json_object,
)
from gamepack_runtime.semantics_v1 import GamepackInterpreter, canonical_trace_step
from gamepack_runtime.session import GameSession

GAME_SAVE_FORMAT = "world-forge.game_save"
GAME_REPLAY_FORMAT = "world-forge.game_replay"
GAME_PERSISTENCE_VERSION = 1
GAMEPACK_RUNTIME_API = MappingProxyType(
    {
        "id": "gamepack_runtime",
        "version": "1.0.0",
    }
)
MAX_GAME_SAVE_BYTES = 256 * 1024
MAX_GAME_REPLAY_BYTES = 4 * 1024 * 1024
MAX_REPLAY_ACTIONS = 128
_MAX_CANONICAL_PERSISTENCE_BYTES = MAX_GAME_REPLAY_BYTES + 64 * 1024

_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_GAME_PATH_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_BUNDLE_PATH_ID_RE = re.compile(r"^game_runtime_bundle_[0-9a-f]{48}$")
_SAVE_ID_RE = re.compile(r"^game_save_[0-9a-f]{48}$")
_REPLAY_ID_RE = re.compile(r"^game_replay_[0-9a-f]{48}$")
_SLOT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_WINDOWS_RESERVED = frozenset(
    {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_RUNTIME_API_FIELDS = frozenset({"id", "version"})
_SEMANTICS_FIELDS = frozenset({"version", "content_hash"})
_BINDING_FIELDS = frozenset(
    {
        "gamepack",
        "runtime_composition",
        "runtime_bundle",
        "runtime_api",
        "execution_semantics",
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
_SAVE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "save_id",
        "bindings",
        "state",
        "content_hash",
    }
)
_SAVE_STATE_FIELDS = frozenset(
    {
        "saved",
        "saved_hash",
        "restored_state_hash",
        "classification",
    }
)
_REPLAY_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "replay_id",
        "bindings",
        "initial_state_hash",
        "steps",
        "final_state_hash",
        "classification",
        "trace_hash",
        "content_hash",
    }
)
_STEP_FIELDS = frozenset(
    {
        "index",
        "action_id",
        "parameters",
        "pre_state_hash",
        "post_state_hash",
        "events",
    }
)
_CONTEXT_CONSTRUCTION_TOKEN = object()


def _fail(reason_code: str, detail: str) -> None:
    raise GameLogicError(reason_code, detail)


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], context: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual, key=lambda item: item.encode("utf-8"))
        extra = sorted(actual - expected, key=lambda item: item.encode("utf-8"))
        _fail(
            "persistence_contract_invalid",
            f"{context} fields differ; missing={missing!r} extra={extra!r}",
        )


def _object(value: object, context: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("persistence_contract_invalid", f"{context} must be an exact object")
    return value


def _array(value: object, context: str, *, maximum: int) -> list[Any]:
    if type(value) is not list or len(value) > maximum:
        _fail(
            "persistence_contract_invalid",
            f"{context} must be an exact array of at most {maximum} items",
        )
    return value


def _sha256(value: object, context: str) -> str:
    if type(value) is not str or _HEX_RE.fullmatch(value) is None:
        _fail("persistence_contract_invalid", f"{context} must be lowercase SHA-256")
    return value


def _nfc_text(value: object, context: str, *, maximum: int = 128) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or unicodedata.normalize("NFC", value) != value
    ):
        _fail(
            "persistence_contract_invalid",
            f"{context} must be a non-empty bounded NFC string",
        )
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise GameLogicError(
            "persistence_contract_invalid",
            f"{context} must be valid UTF-8",
        ) from exc
    return value


def _portable_path_identity(
    value: object,
    context: str,
    *,
    pattern: re.Pattern[str],
) -> str:
    if (
        type(value) is not str
        or pattern.fullmatch(value) is None
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "." in value
        or value.casefold() in _WINDOWS_RESERVED
        or unicodedata.normalize("NFC", value) != value
    ):
        _fail(
            "persistence_path_identity_invalid",
            f"{context} must be one portable lowercase ASCII path segment",
        )
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise GameLogicError(
            "persistence_path_identity_invalid",
            f"{context} must be valid UTF-8",
        ) from exc
    return value


def _own(value: object, *, maximum_bytes: int, context: str) -> Any:
    try:
        owned = snapshot_plain_json(value, maximum_bytes=maximum_bytes)
    except GameLogicError as exc:
        _fail(exc.reason_code, f"{context}: {exc.detail}")
    return owned


def _canonical_bytes(value: object) -> bytes:
    owned = _own(
        value,
        maximum_bytes=_MAX_CANONICAL_PERSISTENCE_BYTES,
        context="canonical persistence value",
    )
    try:
        return json.dumps(
            owned,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError, OverflowError) as exc:
        raise GameLogicError(
            "persistence_contract_invalid",
            f"could not encode canonical persistence JSON: {exc}",
        ) from exc


def canonical_persistence_hash(value: Mapping[str, object]) -> str:
    """Hash one strict persistence object while excluding its content hash."""

    owned = _own(
        value,
        maximum_bytes=_MAX_CANONICAL_PERSISTENCE_BYTES,
        context="persistence hash input",
    )
    if type(owned) is not dict:
        _fail("persistence_contract_invalid", "persistence hash input must be an object")
    owned.pop("content_hash", None)
    return hashlib.sha256(_canonical_bytes(owned)).hexdigest()


def _canonical_value_hash(value: Mapping[str, object]) -> str:
    owned = _own(
        value,
        maximum_bytes=_MAX_CANONICAL_PERSISTENCE_BYTES,
        context="persistence value hash input",
    )
    if type(owned) is not dict:
        _fail("persistence_contract_invalid", "value hash input must be an object")
    return hashlib.sha256(_canonical_bytes(owned)).hexdigest()


def _content_hash_matches(document: Mapping[str, object], context: str) -> None:
    declared = _sha256(document.get("content_hash"), f"{context}.content_hash")
    if declared != canonical_persistence_hash(document):
        _fail("persistence_hash_mismatch", f"{context} content hash does not match")


def _identity(
    *,
    format_name: object,
    format_version: object,
    identifier: object,
    content_hash: object,
    context: str,
) -> dict[str, object]:
    return {
        "format": _nfc_text(format_name, f"{context}.format"),
        "format_version": _positive_integer(
            format_version,
            f"{context}.format_version",
        ),
        "id": _nfc_text(identifier, f"{context}.id"),
        "content_hash": _sha256(content_hash, f"{context}.content_hash"),
    }


def _positive_integer(value: object, context: str, *, maximum: int = 9_007_199_254_740_991) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail("persistence_contract_invalid", f"{context} must be a positive safe integer")
    return value


def _document_identity(
    value: object,
    *,
    format_name: str,
    id_field: str,
    context: str,
) -> dict[str, object]:
    document = _object(value, context)
    if document.get("format") != format_name or document.get("format_version") != 1:
        _fail(
            "persistence_context_mismatch",
            f"{context} has an unsupported format or version",
        )
    _content_hash_matches(document, context)
    return _identity(
        format_name=document["format"],
        format_version=document["format_version"],
        identifier=document.get(id_field),
        content_hash=document["content_hash"],
        context=context,
    )


def _semantics_identity() -> dict[str, object]:
    return {
        "version": 1,
        "content_hash": canonical_persistence_hash(dict(EXECUTION_SEMANTICS)),
    }


class GamePersistenceContext:
    """Owned immutable identities and limits for one exact runtime bundle."""

    __slots__ = (
        "_adapter",
        "_bindings",
        "_gamepack",
        "_max_actions",
        "_max_state_bytes",
    )

    def __init__(
        self,
        *,
        gamepack: dict[str, object],
        adapter: dict[str, object],
        bindings: dict[str, object],
        max_actions: int,
        max_state_bytes: int,
        _token: object | None = None,
    ) -> None:
        if _token is not _CONTEXT_CONSTRUCTION_TOKEN:
            _fail(
                "persistence_context_invalid",
                "contexts must be created by build_game_persistence_context",
            )
        self._gamepack = copy.deepcopy(gamepack)
        self._adapter = copy.deepcopy(adapter)
        self._bindings = copy.deepcopy(bindings)
        self._max_actions = max_actions
        self._max_state_bytes = max_state_bytes

    @property
    def gamepack(self) -> dict[str, object]:
        return copy.deepcopy(self._gamepack)

    @property
    def adapter(self) -> dict[str, object]:
        return copy.deepcopy(self._adapter)

    @property
    def bindings(self) -> dict[str, object]:
        return copy.deepcopy(self._bindings)

    @property
    def gamepack_identity(self) -> dict[str, object]:
        return copy.deepcopy(self._bindings["gamepack"])  # type: ignore[return-value]

    @property
    def runtime_composition_identity(self) -> dict[str, object]:
        return copy.deepcopy(self._bindings["runtime_composition"])  # type: ignore[return-value]

    @property
    def runtime_bundle_identity(self) -> dict[str, object]:
        return copy.deepcopy(self._bindings["runtime_bundle"])  # type: ignore[return-value]

    @property
    def max_actions(self) -> int:
        return self._max_actions

    @property
    def max_state_bytes(self) -> int:
        return self._max_state_bytes


def _require_context(value: object) -> GamePersistenceContext:
    if type(value) is not GamePersistenceContext:
        _fail(
            "persistence_context_invalid",
            "context must be an exact GamePersistenceContext",
        )
    return value


def build_game_persistence_context(
    gamepack: Mapping[str, object],
    composition: Mapping[str, object],
    runtime_bundle: Mapping[str, object],
    adapter: Mapping[str, object],
) -> GamePersistenceContext:
    """Bind persistence to one validated gamepack/composition/bundle/adapter closure."""

    checked_gamepack = validate_runtime_gamepack(gamepack)
    checked_composition = _own(
        composition,
        maximum_bytes=MAX_GAME_REPLAY_BYTES,
        context="runtime composition",
    )
    checked_bundle = _own(
        runtime_bundle,
        maximum_bytes=MAX_GAME_REPLAY_BYTES,
        context="runtime bundle",
    )
    checked_adapter = _own(
        adapter,
        maximum_bytes=MAX_GAME_REPLAY_BYTES,
        context="runtime adapter",
    )
    for document, context in (
        (checked_composition, "runtime composition"),
        (checked_bundle, "runtime bundle"),
        (checked_adapter, "runtime adapter"),
    ):
        if type(document) is not dict:
            _fail("persistence_context_invalid", f"{context} must be an object")

    game = _object(checked_gamepack.get("game"), "gamepack.game")
    gamepack_identity = _identity(
        format_name=checked_gamepack["format"],
        format_version=checked_gamepack["format_version"],
        identifier=game.get("id"),
        content_hash=checked_gamepack["content_hash"],
        context="gamepack",
    )
    composition_identity = _document_identity(
        checked_composition,
        format_name="world-forge.game_runtime_composition",
        id_field="composition_id",
        context="runtime composition",
    )
    bundle_identity = _document_identity(
        checked_bundle,
        format_name="world-forge.game_runtime_bundle",
        id_field="bundle_id",
        context="runtime bundle",
    )
    _portable_path_identity(
        gamepack_identity["id"],
        "gamepack.id",
        pattern=_GAME_PATH_ID_RE,
    )
    _portable_path_identity(
        bundle_identity["id"],
        "runtime bundle.id",
        pattern=_BUNDLE_PATH_ID_RE,
    )
    adapter_identity = _document_identity(
        checked_adapter,
        format_name="world-forge.runtime_adapter",
        id_field="adapter_id",
        context="runtime adapter",
    )

    if checked_composition.get("gamepack") != gamepack_identity:
        _fail(
            "persistence_context_mismatch",
            "runtime composition does not reference the exact gamepack",
        )
    expected_adapter = {
        **adapter_identity,
    }
    composition_adapter = checked_composition.get("adapter")
    if composition_adapter != expected_adapter:
        _fail(
            "persistence_context_mismatch",
            "runtime composition does not reference the exact adapter",
        )
    contracts = _object(checked_bundle.get("contracts"), "runtime bundle.contracts")
    for field, expected in (
        ("gamepack", gamepack_identity),
        ("runtime_composition", composition_identity),
    ):
        actual = _object(contracts.get(field), f"runtime bundle.contracts.{field}")
        projected = {
            key: actual.get(key) for key in ("format", "format_version", "id", "content_hash")
        }
        if projected != expected:
            _fail(
                "persistence_context_mismatch",
                f"runtime bundle {field} identity differs",
            )
    bundle_adapter = _object(
        contracts.get("runtime_adapter"),
        "runtime bundle.contracts.runtime_adapter",
    )
    if {
        key: bundle_adapter.get(key) for key in ("format", "format_version", "id", "content_hash")
    } != adapter_identity:
        _fail(
            "persistence_context_mismatch",
            "runtime bundle adapter identity differs",
        )

    runtime_tree = _object(
        checked_bundle.get("runtime_snapshot_tree"),
        "runtime bundle.runtime_snapshot_tree",
    )
    runtime_api = _object(runtime_tree.get("runtime_api"), "runtime bundle.runtime_api")
    _exact_keys(runtime_api, _RUNTIME_API_FIELDS, "runtime bundle.runtime_api")
    if runtime_api != dict(GAMEPACK_RUNTIME_API):
        _fail(
            "persistence_runtime_api_mismatch",
            "runtime bundle requires a different neutral runtime API",
        )
    implementation = _object(
        checked_adapter.get("implementation"),
        "runtime adapter.implementation",
    )
    if implementation.get("runtime_api") != runtime_api:
        _fail(
            "persistence_runtime_api_mismatch",
            "runtime adapter requires a different neutral runtime API",
        )
    semantics = _object(
        checked_adapter.get("execution_semantics"),
        "runtime adapter.execution_semantics",
    )
    if semantics != _semantics_identity():
        _fail(
            "persistence_semantics_mismatch",
            "runtime adapter requires different execution semantics",
        )
    if checked_gamepack["logic"]["execution_semantics"] != EXECUTION_SEMANTICS:
        _fail(
            "persistence_semantics_mismatch",
            "gamepack requires different execution semantics",
        )
    budgets = _object(checked_adapter.get("budgets"), "runtime adapter.budgets")
    max_actions = _positive_integer(
        budgets.get("max_actions"),
        "runtime adapter.budgets.max_actions",
        maximum=MAX_REPLAY_ACTIONS,
    )
    max_state_bytes = _positive_integer(
        budgets.get("max_state_bytes"),
        "runtime adapter.budgets.max_state_bytes",
        maximum=int(ANALYSIS_LIMITS["state_bytes"]),
    )
    bindings = {
        "gamepack": gamepack_identity,
        "runtime_composition": composition_identity,
        "runtime_bundle": bundle_identity,
        "runtime_api": dict(GAMEPACK_RUNTIME_API),
        "execution_semantics": _semantics_identity(),
    }
    return GamePersistenceContext(
        gamepack=checked_gamepack,
        adapter=checked_adapter,
        bindings=bindings,
        max_actions=max_actions,
        max_state_bytes=max_state_bytes,
        _token=_CONTEXT_CONSTRUCTION_TOKEN,
    )


def _classification_document(value: StateClassification) -> dict[str, object]:
    if type(value) is not StateClassification:
        _fail("classification_invalid", "classification must be exact")
    return {
        "goal_ids": list(value.goal_ids),
        "ending_ids": list(value.ending_ids),
        "ending_kind": value.ending_kind,
        "failure_ids": list(value.failure_ids),
        "recovery_action_ids": list(value.recovery_action_ids),
        "terminal": value.terminal,
    }


def _validate_classification(
    value: object,
    expected: StateClassification,
    *,
    reason_code: str,
) -> dict[str, object]:
    document = _object(value, "classification")
    _exact_keys(document, _CLASSIFICATION_FIELDS, "classification")
    expected_document = _classification_document(expected)
    if document != expected_document:
        _fail(reason_code, "recorded classification differs from runtime classification")
    return document


def _state_schema(context: GamePersistenceContext) -> list[dict[str, Any]]:
    logic = _object(context._gamepack.get("logic"), "gamepack.logic")
    schema = _array(logic.get("state_schema"), "gamepack.logic.state_schema", maximum=256)
    result: list[dict[str, Any]] = []
    for index, item in enumerate(schema):
        record = _object(item, f"gamepack.logic.state_schema/{index}")
        result.append(record)
    return result


def _validated_full_state(
    context: GamePersistenceContext,
    state: object,
) -> tuple[dict[str, JsonValue], StateClassification]:
    checked = snapshot_strict_state(state)
    encoded = canonical_state_bytes(checked)
    if len(encoded) > context.max_state_bytes:
        _fail("state_bytes_exceeded", "state exceeds the adapter state-byte budget")
    interpreter = GamepackInterpreter(context._gamepack, already_validated=True)
    classification = interpreter.classify(checked)
    for record in _state_schema(context):
        state_id = record["id"]
        if record.get("mutability") == "constant" and checked[state_id] != record.get("initial"):
            _fail(
                "save_constant_mismatch",
                f"constant state {state_id} differs from its immutable initial value",
            )
    return checked, classification


def _saved_projection(
    context: GamePersistenceContext,
    state: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return {
        record["id"]: copy.deepcopy(state[record["id"]])
        for record in _state_schema(context)
        if record.get("persistence") == "saved"
    }


def _restore_projection(
    context: GamePersistenceContext,
    saved: object,
) -> tuple[dict[str, JsonValue], StateClassification]:
    projection = _object(saved, "save.state.saved")
    expected = [record for record in _state_schema(context) if record.get("persistence") == "saved"]
    expected_ids = {record["id"] for record in expected}
    if set(projection) != expected_ids:
        _fail(
            "save_state_keys_mismatch",
            "saved-state keys do not exactly match persistence:saved state",
        )
    initial = GamepackInterpreter(
        context._gamepack,
        already_validated=True,
    ).initial_state()
    for record in expected:
        state_id = record["id"]
        if record.get("mutability") == "constant" and projection[state_id] != record.get("initial"):
            _fail(
                "save_constant_mismatch",
                f"constant state {state_id} differs from its immutable initial value",
            )
        initial[state_id] = copy.deepcopy(projection[state_id])
    return _validated_full_state(context, initial)


def _seal_document(
    document: dict[str, object],
    *,
    id_field: str,
    prefix: str,
) -> dict[str, object]:
    seed = {key: value for key, value in document.items() if key not in {id_field, "content_hash"}}
    document[id_field] = prefix + canonical_persistence_hash(seed)[:48]
    document["content_hash"] = canonical_persistence_hash(document)
    return document


def build_game_save(
    context: GamePersistenceContext,
    state: Mapping[str, JsonValue],
) -> dict[str, object]:
    """Create a deterministic saved-state projection for one exact bundle."""

    checked_context = _require_context(context)
    checked_state, _ = _validated_full_state(checked_context, state)
    saved = _saved_projection(checked_context, checked_state)
    restored, classification = _restore_projection(checked_context, saved)
    document: dict[str, object] = {
        "format": GAME_SAVE_FORMAT,
        "format_version": GAME_PERSISTENCE_VERSION,
        "save_id": "",
        "bindings": checked_context.bindings,
        "state": {
            "saved": saved,
            "saved_hash": _canonical_value_hash(saved),
            "restored_state_hash": canonical_state_hash(restored),
            "classification": _classification_document(classification),
        },
        "content_hash": "",
    }
    return validate_game_save_document(
        _seal_document(document, id_field="save_id", prefix="game_save_"),
        checked_context,
    )


def _validate_bindings(
    value: object,
    context: GamePersistenceContext,
) -> dict[str, object]:
    bindings = _object(value, "persistence.bindings")
    _exact_keys(bindings, _BINDING_FIELDS, "persistence.bindings")
    for field in ("gamepack", "runtime_composition", "runtime_bundle"):
        identity = _object(bindings.get(field), f"persistence.bindings.{field}")
        _exact_keys(identity, _IDENTITY_FIELDS, f"persistence.bindings.{field}")
    runtime_api = _object(bindings.get("runtime_api"), "persistence.bindings.runtime_api")
    _exact_keys(runtime_api, _RUNTIME_API_FIELDS, "persistence.bindings.runtime_api")
    semantics = _object(
        bindings.get("execution_semantics"),
        "persistence.bindings.execution_semantics",
    )
    _exact_keys(semantics, _SEMANTICS_FIELDS, "persistence.bindings.execution_semantics")
    if bindings != context._bindings:
        _fail(
            "persistence_identity_mismatch",
            "persistence bindings differ from the exact runtime context",
        )
    return bindings


def validate_game_save_document(
    value: object,
    context: GamePersistenceContext,
) -> dict[str, object]:
    checked_context = _require_context(context)
    document = _own(
        value,
        maximum_bytes=MAX_GAME_SAVE_BYTES,
        context="game save",
    )
    document = _object(document, "game save")
    _exact_keys(document, _SAVE_FIELDS, "game save")
    if document.get("format") != GAME_SAVE_FORMAT:
        _fail("save_format_invalid", f"format must be {GAME_SAVE_FORMAT}")
    if document.get("format_version") != GAME_PERSISTENCE_VERSION:
        _fail("save_version_invalid", "game save format_version must be 1")
    if (
        type(document.get("save_id")) is not str
        or _SAVE_ID_RE.fullmatch(document["save_id"]) is None
    ):
        _fail("save_id_invalid", "save_id must be game_save_<48 lowercase hex>")
    _validate_bindings(document.get("bindings"), checked_context)
    state = _object(document.get("state"), "game save.state")
    _exact_keys(state, _SAVE_STATE_FIELDS, "game save.state")
    saved = _object(state.get("saved"), "game save.state.saved")
    if state.get("saved_hash") != _canonical_value_hash(saved):
        _fail("save_hash_mismatch", "saved-state projection hash does not match")
    restored, classification = _restore_projection(checked_context, saved)
    if state.get("restored_state_hash") != canonical_state_hash(restored):
        _fail("save_restored_state_mismatch", "restored full-state hash does not match")
    _validate_classification(
        state.get("classification"),
        classification,
        reason_code="save_classification_mismatch",
    )
    expected_id = _seal_document(
        {
            **copy.deepcopy(document),
            "save_id": "",
            "content_hash": "",
        },
        id_field="save_id",
        prefix="game_save_",
    )["save_id"]
    if document["save_id"] != expected_id:
        _fail("save_id_mismatch", "save_id is not derived from the exact document")
    _content_hash_matches(document, "game save")
    return document


def restore_game_save(
    context: GamePersistenceContext,
    value: object,
) -> dict[str, JsonValue]:
    """Validate one save and reconstruct full state from immutable initial state."""

    checked = validate_game_save_document(value, context)
    state = _object(checked["state"], "game save.state")
    restored, _ = _restore_projection(context, state["saved"])
    return restored


def _step_document(index: int, result: TransitionResult) -> dict[str, object]:
    if type(result) is not TransitionResult:
        _fail("replay_step_invalid", "replay input must contain exact TransitionResult values")
    if not result.accepted:
        _fail("replay_step_rejected", "replays may contain only accepted actions")
    trace = canonical_trace_step(result)
    return {
        "index": index,
        **trace,
    }


def build_game_replay(
    context: GamePersistenceContext,
    results: Sequence[TransitionResult],
) -> dict[str, object]:
    checked_context = _require_context(context)
    if type(results) not in {list, tuple}:
        _fail("replay_steps_invalid", "replay results must be an exact list or tuple")
    if len(results) > checked_context.max_actions:
        _fail("replay_action_limit", "replay exceeds the adapter action budget")
    steps = [_step_document(index, result) for index, result in enumerate(results)]
    interpreter = GamepackInterpreter(
        checked_context._gamepack,
        already_validated=True,
    )
    if steps:
        final_hash = steps[-1]["post_state_hash"]
        final_state = results[-1].post_state
    else:
        final_state = interpreter.initial_state()
        final_hash = canonical_state_hash(final_state)
    _, classification = _validated_full_state(checked_context, final_state)
    document: dict[str, object] = {
        "format": GAME_REPLAY_FORMAT,
        "format_version": GAME_PERSISTENCE_VERSION,
        "replay_id": "",
        "bindings": checked_context.bindings,
        "initial_state_hash": canonical_state_hash(interpreter.initial_state()),
        "steps": steps,
        "final_state_hash": final_hash,
        "classification": _classification_document(classification),
        "trace_hash": _canonical_value_hash({"steps": steps}),
        "content_hash": "",
    }
    sealed = _seal_document(
        document,
        id_field="replay_id",
        prefix="game_replay_",
    )
    play_game_replay(checked_context, sealed)
    return validate_game_replay_document(sealed, checked_context)


def validate_game_replay_document(
    value: object,
    context: GamePersistenceContext,
) -> dict[str, object]:
    checked_context = _require_context(context)
    document = _own(
        value,
        maximum_bytes=MAX_GAME_REPLAY_BYTES,
        context="game replay",
    )
    document = _object(document, "game replay")
    _exact_keys(document, _REPLAY_FIELDS, "game replay")
    if document.get("format") != GAME_REPLAY_FORMAT:
        _fail("replay_format_invalid", f"format must be {GAME_REPLAY_FORMAT}")
    if document.get("format_version") != GAME_PERSISTENCE_VERSION:
        _fail("replay_version_invalid", "game replay format_version must be 1")
    if (
        type(document.get("replay_id")) is not str
        or _REPLAY_ID_RE.fullmatch(document["replay_id"]) is None
    ):
        _fail("replay_id_invalid", "replay_id must be game_replay_<48 lowercase hex>")
    _validate_bindings(document.get("bindings"), checked_context)
    _sha256(document.get("initial_state_hash"), "game replay.initial_state_hash")
    steps = _array(
        document.get("steps"),
        "game replay.steps",
        maximum=checked_context.max_actions,
    )
    for index, raw in enumerate(steps):
        step = _object(raw, f"game replay.steps/{index}")
        _exact_keys(step, _STEP_FIELDS, f"game replay.steps/{index}")
        if step.get("index") != index:
            _fail("replay_step_index_mismatch", f"step {index} has the wrong index")
        candidate = snapshot_strict_candidate(
            CandidateAction(
                step.get("action_id"),  # type: ignore[arg-type]
                step.get("parameters"),  # type: ignore[arg-type]
            )
        )
        if candidate.action_id != step.get("action_id") or candidate.parameters != step.get(
            "parameters"
        ):
            _fail("replay_step_invalid", f"step {index} action is noncanonical")
        _sha256(step.get("pre_state_hash"), f"game replay.steps/{index}.pre_state_hash")
        _sha256(step.get("post_state_hash"), f"game replay.steps/{index}.post_state_hash")
        events = _array(
            step.get("events"),
            f"game replay.steps/{index}.events",
            maximum=256,
        )
        for event_index, event in enumerate(events):
            _nfc_text(
                event,
                f"game replay.steps/{index}.events/{event_index}",
                maximum=64,
            )
    _sha256(document.get("final_state_hash"), "game replay.final_state_hash")
    classification = _object(document.get("classification"), "game replay.classification")
    _exact_keys(classification, _CLASSIFICATION_FIELDS, "game replay.classification")
    _sha256(document.get("trace_hash"), "game replay.trace_hash")
    if document["trace_hash"] != _canonical_value_hash({"steps": steps}):
        _fail("replay_trace_hash_mismatch", "replay trace hash does not match")
    expected_id = _seal_document(
        {
            **copy.deepcopy(document),
            "replay_id": "",
            "content_hash": "",
        },
        id_field="replay_id",
        prefix="game_replay_",
    )["replay_id"]
    if document["replay_id"] != expected_id:
        _fail("replay_id_mismatch", "replay_id is not derived from the exact document")
    _content_hash_matches(document, "game replay")
    return document


def play_game_replay(
    context: GamePersistenceContext,
    value: object,
) -> GameSession:
    """Re-execute one accepted trace and reject its first semantic mismatch."""

    checked_context = _require_context(context)
    document = validate_game_replay_document(value, checked_context)
    session = GameSession(checked_context._gamepack)
    if document["initial_state_hash"] != session.state_hash:
        _fail("replay_initial_mismatch", "initial state hash differs from gamepack")
    steps = document["steps"]
    assert type(steps) is list
    for index, step in enumerate(steps):
        assert type(step) is dict
        if step["pre_state_hash"] != session.state_hash:
            _fail("replay_step_mismatch", f"step {index} pre-state hash differs")
        result = session.apply(step["action_id"], step["parameters"])
        if not result.accepted:
            _fail("replay_step_mismatch", f"step {index} action was rejected")
        if (
            result.pre_state_hash != step["pre_state_hash"]
            or result.post_state_hash != step["post_state_hash"]
            or list(result.events) != step["events"]
        ):
            _fail("replay_step_mismatch", f"step {index} trace differs")
    if document["final_state_hash"] != session.state_hash:
        _fail("replay_final_mismatch", "final state hash differs")
    _validate_classification(
        document["classification"],
        session.classification,
        reason_code="replay_classification_mismatch",
    )
    return session


class GameReplayRecorder:
    """Collect only accepted results for one bounded deterministic trace."""

    __slots__ = ("_context", "_results")

    def __init__(self, context: GamePersistenceContext) -> None:
        self._context = _require_context(context)
        self._results: list[TransitionResult] = []

    def record(self, result: TransitionResult) -> None:
        if type(result) is not TransitionResult:
            _fail("replay_step_invalid", "recorder requires exact TransitionResult values")
        if not result.accepted:
            return
        if len(self._results) >= self._context.max_actions:
            _fail("replay_action_limit", "replay exceeds the adapter action budget")
        trace = canonical_trace_step(result)
        self._results.append(
            TransitionResult(
                True,
                CandidateAction(
                    trace["action_id"],  # type: ignore[arg-type]
                    trace["parameters"],  # type: ignore[arg-type]
                ),
                snapshot_strict_state(result.pre_state),
                snapshot_strict_state(result.post_state),
                trace["pre_state_hash"],  # type: ignore[arg-type]
                trace["post_state_hash"],  # type: ignore[arg-type]
                tuple(trace["events"]),  # type: ignore[arg-type]
                None,
            )
        )

    def finish(self) -> dict[str, object]:
        return build_game_replay(self._context, self._results)


class RecordingGameSession:
    """Game session that records accepted actions and forbids save restoration."""

    __slots__ = ("_context", "_recorder", "_session")

    def __init__(self, context: GamePersistenceContext) -> None:
        self._context = _require_context(context)
        self._session = GameSession(self._context._gamepack)
        self._recorder = GameReplayRecorder(self._context)

    @property
    def state(self) -> dict[str, JsonValue]:
        return self._session.state

    @property
    def state_hash(self) -> str:
        return self._session.state_hash

    @property
    def classification(self) -> StateClassification:
        return self._session.classification

    def apply(
        self,
        action_id: str,
        parameters: Mapping[str, JsonValue],
    ) -> TransitionResult:
        result = self._session.apply(action_id, parameters)
        self._recorder.record(result)
        return result

    def restore(self, _save: object) -> None:
        _fail(
            "recording_restore_forbidden",
            "a recording session cannot restore a save",
        )

    def finish(self) -> dict[str, object]:
        return self._recorder.finish()


def serialize_game_save(
    value: object,
    context: GamePersistenceContext | None = None,
) -> bytes:
    document = (
        validate_game_save_document(value, context)
        if context is not None
        else _structural_save(value)
    )
    return _pretty_bytes(document, maximum_bytes=MAX_GAME_SAVE_BYTES)


def serialize_game_replay(
    value: object,
    context: GamePersistenceContext | None = None,
) -> bytes:
    document = (
        validate_game_replay_document(value, context)
        if context is not None
        else _structural_replay(value)
    )
    return _pretty_bytes(document, maximum_bytes=MAX_GAME_REPLAY_BYTES)


def _pretty_bytes(value: object, *, maximum_bytes: int) -> bytes:
    owned = _own(
        value,
        maximum_bytes=MAX_GAME_REPLAY_BYTES,
        context="persistence serialization",
    )
    try:
        payload = json.dumps(
            owned,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError, RecursionError, UnicodeError, OverflowError) as exc:
        raise GameLogicError(
            "persistence_contract_invalid",
            f"could not serialize persistence JSON: {exc}",
        ) from exc
    encoded = (payload + "\n").encode("utf-8")
    if len(encoded) > maximum_bytes:
        _fail(
            "persistence_bytes_exceeded",
            f"serialized persistence document exceeds {maximum_bytes} bytes",
        )
    return encoded


def _structural_save(value: object) -> dict[str, object]:
    document = _own(
        value,
        maximum_bytes=MAX_GAME_SAVE_BYTES,
        context="game save",
    )
    document = _object(document, "game save")
    _exact_keys(document, _SAVE_FIELDS, "game save")
    if (
        document.get("format") != GAME_SAVE_FORMAT
        or document.get("format_version") != GAME_PERSISTENCE_VERSION
    ):
        _fail("save_format_invalid", "unsupported game save")
    _content_hash_matches(document, "game save")
    return document


def _structural_replay(value: object) -> dict[str, object]:
    document = _own(
        value,
        maximum_bytes=MAX_GAME_REPLAY_BYTES,
        context="game replay",
    )
    document = _object(document, "game replay")
    _exact_keys(document, _REPLAY_FIELDS, "game replay")
    if (
        document.get("format") != GAME_REPLAY_FORMAT
        or document.get("format_version") != GAME_PERSISTENCE_VERSION
    ):
        _fail("replay_format_invalid", "unsupported game replay")
    _content_hash_matches(document, "game replay")
    return document


def load_game_save_bytes(
    payload: bytes,
    context: GamePersistenceContext,
    *,
    source: str = "<game save bytes>",
) -> dict[str, object]:
    document = decode_json_object(
        payload,
        source=source,
        limit=MAX_GAME_SAVE_BYTES,
    )
    return validate_game_save_document(document, context)


def load_game_replay_bytes(
    payload: bytes,
    context: GamePersistenceContext,
    *,
    source: str = "<game replay bytes>",
) -> dict[str, object]:
    document = decode_json_object(
        payload,
        source=source,
        limit=MAX_GAME_REPLAY_BYTES,
    )
    checked = validate_game_replay_document(document, context)
    play_game_replay(context, checked)
    return checked


def validate_slot_name(value: object) -> str:
    if (
        type(value) is not str
        or _SLOT_RE.fullmatch(value) is None
        or value.casefold() in _WINDOWS_RESERVED
        or unicodedata.normalize("NFC", value) != value
    ):
        _fail(
            "slot_invalid",
            "slot must match [a-z][a-z0-9_-]{0,31} and not be Windows-reserved",
        )
    return value


def _slot_path(
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
    *,
    kind: str,
) -> Path:
    checked_context = _require_context(context)
    checked_slot = validate_slot_name(slot)
    if type(root) not in {str, PosixPath, WindowsPath}:
        _fail("persistence_root_invalid", "user-data root must be an exact path")
    if kind not in {"saves", "replays"}:
        _fail("persistence_path_invalid", "persistence kind is unsupported")
    base = Path(os.path.abspath(Path(root)))
    game_id = _portable_path_identity(
        checked_context._bindings["gamepack"]["id"],  # type: ignore[index]
        "gamepack.id",
        pattern=_GAME_PATH_ID_RE,
    )
    bundle_id = _portable_path_identity(
        checked_context._bindings["runtime_bundle"]["id"],  # type: ignore[index]
        "runtime bundle.id",
        pattern=_BUNDLE_PATH_ID_RE,
    )
    destination = base / kind / game_id / bundle_id / f"{checked_slot}.json"
    try:
        lexical_common = Path(os.path.commonpath((base, destination)))
        resolved_base = base.resolve(strict=False)
        resolved_parent = destination.parent.resolve(strict=False)
        resolved_common = Path(os.path.commonpath((resolved_base, resolved_parent)))
    except (OSError, ValueError) as exc:
        raise GameLogicError(
            "persistence_path_outside_root",
            "persistence slot path containment could not be established",
        ) from exc
    if lexical_common != base or resolved_common != resolved_base:
        _fail(
            "persistence_path_outside_root",
            "persistence slot path must remain inside the exact user-data root",
        )
    return destination


def write_game_save_slot(
    root: str | os.PathLike[str],
    slot: object,
    value: object,
    context: GamePersistenceContext,
) -> Any:
    from gamepack_runtime.persistence_generation import (
        write_game_save_slot as write_generation,
    )

    return write_generation(root, slot, value, context)


def write_game_replay_slot(
    root: str | os.PathLike[str],
    slot: object,
    value: object,
    context: GamePersistenceContext,
) -> Any:
    from gamepack_runtime.persistence_generation import (
        write_game_replay_slot as write_generation,
    )

    return write_generation(root, slot, value, context)


def read_game_save_slot(
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
) -> dict[str, object]:
    from gamepack_runtime.persistence_generation import (
        read_game_save_slot as read_generation,
    )

    return read_generation(root, slot, context)


def read_game_replay_slot(
    root: str | os.PathLike[str],
    slot: object,
    context: GamePersistenceContext,
) -> dict[str, object]:
    from gamepack_runtime.persistence_generation import (
        read_game_replay_slot as read_generation,
    )

    return read_generation(root, slot, context)


__all__ = [
    "GAMEPACK_RUNTIME_API",
    "GAME_PERSISTENCE_VERSION",
    "GAME_REPLAY_FORMAT",
    "GAME_SAVE_FORMAT",
    "MAX_GAME_REPLAY_BYTES",
    "MAX_GAME_SAVE_BYTES",
    "GamePersistenceContext",
    "GameReplayRecorder",
    "RecordingGameSession",
    "build_game_persistence_context",
    "build_game_replay",
    "build_game_save",
    "canonical_persistence_hash",
    "load_game_replay_bytes",
    "load_game_save_bytes",
    "play_game_replay",
    "read_game_replay_slot",
    "read_game_save_slot",
    "restore_game_save",
    "serialize_game_replay",
    "serialize_game_save",
    "validate_game_replay_document",
    "validate_game_save_document",
    "validate_slot_name",
    "write_game_replay_slot",
    "write_game_save_slot",
]
