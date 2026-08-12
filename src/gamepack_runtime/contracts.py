"""Closed, deterministic runtime contracts for ``world-forge.gamepack`` v1.

This package is intentionally independent from the Forge compiler and from the
legacy RPG runtime.  It accepts only the already-published v1 state-machine
language and fails closed on new executable semantics.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias

JsonScalar: TypeAlias = None | bool | int | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

GAMEPACK_FORMAT = "world-forge.gamepack"
GAMEPACK_VERSION = 1
MAX_GAMEPACK_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000
MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_TERMINAL_ENDING_IDS = 64

EXECUTION_SEMANTICS: Mapping[str, object] = MappingProxyType(
    {
        "semantics_version": 1,
        "owned_action_rules": "all_required",
        "condition_collections": "and",
        "condition_snapshot": "pre_transition",
        "effect_order": "rule_order_then_reference_order",
        "effect_state_operands": "current_candidate_state",
        "event_commit": "after_success_rule_reference_order",
        "active_failure_recovery": "intersection",
        "terminal_precedence": "endings_before_failures",
        "ending_match": "exactly_one",
        "narrative_transition": "source_effects_then_cursor_atomic",
        "invalid_transition": "reject_without_mutation",
    }
)

ANALYSIS_LIMITS: Mapping[str, int] = MappingProxyType(
    {
        "candidate_evaluations": 262_144,
        "depth": 512,
        "parameter_combinations_per_action": 4_096,
        "state_bytes": 65_536,
        "states": 16_384,
        "total_state_bytes": 67_108_864,
        "total_witness_steps": 4_096,
        "witness_traces": 128,
    }
)

ANALYZERS: Mapping[str, tuple[str, int]] = MappingProxyType(
    {
        "abstract_puzzle": ("worldforge.abstract_puzzle_exhaustive", 1),
        "branching_narrative": ("worldforge.branching_narrative_exhaustive", 1),
        "unsupported": ("worldforge.unsupported_profile", 1),
    }
)

_GAMEPACK_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "game",
        "source",
        "modules",
        "logic",
        "presentation",
        "asset_requirements",
        "runtime_requirements",
        "analysis_requirements",
        "localization",
        "mechanic_requirements",
        "provenance",
        "registered_extensions",
        "content_hash",
    }
)
_LOGIC_FIELDS = frozenset(
    {
        "source",
        "title",
        "state_schema",
        "initial_state",
        "core_verbs",
        "actions",
        "conditions",
        "effects",
        "rules",
        "goals",
        "failures",
        "endings",
        "events",
        "presentation_hooks",
        "mechanics",
        "narrative_cursor",
        "narrative_transitions",
        "execution_semantics",
    }
)
_LOGIC_LIMITS = MappingProxyType(
    {
        "state_schema": 129,
        "core_verbs": 128,
        "actions": 128,
        "conditions": 512,
        "effects": 512,
        "rules": 512,
        "goals": 64,
        "failures": 64,
        "endings": MAX_TERMINAL_ENDING_IDS,
        "events": 256,
        "presentation_hooks": 256,
        "mechanics": 128,
        "narrative_transitions": 128,
    }
)
_FORBIDDEN_FIELDS = frozenset(
    {
        "callback",
        "command",
        "credential",
        "credentials",
        "endpoint",
        "executable",
        "executable_script",
        "expression",
        "import",
        "javascript",
        "model_id",
        "absolute_path",
        "authoring_path",
        "mutable_path",
        "native_code",
        "project_path",
        "prompt",
        "provider",
        "provider_credentials",
        "provider_details",
        "provider_id",
        "python",
        "runtime_ai",
        "script",
        "source_path",
        "token",
        "tool",
    }
)
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_EXTENSION_ID_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_.-]*)?$")
_SEMVER_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_LOCALE_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_WINDOWS_RESERVED = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
SUPPORTED_REQUIRED_FEATURES_V1 = frozenset(
    {
        "logic:branching_choice",
        "logic:deterministic_actions",
        "logic:finite_state",
        "logic:persistent_variables",
    }
)
SUPPORTED_REQUIRED_EXTENSIONS_V1: Mapping[str, frozenset[int]] = MappingProxyType({})
SUPPORTED_OPTIONAL_EXTENSIONS_V1: Mapping[str, frozenset[int]] = MappingProxyType(
    {"example.optional-metadata": frozenset({1})}
)
_RUNTIME_PRESENTATIONS_V1: Mapping[str, tuple[str, str, str, str]] = MappingProxyType(
    {
        "gamepack_raylib_2d_puzzle": (
            "2d",
            "fixed",
            "orthographic board",
            "raylib",
        ),
        "gamepack_raylib_2d_text": (
            "text",
            "none",
            "text interface",
            "raylib",
        ),
    }
)
_ROLE_BY_HOOK_KIND: Mapping[str, str] = MappingProxyType(
    {
        "board": "board_visual",
        "text": "text_ui",
        "feedback": "interaction_feedback",
        "ending": "ending_ui",
    }
)
_MODULE_FIELDS = frozenset({"world", "activities", "narrative", "systems"})
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_WORLD_RECORD_FIELDS = {
    "canon": frozenset({"id", "statement", "status"}),
    "chronology": frozenset({"id", "sequence", "summary"}),
    "space": frozenset({"id", "name", "topology"}),
    "group": frozenset({"id", "name", "group_type"}),
    "character": frozenset({"id", "name", "role"}),
    "knowledge": frozenset({"id", "statement", "access"}),
}
_ACTIVITY_FIELDS = frozenset(
    {
        "id",
        "activity_type",
        "title",
        "participant_ids",
        "spatial_context_ids",
        "start_condition_ids",
        "end_condition_ids",
        "success_condition_ids",
        "failure_condition_ids",
        "effect_ids",
        "event_ids",
        "presentation_hook_ids",
        "asset_binding_ids",
    }
)
_SYSTEM_FIELDS = frozenset(
    {
        "id",
        "system_type",
        "title",
        "precondition_ids",
        "effect_ids",
        "event_ids",
        "asset_binding_ids",
    }
)
_NARRATIVE_COMMON_FIELDS = frozenset(
    {
        "id",
        "unit_type",
        "title",
        "prerequisite_ids",
        "effect_ids",
        "next_unit_ids",
        "asset_binding_ids",
    }
)


class GameLogicError(ValueError):
    """Stable fail-closed error shared by the kernel and Forge façade."""

    def __init__(self, reason_code: str, detail: str) -> None:
        if type(reason_code) is not str or not reason_code:
            reason_code = "error_contract_invalid"
        if type(detail) is not str:
            detail = "error detail must be an exact string"
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


@dataclass(frozen=True, slots=True)
class CandidateAction:
    action_id: str
    parameters: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class StateClassification:
    goal_ids: tuple[str, ...]
    ending_ids: tuple[str, ...]
    ending_kind: str | None
    failure_ids: tuple[str, ...]
    recovery_action_ids: tuple[str, ...]

    @property
    def terminal(self) -> bool:
        if type(self.ending_ids) is not tuple:
            _fail(
                "classification_invalid",
                "ending_ids must be an exact tuple of runtime IDs",
            )
        if len(self.ending_ids) > MAX_TERMINAL_ENDING_IDS:
            _fail(
                "classification_invalid",
                f"ending_ids exceed {MAX_TERMINAL_ENDING_IDS} items",
            )
        checked: list[str] = []
        for ending_id in self.ending_ids:
            value = _validate_exact_nfc_string(
                ending_id,
                maximum_codepoints=64,
                reason_code="classification_invalid",
                detail="ending_ids must contain bounded NFC runtime IDs",
            )
            if _ID_RE.fullmatch(value) is None:
                _fail(
                    "classification_invalid",
                    "ending_ids must contain bounded NFC runtime IDs",
                )
            checked.append(value)
        if len(set(checked)) != len(checked):
            _fail("classification_invalid", "ending_ids must not contain duplicates")
        return bool(self.ending_ids)


@dataclass(frozen=True, slots=True)
class TransitionResult:
    accepted: bool
    action: CandidateAction
    pre_state: dict[str, JsonValue]
    post_state: dict[str, JsonValue]
    pre_state_hash: str
    post_state_hash: str
    events: tuple[str, ...]
    rejection_reason: str | None


@dataclass(frozen=True, slots=True)
class _ValueDomain:
    value_type: str
    minimum: int | None = None
    maximum: int | None = None
    allowed_values: frozenset[str] | None = None
    min_items: int | None = None
    max_items: int | None = None


def _fail(reason_code: str, detail: str) -> None:
    raise GameLogicError(reason_code, detail)


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], context: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        _fail(
            "gamepack_fields_invalid",
            f"{context} keys are not exact; missing={missing!r} extra={extra!r}",
        )


def _object(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail("gamepack_structure_invalid", f"{context} must be an object")
    return value


def _array(value: object, context: str, *, maximum: int) -> list[object]:
    if not isinstance(value, list):
        _fail("gamepack_structure_invalid", f"{context} must be an array")
    if len(value) > maximum:
        _fail("gamepack_bounds_exceeded", f"{context} exceeds {maximum} items")
    return value


def _validate_exact_nfc_string(
    value: object,
    *,
    maximum_codepoints: int,
    reason_code: str,
    detail: str,
    reject_controls: bool = False,
) -> str:
    """Validate a bounded exact scalar string before normalization or encoding."""

    if (
        type(value) is not str
        or not value
        or type(maximum_codepoints) is not int
        or maximum_codepoints < 1
        or len(value) > maximum_codepoints
    ):
        _fail(reason_code, detail)
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            _fail(reason_code, detail)
        if reject_controls and unicodedata.category(character) == "Cc":
            _fail(reason_code, detail)
    try:
        if unicodedata.normalize("NFC", value) != value:
            _fail(reason_code, detail)
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise GameLogicError(reason_code, detail) from exc
    return value


def _json_string_size(value: str, *, maximum_bytes: int | None = None) -> int:
    if maximum_bytes is not None and (
        type(maximum_bytes) is not int or maximum_bytes < 2 or len(value) > maximum_bytes - 2
    ):
        _fail(
            "gamepack_bytes_exceeded",
            "JSON string exceeds the remaining canonical byte budget",
        )
    size = 2
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            _fail("json_unicode_invalid", "JSON string is not a Unicode scalar string")
        if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
            size += 2
        elif codepoint < 0x20:
            size += 6
        elif codepoint <= 0x7F:
            size += 1
        elif codepoint <= 0x7FF:
            size += 2
        elif codepoint <= 0xFFFF:
            size += 3
        else:
            size += 4
        if maximum_bytes is not None and size > maximum_bytes:
            _fail(
                "gamepack_bytes_exceeded",
                "JSON string exceeds the remaining canonical byte budget",
            )
    return size


_MAPPING_PROXY_TYPE = type(MappingProxyType({}))


def _snapshot_json_graph(
    value: object,
    *,
    maximum_bytes: int = MAX_GAMEPACK_BYTES,
    allow_float: bool = False,
    allow_root_mapping_proxy: bool = False,
) -> object:

    if type(maximum_bytes) is not int or maximum_bytes < 1:
        _fail("json_bounds_invalid", "maximum_bytes must be a positive integer")
    encoded_bytes = 0
    nodes = 0
    active: set[int] = set()
    seen: set[int] = set()
    root: list[object] = [None]
    stack: list[tuple[bool, object, object, object, int]] = [(True, value, root, 0, 1)]

    def add_bytes(amount: int) -> None:
        nonlocal encoded_bytes
        encoded_bytes += amount
        if encoded_bytes > maximum_bytes:
            _fail(
                "gamepack_bytes_exceeded",
                f"canonical JSON exceeds {maximum_bytes} bytes",
            )

    def reject_impossible_string_batch(values: list[str]) -> None:
        remaining = maximum_bytes - encoded_bytes
        minimum = 0
        for item in values:
            minimum += len(item) + 2
            if minimum > remaining:
                _fail(
                    "gamepack_bytes_exceeded",
                    f"canonical JSON exceeds {maximum_bytes} bytes",
                )

    def add_string(value: str) -> None:
        remaining = maximum_bytes - encoded_bytes
        if remaining < 2 or len(value) > remaining - 2:
            _fail(
                "gamepack_bytes_exceeded",
                f"canonical JSON exceeds {maximum_bytes} bytes",
            )
        add_bytes(_json_string_size(value, maximum_bytes=remaining))

    def attach(parent: object, slot: object, item: object) -> None:
        if type(parent) is list:
            parent[int(slot)] = item
        else:
            assert type(parent) is dict
            parent[str(slot)] = item

    while stack:
        entering, current, parent, slot, depth = stack.pop()
        if not entering:
            active.remove(id(current))
            continue
        nodes += 1
        if nodes > MAX_JSON_NODES:
            _fail("json_nodes_exceeded", f"JSON exceeds {MAX_JSON_NODES} nodes")
        if depth > MAX_JSON_DEPTH:
            _fail("json_depth_exceeded", f"JSON exceeds {MAX_JSON_DEPTH} levels")
        current_type = type(current)
        is_legacy_root_proxy = (
            allow_root_mapping_proxy and depth == 1 and current_type is _MAPPING_PROXY_TYPE
        )
        if current_type is dict or is_legacy_root_proxy:
            identity = id(current)
            if identity in active:
                _fail("json_cycle", "JSON object graph contains a cycle")
            if identity in seen:
                _fail("json_alias", "JSON object graph contains a shared container")
            child_count = len(current)
            if nodes + child_count > MAX_JSON_NODES:
                _fail("json_nodes_exceeded", f"JSON exceeds {MAX_JSON_NODES} nodes")
            add_bytes(2 + max(0, child_count - 1) + child_count)
            seen.add(identity)
            active.add(identity)
            clone: dict[str, object] = {}
            attach(parent, slot, clone)
            children: list[tuple[str, object]] = []
            for key, item in current.items():
                if type(key) is not str or not key:
                    _fail("json_key_invalid", "JSON object keys must be non-empty strings")
                children.append((key, item))
                if len(children) > MAX_JSON_NODES - nodes:
                    _fail("json_nodes_exceeded", f"JSON exceeds {MAX_JSON_NODES} nodes")
            reject_impossible_string_batch(
                [key for key, _item in children]
                + [item for _key, item in children if type(item) is str]
            )
            for key, _item in children:
                add_string(key)
            stack.append((False, current, parent, slot, depth))
            stack.extend((True, item, clone, key, depth + 1) for key, item in reversed(children))
        elif current_type is list:
            identity = id(current)
            if identity in active:
                _fail("json_cycle", "JSON object graph contains a cycle")
            if identity in seen:
                _fail("json_alias", "JSON object graph contains a shared container")
            child_count = len(current)
            if nodes + child_count > MAX_JSON_NODES:
                _fail("json_nodes_exceeded", f"JSON exceeds {MAX_JSON_NODES} nodes")
            add_bytes(2 + max(0, child_count - 1))
            reject_impossible_string_batch([item for item in current if type(item) is str])
            seen.add(identity)
            active.add(identity)
            clone_list: list[object] = [None] * child_count
            attach(parent, slot, clone_list)
            stack.append((False, current, parent, slot, depth))
            stack.extend(
                (True, item, clone_list, index, depth + 1)
                for index, item in reversed(tuple(enumerate(current)))
            )
        elif current is None:
            attach(parent, slot, None)
            add_bytes(4)
        elif current_type is bool:
            attach(parent, slot, current)
            add_bytes(4 if current else 5)
        elif current_type is int:
            if not -MAX_SAFE_INTEGER <= current <= MAX_SAFE_INTEGER:
                _fail(
                    "json_integer_unsupported",
                    "JSON integer is outside the JavaScript-safe range",
                )
            attach(parent, slot, current)
            add_bytes(len(str(current)))
        elif current_type is str:
            add_string(current)
            attach(parent, slot, current)
        elif current_type is float:
            if not allow_float:
                _fail("json_float_unsupported", "decimal and exponent numbers are unsupported")
            attach(parent, slot, current)
            add_bytes(len(repr(current)))
        else:
            _fail(
                "json_type_unsupported",
                f"unsupported JSON value type {current_type.__name__}",
            )
    return root[0]


def snapshot_plain_json(
    value: object,
    *,
    maximum_bytes: int = MAX_GAMEPACK_BYTES,
) -> JsonValue:
    """Return an owned, alias-free exact plain-JSON snapshot iteratively."""

    return _snapshot_json_graph(value, maximum_bytes=maximum_bytes)  # type: ignore[return-value]


def snapshot_legacy_action_parameters(value: object) -> dict[str, object]:
    """Attempt one bounded compatibility copy of historical mapping inputs."""

    try:
        owned = _snapshot_json_graph(
            value,
            maximum_bytes=int(ANALYSIS_LIMITS["state_bytes"]),
            allow_float=True,
            allow_root_mapping_proxy=True,
        )
    except GameLogicError:
        raise
    except Exception as exc:
        raise GameLogicError(
            "action_invalid",
            "legacy mapping input could not be read safely",
        ) from exc
    if type(owned) is not dict:
        _fail("action_invalid", "action parameters must be an object")
    return owned


def snapshot_strict_candidate(action: CandidateAction) -> CandidateAction:
    """Validate and own one candidate under the strict neutral input policy."""

    if (
        type(action) is not CandidateAction
        or type(action.action_id) is not str
        or not action.action_id
    ):
        _fail("action_invalid", "action must be a CandidateAction with an ID")
    owned = snapshot_plain_json(
        {
            "action_id": action.action_id,
            "parameters": action.parameters,
        },
        maximum_bytes=int(ANALYSIS_LIMITS["state_bytes"]),
    )
    assert type(owned) is dict
    parameters = owned["parameters"]
    if type(parameters) is not dict:
        _fail("action_invalid", "action parameters must be an exact object")
    return CandidateAction(str(owned["action_id"]), parameters)  # type: ignore[arg-type]


def snapshot_strict_state(value: object) -> dict[str, JsonValue]:
    """Own one strict state input before schema validation or hashing."""

    owned = snapshot_plain_json(
        value,
        maximum_bytes=int(ANALYSIS_LIMITS["state_bytes"]),
    )
    if type(owned) is not dict:
        _fail("state_domain_invalid", "state must be an object")
    return owned  # type: ignore[return-value]


def _canonical_bytes_owned(value: JsonValue) -> bytes:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (RecursionError, TypeError, UnicodeError, ValueError, OverflowError) as exc:
        raise GameLogicError("json_encode_failed", str(exc)) from exc
    if len(encoded) > MAX_GAMEPACK_BYTES:
        _fail(
            "gamepack_bytes_exceeded",
            f"canonical JSON exceeds {MAX_GAMEPACK_BYTES} bytes",
        )
    return encoded


def _canonical_bytes(value: object) -> bytes:
    return _canonical_bytes_owned(snapshot_plain_json(value))


def canonical_gamepack_hash(value: Mapping[str, object]) -> str:
    payload = snapshot_plain_json(value)
    if type(payload) is not dict:
        _fail("json_root_invalid", "gamepack hash input must be an object")
    payload.pop("content_hash", None)
    return hashlib.sha256(_canonical_bytes_owned(payload)).hexdigest()


def canonical_state_bytes(state: Mapping[str, JsonValue]) -> bytes:
    owned = snapshot_plain_json(
        state,
        maximum_bytes=int(ANALYSIS_LIMITS["state_bytes"]),
    )
    if type(owned) is not dict or not all(type(key) is str and key for key in owned):
        _fail("state_domain_invalid", "state must have non-empty string keys")
    for key, value in owned.items():
        if type(value) is bool:
            continue
        if type(value) is int:
            if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
                _fail(
                    "state_domain_invalid",
                    f"state {key} is outside the safe integer range",
                )
            continue
        if type(value) is str:
            continue
        if type(value) is list and all(type(item) is str for item in value):
            continue
        _fail(
            "state_domain_invalid",
            f"state {key} uses an unsupported JSON value type",
        )
    return _canonical_bytes_owned(owned)


def canonical_state_hash(state: Mapping[str, JsonValue]) -> str:
    return hashlib.sha256(canonical_state_bytes(state)).hexdigest()


def canonical_action_hash(action: CandidateAction) -> str:
    owned = snapshot_strict_candidate(action)
    payload = {
        "action_id": owned.action_id,
        "parameters": owned.parameters,
    }
    return hashlib.sha256(_canonical_bytes_owned(payload)).hexdigest()  # type: ignore[arg-type]


def _snapshot_event_sequence(
    events: object,
    *,
    exact_tuple: bool,
    reason_code: str,
) -> tuple[str, ...]:
    accepted_types = {tuple} if exact_tuple else {tuple, list}
    if type(events) not in accepted_types:
        _fail(reason_code, "events must be an exact bounded sequence of NFC event IDs")
    if len(events) > MAX_JSON_NODES - 1:
        _fail(reason_code, "events exceed the bounded sequence limit")
    event_values = events if type(events) is list else list(events)
    try:
        owned = snapshot_plain_json(
            event_values,
            maximum_bytes=int(ANALYSIS_LIMITS["state_bytes"]),
        )
    except GameLogicError as exc:
        raise GameLogicError(
            reason_code,
            "events must be an exact bounded sequence of NFC event IDs",
        ) from exc
    if type(owned) is not list or not all(
        type(event) is str and event and unicodedata.normalize("NFC", event) == event
        for event in owned
    ):
        _fail(reason_code, "events must be an exact bounded sequence of NFC event IDs")
    return tuple(owned)


def canonical_events_hash(events: tuple[str, ...] | list[str]) -> str:
    owned = _snapshot_event_sequence(
        events,
        exact_tuple=False,
        reason_code="events_invalid",
    )
    return hashlib.sha256(_canonical_bytes_owned(list(owned))).hexdigest()


def _forbidden_field_scan(value: object) -> None:
    forbidden = {re.sub(r"[^a-z0-9]", "", field.casefold()) for field in _FORBIDDEN_FIELDS}
    stack: list[tuple[str, object]] = [("gamepack", value)]
    while stack:
        context, current = stack.pop()
        if isinstance(current, Mapping):
            for key, item in current.items():
                folded = re.sub(r"[^a-z0-9]", "", key.casefold())
                if folded in forbidden:
                    _fail(
                        "unsafe_runtime_field",
                        f"{context}.{key} is an executable, authoring, or provider field",
                    )
                stack.append((f"{context}.{key}", item))
        elif isinstance(current, list):
            stack.extend((f"{context}/{index}", item) for index, item in enumerate(current))


def _validate_extensions(value: object) -> None:
    extensions = _array(value, "gamepack.registered_extensions", maximum=64)
    previous_key: tuple[bytes, int, bool, bytes] | None = None
    seen: set[str] = set()
    for index, raw in enumerate(extensions):
        context = f"gamepack.registered_extensions/{index}"
        extension = _object(raw, context)
        _exact_keys(
            extension,
            frozenset({"id", "version", "required", "content_hash"}),
            context,
        )
        extension_id = extension.get("id")
        version = extension.get("version")
        required = extension.get("required")
        content_hash = extension.get("content_hash")
        if (
            type(extension_id) is not str
            or _EXTENSION_ID_RE.fullmatch(extension_id) is None
            or unicodedata.normalize("NFC", extension_id) != extension_id
            or type(version) is not int
            or version < 1
            or type(required) is not bool
            or type(content_hash) is not str
            or _SHA256_RE.fullmatch(content_hash) is None
        ):
            _fail("extension_identity_invalid", f"{context} identity is invalid")
        folded = extension_id.casefold()
        if folded in seen:
            _fail("extension_identity_invalid", "extension IDs collide under casefold")
        seen.add(folded)
        key = (
            extension_id.encode("utf-8"),
            version,
            required,
            content_hash.encode("ascii"),
        )
        if previous_key is not None and key < previous_key:
            _fail("logic_order_invalid", "registered_extensions is not canonical")
        previous_key = key
        supported_versions = SUPPORTED_REQUIRED_EXTENSIONS_V1.get(extension_id)
        if required and (supported_versions is None or version not in supported_versions):
            _fail(
                "required_extension_unsupported",
                f"required extension {extension_id!r} has no v1 runtime implementation",
            )
        optional_versions = SUPPORTED_OPTIONAL_EXTENSIONS_V1.get(extension_id)
        if not required and (optional_versions is None or version not in optional_versions):
            _fail(
                "optional_extension_unsupported",
                f"optional extension {extension_id!r} is not registered for v1",
            )


def _record_id(record: object, context: str) -> str:
    mapping = _object(record, context)
    identifier = mapping.get("id")
    if not isinstance(identifier, str) or _ID_RE.fullmatch(identifier) is None:
        _fail("gamepack_structure_invalid", f"{context}.id is not a runtime identifier")
    return identifier


def _identifier(
    value: object,
    context: str,
    *,
    allow_internal: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or _ID_RE.fullmatch(value) is None
        or value.casefold() in _WINDOWS_RESERVED
        or (not allow_internal and value.startswith("wf_internal_"))
    ):
        _fail("gamepack_logic_invalid", f"{context} is not a portable runtime ID")
    return value


def _non_empty_string(value: object, context: str, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or unicodedata.normalize("NFC", value) != value
    ):
        _fail("gamepack_structure_invalid", f"{context} is not a bounded NFC string")
    return value


def _id_array(
    value: object,
    context: str,
    *,
    registry: Mapping[str, object] | None = None,
    allow_empty: bool = True,
    maximum: int = 64,
    allow_internal: bool = False,
) -> list[str]:
    items = _array(value, context, maximum=maximum)
    if not allow_empty and not items:
        _fail("gamepack_logic_invalid", f"{context} must be non-empty")
    identifiers = [
        _identifier(item, f"{context}/{index}", allow_internal=allow_internal)
        for index, item in enumerate(items)
    ]
    folded = [item.casefold() for item in identifiers]
    if len(set(folded)) != len(folded):
        _fail("gamepack_logic_invalid", f"{context} contains duplicate IDs")
    if identifiers != sorted(identifiers, key=lambda item: item.encode("utf-8")):
        _fail("logic_order_invalid", f"{context} is not canonical")
    if registry is not None:
        for identifier, key in zip(identifiers, folded, strict=True):
            if key not in registry:
                _fail(
                    "gamepack_logic_invalid",
                    f"{context} references unknown ID {identifier}",
                )
    return identifiers


def _token_array(
    value: object,
    context: str,
    *,
    allow_empty: bool = True,
    maximum: int = 256,
) -> list[str]:
    items = _array(value, context, maximum=maximum)
    if not allow_empty and not items:
        _fail("gamepack_logic_invalid", f"{context} must be non-empty")
    if not all(
        isinstance(item, str)
        and _TOKEN_RE.fullmatch(item) is not None
        and unicodedata.normalize("NFC", item) == item
        for item in items
    ):
        _fail("gamepack_logic_invalid", f"{context} contains an invalid token")
    checked = [str(item) for item in items]
    if len({item.casefold() for item in checked}) != len(checked):
        _fail("gamepack_logic_invalid", f"{context} contains duplicate tokens")
    if checked != sorted(checked, key=lambda item: item.encode("utf-8")):
        _fail("logic_order_invalid", f"{context} is not canonical")
    return checked


def _record_registry(
    logic: Mapping[str, object],
    collection: str,
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    records = logic.get(collection)
    assert isinstance(records, list)
    for index, raw in enumerate(records):
        context = f"gamepack.logic.{collection}/{index}"
        record = _object(raw, context)
        identifier = _identifier(
            record.get("id"),
            f"{context}.id",
            allow_internal=collection in {"state_schema", "narrative_transitions"},
        )
        key = identifier.casefold()
        if key in result:
            _fail("gamepack_logic_invalid", f"logic.{collection} contains duplicate IDs")
        result[key] = record
    return result


def _state_domain(record: Mapping[str, object]) -> _ValueDomain:
    kind = str(record["type"])
    if kind == "integer":
        return _ValueDomain(
            kind,
            minimum=int(record["minimum"]),
            maximum=int(record["maximum"]),
        )
    if kind in {"string", "string_array"}:
        allowed = frozenset(str(item) for item in record["allowed_values"])  # type: ignore[union-attr]
        if kind == "string":
            return _ValueDomain(kind, allowed_values=allowed)
        return _ValueDomain(
            kind,
            allowed_values=allowed,
            min_items=int(record["min_items"]),
            max_items=int(record["max_items"]),
        )
    return _ValueDomain(kind)


def _domain_subset(source: _ValueDomain, target: _ValueDomain, context: str) -> None:
    if source.value_type != target.value_type:
        _fail(
            "operand_type_mismatch",
            f"{context} requires {target.value_type}, got {source.value_type}",
        )
    if target.value_type == "integer":
        assert source.minimum is not None and source.maximum is not None
        assert target.minimum is not None and target.maximum is not None
        if source.minimum < target.minimum or source.maximum > target.maximum:
            _fail("operand_domain_invalid", f"{context} is outside its target domain")
    elif target.value_type in {"string", "string_array"}:
        assert source.allowed_values is not None and target.allowed_values is not None
        if not source.allowed_values.issubset(target.allowed_values):
            _fail("operand_domain_invalid", f"{context} contains disallowed values")
        if target.value_type == "string_array":
            assert source.min_items is not None and source.max_items is not None
            assert target.min_items is not None and target.max_items is not None
            if source.min_items < target.min_items or source.max_items > target.max_items:
                _fail("operand_domain_invalid", f"{context} has invalid array bounds")


def _validate_record_order(logic: Mapping[str, object]) -> None:
    for collection in (
        "core_verbs",
        "actions",
        "conditions",
        "effects",
        "goals",
        "failures",
        "endings",
        "events",
        "presentation_hooks",
        "mechanics",
        "narrative_transitions",
    ):
        records = logic[collection]
        assert isinstance(records, list)
        identifiers = [
            _record_id(item, f"gamepack.logic.{collection}/{index}")
            for index, item in enumerate(records)
        ]
        if identifiers != sorted(identifiers, key=lambda item: item.encode("utf-8")):
            _fail("logic_order_invalid", f"logic.{collection} is not canonical")
        if len({item.casefold() for item in identifiers}) != len(identifiers):
            _fail("gamepack_logic_invalid", f"logic.{collection} contains duplicate IDs")
    rules = logic["rules"]
    assert isinstance(rules, list)
    rule_keys: list[tuple[int, bytes]] = []
    for index, raw in enumerate(rules):
        rule = _object(raw, f"gamepack.logic.rules/{index}")
        order = rule.get("order")
        if not isinstance(order, int) or isinstance(order, bool):
            _fail("gamepack_structure_invalid", "logic rule order must be an integer")
        rule_keys.append((order, _record_id(rule, f"gamepack.logic.rules/{index}").encode("utf-8")))
    if rule_keys != sorted(rule_keys):
        _fail("logic_order_invalid", "logic.rules is not canonical")
    if len({key[1].decode("utf-8").casefold() for key in rule_keys}) != len(rule_keys):
        _fail("gamepack_logic_invalid", "logic.rules contains duplicate IDs")
    states = logic["state_schema"]
    assert isinstance(states, list)
    state_ids = [
        _record_id(item, f"gamepack.logic.state_schema/{index}")
        for index, item in enumerate(states)
    ]
    if len({item.casefold() for item in state_ids}) != len(state_ids):
        _fail("gamepack_logic_invalid", "logic.state_schema contains duplicate IDs")
    compiler_owned = [
        index
        for index, item in enumerate(states)
        if isinstance(item, Mapping) and item.get("compiler_owned") is True
    ]
    source_ids = state_ids
    if compiler_owned:
        if compiler_owned != [len(states) - 1]:
            _fail(
                "logic_order_invalid",
                "compiler-owned state must be the final state_schema entry",
            )
        source_ids = state_ids[:-1]
    if source_ids != sorted(source_ids, key=lambda item: item.encode("utf-8")):
        _fail("logic_order_invalid", "logic.state_schema is not canonical")


def _state_fields(record: Mapping[str, object], *, compiler_owned: bool) -> frozenset[str]:
    common = {"id", "type", "initial", "mutability", "persistence"}
    if compiler_owned:
        common.add("compiler_owned")
    kind = record.get("type")
    if kind == "integer":
        common.update({"minimum", "maximum"})
    elif kind == "string":
        common.add("allowed_values")
    elif kind == "string_array":
        common.update({"allowed_values", "min_items", "max_items"})
    elif kind != "boolean":
        _fail("state_type_unsupported", f"unsupported state type {kind!r}")
    return frozenset(common)


def _string_domain(
    value: object,
    *,
    context: str,
    reason_code: str,
    allow_empty: bool = False,
    canonical_order: bool = True,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        _fail(reason_code, f"{context} must be a bounded string array")
    if len(value) > 256 or not all(
        isinstance(item, str)
        and 0 < len(item) <= 256
        and unicodedata.normalize("NFC", item) == item
        for item in value
    ):
        _fail(reason_code, f"{context} exceeds its string-domain bounds")
    if len(set(value)) != len(value):
        _fail(reason_code, f"{context} contains duplicate values")
    if canonical_order and value != sorted(value, key=lambda item: item.encode("utf-8")):
        _fail(reason_code, f"{context} is not canonical")
    return value


def _validate_state_domain(state: Mapping[str, object], context: str) -> None:
    _record_id(state, context)
    if state.get("mutability") not in {"mutable", "constant"}:
        _fail("state_domain_invalid", f"{context}.mutability is unsupported")
    if state.get("persistence") not in {"saved", "transient"}:
        _fail("state_domain_invalid", f"{context}.persistence is unsupported")
    kind = state.get("type")
    initial = state.get("initial")
    if kind == "boolean":
        if not isinstance(initial, bool):
            _fail("state_domain_invalid", f"{context}.initial must be boolean")
        return
    if kind == "integer":
        minimum = state.get("minimum")
        maximum = state.get("maximum")
        if (
            not isinstance(initial, int)
            or isinstance(initial, bool)
            or not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or not -MAX_SAFE_INTEGER <= minimum <= initial <= maximum <= MAX_SAFE_INTEGER
        ):
            _fail("state_domain_invalid", f"{context} integer domain is invalid")
        return
    allowed = _string_domain(
        state.get("allowed_values"),
        context=f"{context}.allowed_values",
        reason_code="state_domain_invalid",
    )
    if kind == "string":
        if not isinstance(initial, str) or initial not in allowed:
            _fail("state_domain_invalid", f"{context}.initial is not allowed")
        return
    if kind == "string_array":
        minimum = state.get("min_items")
        maximum = state.get("max_items")
        initial_values = _string_domain(
            initial,
            context=f"{context}.initial",
            reason_code="state_domain_invalid",
            allow_empty=True,
            canonical_order=False,
        )
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or not 0 <= minimum <= len(initial_values) <= maximum <= len(allowed)
            or maximum > 256
            or not set(initial_values).issubset(allowed)
        ):
            _fail("state_domain_invalid", f"{context} string-array domain is invalid")
        return
    _fail("state_type_unsupported", f"unsupported state type {kind!r}")


def _validate_parameter_domain(parameter: Mapping[str, object], context: str) -> None:
    _record_id(parameter, context)
    kind = parameter.get("type")
    if kind == "boolean":
        return
    if kind == "integer":
        minimum = parameter.get("minimum")
        maximum = parameter.get("maximum")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or not -MAX_SAFE_INTEGER <= minimum <= maximum <= MAX_SAFE_INTEGER
        ):
            _fail("parameter_domain_invalid", f"{context} integer domain is invalid")
        return
    allowed = _string_domain(
        parameter.get("allowed_values"),
        context=f"{context}.allowed_values",
        reason_code="parameter_domain_invalid",
    )
    if kind == "string":
        return
    if kind == "string_array":
        minimum = parameter.get("min_items")
        maximum = parameter.get("max_items")
        if (
            not isinstance(minimum, int)
            or isinstance(minimum, bool)
            or not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or not 0 <= minimum <= maximum <= len(allowed)
            or maximum > 256
        ):
            _fail("parameter_domain_invalid", f"{context} string-array domain is invalid")
        return
    _fail("parameter_type_unsupported", f"unsupported parameter type {kind!r}")


def _bounded_parameter_domain_cardinality(
    parameter: Mapping[str, object],
    *,
    limit: int,
) -> int:
    """Return a domain size capped at ``limit + 1`` without large products."""

    kind = parameter["type"]
    if kind == "boolean":
        return 2
    if kind == "integer":
        cardinality = int(parameter["maximum"]) - int(parameter["minimum"]) + 1
        return cardinality if cardinality <= limit else limit + 1
    allowed = parameter["allowed_values"]
    assert isinstance(allowed, list)
    if kind == "string":
        return len(allowed)
    assert kind == "string_array"
    minimum = int(parameter["min_items"])
    maximum = int(parameter["max_items"])
    permutation = 1
    total = 0
    for size in range(maximum + 1):
        if size >= minimum:
            total += permutation
            if total > limit:
                return limit + 1
        if size == maximum:
            break
        factor = len(allowed) - size
        if permutation > limit // factor:
            permutation = limit + 1
        else:
            permutation *= factor
    return total


def _condition_fields(record: Mapping[str, object]) -> frozenset[str]:
    operator = record.get("operator")
    common = {"id", "action_id", "operator"}
    if operator == "constant":
        common.add("value")
    elif operator == "compare":
        common.update({"comparison", "left", "right"})
    elif operator in {"all", "any"}:
        common.add("condition_ids")
    elif operator == "not":
        common.add("condition_id")
    elif operator == "index_valid":
        common.update({"array_state_id", "index"})
    elif operator == "integer_distance":
        common.update({"left", "right", "distance"})
    else:
        _fail("operator_unsupported", f"unsupported condition operator {operator!r}")
    return frozenset(common)


def _effect_fields(record: Mapping[str, object]) -> frozenset[str]:
    operation = record.get("operation")
    common = {"id", "action_id", "operation", "invalid_transition_policy"}
    if operation == "set":
        common.update({"state_id", "value"})
    elif operation == "reset":
        common.add("state_id")
    elif operation == "increment":
        common.update({"state_id", "amount"})
    elif operation == "swap_array_items":
        common.update({"array_state_id", "first_index", "second_index"})
    elif operation == "append_unique":
        common.update({"array_state_id", "value"})
    else:
        _fail("operator_unsupported", f"unsupported effect operation {operation!r}")
    return frozenset(common)


def _validate_operand(value: object, context: str) -> None:
    operand = _object(value, context)
    kind = operand.get("kind")
    if kind == "literal":
        _exact_keys(operand, frozenset({"kind", "value_type", "value"}), context)
        if operand.get("value_type") not in {
            "boolean",
            "integer",
            "string",
            "string_array",
        }:
            _fail("operand_invalid", f"{context}.value_type is unsupported")
    elif kind == "state":
        _exact_keys(operand, frozenset({"kind", "state_id"}), context)
    elif kind == "parameter":
        _exact_keys(
            operand,
            frozenset({"kind", "action_id", "parameter_id"}),
            context,
        )
    else:
        _fail("operator_unsupported", f"unsupported operand kind {kind!r}")


def _operand_domain(
    value: object,
    context: str,
    *,
    state_domains: Mapping[str, _ValueDomain],
    parameter_domains: Mapping[str, Mapping[str, _ValueDomain]],
    action_scope: str | None,
) -> _ValueDomain:
    _validate_operand(value, context)
    operand = _object(value, context)
    kind = operand["kind"]
    if kind == "literal":
        value_type = str(operand["value_type"])
        literal = operand["value"]
        if value_type == "boolean":
            if type(literal) is not bool:
                _fail("operand_invalid", f"{context}.value must be boolean")
            return _ValueDomain("boolean")
        if value_type == "integer":
            if type(literal) is not int or not -MAX_SAFE_INTEGER <= literal <= MAX_SAFE_INTEGER:
                _fail("operand_invalid", f"{context}.value must be a safe integer")
            return _ValueDomain("integer", minimum=literal, maximum=literal)
        if value_type == "string":
            checked = _non_empty_string(literal, f"{context}.value", maximum=256)
            return _ValueDomain("string", allowed_values=frozenset({checked}))
        if type(literal) is not list or len(literal) > 256:
            _fail("operand_invalid", f"{context}.value must be a bounded string array")
        values = [
            _non_empty_string(item, f"{context}.value/{index}", maximum=256)
            for index, item in enumerate(literal)
        ]
        if len(set(values)) != len(values):
            _fail("operand_invalid", f"{context}.value contains duplicates")
        return _ValueDomain(
            "string_array",
            allowed_values=frozenset(values),
            min_items=len(values),
            max_items=len(values),
        )
    if kind == "state":
        state_id = _identifier(
            operand["state_id"],
            f"{context}.state_id",
            allow_internal=True,
        )
        domain = state_domains.get(state_id.casefold())
        if domain is None:
            _fail("operand_invalid", f"{context} references unknown state {state_id}")
        return domain
    action_id = _identifier(operand["action_id"], f"{context}.action_id")
    parameter_id = _identifier(operand["parameter_id"], f"{context}.parameter_id")
    if action_scope is None or action_id != action_scope:
        _fail("operand_invalid", f"{context} parameter action is out of scope")
    parameters = parameter_domains.get(action_id.casefold())
    if parameters is None or parameter_id.casefold() not in parameters:
        _fail("operand_invalid", f"{context} references an unknown action parameter")
    return parameters[parameter_id.casefold()]


def _condition_closure(
    roots: list[str] | tuple[str, ...],
    children: Mapping[str, tuple[str, ...]],
) -> set[str]:
    closure: set[str] = set()
    pending = [item.casefold() for item in roots]
    while pending:
        key = pending.pop()
        if key in closure:
            continue
        closure.add(key)
        pending.extend(item.casefold() for item in children.get(key, ()))
    return closure


def _condition_operand_state_ids(condition: Mapping[str, object]) -> set[str]:
    result: set[str] = set()
    if condition.get("operator") == "index_valid":
        state_id = condition.get("array_state_id")
        if isinstance(state_id, str):
            result.add(state_id.casefold())
    fields = (
        ("left", "right")
        if condition.get("operator") in {"compare", "integer_distance"}
        else ("index",)
        if condition.get("operator") == "index_valid"
        else ()
    )
    for field in fields:
        operand = condition.get(field)
        if isinstance(operand, Mapping) and operand.get("kind") == "state":
            state_id = operand.get("state_id")
            if isinstance(state_id, str):
                result.add(state_id.casefold())
    return result


def _effect_state_ids(effect: Mapping[str, object]) -> set[str]:
    state_field = (
        "array_state_id"
        if effect.get("operation") in {"swap_array_items", "append_unique"}
        else "state_id"
    )
    result = {str(effect.get(state_field)).casefold()}
    for field in ("value", "amount", "first_index", "second_index"):
        operand = effect.get(field)
        if isinstance(operand, Mapping) and operand.get("kind") == "state":
            state_id = operand.get("state_id")
            if isinstance(state_id, str):
                result.add(state_id.casefold())
    return result


def _validate_logic_record_shapes(logic: Mapping[str, object]) -> None:
    exact_collections = {
        "core_verbs": frozenset({"id", "description"}),
        "actions": frozenset(
            {
                "id",
                "core_verb_id",
                "parameters",
                "source_bindings",
                "rule_ids",
                "presentation_hook_ids",
                "required_feature_ids",
            }
        ),
        "rules": frozenset(
            {"id", "action_id", "order", "condition_ids", "effect_ids", "event_ids"}
        ),
        "goals": frozenset({"id", "condition_ids", "success_ending_id"}),
        "failures": frozenset({"id", "condition_ids", "recovery_action_ids"}),
        "endings": frozenset({"id", "kind", "condition_ids", "event_ids", "presentation_hook_ids"}),
        "events": frozenset({"id"}),
        "presentation_hooks": frozenset({"id", "kind", "asset_binding_ids"}),
        "mechanics": frozenset(
            {
                "id",
                "core_verb_id",
                "action_id",
                "authoritative_state_ids",
                "condition_ids",
                "rule_ids",
                "effect_ids",
                "event_ids",
                "presentation_hook_ids",
                "asset_binding_ids",
                "required_feature_ids",
            }
        ),
        "narrative_transitions": frozenset(
            {
                "compiler_owned",
                "id",
                "action_id",
                "source_unit_id",
                "option_id",
                "target_unit_id",
                "precondition",
                "effect",
                "atomic_source_condition_ids",
                "atomic_source_effect_ids",
            }
        ),
    }
    for index, raw in enumerate(logic["state_schema"]):  # type: ignore[union-attr]
        context = f"gamepack.logic.state_schema/{index}"
        record = _object(raw, context)
        _exact_keys(
            record,
            _state_fields(record, compiler_owned=record.get("compiler_owned") is True),
            context,
        )
        _validate_state_domain(record, context)
    cursor = logic.get("narrative_cursor")
    if cursor is not None:
        checked_cursor = _object(cursor, "gamepack.logic.narrative_cursor")
        _exact_keys(
            checked_cursor,
            _state_fields(checked_cursor, compiler_owned=True),
            "gamepack.logic.narrative_cursor",
        )
        if checked_cursor.get("compiler_owned") is not True:
            _fail("gamepack_logic_invalid", "narrative cursor must be compiler-owned")
    for collection, fields in exact_collections.items():
        for index, raw in enumerate(logic[collection]):  # type: ignore[union-attr]
            context = f"gamepack.logic.{collection}/{index}"
            record = _object(raw, context)
            _exact_keys(record, fields, context)
            if collection == "actions":
                parameters = _array(
                    record.get("parameters"),
                    f"{context}.parameters",
                    maximum=16,
                )
                for parameter_index, parameter_raw in enumerate(parameters):
                    parameter_context = f"{context}.parameters/{parameter_index}"
                    parameter = _object(parameter_raw, parameter_context)
                    kind = parameter.get("type")
                    if kind == "boolean":
                        parameter_fields = frozenset({"id", "type"})
                    elif kind == "integer":
                        parameter_fields = frozenset({"id", "type", "minimum", "maximum"})
                    elif kind == "string":
                        parameter_fields = frozenset({"id", "type", "allowed_values"})
                    elif kind == "string_array":
                        parameter_fields = frozenset(
                            {"id", "type", "allowed_values", "min_items", "max_items"}
                        )
                    else:
                        _fail("parameter_type_unsupported", f"unsupported parameter type {kind!r}")
                    _exact_keys(parameter, parameter_fields, parameter_context)
                    _validate_parameter_domain(parameter, parameter_context)
            elif collection == "narrative_transitions":
                precondition = _object(record.get("precondition"), f"{context}.precondition")
                _exact_keys(
                    precondition,
                    frozenset(
                        {
                            "compiler_owned",
                            "id",
                            "operator",
                            "cursor_state_id",
                            "value",
                        }
                    ),
                    f"{context}.precondition",
                )
                effect = _object(record.get("effect"), f"{context}.effect")
                _exact_keys(
                    effect,
                    frozenset(
                        {
                            "compiler_owned",
                            "id",
                            "operation",
                            "cursor_state_id",
                            "value",
                            "invalid_transition_policy",
                        }
                    ),
                    f"{context}.effect",
                )
    for index, raw in enumerate(logic["conditions"]):  # type: ignore[union-attr]
        context = f"gamepack.logic.conditions/{index}"
        condition = _object(raw, context)
        _exact_keys(condition, _condition_fields(condition), context)
        operator = condition.get("operator")
        if operator in {"compare", "integer_distance"}:
            _validate_operand(condition.get("left"), f"{context}.left")
            _validate_operand(condition.get("right"), f"{context}.right")
        elif operator == "index_valid":
            _validate_operand(condition.get("index"), f"{context}.index")
    for index, raw in enumerate(logic["effects"]):  # type: ignore[union-attr]
        context = f"gamepack.logic.effects/{index}"
        effect = _object(raw, context)
        _exact_keys(effect, _effect_fields(effect), context)
        operation = effect.get("operation")
        for field in {
            "set": ("value",),
            "increment": ("amount",),
            "swap_array_items": ("first_index", "second_index"),
            "append_unique": ("value",),
        }.get(str(operation), ()):
            _validate_operand(effect.get(field), f"{context}.{field}")


def _validate_executable_logic(logic: Mapping[str, object]) -> dict[str, object]:
    states = _record_registry(logic, "state_schema")
    actions = _record_registry(logic, "actions")
    conditions = _record_registry(logic, "conditions")
    effects = _record_registry(logic, "effects")
    rules = _record_registry(logic, "rules")
    goals = _record_registry(logic, "goals")
    _record_registry(logic, "failures")
    endings = _record_registry(logic, "endings")
    events = _record_registry(logic, "events")
    hooks = _record_registry(logic, "presentation_hooks")
    _record_registry(logic, "mechanics")
    if not goals:
        _fail("gamepack_logic_invalid", "logic.goals must be non-empty")

    core_verbs = _record_registry(logic, "core_verbs")
    for index, record in enumerate(logic["core_verbs"]):  # type: ignore[index]
        assert isinstance(record, Mapping)
        _non_empty_string(
            record.get("description"),
            f"gamepack.logic.core_verbs/{index}.description",
        )

    state_domains = {key: _state_domain(record) for key, record in states.items()}
    parameter_domains: dict[str, dict[str, _ValueDomain]] = {}
    source_bindings_by_action: dict[str, tuple[tuple[str, str, str], ...]] = {}
    mapped_core_verbs: set[str] = set()
    for index, action in enumerate(logic["actions"]):  # type: ignore[index]
        assert isinstance(action, Mapping)
        context = f"gamepack.logic.actions/{index}"
        action_id = _identifier(action.get("id"), f"{context}.id")
        core_verb_id = _identifier(action.get("core_verb_id"), f"{context}.core_verb_id")
        if core_verb_id.casefold() not in core_verbs:
            _fail("gamepack_logic_invalid", f"{context} references an unknown core verb")
        mapped_core_verbs.add(core_verb_id.casefold())

        parameters = _array(action.get("parameters"), f"{context}.parameters", maximum=16)
        domains: dict[str, _ValueDomain] = {}
        parameter_ids: list[str] = []
        parameter_combinations = 1
        combination_limit = int(ANALYSIS_LIMITS["parameter_combinations_per_action"])
        for parameter_index, raw_parameter in enumerate(parameters):
            parameter_context = f"{context}.parameters/{parameter_index}"
            parameter = _object(raw_parameter, parameter_context)
            parameter_id = _identifier(parameter.get("id"), f"{parameter_context}.id")
            key = parameter_id.casefold()
            if key in domains:
                _fail("gamepack_logic_invalid", f"{context}.parameters contains duplicate IDs")
            domains[key] = _state_domain(parameter)
            parameter_ids.append(parameter_id)
            cardinality = _bounded_parameter_domain_cardinality(
                parameter,
                limit=combination_limit,
            )
            if (
                cardinality > combination_limit
                or parameter_combinations > combination_limit // cardinality
            ):
                _fail(
                    "parameter_combinations_exceeded",
                    f"action {action_id} exceeds the per-action combination limit",
                )
            parameter_combinations *= cardinality
        if parameter_ids != sorted(parameter_ids, key=lambda item: item.encode("utf-8")):
            _fail("logic_order_invalid", f"{context}.parameters is not canonical")
        parameter_domains[action_id.casefold()] = domains

        raw_bindings = _array(
            action.get("source_bindings"),
            f"{context}.source_bindings",
            maximum=16,
        )
        if not raw_bindings:
            _fail("gamepack_logic_invalid", f"{context}.source_bindings must be non-empty")
        binding_keys: list[tuple[str, str, str]] = []
        for binding_index, raw_binding in enumerate(raw_bindings):
            binding_context = f"{context}.source_bindings/{binding_index}"
            binding = _object(raw_binding, binding_context)
            kind = binding.get("kind")
            if kind == "narrative_option":
                _exact_keys(
                    binding,
                    frozenset({"kind", "source_id", "option_id"}),
                    binding_context,
                )
                option_id = _identifier(
                    binding.get("option_id"),
                    f"{binding_context}.option_id",
                )
            elif kind in {"activity", "system"}:
                _exact_keys(
                    binding,
                    frozenset({"kind", "source_id"}),
                    binding_context,
                )
                option_id = ""
            else:
                _fail("gamepack_logic_invalid", f"{binding_context}.kind is unsupported")
            source_id = _identifier(binding.get("source_id"), f"{binding_context}.source_id")
            binding_keys.append((str(kind), source_id, option_id))
        if len(set(binding_keys)) != len(binding_keys):
            _fail("gamepack_logic_invalid", f"{context}.source_bindings contains duplicates")
        if binding_keys != sorted(
            binding_keys,
            key=lambda item: tuple(part.encode("utf-8") for part in item),
        ):
            _fail("logic_order_invalid", f"{context}.source_bindings is not canonical")
        source_bindings_by_action[action_id.casefold()] = tuple(binding_keys)

        _id_array(action.get("rule_ids"), f"{context}.rule_ids", registry=rules, allow_empty=False)
        _id_array(
            action.get("presentation_hook_ids"),
            f"{context}.presentation_hook_ids",
            registry=hooks,
        )
        features = _token_array(
            action.get("required_feature_ids"),
            f"{context}.required_feature_ids",
            allow_empty=False,
            maximum=64,
        )
        unsupported = sorted(set(features) - SUPPORTED_REQUIRED_FEATURES_V1)
        if unsupported:
            _fail(
                "required_feature_unsupported",
                f"{context} requires unsupported v1 features {unsupported!r}",
            )
    if mapped_core_verbs != set(core_verbs):
        _fail("gamepack_logic_invalid", "actions do not map the exact core-verb set")

    condition_scopes: dict[str, str | None] = {}
    condition_children: dict[str, tuple[str, ...]] = {}
    for index, condition in enumerate(logic["conditions"]):  # type: ignore[index]
        assert isinstance(condition, Mapping)
        context = f"gamepack.logic.conditions/{index}"
        condition_id = _identifier(condition.get("id"), f"{context}.id")
        raw_scope = condition.get("action_id")
        scope = None if raw_scope is None else _identifier(raw_scope, f"{context}.action_id")
        if scope is not None and scope.casefold() not in actions:
            _fail("gamepack_logic_invalid", f"{context} references an unknown action")
        operator = condition.get("operator")
        children: tuple[str, ...] = ()
        if operator == "constant":
            if type(condition.get("value")) is not bool:
                _fail("condition_invalid", f"{context}.value must be boolean")
        elif operator == "compare":
            comparison = condition.get("comparison")
            if comparison not in {
                "equal",
                "not_equal",
                "less_than",
                "less_or_equal",
                "greater_than",
                "greater_or_equal",
            }:
                _fail("operator_unsupported", f"{context}.comparison is unsupported")
            left = _operand_domain(
                condition.get("left"),
                f"{context}.left",
                state_domains=state_domains,
                parameter_domains=parameter_domains,
                action_scope=scope,
            )
            right = _operand_domain(
                condition.get("right"),
                f"{context}.right",
                state_domains=state_domains,
                parameter_domains=parameter_domains,
                action_scope=scope,
            )
            if left.value_type != right.value_type:
                _fail("operand_type_mismatch", f"{context} compare operand types differ")
            if comparison not in {"equal", "not_equal"} and left.value_type != "integer":
                _fail("operand_type_mismatch", f"{context} ordered compare requires integers")
        elif operator in {"all", "any"}:
            children = tuple(
                _id_array(
                    condition.get("condition_ids"),
                    f"{context}.condition_ids",
                    registry=conditions,
                    allow_empty=False,
                )
            )
        elif operator == "not":
            child = _identifier(condition.get("condition_id"), f"{context}.condition_id")
            if child.casefold() not in conditions:
                _fail("gamepack_logic_invalid", f"{context} references unknown condition {child}")
            children = (child,)
        elif operator == "index_valid":
            if scope is None:
                _fail("condition_scope_invalid", f"{context} requires an action scope")
            state_id = _identifier(
                condition.get("array_state_id"),
                f"{context}.array_state_id",
                allow_internal=True,
            )
            domain = state_domains.get(state_id.casefold())
            if domain is None or domain.value_type != "string_array":
                _fail("condition_invalid", f"{context} requires a known string-array state")
            index_domain = _operand_domain(
                condition.get("index"),
                f"{context}.index",
                state_domains=state_domains,
                parameter_domains=parameter_domains,
                action_scope=scope,
            )
            if index_domain.value_type != "integer":
                _fail("operand_type_mismatch", f"{context}.index requires integer")
        elif operator == "integer_distance":
            if scope is None:
                _fail("condition_scope_invalid", f"{context} requires an action scope")
            for field in ("left", "right"):
                domain = _operand_domain(
                    condition.get(field),
                    f"{context}.{field}",
                    state_domains=state_domains,
                    parameter_domains=parameter_domains,
                    action_scope=scope,
                )
                if domain.value_type != "integer":
                    _fail("operand_type_mismatch", f"{context}.{field} requires integer")
            distance = condition.get("distance")
            if type(distance) is not int or distance != 1:
                _fail("condition_invalid", f"{context}.distance must be exactly 1")
        else:
            _fail("operator_unsupported", f"{context}.operator is unsupported")
        condition_scopes[condition_id.casefold()] = scope
        condition_children[condition_id.casefold()] = children

    adjacency = {
        key: tuple(child.casefold() for child in children)
        for key, children in condition_children.items()
    }
    for key, child_keys in adjacency.items():
        for child_key in child_keys:
            if condition_scopes[child_key] != condition_scopes[key]:
                _fail("condition_scope_invalid", "condition graph crosses action scope")
    color: dict[str, int] = {}
    for root in conditions:
        if color.get(root) == 2:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            key, next_index = stack[-1]
            if color.get(key, 0) == 0:
                color[key] = 1
            child_keys = adjacency.get(key, ())
            if next_index >= len(child_keys):
                color[key] = 2
                stack.pop()
                continue
            child = child_keys[next_index]
            stack[-1] = (key, next_index + 1)
            if color.get(child, 0) == 1:
                _fail("condition_cycle", f"condition cycle at {conditions[child]['id']}")
            if color.get(child, 0) == 0:
                stack.append((child, 0))

    effect_actions: dict[str, str] = {}
    for index, effect in enumerate(logic["effects"]):  # type: ignore[index]
        assert isinstance(effect, Mapping)
        context = f"gamepack.logic.effects/{index}"
        effect_id = _identifier(effect.get("id"), f"{context}.id")
        action_id = _identifier(effect.get("action_id"), f"{context}.action_id")
        if action_id.casefold() not in actions:
            _fail("gamepack_logic_invalid", f"{context} references an unknown action")
        if effect.get("invalid_transition_policy") != "reject_transition":
            _fail("effect_policy_unsupported", f"{context} policy is unsupported")
        operation = effect.get("operation")
        state_field = (
            "array_state_id" if operation in {"swap_array_items", "append_unique"} else "state_id"
        )
        state_id = _identifier(
            effect.get(state_field),
            f"{context}.{state_field}",
            allow_internal=True,
        )
        state = states.get(state_id.casefold())
        if state is None or state.get("mutability") != "mutable":
            _fail("effect_state_invalid", f"{context} target is unknown or constant")
        target_domain = state_domains[state_id.casefold()]
        if operation == "set":
            source_domain = _operand_domain(
                effect.get("value"),
                f"{context}.value",
                state_domains=state_domains,
                parameter_domains=parameter_domains,
                action_scope=action_id,
            )
            _domain_subset(source_domain, target_domain, f"{context}.value")
        elif operation == "increment":
            if target_domain.value_type != "integer":
                _fail("operand_type_mismatch", f"{context} increment target is not integer")
            amount = _operand_domain(
                effect.get("amount"),
                f"{context}.amount",
                state_domains=state_domains,
                parameter_domains=parameter_domains,
                action_scope=action_id,
            )
            if amount.value_type != "integer":
                _fail("operand_type_mismatch", f"{context}.amount is not integer")
        elif operation == "swap_array_items":
            if target_domain.value_type != "string_array":
                _fail("operand_type_mismatch", f"{context} swap target is not an array")
            for field in ("first_index", "second_index"):
                index_domain = _operand_domain(
                    effect.get(field),
                    f"{context}.{field}",
                    state_domains=state_domains,
                    parameter_domains=parameter_domains,
                    action_scope=action_id,
                )
                if index_domain.value_type != "integer":
                    _fail("operand_type_mismatch", f"{context}.{field} is not integer")
        elif operation == "append_unique":
            if target_domain.value_type != "string_array":
                _fail("operand_type_mismatch", f"{context} append target is not an array")
            value_domain = _operand_domain(
                effect.get("value"),
                f"{context}.value",
                state_domains=state_domains,
                parameter_domains=parameter_domains,
                action_scope=action_id,
            )
            if value_domain.value_type != "string":
                _fail("operand_type_mismatch", f"{context}.value is not string")
            assert target_domain.allowed_values is not None
            assert value_domain.allowed_values is not None
            if not value_domain.allowed_values.issubset(target_domain.allowed_values):
                _fail("operand_domain_invalid", f"{context}.value is not allowed")
        elif operation != "reset":
            _fail("operator_unsupported", f"{context}.operation is unsupported")
        effect_actions[effect_id.casefold()] = action_id

    for index, hook in enumerate(logic["presentation_hooks"]):  # type: ignore[index]
        assert isinstance(hook, Mapping)
        context = f"gamepack.logic.presentation_hooks/{index}"
        if hook.get("kind") not in {"board", "text", "feedback", "ending"}:
            _fail("gamepack_logic_invalid", f"{context}.kind is unsupported")
        _id_array(
            hook.get("asset_binding_ids"),
            f"{context}.asset_binding_ids",
            allow_empty=False,
        )

    rule_actions: dict[str, str] = {}
    rule_conditions: dict[str, tuple[str, ...]] = {}
    rule_effects: dict[str, tuple[str, ...]] = {}
    rule_events: dict[str, tuple[str, ...]] = {}
    seen_orders: set[int] = set()
    for index, rule in enumerate(logic["rules"]):  # type: ignore[index]
        assert isinstance(rule, Mapping)
        context = f"gamepack.logic.rules/{index}"
        rule_id = _identifier(rule.get("id"), f"{context}.id")
        action_id = _identifier(rule.get("action_id"), f"{context}.action_id")
        if action_id.casefold() not in actions:
            _fail("gamepack_logic_invalid", f"{context} references an unknown action")
        order = rule.get("order")
        if type(order) is not int or not 0 <= order <= MAX_SAFE_INTEGER or order in seen_orders:
            _fail("gamepack_logic_invalid", f"{context}.order is invalid or ambiguous")
        seen_orders.add(order)
        checked_conditions = tuple(
            _id_array(
                rule.get("condition_ids"),
                f"{context}.condition_ids",
                registry=conditions,
            )
        )
        if any(condition_scopes[item.casefold()] != action_id for item in checked_conditions):
            _fail("condition_scope_invalid", f"{context} condition crosses action scope")
        checked_effects = tuple(
            _id_array(
                rule.get("effect_ids"),
                f"{context}.effect_ids",
                registry=effects,
                allow_empty=False,
            )
        )
        if any(effect_actions[item.casefold()] != action_id for item in checked_effects):
            _fail("effect_scope_invalid", f"{context} effect crosses action scope")
        checked_events = tuple(
            _id_array(
                rule.get("event_ids"),
                f"{context}.event_ids",
                registry=events,
            )
        )
        key = rule_id.casefold()
        rule_actions[key] = action_id
        rule_conditions[key] = checked_conditions
        rule_effects[key] = checked_effects
        rule_events[key] = checked_events

    action_closures: dict[str, dict[str, set[str]]] = {}
    for index, action in enumerate(logic["actions"]):  # type: ignore[index]
        assert isinstance(action, Mapping)
        context = f"gamepack.logic.actions/{index}"
        action_id = str(action["id"])
        action_key = action_id.casefold()
        action_rule_ids = _id_array(
            action.get("rule_ids"),
            f"{context}.rule_ids",
            registry=rules,
            allow_empty=False,
        )
        if any(rule_actions[item.casefold()] != action_id for item in action_rule_ids):
            _fail("gamepack_logic_invalid", f"{context} references another action's rule")
        expected_rules = {key for key, owner in rule_actions.items() if owner == action_id}
        actual_rules = {item.casefold() for item in action_rule_ids}
        if actual_rules != expected_rules:
            _fail("gamepack_logic_invalid", f"{context}.rule_ids is not the exact closure")
        direct_conditions = {
            item.casefold() for key in actual_rules for item in rule_conditions[key]
        }
        condition_keys = _condition_closure(tuple(direct_conditions), condition_children)
        effect_keys = {item.casefold() for key in actual_rules for item in rule_effects[key]}
        event_keys = {item.casefold() for key in actual_rules for item in rule_events[key]}
        hook_keys = {
            item.casefold()
            for item in _id_array(
                action.get("presentation_hook_ids"),
                f"{context}.presentation_hook_ids",
                registry=hooks,
            )
        }
        binding_keys = {
            str(binding).casefold()
            for hook_key in hook_keys
            for binding in hooks[hook_key]["asset_binding_ids"]  # type: ignore[index,union-attr]
        }
        state_keys = {
            state_id
            for condition_key in condition_keys
            for state_id in _condition_operand_state_ids(conditions[condition_key])
        } | {
            state_id
            for effect_key in effect_keys
            for state_id in _effect_state_ids(effects[effect_key])
        }
        feature_keys = {
            item.casefold()
            for item in _token_array(
                action.get("required_feature_ids"),
                f"{context}.required_feature_ids",
                allow_empty=False,
                maximum=64,
            )
        }
        action_closures[action_key] = {
            "rule_ids": actual_rules,
            "condition_ids": condition_keys,
            "effect_ids": effect_keys,
            "event_ids": event_keys,
            "authoritative_state_ids": state_keys,
            "presentation_hook_ids": hook_keys,
            "asset_binding_ids": binding_keys,
            "required_feature_ids": feature_keys,
        }

    ending_conditions: dict[str, set[str]] = {}
    ending_kinds: dict[str, str] = {}
    for index, ending in enumerate(logic["endings"]):  # type: ignore[index]
        assert isinstance(ending, Mapping)
        context = f"gamepack.logic.endings/{index}"
        ending_id = _identifier(ending.get("id"), f"{context}.id")
        kind = ending.get("kind")
        if kind not in {"success", "failure", "neutral"}:
            _fail("ending_kind_unsupported", f"{context}.kind is unsupported")
        checked_conditions = _id_array(
            ending.get("condition_ids"),
            f"{context}.condition_ids",
            registry=conditions,
            allow_empty=False,
        )
        if any(condition_scopes[item.casefold()] is not None for item in checked_conditions):
            _fail("condition_scope_invalid", f"{context} requires global conditions")
        _id_array(
            ending.get("event_ids"),
            f"{context}.event_ids",
            registry=events,
        )
        _id_array(
            ending.get("presentation_hook_ids"),
            f"{context}.presentation_hook_ids",
            registry=hooks,
            allow_empty=False,
        )
        ending_conditions[ending_id.casefold()] = {item.casefold() for item in checked_conditions}
        ending_kinds[ending_id.casefold()] = str(kind)

    for index, goal in enumerate(logic["goals"]):  # type: ignore[index]
        assert isinstance(goal, Mapping)
        context = f"gamepack.logic.goals/{index}"
        checked_conditions = _id_array(
            goal.get("condition_ids"),
            f"{context}.condition_ids",
            registry=conditions,
            allow_empty=False,
        )
        if any(condition_scopes[item.casefold()] is not None for item in checked_conditions):
            _fail("condition_scope_invalid", f"{context} requires global conditions")
        ending_id = _identifier(goal.get("success_ending_id"), f"{context}.success_ending_id")
        ending_key = ending_id.casefold()
        if ending_key not in endings or ending_kinds[ending_key] != "success":
            _fail("gamepack_logic_invalid", f"{context} requires a success ending")
        if {item.casefold() for item in checked_conditions} != ending_conditions[ending_key]:
            _fail("gamepack_logic_invalid", f"{context} does not match its success ending")

    cursor = logic.get("narrative_cursor")
    compiler_owned_states = [
        state for state in states.values() if state.get("compiler_owned") is True
    ]
    if cursor is None:
        if compiler_owned_states:
            _fail(
                "narrative_transition_invalid",
                "narrative-free logic cannot declare compiler-owned state",
            )
    else:
        checked_cursor = _object(cursor, "gamepack.logic.narrative_cursor")
        cursor_state = states.get("wf_internal_narrative_cursor")
        if (
            len(compiler_owned_states) != 1
            or cursor_state is None
            or cursor_state != checked_cursor
        ):
            _fail(
                "narrative_transition_invalid",
                "state_schema must contain the exact compiler-owned narrative cursor",
            )

    for index, failure in enumerate(logic["failures"]):  # type: ignore[index]
        assert isinstance(failure, Mapping)
        context = f"gamepack.logic.failures/{index}"
        checked_conditions = _id_array(
            failure.get("condition_ids"),
            f"{context}.condition_ids",
            registry=conditions,
            allow_empty=False,
        )
        if any(condition_scopes[item.casefold()] is not None for item in checked_conditions):
            _fail("condition_scope_invalid", f"{context} requires global conditions")
        _id_array(
            failure.get("recovery_action_ids"),
            f"{context}.recovery_action_ids",
            registry=actions,
            allow_empty=False,
        )

    actions_with_mechanics: set[str] = set()
    for index, mechanic in enumerate(logic["mechanics"]):  # type: ignore[index]
        assert isinstance(mechanic, Mapping)
        context = f"gamepack.logic.mechanics/{index}"
        action_id = _identifier(mechanic.get("action_id"), f"{context}.action_id")
        action_key = action_id.casefold()
        action = actions.get(action_key)
        if action is None or action_key in actions_with_mechanics:
            _fail("gamepack_logic_invalid", f"{context} action is unknown or duplicated")
        actions_with_mechanics.add(action_key)
        core_verb_id = _identifier(mechanic.get("core_verb_id"), f"{context}.core_verb_id")
        if action.get("core_verb_id") != core_verb_id:
            _fail("gamepack_logic_invalid", f"{context} core verb differs from its action")
        actual = {
            "authoritative_state_ids": {
                item.casefold()
                for item in _id_array(
                    mechanic.get("authoritative_state_ids"),
                    f"{context}.authoritative_state_ids",
                    registry=states,
                    allow_empty=False,
                    allow_internal=True,
                )
            },
            "condition_ids": {
                item.casefold()
                for item in _id_array(
                    mechanic.get("condition_ids"),
                    f"{context}.condition_ids",
                    registry=conditions,
                )
            },
            "rule_ids": {
                item.casefold()
                for item in _id_array(
                    mechanic.get("rule_ids"),
                    f"{context}.rule_ids",
                    registry=rules,
                    allow_empty=False,
                )
            },
            "effect_ids": {
                item.casefold()
                for item in _id_array(
                    mechanic.get("effect_ids"),
                    f"{context}.effect_ids",
                    registry=effects,
                    allow_empty=False,
                )
            },
            "event_ids": {
                item.casefold()
                for item in _id_array(
                    mechanic.get("event_ids"),
                    f"{context}.event_ids",
                    registry=events,
                )
            },
            "presentation_hook_ids": {
                item.casefold()
                for item in _id_array(
                    mechanic.get("presentation_hook_ids"),
                    f"{context}.presentation_hook_ids",
                    registry=hooks,
                    allow_empty=False,
                )
            },
            "asset_binding_ids": {
                item.casefold()
                for item in _id_array(
                    mechanic.get("asset_binding_ids"),
                    f"{context}.asset_binding_ids",
                    allow_empty=False,
                )
            },
            "required_feature_ids": {
                item.casefold()
                for item in _token_array(
                    mechanic.get("required_feature_ids"),
                    f"{context}.required_feature_ids",
                    allow_empty=False,
                    maximum=64,
                )
            },
        }
        if actual != action_closures[action_key]:
            _fail("gamepack_logic_invalid", f"{context} is not the exact action closure")
    if actions_with_mechanics != set(actions):
        _fail("gamepack_logic_invalid", "every action requires exactly one mechanic")

    return {
        "states": states,
        "actions": actions,
        "conditions": conditions,
        "effects": effects,
        "rules": rules,
        "events": events,
        "hooks": hooks,
        "endings": endings,
        "condition_scopes": condition_scopes,
        "condition_children": condition_children,
        "action_closures": action_closures,
        "source_bindings": source_bindings_by_action,
    }


def _validate_identity(
    value: object,
    context: str,
    *,
    expected_format: str | None = None,
) -> Mapping[str, object]:
    identity = _object(value, context)
    _exact_keys(identity, _IDENTITY_FIELDS, context)
    format_name = _non_empty_string(identity.get("format"), f"{context}.format")
    if expected_format is not None and format_name != expected_format:
        _fail("gamepack_structure_invalid", f"{context}.format is unsupported")
    version = identity.get("format_version")
    if type(version) is not int or version != 1:
        _fail("gamepack_structure_invalid", f"{context}.format_version must be 1")
    _identifier(identity.get("id"), f"{context}.id")
    if (
        not isinstance(identity.get("content_hash"), str)
        or _SHA256_RE.fullmatch(str(identity["content_hash"])) is None
    ):
        _fail("gamepack_structure_invalid", f"{context}.content_hash is invalid")
    return identity


def _validate_modules(
    value: object,
    *,
    logic: Mapping[str, object],
    executable: Mapping[str, object],
) -> Mapping[str, object]:
    modules = _object(value, "gamepack.modules")
    _exact_keys(modules, _MODULE_FIELDS, "gamepack.modules")
    conditions = executable["conditions"]
    effects = executable["effects"]
    events = executable["events"]
    hooks = executable["hooks"]
    actions = executable["actions"]
    closures = executable["action_closures"]
    source_bindings = executable["source_bindings"]
    assert isinstance(conditions, Mapping)
    assert isinstance(effects, Mapping)
    assert isinstance(events, Mapping)
    assert isinstance(hooks, Mapping)
    assert isinstance(actions, Mapping)
    assert isinstance(closures, Mapping)
    assert isinstance(source_bindings, Mapping)

    activities: dict[str, Mapping[str, object]] = {}
    systems: dict[str, Mapping[str, object]] = {}
    narrative_units: dict[str, Mapping[str, object]] = {}
    narrative_options: dict[tuple[str, str], Mapping[str, object]] = {}
    global_sources: dict[str, str] = {}
    expected_formats = {
        "world": "world-forge.world_module",
        "activities": "world-forge.activity_module",
        "narrative": "world-forge.narrative_module",
        "systems": "world-forge.system_module",
    }
    for collection in ("world", "activities", "narrative", "systems"):
        raw_modules = _array(
            modules.get(collection),
            f"gamepack.modules.{collection}",
            maximum=256,
        )
        source_ids: list[str] = []
        for module_index, raw_module in enumerate(raw_modules):
            context = f"gamepack.modules.{collection}/{module_index}"
            module = _object(raw_module, context)
            expected_fields = (
                frozenset({"source", "module_type", "title", "records"})
                if collection == "world"
                else frozenset({"source", "title", "activities"})
                if collection == "activities"
                else frozenset({"source", "title", "entry_unit_ids", "units"})
                if collection == "narrative"
                else frozenset({"source", "title", "systems"})
            )
            _exact_keys(module, expected_fields, context)
            identity = _validate_identity(
                module.get("source"),
                f"{context}.source",
                expected_format=expected_formats[collection],
            )
            source_ids.append(str(identity["id"]))
            _non_empty_string(module.get("title"), f"{context}.title")

            payload_field = (
                "records"
                if collection == "world"
                else "units"
                if collection == "narrative"
                else collection
            )
            payload = _array(
                module.get(payload_field),
                f"{context}.{payload_field}",
                maximum=4096 if collection == "world" else 1024,
            )
            if not payload:
                _fail("gamepack_structure_invalid", f"{context} payload must be non-empty")
            payload_ids: list[str] = []
            if collection == "world":
                module_type = module.get("module_type")
                fields = _WORLD_RECORD_FIELDS.get(str(module_type))
                if fields is None:
                    _fail("gamepack_structure_invalid", f"{context}.module_type is unsupported")
                for record_index, raw_record in enumerate(payload):
                    record_context = f"{context}.records/{record_index}"
                    record = _object(raw_record, record_context)
                    _exact_keys(record, fields, record_context)
                    payload_ids.append(_identifier(record.get("id"), f"{record_context}.id"))
                    if module_type == "canon":
                        _non_empty_string(record.get("statement"), f"{record_context}.statement")
                        if record.get("status") not in {"canon", "provisional"}:
                            _fail("gamepack_structure_invalid", f"{record_context}.status")
                    elif module_type == "chronology":
                        if type(record.get("sequence")) is not int:
                            _fail("gamepack_structure_invalid", f"{record_context}.sequence")
                        _non_empty_string(record.get("summary"), f"{record_context}.summary")
                    elif module_type == "space":
                        _non_empty_string(record.get("name"), f"{record_context}.name")
                        if record.get("topology") not in {"abstract", "symbolic", "diegetic"}:
                            _fail("gamepack_structure_invalid", f"{record_context}.topology")
                    elif module_type in {"group", "character"}:
                        _non_empty_string(record.get("name"), f"{record_context}.name")
                        _non_empty_string(
                            record.get("group_type" if module_type == "group" else "role"),
                            f"{record_context}.kind",
                        )
                    else:
                        _non_empty_string(record.get("statement"), f"{record_context}.statement")
                        if record.get("access") not in {"public", "restricted", "secret"}:
                            _fail("gamepack_structure_invalid", f"{record_context}.access")
            elif collection == "activities":
                for record_index, raw_record in enumerate(payload):
                    record_context = f"{context}.activities/{record_index}"
                    record = _object(raw_record, record_context)
                    _exact_keys(record, _ACTIVITY_FIELDS, record_context)
                    identifier = _identifier(record.get("id"), f"{record_context}.id")
                    payload_ids.append(identifier)
                    if record.get("activity_type") not in {
                        "level",
                        "mission",
                        "quest",
                        "scenario",
                        "match",
                        "race",
                        "puzzle",
                        "encounter",
                        "contract",
                        "expedition",
                        "run",
                        "tutorial",
                        "challenge",
                    }:
                        _fail(
                            "gamepack_structure_invalid",
                            f"{record_context}.activity_type is unsupported",
                        )
                    _non_empty_string(record.get("title"), f"{record_context}.title")
                    for field in (
                        "participant_ids",
                        "spatial_context_ids",
                        "start_condition_ids",
                        "end_condition_ids",
                        "success_condition_ids",
                        "failure_condition_ids",
                        "effect_ids",
                        "event_ids",
                        "presentation_hook_ids",
                        "asset_binding_ids",
                    ):
                        registry = (
                            conditions
                            if field.endswith("condition_ids")
                            else effects
                            if field == "effect_ids"
                            else events
                            if field == "event_ids"
                            else hooks
                            if field == "presentation_hook_ids"
                            else None
                        )
                        _id_array(
                            record.get(field),
                            f"{record_context}.{field}",
                            registry=registry,
                            maximum=256,
                        )
                    key = identifier.casefold()
                    if key in activities:
                        _fail("gamepack_structure_invalid", "duplicate global activity ID")
                    activities[key] = record
            elif collection == "systems":
                for record_index, raw_record in enumerate(payload):
                    record_context = f"{context}.systems/{record_index}"
                    record = _object(raw_record, record_context)
                    _exact_keys(record, _SYSTEM_FIELDS, record_context)
                    identifier = _identifier(record.get("id"), f"{record_context}.id")
                    payload_ids.append(identifier)
                    if record.get("system_type") not in {
                        "rule",
                        "event",
                        "consequence",
                        "schedule",
                        "economy",
                        "production_process",
                        "simulation_scenario",
                        "world_modifier",
                        "season",
                    }:
                        _fail(
                            "gamepack_structure_invalid",
                            f"{record_context}.system_type is unsupported",
                        )
                    _non_empty_string(record.get("title"), f"{record_context}.title")
                    for field, registry in (
                        ("precondition_ids", conditions),
                        ("effect_ids", effects),
                        ("event_ids", events),
                        ("asset_binding_ids", None),
                    ):
                        _id_array(
                            record.get(field),
                            f"{record_context}.{field}",
                            registry=registry,
                            maximum=256,
                        )
                    key = identifier.casefold()
                    if key in systems:
                        _fail("gamepack_structure_invalid", "duplicate global system ID")
                    systems[key] = record
            else:
                entry_ids = _id_array(
                    module.get("entry_unit_ids"),
                    f"{context}.entry_unit_ids",
                    allow_empty=False,
                    maximum=256,
                )
                del entry_ids
                for record_index, raw_record in enumerate(payload):
                    record_context = f"{context}.units/{record_index}"
                    record = _object(raw_record, record_context)
                    unit_type = record.get("unit_type")
                    extra = (
                        frozenset({"options"})
                        if unit_type == "choice"
                        else frozenset({"ending_kind"})
                        if unit_type == "ending"
                        else frozenset()
                    )
                    _exact_keys(record, _NARRATIVE_COMMON_FIELDS | extra, record_context)
                    identifier = _identifier(record.get("id"), f"{record_context}.id")
                    payload_ids.append(identifier)
                    if unit_type not in {
                        "arc",
                        "beat",
                        "scene",
                        "dialogue",
                        "storylet",
                        "clue",
                        "reveal",
                        "memory",
                        "episode",
                        "choice",
                        "ending",
                    }:
                        _fail(
                            "gamepack_structure_invalid",
                            f"{record_context}.unit_type is unsupported",
                        )
                    _non_empty_string(record.get("title"), f"{record_context}.title")
                    next_unit_ids: list[str] = []
                    for field, registry in (
                        ("prerequisite_ids", conditions),
                        ("effect_ids", effects),
                        ("next_unit_ids", None),
                        ("asset_binding_ids", None),
                    ):
                        checked_ids = _id_array(
                            record.get(field),
                            f"{record_context}.{field}",
                            registry=registry,
                            maximum=256,
                        )
                        if field == "next_unit_ids":
                            next_unit_ids = checked_ids
                    if unit_type == "choice":
                        options = _array(
                            record.get("options"),
                            f"{record_context}.options",
                            maximum=64,
                        )
                        if len(options) < 2:
                            _fail(
                                "gamepack_structure_invalid",
                                f"{record_context}.options requires two choices",
                            )
                        option_ids: list[str] = []
                        for option_index, raw_option in enumerate(options):
                            option_context = f"{record_context}.options/{option_index}"
                            option = _object(raw_option, option_context)
                            _exact_keys(
                                option,
                                frozenset(
                                    {
                                        "id",
                                        "label",
                                        "next_unit_id",
                                        "condition_ids",
                                        "effect_ids",
                                    }
                                ),
                                option_context,
                            )
                            option_id = _identifier(option.get("id"), f"{option_context}.id")
                            option_ids.append(option_id)
                            _non_empty_string(option.get("label"), f"{option_context}.label")
                            _identifier(
                                option.get("next_unit_id"),
                                f"{option_context}.next_unit_id",
                            )
                            _id_array(
                                option.get("condition_ids"),
                                f"{option_context}.condition_ids",
                                registry=conditions,
                            )
                            _id_array(
                                option.get("effect_ids"),
                                f"{option_context}.effect_ids",
                                registry=effects,
                            )
                            narrative_options[(identifier.casefold(), option_id.casefold())] = (
                                option
                            )
                        if option_ids != sorted(
                            option_ids,
                            key=lambda item: item.encode("utf-8"),
                        ) or len(set(option_ids)) != len(option_ids):
                            _fail(
                                "logic_order_invalid",
                                f"{record_context}.options is not canonical",
                            )
                        option_targets = sorted(
                            (str(option["next_unit_id"]) for option in options),
                            key=lambda item: item.encode("utf-8"),
                        )
                        if not next_unit_ids or next_unit_ids != option_targets:
                            _fail(
                                "narrative_transition_invalid",
                                f"{record_context}.next_unit_ids must equal option targets",
                            )
                    elif unit_type == "ending":
                        if (
                            record.get("ending_kind")
                            not in {
                                "success",
                                "failure",
                                "neutral",
                            }
                            or record.get("next_unit_ids") != []
                        ):
                            _fail(
                                "gamepack_structure_invalid",
                                f"{record_context} ending is invalid",
                            )
                    key = identifier.casefold()
                    if key in narrative_units:
                        _fail("gamepack_structure_invalid", "duplicate global narrative ID")
                    narrative_units[key] = record
            if payload_ids != sorted(payload_ids, key=lambda item: item.encode("utf-8")):
                _fail("logic_order_invalid", f"{context} payload is not canonical")
            if len({item.casefold() for item in payload_ids}) != len(payload_ids):
                _fail("gamepack_structure_invalid", f"{context} payload contains duplicate IDs")
        if source_ids != sorted(source_ids, key=lambda item: item.encode("utf-8")):
            _fail("logic_order_invalid", f"gamepack.modules.{collection} is not canonical")
        if len({item.casefold() for item in source_ids}) != len(source_ids):
            _fail("gamepack_structure_invalid", f"modules.{collection} has duplicate sources")

    for kind, registry in (
        ("activity", activities),
        ("system", systems),
        ("narrative", narrative_units),
    ):
        for key, record in registry.items():
            previous = global_sources.get(key)
            if previous is not None:
                _fail(
                    "gamepack_structure_invalid",
                    f"global source ID collision between {previous} and {kind}: {record['id']}",
                )
            global_sources[key] = kind

    used_options: dict[tuple[str, str], str] = {}
    bound_actions: dict[tuple[str, str, str], set[str]] = {}
    for action_key, bindings in source_bindings.items():
        assert isinstance(bindings, tuple)
        action = actions[action_key]
        for kind, source_id, option_id in bindings:
            binding_key = (kind, source_id.casefold(), option_id.casefold())
            bound_actions.setdefault(binding_key, set()).add(action_key)
            if kind == "activity" and source_id.casefold() not in activities:
                _fail(
                    "source_binding_invalid",
                    f"action {action['id']} binds unknown activity {source_id}",
                )
            elif kind == "system" and source_id.casefold() not in systems:
                _fail(
                    "source_binding_invalid",
                    f"action {action['id']} binds unknown system {source_id}",
                )
            elif kind == "narrative_option":
                option_key = (source_id.casefold(), option_id.casefold())
                if option_key not in narrative_options or option_key in used_options:
                    _fail(
                        "source_binding_invalid",
                        f"action {action['id']} has an unknown or duplicate narrative binding",
                    )
                used_options[option_key] = str(action["id"])
    if set(used_options) != set(narrative_options):
        _fail("source_binding_invalid", "narrative options require exact action bindings")

    condition_children = executable["condition_children"]
    assert isinstance(condition_children, Mapping)

    def exact_bound_closure(
        action_keys: set[str],
        field: str,
        values: object,
        context: str,
    ) -> None:
        expected = {
            item
            for action_key in action_keys
            for item in closures[action_key][field]  # type: ignore[index]
        }
        actual = {item.casefold() for item in _id_array(values, context, maximum=256)}
        if field == "condition_ids":
            actual = _condition_closure(tuple(actual), condition_children)  # type: ignore[arg-type]
        if actual != expected:
            _fail("source_binding_invalid", f"{context} is not the exact bound action closure")

    for key, activity in activities.items():
        action_keys = bound_actions.get(("activity", key, ""), set())
        if action_keys:
            for field in (
                "effect_ids",
                "event_ids",
                "presentation_hook_ids",
                "asset_binding_ids",
            ):
                exact_bound_closure(
                    action_keys,
                    field,
                    activity[field],
                    f"activity {activity['id']}.{field}",
                )
    for key, system in systems.items():
        action_keys = bound_actions.get(("system", key, ""), set())
        if action_keys:
            for source_field, closure_field in (
                ("precondition_ids", "condition_ids"),
                ("effect_ids", "effect_ids"),
                ("event_ids", "event_ids"),
                ("asset_binding_ids", "asset_binding_ids"),
            ):
                exact_bound_closure(
                    action_keys,
                    closure_field,
                    system[source_field],
                    f"system {system['id']}.{source_field}",
                )
    for (unit_key, option_key), option in narrative_options.items():
        action_keys = bound_actions.get(
            ("narrative_option", unit_key, option_key),
            set(),
        )
        if action_keys:
            for field in ("condition_ids", "effect_ids"):
                exact_bound_closure(
                    action_keys,
                    field,
                    option[field],
                    f"narrative option {unit_key}/{option_key}.{field}",
                )

    narrative_modules = modules["narrative"]
    assert isinstance(narrative_modules, list)
    if not narrative_modules:
        if logic.get("narrative_cursor") is not None or logic.get("narrative_transitions") != []:
            _fail("narrative_transition_invalid", "narrative-free logic has cursor state")
        return modules

    entries = [
        entry
        for module in narrative_modules
        for entry in module["entry_unit_ids"]  # type: ignore[index,union-attr]
    ]
    if len(entries) != 1 or entries[0].casefold() not in narrative_units:
        _fail("narrative_transition_invalid", "narrative requires one known entry")
    reachable: set[str] = set()
    pending = [entries[0].casefold()]
    while pending:
        unit_key = pending.pop()
        if unit_key in reachable:
            continue
        unit = narrative_units.get(unit_key)
        if unit is None:
            _fail("narrative_transition_invalid", f"unknown narrative target {unit_key}")
        reachable.add(unit_key)
        targets = (
            [option["next_unit_id"] for option in unit["options"]]  # type: ignore[index,union-attr]
            if unit["unit_type"] == "choice"
            else unit["next_unit_ids"]  # type: ignore[index]
        )
        for target in reversed(targets):
            target_key = str(target).casefold()
            if target_key not in narrative_units:
                _fail("narrative_transition_invalid", f"unknown narrative target {target}")
            pending.append(target_key)
    if reachable != set(narrative_units):
        _fail("narrative_transition_invalid", "narrative contains unreachable units")
    if any(narrative_units[key]["unit_type"] not in {"choice", "ending"} for key in reachable):
        _fail("narrative_transition_invalid", "v1 supports choice and ending units only")

    expected_transitions: list[dict[str, object]] = []
    rules = executable["rules"]
    assert isinstance(rules, Mapping)
    for unit_key in sorted(reachable):
        unit = narrative_units[unit_key]
        if unit["unit_type"] != "choice":
            continue
        for option in unit["options"]:  # type: ignore[index,union-attr]
            option_key = (unit_key, str(option["id"]).casefold())
            bound = used_options.get(option_key)
            if bound is None:
                _fail("narrative_transition_invalid", "narrative option is unbound")
            action = actions[bound.casefold()]
            owned_rules = [
                rules[str(rule_id).casefold()]
                for rule_id in action["rule_ids"]  # type: ignore[index,union-attr]
            ]
            ordered = sorted(
                owned_rules,
                key=lambda rule: (
                    int(rule["order"]),
                    str(rule["id"]).encode("utf-8"),
                ),
            )
            conditions_for_action = [
                condition_id
                for rule in ordered
                for condition_id in rule["condition_ids"]  # type: ignore[index,union-attr]
            ]
            effects_for_action = [
                effect_id
                for rule in ordered
                for effect_id in rule["effect_ids"]  # type: ignore[index,union-attr]
            ]
            action_id = str(action["id"])
            expected_transitions.append(
                {
                    "compiler_owned": True,
                    "id": f"wf_internal_transition_{action_id}",
                    "action_id": action_id,
                    "source_unit_id": str(unit["id"]),
                    "option_id": str(option["id"]),
                    "target_unit_id": str(option["next_unit_id"]),
                    "precondition": {
                        "compiler_owned": True,
                        "id": f"wf_internal_cursor_at_{action_id}",
                        "operator": "cursor_equals",
                        "cursor_state_id": "wf_internal_narrative_cursor",
                        "value": str(unit["id"]),
                    },
                    "effect": {
                        "compiler_owned": True,
                        "id": f"wf_internal_advance_{action_id}",
                        "operation": "set_cursor",
                        "cursor_state_id": "wf_internal_narrative_cursor",
                        "value": str(option["next_unit_id"]),
                        "invalid_transition_policy": "reject_transition",
                    },
                    "atomic_source_condition_ids": conditions_for_action,
                    "atomic_source_effect_ids": effects_for_action,
                }
            )
    expected_transitions.sort(key=lambda item: str(item["id"]).encode("utf-8"))
    expected_cursor = {
        "compiler_owned": True,
        "id": "wf_internal_narrative_cursor",
        "type": "string",
        "initial": entries[0],
        "allowed_values": sorted(
            (str(narrative_units[key]["id"]) for key in reachable),
            key=lambda item: item.encode("utf-8"),
        ),
        "mutability": "mutable",
        "persistence": "saved",
    }
    if (
        logic.get("narrative_cursor") != expected_cursor
        or logic.get("narrative_transitions") != expected_transitions
    ):
        _fail(
            "narrative_transition_invalid",
            "narrative cursor/transitions do not exactly derive from modules",
        )
    return modules


def _validate_runtime_requirements(value: object) -> Mapping[str, object]:
    context = "gamepack.runtime_requirements"
    runtime = _object(value, context)
    _exact_keys(
        runtime,
        frozenset(
            {
                "requested_adapter",
                "accepted_logic_formats",
                "required_features",
                "optional_features",
                "presentation",
                "platform_matrix",
                "input_capabilities",
                "asset_formats",
                "save_expected",
                "replay_expected",
                "packaging_target",
            }
        ),
        context,
    )
    if runtime.get("accepted_logic_formats") != [
        {"format": GAMEPACK_FORMAT, "versions": [GAMEPACK_VERSION]}
    ]:
        _fail(
            "accepted_logic_format_unsupported",
            "runtime must accept exactly world-forge.gamepack v1",
        )
    presentation = _object(runtime.get("presentation"), f"{context}.presentation")
    _exact_keys(
        presentation,
        frozenset({"mode", "camera", "perspective", "renderer"}),
        f"{context}.presentation",
    )
    if presentation.get("mode") not in {
        "text",
        "2d",
        "2_5d",
        "3d",
        "mixed",
        "vr",
        "ar",
    }:
        _fail("gamepack_structure_invalid", f"{context}.presentation.mode is unsupported")
    for field in ("camera", "perspective", "renderer"):
        _non_empty_string(
            presentation.get(field),
            f"{context}.presentation.{field}",
        )
    platforms = _array(
        runtime.get("platform_matrix"),
        f"{context}.platform_matrix",
        maximum=32,
    )
    if not platforms:
        _fail(
            "gamepack_structure_invalid",
            f"{context}.platform_matrix must be non-empty",
        )
    platform_ids: list[str] = []
    for index, raw in enumerate(platforms):
        platform_context = f"{context}.platform_matrix/{index}"
        platform = _object(raw, platform_context)
        _exact_keys(
            platform,
            frozenset(
                {
                    "platform_id",
                    "platform_family",
                    "architecture",
                    "backend",
                    "renderer",
                }
            ),
            platform_context,
        )
        platform_id = platform.get("platform_id")
        if not isinstance(platform_id, str) or not platform_id:
            _fail("gamepack_structure_invalid", f"{platform_context}.platform_id is invalid")
        supported = {
            "platform:linux_x86_64": ("platform:linux", "architecture:x86_64"),
            "platform:windows_x86_64": ("platform:windows", "architecture:x86_64"),
        }.get(platform_id)
        if supported is None:
            _fail("gamepack_structure_invalid", f"{platform_context}.platform_id is unsupported")
        if (
            platform.get("platform_family") != supported[0]
            or platform.get("architecture") != supported[1]
            or platform.get("backend") != "backend:unspecified"
            or platform.get("renderer") != presentation["renderer"]
        ):
            _fail("gamepack_structure_invalid", f"{platform_context} is not an exact projection")
        platform_ids.append(platform_id)
    if platform_ids != sorted(platform_ids, key=lambda item: item.encode("utf-8")):
        _fail("runtime_order_invalid", "runtime platform_matrix is not canonical")
    if len(set(platform_ids)) != len(platform_ids):
        _fail("runtime_order_invalid", "runtime platform_matrix contains duplicate IDs")
    required = _token_array(
        runtime.get("required_features"),
        f"{context}.required_features",
        allow_empty=False,
    )
    optional = _token_array(
        runtime.get("optional_features"),
        f"{context}.optional_features",
    )
    for field in ("input_capabilities", "asset_formats"):
        _token_array(
            runtime.get(field),
            f"{context}.{field}",
            allow_empty=False,
        )
    unsupported_required = sorted(set(required) - SUPPORTED_REQUIRED_FEATURES_V1)
    if unsupported_required:
        _fail(
            "required_feature_unsupported",
            f"runtime requires unsupported v1 features {unsupported_required!r}",
        )
    if {str(item).casefold() for item in required}.intersection(
        str(item).casefold() for item in optional
    ):
        _fail("gamepack_logic_invalid", "required and optional runtime features overlap")
    requested_adapter = _identifier(
        runtime.get("requested_adapter"),
        f"{context}.requested_adapter",
    )
    expected_presentation = _RUNTIME_PRESENTATIONS_V1.get(requested_adapter)
    actual_presentation = (
        presentation.get("mode"),
        presentation.get("camera"),
        presentation.get("perspective"),
        presentation.get("renderer"),
    )
    if expected_presentation is None or actual_presentation != expected_presentation:
        _fail(
            "runtime_presentation_unsupported",
            f"{context}.presentation is unsupported by {requested_adapter}",
        )
    _non_empty_string(
        runtime.get("packaging_target"),
        f"{context}.packaging_target",
    )
    for field in ("save_expected", "replay_expected"):
        if not isinstance(runtime.get(field), bool):
            _fail("gamepack_structure_invalid", f"{context}.{field} must be boolean")
    return runtime


def _validate_presentation_contract(
    value: object,
    *,
    runtime: Mapping[str, object],
) -> Mapping[str, object]:
    context = "gamepack.presentation"
    presentation = _object(value, context)
    _exact_keys(
        presentation,
        frozenset(
            {
                "mode",
                "camera",
                "perspective",
                "visual_language",
                "ui_density",
                "audio_role",
                "input_assumptions",
                "accessibility",
                "localization",
            }
        ),
        context,
    )
    if presentation.get("mode") not in {
        "text",
        "2d",
        "2_5d",
        "3d",
        "mixed",
        "vr",
        "ar",
    }:
        _fail("gamepack_structure_invalid", f"{context}.mode is unsupported")
    for field in (
        "camera",
        "perspective",
        "visual_language",
        "ui_density",
        "audio_role",
    ):
        _non_empty_string(presentation.get(field), f"{context}.{field}")
    _token_array(
        presentation.get("input_assumptions"),
        f"{context}.input_assumptions",
        allow_empty=False,
    )
    accessibility = _object(
        presentation.get("accessibility"),
        f"{context}.accessibility",
    )
    accessibility_fields = frozenset(
        {
            "remapping",
            "keyboard_only",
            "captions",
            "text_scaling",
            "high_contrast",
            "color_independence",
            "reduced_motion",
            "timing_alternatives",
            "screen_reader_structure",
        }
    )
    _exact_keys(accessibility, accessibility_fields, f"{context}.accessibility")
    if any(type(accessibility.get(field)) is not bool for field in accessibility_fields):
        _fail(
            "gamepack_structure_invalid",
            f"{context}.accessibility values must be boolean",
        )
    localization = _object(
        presentation.get("localization"),
        f"{context}.localization",
    )
    _exact_keys(
        localization,
        frozenset({"source_locale", "supported_locales", "externalized_text"}),
        f"{context}.localization",
    )
    source_locale = localization.get("source_locale")
    if (
        type(source_locale) is not str
        or _LOCALE_RE.fullmatch(source_locale) is None
        or unicodedata.normalize("NFC", source_locale) != source_locale
    ):
        _fail(
            "gamepack_structure_invalid",
            f"{context}.localization.source_locale is invalid",
        )
    supported_locales = _string_domain(
        localization.get("supported_locales"),
        context=f"{context}.localization.supported_locales",
        reason_code="gamepack_structure_invalid",
    )
    if any(_LOCALE_RE.fullmatch(locale) is None for locale in supported_locales):
        _fail(
            "gamepack_structure_invalid",
            f"{context}.localization.supported_locales contains an invalid locale",
        )
    if source_locale.casefold() not in {locale.casefold() for locale in supported_locales}:
        _fail(
            "gamepack_structure_invalid",
            f"{context}.localization omits source_locale",
        )
    if localization.get("externalized_text") is not True:
        _fail(
            "gamepack_structure_invalid",
            f"{context}.localization must externalize text",
        )
    runtime_presentation = _object(
        runtime.get("presentation"),
        "gamepack.runtime_requirements.presentation",
    )
    for field in ("mode", "camera", "perspective"):
        if presentation.get(field) != runtime_presentation.get(field):
            _fail(
                "runtime_presentation_mismatch",
                f"{context}.{field} differs from runtime requirements",
            )
    return presentation


def _expected_localization_references(
    game: Mapping[str, object],
    modules: Mapping[str, object],
) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []

    def add(kind: str, subject_id: object, field: str, text: object) -> None:
        assert type(subject_id) is str
        assert type(text) is str
        references.append(
            {
                "key": f"{kind}.{subject_id}.{field}",
                "subject_kind": kind,
                "subject_id": subject_id,
                "field": field,
                "source_text": text,
            }
        )

    add("game", game["id"], "title", game["title"])
    for collection, kind in (
        ("world", "world_module"),
        ("activities", "activity_module"),
        ("narrative", "narrative_module"),
        ("systems", "system_module"),
    ):
        collection_modules = modules[collection]
        assert isinstance(collection_modules, list)
        for module in collection_modules:
            assert isinstance(module, Mapping)
            source = module["source"]
            assert isinstance(source, Mapping)
            add(kind, source["id"], "title", module["title"])
    activity_modules = modules["activities"]
    narrative_modules = modules["narrative"]
    system_modules = modules["systems"]
    assert isinstance(activity_modules, list)
    assert isinstance(narrative_modules, list)
    assert isinstance(system_modules, list)
    for module in activity_modules:
        assert isinstance(module, Mapping)
        for activity in module["activities"]:  # type: ignore[index,union-attr]
            add("activity", activity["id"], "title", activity["title"])
    for module in narrative_modules:
        assert isinstance(module, Mapping)
        for unit in module["units"]:  # type: ignore[index,union-attr]
            add("narrative_unit", unit["id"], "title", unit["title"])
            for option in unit.get("options", []):
                add(
                    "narrative_option",
                    f"{unit['id']}_{option['id']}",
                    "label",
                    option["label"],
                )
    for module in system_modules:
        assert isinstance(module, Mapping)
        for system in module["systems"]:  # type: ignore[index,union-attr]
            add("system", system["id"], "title", system["title"])
    references.sort(key=lambda item: item["key"].encode("utf-8"))
    return references


def _validate_localization_contract(
    value: object,
    *,
    game: Mapping[str, object],
    modules: Mapping[str, object],
    presentation: Mapping[str, object],
) -> None:
    context = "gamepack.localization"
    localization = _object(value, context)
    _exact_keys(
        localization,
        frozenset(
            {
                "source_locale",
                "supported_locales",
                "externalized_text",
                "references",
            }
        ),
        context,
    )
    presentation_localization = _object(
        presentation.get("localization"),
        "gamepack.presentation.localization",
    )
    if (
        localization.get("source_locale") != game.get("default_locale")
        or localization.get("source_locale") != presentation_localization.get("source_locale")
        or localization.get("supported_locales")
        != presentation_localization.get("supported_locales")
        or localization.get("externalized_text") is not True
        or localization.get("externalized_text")
        != presentation_localization.get("externalized_text")
    ):
        _fail(
            "localization_mismatch",
            "localization differs from game and presentation",
        )
    references = _array(
        localization.get("references"),
        f"{context}.references",
        maximum=4096,
    )
    expected = _expected_localization_references(game, modules)
    if not references or references != expected:
        _fail(
            "localization_mismatch",
            "localization references do not exactly match projected source text",
        )


def _expected_asset_requirements(
    *,
    modules: Mapping[str, object],
    logic: Mapping[str, object],
    runtime: Mapping[str, object],
) -> list[dict[str, object]]:
    subjects: dict[str, set[tuple[str, str]]] = {}
    contexts: dict[str, set[str]] = {}
    roles: dict[str, set[str]] = {}

    def add(binding: str, kind: str, subject_id: str, context: str, role: str) -> None:
        subjects.setdefault(binding, set()).add((kind, subject_id))
        contexts.setdefault(binding, set()).add(context)
        roles.setdefault(binding, set()).add(role)

    for collection, records_field, kind, usage, role in (
        ("activities", "activities", "activity", "activity", "activity_visual"),
        ("systems", "systems", "system", "system", "system_feedback"),
        ("narrative", "units", "narrative_unit", "narrative", "narrative_ui"),
    ):
        collection_modules = modules[collection]
        assert isinstance(collection_modules, list)
        for module in collection_modules:
            assert isinstance(module, Mapping)
            for record in module[records_field]:  # type: ignore[index,union-attr]
                for binding in record["asset_binding_ids"]:
                    add(binding, kind, record["id"], usage, role)
    hooks: dict[str, Mapping[str, object]] = {}
    for hook in logic["presentation_hooks"]:  # type: ignore[index]
        assert isinstance(hook, Mapping)
        hook_id = str(hook["id"])
        hooks[hook_id.casefold()] = hook
        hook_kind = str(hook["kind"])
        for binding in hook["asset_binding_ids"]:  # type: ignore[index,union-attr]
            add(
                binding,
                "presentation_hook",
                hook_id,
                f"presentation:{hook_kind}",
                _ROLE_BY_HOOK_KIND[hook_kind],
            )
    for mechanic in logic["mechanics"]:  # type: ignore[index]
        assert isinstance(mechanic, Mapping)
        mechanic_id = str(mechanic["id"])
        for binding in mechanic["asset_binding_ids"]:  # type: ignore[index,union-attr]
            add(
                binding,
                "mechanic",
                mechanic_id,
                "mechanic",
                "mechanic_feedback",
            )
        for hook_id in mechanic["presentation_hook_ids"]:  # type: ignore[index,union-attr]
            hook = hooks[str(hook_id).casefold()]
            hook_kind = str(hook["kind"])
            for binding in hook["asset_binding_ids"]:  # type: ignore[index,union-attr]
                add(
                    binding,
                    "mechanic",
                    mechanic_id,
                    f"mechanic:{hook_kind}",
                    _ROLE_BY_HOOK_KIND[hook_kind],
                )

    formats = runtime["asset_formats"]
    assert isinstance(formats, list)
    result: list[dict[str, object]] = []
    for binding in sorted(subjects, key=lambda item: item.encode("utf-8")):
        result.append(
            {
                "binding_id": binding,
                "required": True,
                "accepted_formats": list(formats),
                "roles": sorted(roles[binding], key=lambda item: item.encode("utf-8")),
                "usage_contexts": sorted(
                    contexts[binding],
                    key=lambda item: item.encode("utf-8"),
                ),
                "referencing_subjects": [
                    {"kind": kind, "id": subject_id}
                    for kind, subject_id in sorted(
                        subjects[binding],
                        key=lambda item: (
                            item[0].encode("utf-8"),
                            item[1].encode("utf-8"),
                        ),
                    )
                ],
            }
        )
    return result


def _validate_asset_requirements_contract(
    value: object,
    *,
    modules: Mapping[str, object],
    logic: Mapping[str, object],
    runtime: Mapping[str, object],
) -> None:
    requirements = _array(value, "gamepack.asset_requirements", maximum=4096)
    expected = _expected_asset_requirements(
        modules=modules,
        logic=logic,
        runtime=runtime,
    )
    if not requirements or requirements != expected:
        _fail(
            "asset_requirements_mismatch",
            "asset requirements do not exactly derive from runtime bindings",
        )


def _identity_sort_key(identity: Mapping[str, object]) -> tuple[bytes, int, bytes, bytes]:
    return (
        str(identity["format"]).encode("utf-8"),
        int(identity["format_version"]),
        str(identity["id"]).encode("utf-8"),
        str(identity["content_hash"]).encode("ascii"),
    )


def _validate_provenance_contract(
    value: object,
    *,
    source: Mapping[str, object],
    modules: Mapping[str, object],
) -> None:
    provenance = _array(value, "gamepack.provenance", maximum=4096)
    checked: list[dict[str, object]] = []
    for index, raw in enumerate(provenance):
        context = f"gamepack.provenance/{index}"
        entry = _object(raw, context)
        _exact_keys(entry, frozenset({"kind", "subject"}), context)
        if entry.get("kind") != "compiled_from":
            _fail("provenance_invalid", f"{context}.kind is unsupported")
        subject = _validate_identity(entry.get("subject"), f"{context}.subject")
        checked.append({"kind": "compiled_from", "subject": dict(subject)})

    source_subjects = [
        source["project"],
        source["profile"],
        source["source_manifest"],
        *source["logic_modules"],  # type: ignore[misc]
    ]
    for collection in ("world", "activities", "narrative", "systems"):
        collection_modules = modules[collection]
        assert isinstance(collection_modules, list)
        source_subjects.extend(module["source"] for module in collection_modules)
    expected = [
        {"kind": "compiled_from", "subject": subject}
        for subject in sorted(source_subjects, key=_identity_sort_key)  # type: ignore[arg-type]
    ]
    if not checked or checked != expected:
        _fail(
            "provenance_invalid",
            "provenance must exactly and canonically cover every source identity",
        )


def _validate_runtime_correlations(
    document: Mapping[str, object],
    *,
    logic: Mapping[str, object],
    executable: Mapping[str, object],
    modules: Mapping[str, object],
    runtime: Mapping[str, object],
) -> None:
    game = _object(document.get("game"), "gamepack.game")
    _exact_keys(
        game,
        frozenset({"id", "title", "version", "default_locale"}),
        "gamepack.game",
    )
    game_id = _identifier(game.get("id"), "gamepack.game.id")
    _non_empty_string(game.get("title"), "gamepack.game.title")
    if (
        not isinstance(game.get("version"), str)
        or _SEMVER_RE.fullmatch(str(game["version"])) is None
    ):
        _fail("gamepack_structure_invalid", "gamepack.game.version is not strict SemVer")
    if (
        not isinstance(game.get("default_locale"), str)
        or _LOCALE_RE.fullmatch(str(game["default_locale"])) is None
    ):
        _fail("gamepack_structure_invalid", "gamepack.game.default_locale is invalid")

    source = _object(document.get("source"), "gamepack.source")
    _exact_keys(
        source,
        frozenset({"project", "profile", "source_manifest", "logic_modules"}),
        "gamepack.source",
    )
    project = _validate_identity(
        source.get("project"),
        "gamepack.source.project",
        expected_format="world-forge.project",
    )
    _validate_identity(
        source.get("profile"),
        "gamepack.source.profile",
        expected_format="world-forge.creation_profile",
    )
    manifest = _validate_identity(
        source.get("source_manifest"),
        "gamepack.source.source_manifest",
        expected_format="world-forge.creation_source_manifest",
    )
    if project["id"] != game_id or manifest["id"] != game_id:
        _fail("source_binding_invalid", "game and project/source-manifest IDs differ")
    logic_sources = _array(
        source.get("logic_modules"),
        "gamepack.source.logic_modules",
        maximum=1,
    )
    if len(logic_sources) != 1:
        _fail("source_binding_invalid", "exactly one logic source is required")
    source_logic = _validate_identity(
        logic_sources[0],
        "gamepack.source.logic_modules/0",
        expected_format="world-forge.logic_module",
    )
    logic_source = _validate_identity(
        logic.get("source"),
        "gamepack.logic.source",
        expected_format="world-forge.logic_module",
    )
    if logic_source != source_logic:
        _fail("source_binding_invalid", "logic source identity is inconsistent")
    _non_empty_string(logic.get("title"), "gamepack.logic.title")

    expected_analysis = analysis_requirements_for(modules, logic)
    if document.get("analysis_requirements") != expected_analysis:
        _fail(
            "analysis_requirements_invalid",
            "analysis requirements do not exactly derive from executable structure",
        )

    presentation = _validate_presentation_contract(
        document.get("presentation"),
        runtime=runtime,
    )
    _validate_localization_contract(
        document.get("localization"),
        game=game,
        modules=modules,
        presentation=presentation,
    )
    _validate_asset_requirements_contract(
        document.get("asset_requirements"),
        modules=modules,
        logic=logic,
        runtime=runtime,
    )
    _validate_provenance_contract(
        document.get("provenance"),
        source=source,
        modules=modules,
    )

    mechanics = executable["action_closures"]
    assert isinstance(mechanics, Mapping)
    expected_required = sorted(
        {
            feature
            for closure in mechanics.values()
            for feature in closure["required_feature_ids"]  # type: ignore[index,union-attr]
        },
        key=lambda item: item.encode("utf-8"),
    )
    if runtime.get("required_features") != expected_required:
        _fail(
            "required_feature_mismatch",
            "runtime required_features differs from the exact mechanic closure",
        )

    logic_mechanics = _record_registry(logic, "mechanics")
    raw_requirements = _array(
        document.get("mechanic_requirements"),
        "gamepack.mechanic_requirements",
        maximum=128,
    )
    if len(raw_requirements) != len(logic_mechanics):
        _fail("gamepack_logic_invalid", "mechanic requirements are incomplete")
    expected_requirements: list[dict[str, object]] = []
    for mechanic in logic["mechanics"]:  # type: ignore[index]
        assert isinstance(mechanic, Mapping)
        expected_requirements.append(
            {
                "mechanic_id": mechanic["id"],
                "core_verb_id": mechanic["core_verb_id"],
                "action_id": mechanic["action_id"],
                "required_feature_ids": mechanic["required_feature_ids"],
            }
        )
    expected_requirements.sort(key=lambda item: str(item["mechanic_id"]).encode("utf-8"))
    if raw_requirements != expected_requirements:
        _fail(
            "gamepack_logic_invalid",
            "mechanic requirements do not exactly reconstruct logic mechanics",
        )

    events = executable["events"]
    assert isinstance(events, Mapping)
    live_events = {
        str(event_id).casefold()
        for rule in logic["rules"]  # type: ignore[index]
        for event_id in rule["event_ids"]  # type: ignore[index,union-attr]
    } | {
        str(event_id).casefold()
        for ending in logic["endings"]  # type: ignore[index]
        for event_id in ending["event_ids"]  # type: ignore[index,union-attr]
    }
    for collection, records_field in (
        ("activities", "activities"),
        ("systems", "systems"),
    ):
        for module in modules[collection]:  # type: ignore[index,union-attr]
            for record in module[records_field]:
                live_events.update(str(event_id).casefold() for event_id in record["event_ids"])
    if set(events) != live_events:
        _fail("event_closure_invalid", "declared events do not equal the executable closure")


def _validate_logic(value: object) -> Mapping[str, object]:
    logic = _object(value, "gamepack.logic")
    _exact_keys(logic, _LOGIC_FIELDS, "gamepack.logic")
    if logic.get("execution_semantics") != EXECUTION_SEMANTICS:
        _fail(
            "execution_semantics_unsupported",
            "gamepack does not require the exact v1 execution policy",
        )
    source = _object(logic.get("source"), "gamepack.logic.source")
    _exact_keys(
        source,
        frozenset({"format", "format_version", "id", "content_hash"}),
        "gamepack.logic.source",
    )
    for collection, maximum in _LOGIC_LIMITS.items():
        _array(logic.get(collection), f"gamepack.logic.{collection}", maximum=maximum)
    _validate_record_order(logic)
    _validate_logic_record_shapes(logic)
    initial = _object(logic.get("initial_state"), "gamepack.logic.initial_state")
    if len(initial) > _LOGIC_LIMITS["state_schema"]:
        _fail("gamepack_bounds_exceeded", "logic.initial_state exceeds its limit")
    state_schema = logic["state_schema"]
    assert isinstance(state_schema, list)
    expected_initial: dict[str, object] = {}
    for index, raw in enumerate(state_schema):
        state = _object(raw, f"gamepack.logic.state_schema/{index}")
        state_id = state.get("id")
        if not isinstance(state_id, str) or not state_id:
            _fail("gamepack_logic_invalid", "state schema contains an invalid ID")
        expected_initial[state_id] = copy.deepcopy(state.get("initial"))
    if dict(initial) != expected_initial:
        _fail(
            "initial_state_invalid",
            "logic.initial_state does not exactly match state_schema",
        )
    return logic


def validate_runtime_gamepack(value: object) -> dict[str, object]:
    """Validate the closed runtime-consumable subset of gamepack v1."""

    owned = snapshot_plain_json(value)
    if type(owned) is not dict:
        _fail("json_root_invalid", "gamepack must be an object")
    document = owned
    _forbidden_field_scan(document)
    _exact_keys(document, _GAMEPACK_FIELDS, "gamepack")
    if document.get("format") != GAMEPACK_FORMAT:
        _fail(
            "gamepack_format_unsupported",
            f"format must be {GAMEPACK_FORMAT}",
        )
    version = document.get("format_version")
    if version != GAMEPACK_VERSION or isinstance(version, bool):
        _fail("gamepack_version_unsupported", "format_version must be 1")
    declared_hash = document.get("content_hash")
    if (
        not isinstance(declared_hash, str)
        or _SHA256_RE.fullmatch(declared_hash) is None
        or declared_hash
        != hashlib.sha256(
            _canonical_bytes_owned(
                {key: item for key, item in document.items() if key != "content_hash"}
            )
        ).hexdigest()
    ):
        _fail("content_hash_mismatch", "gamepack content_hash does not match")
    runtime = _validate_runtime_requirements(document.get("runtime_requirements"))
    _validate_extensions(document.get("registered_extensions"))
    logic = _validate_logic(document.get("logic"))
    executable = _validate_executable_logic(logic)
    modules = _validate_modules(
        document.get("modules"),
        logic=logic,
        executable=executable,
    )
    _validate_runtime_correlations(
        document,
        logic=logic,
        executable=executable,
        modules=modules,
        runtime=runtime,
    )
    return document


def analysis_requirements_for(
    modules: Mapping[str, object],
    logic: Mapping[str, object],
    *,
    limits: Mapping[str, int] = ANALYSIS_LIMITS,
    analyzers: Mapping[str, tuple[str, int]] = ANALYZERS,
) -> dict[str, object]:
    """Derive the frozen analyzer selection from exact compiled structure."""

    owned_modules = snapshot_plain_json(modules)
    owned_logic = snapshot_plain_json(logic)
    if type(owned_modules) is not dict or type(owned_logic) is not dict:
        _fail(
            "analysis_input_invalid",
            "analysis modules and logic must be exact objects",
        )
    if limits is ANALYSIS_LIMITS:
        checked_limits = dict(ANALYSIS_LIMITS)
    elif type(limits) is dict:
        owned_limits = snapshot_plain_json(
            limits,
            maximum_bytes=int(ANALYSIS_LIMITS["state_bytes"]),
        )
        assert type(owned_limits) is dict
        if frozenset(owned_limits) != frozenset(ANALYSIS_LIMITS) or any(
            type(value) is not int or value < 1 for value in owned_limits.values()
        ):
            _fail(
                "analysis_policy_invalid",
                "analysis limits must be the exact positive v1 limit map",
            )
        checked_limits = owned_limits
    else:
        _fail(
            "analysis_policy_invalid",
            "analysis limits must be an exact object",
        )
    if analyzers is ANALYZERS:
        checked_analyzers = dict(ANALYZERS)
    elif type(analyzers) is dict:
        if dict.__len__(analyzers) != len(ANALYZERS):
            _fail(
                "analysis_policy_invalid",
                "analyzers must define the exact v1 profile set",
            )
        validated_descriptors: list[tuple[str, str, int]] = []
        for profile_id, descriptor in dict.items(analyzers):
            checked_profile = _validate_exact_nfc_string(
                profile_id,
                maximum_codepoints=64,
                reason_code="analysis_policy_invalid",
                detail="analyzer profile IDs must be bounded exact NFC strings",
            )
            if (
                type(descriptor) is not tuple
                or len(descriptor) != 2
                or type(descriptor[1]) is not int
                or descriptor[1] < 1
                or descriptor[1] > MAX_SAFE_INTEGER
            ):
                _fail(
                    "analysis_policy_invalid",
                    "analyzer descriptors must be exact (ID, version) tuples",
                )
            checked_analyzer_id = _validate_exact_nfc_string(
                descriptor[0],
                maximum_codepoints=512,
                reason_code="analysis_policy_invalid",
                detail="analyzer IDs must be bounded exact NFC strings",
            )
            validated_descriptors.append((checked_profile, checked_analyzer_id, descriptor[1]))
        actual_profiles = sorted(item[0] for item in validated_descriptors)
        expected_profiles = sorted(str(item) for item in ANALYZERS)
        if actual_profiles != expected_profiles:
            _fail(
                "analysis_policy_invalid",
                "analyzers must define the exact v1 profile set",
            )
        checked_analyzers = {
            profile_id: (analyzer_id, version)
            for profile_id, analyzer_id, version in validated_descriptors
        }
    else:
        _fail(
            "analysis_policy_invalid",
            "analyzers must be an exact object",
        )

    modules = owned_modules
    logic = owned_logic
    world = modules.get("world")
    narrative = modules.get("narrative")
    activities = modules.get("activities")
    cursor = logic.get("narrative_cursor")
    transitions = logic.get("narrative_transitions")
    profile = "unsupported"
    reason_code: str | None = "analysis_profile_unsupported"

    puzzle_activities: list[Mapping[str, object]] = []
    all_activities: list[Mapping[str, object]] = []
    if isinstance(activities, list):
        for projection in activities:
            if not isinstance(projection, Mapping):
                continue
            records = projection.get("activities")
            if not isinstance(records, list):
                continue
            for record in records:
                if isinstance(record, Mapping):
                    all_activities.append(record)
                    if record.get("activity_type") == "puzzle":
                        puzzle_activities.append(record)

    no_world = isinstance(world, list) and not world
    no_narrative = isinstance(narrative, list) and not narrative
    no_cursor = cursor is None and isinstance(transitions, list) and not transitions
    exact_puzzle = bool(puzzle_activities) and len(puzzle_activities) == len(all_activities)
    has_narrative = isinstance(narrative, list) and bool(narrative)
    has_cursor = isinstance(cursor, Mapping) and isinstance(transitions, list) and bool(transitions)

    if no_world and no_narrative and no_cursor and exact_puzzle:
        profile = "abstract_puzzle"
        reason_code = None
    elif has_narrative and has_cursor:
        profile = "branching_narrative"
        reason_code = None

    analyzer_id, analyzer_version = checked_analyzers[profile]
    requirement: dict[str, object] = {
        "profile": profile,
        "analyzer_id": analyzer_id,
        "analyzer_version": analyzer_version,
        "reason_code": reason_code,
        "limits": checked_limits,
    }
    requirement["content_hash"] = canonical_gamepack_hash(requirement)
    return requirement
