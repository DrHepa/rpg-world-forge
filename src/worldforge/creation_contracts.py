from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import re
import stat
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from worldforge.file_stat import is_link_or_reparse, path_file_stat
from worldforge.studio.changesets import read_workspace_file_snapshot
from worldforge.studio.errors import StudioError
from worldforge.studio.workspaces import _pinned_ancestor_identities

CREATION_PROJECT_FORMAT = "world-forge.project"
CREATION_PROFILE_FORMAT = "world-forge.creation_profile"
CREATION_SOURCE_MANIFEST_FORMAT = "world-forge.creation_source_manifest"
WORLD_MODULE_FORMAT = "world-forge.world_module"
ACTIVITY_MODULE_FORMAT = "world-forge.activity_module"
NARRATIVE_MODULE_FORMAT = "world-forge.narrative_module"
SYSTEM_MODULE_FORMAT = "world-forge.system_module"
LOGIC_MODULE_FORMAT = "world-forge.logic_module"
CREATION_CONTRACT_VERSION = 1
MAX_CREATION_CONTRACT_BYTES = 4 * 1024 * 1024
MAX_CREATION_AGGREGATE_BYTES = 16 * 1024 * 1024
MAX_CREATION_PROJECT_FILES = 256
MAX_CREATION_PATH_BYTES = 1024
MAX_CREATION_PATH_DEPTH = 16
MAX_CREATION_JSON_DEPTH = 64
MAX_SAFE_INTEGER = 9_007_199_254_740_991
_MAX_SAFE_INTEGER_DECIMAL = str(MAX_SAFE_INTEGER)

_FORMATS = frozenset(
    {
        CREATION_PROJECT_FORMAT,
        CREATION_PROFILE_FORMAT,
        CREATION_SOURCE_MANIFEST_FORMAT,
        WORLD_MODULE_FORMAT,
        ACTIVITY_MODULE_FORMAT,
        NARRATIVE_MODULE_FORMAT,
        SYSTEM_MODULE_FORMAT,
        LOGIC_MODULE_FORMAT,
    }
)
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_LOCALE_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
_EXTENSION_ID_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_.-]*)?$")
_WINDOWS_RESERVED = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)

_PROJECT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "project_kind",
        "project_id",
        "title",
        "project_version",
        "default_locale",
        "profile",
        "source_manifest",
        "extensions",
        "content_hash",
    }
)
_PROFILE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "profile_id",
        "project_id",
        "title",
        "experience",
        "gameplay",
        "world",
        "narrative",
        "fiction",
        "presentation",
        "production",
        "runtime_target",
        "extensions",
        "content_hash",
    }
)
_MANIFEST_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "project_id",
        "profile",
        "modules",
        "extensions",
        "content_hash",
    }
)
_WORLD_MODULE_BASE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "module_id",
        "project_id",
        "module_type",
        "title",
        "extensions",
        "content_hash",
    }
)
_WORLD_PAYLOAD_FIELDS = {
    "canon": "facts",
    "chronology": "events",
    "space": "spaces",
    "group": "groups",
    "character": "characters",
    "knowledge": "knowledge_items",
}
_ACTIVITY_MODULE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "module_id",
        "project_id",
        "title",
        "activities",
        "extensions",
        "content_hash",
    }
)
_NARRATIVE_MODULE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "module_id",
        "project_id",
        "title",
        "entry_unit_ids",
        "units",
        "extensions",
        "content_hash",
    }
)
_SYSTEM_MODULE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "module_id",
        "project_id",
        "title",
        "systems",
        "extensions",
        "content_hash",
    }
)
_LOGIC_MODULE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "module_id",
        "project_id",
        "title",
        "state_variables",
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
        "extensions",
        "content_hash",
    }
)
_REFERENCE_FIELDS = frozenset({"format", "format_version", "id", "path", "content_hash"})
_EXTENSION_FIELDS = frozenset({"id", "version", "required", "content_hash"})
_MODULE_COLLECTIONS = {
    "world_modules": WORLD_MODULE_FORMAT,
    "activity_modules": ACTIVITY_MODULE_FORMAT,
    "narrative_modules": NARRATIVE_MODULE_FORMAT,
    "system_modules": SYSTEM_MODULE_FORMAT,
    "logic_modules": LOGIC_MODULE_FORMAT,
}
_PROJECT_KINDS = frozenset({"game", "universe_library", "asset_library"})
_GAMEPLAY_FAMILIES = frozenset(
    {
        "none",
        "action",
        "adventure",
        "educational",
        "narrative",
        "puzzle",
        "rhythm",
        "role_playing",
        "sandbox",
        "simulation",
        "sports",
        "strategy",
    }
)
_WORLD_PRESENCE = frozenset({"none", "abstract", "symbolic", "diegetic"})
_NARRATIVE_REQUIREMENTS = frozenset({"none", "optional", "required"})
_NARRATIVE_AUTHORSHIP = frozenset(
    {"none", "authored", "emergent", "procedural", "player_authored", "social", "hybrid"}
)
_NARRATIVE_TOPOLOGIES = frozenset(
    {
        "none",
        "linear",
        "foldback",
        "branching",
        "branch_and_bottleneck",
        "hub_and_spoke",
        "modular",
        "storylet",
        "loop_reset",
        "episodic",
        "seasonal",
        "open_ended",
    }
)
_PRESENTATION_MODES = frozenset({"text", "2d", "2_5d", "3d", "mixed", "vr", "ar"})
_PRODUCTION_MODES = frozenset(
    {
        "authored",
        "modular",
        "deterministic_procedural",
        "generated_at_authoring_time",
        "player_generated",
        "hybrid",
        "not_applicable",
    }
)
_ACTIVITY_TYPES = frozenset(
    {
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
    }
)
_NARRATIVE_UNIT_TYPES = frozenset(
    {
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
    }
)
_SYSTEM_TYPES = frozenset(
    {
        "rule",
        "event",
        "consequence",
        "schedule",
        "economy",
        "production_process",
        "simulation_scenario",
        "world_modifier",
        "season",
    }
)

ExtensionValidator = Callable[[dict[str, Any]], None]


CREATION_CONTRACT_REASON_CODES = frozenset(
    {
        "creation_contract_invalid",
        "creation_project_aggregate_limit",
        "creation_project_file_changed",
        "creation_project_file_byte_limit",
        "creation_project_file_limit",
        "creation_project_file_unsafe",
        "creation_project_inspection_failed",
        "creation_project_root_changed",
        "creation_project_root_linked",
        "creation_project_root_non_directory",
    }
)


class CreationContractError(ValueError):
    """Raised when a generic creation contract fails closed validation."""

    def __init__(
        self,
        detail: str,
        *,
        reason_code: str = "creation_contract_invalid",
    ) -> None:
        if reason_code not in CREATION_CONTRACT_REASON_CODES:
            raise ValueError("unknown creation contract reason code")
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class LoadedCreationProject:
    project: dict[str, Any]
    profile: dict[str, Any]
    manifest: dict[str, Any]
    world_modules: tuple[dict[str, Any], ...]
    activity_modules: tuple[dict[str, Any], ...]
    narrative_modules: tuple[dict[str, Any], ...]
    system_modules: tuple[dict[str, Any], ...]
    logic_modules: tuple[dict[str, Any], ...] = ()

    @property
    def activities(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            activity for module in self.activity_modules for activity in module["activities"]
        )

    @property
    def narrative_units(self) -> tuple[dict[str, Any], ...]:
        return tuple(unit for module in self.narrative_modules for unit in module["units"])

    @property
    def systems(self) -> tuple[dict[str, Any], ...]:
        return tuple(system for module in self.system_modules for system in module["systems"])


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _reject_float_lexeme(value: str) -> None:
    raise ValueError(
        f"non-finite JSON number or decimal or exponent JSON number is unsupported: {value}"
    )


def _strict_json_integer(value: str) -> int:
    magnitude = value.removeprefix("-")
    if len(magnitude) > len(_MAX_SAFE_INTEGER_DECIMAL) or (
        len(magnitude) == len(_MAX_SAFE_INTEGER_DECIMAL) and magnitude > _MAX_SAFE_INTEGER_DECIMAL
    ):
        raise ValueError(f"JSON integer is outside the JavaScript-safe integer range: {value}")
    parsed = int(value)
    return parsed


def _validate_json_structure(value: object, *, context: str) -> None:
    active: set[int] = set()
    stack: list[tuple[bool, object, int]] = [(True, value, 1)]
    while stack:
        entering, current, depth = stack.pop()
        if not entering:
            active.remove(id(current))
            continue
        if isinstance(current, (dict, list)):
            if depth > MAX_CREATION_JSON_DEPTH:
                raise CreationContractError(
                    f"{context}: JSON depth exceeds the {MAX_CREATION_JSON_DEPTH}-level limit"
                )
            identity = id(current)
            if identity in active:
                raise CreationContractError(f"{context}: JSON container cycle is unsupported")
            active.add(identity)
            stack.append((False, current, depth))
            if isinstance(current, dict):
                children: list[object] = []
                for key, item in current.items():
                    if not isinstance(key, str):
                        raise CreationContractError(f"{context}: JSON object keys must be strings")
                    try:
                        key.encode("utf-8")
                    except UnicodeError as exc:
                        raise CreationContractError(
                            f"{context}: JSON strings must contain Unicode scalar values"
                        ) from exc
                    children.append(item)
            else:
                children = list(current)
            stack.extend((True, item, depth + 1) for item in reversed(children))
            continue
        if current is None or isinstance(current, bool):
            continue
        if isinstance(current, int):
            if not -MAX_SAFE_INTEGER <= current <= MAX_SAFE_INTEGER:
                raise CreationContractError(
                    f"{context}: JSON integer is outside the JavaScript-safe integer range"
                )
            continue
        if isinstance(current, float):
            raise CreationContractError(
                f"{context}: decimal or exponent JSON numbers are unsupported"
            )
        if isinstance(current, str):
            try:
                current.encode("utf-8")
            except UnicodeError as exc:
                raise CreationContractError(
                    f"{context}: JSON strings must contain Unicode scalar values"
                ) from exc
            continue
        raise CreationContractError(
            f"{context}: unsupported JSON value type {type(current).__name__}"
        )


def read_creation_object(
    path: str | Path,
    *,
    limit: int = MAX_CREATION_CONTRACT_BYTES,
    preflight: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    source = Path(os.path.abspath(os.fspath(path)))
    root = source.parent
    name = _portable_relative_path(source.name, "creation contract path")
    value: dict[str, Any] | None = None
    contract_error: CreationContractError | None = None
    try:
        with _pinned_ancestor_identities(root, context="creation contract root") as identities:
            try:
                initial = source.lstat()
                initial_state = (
                    initial.st_dev,
                    initial.st_ino,
                    initial.st_mode,
                    initial.st_nlink,
                    initial.st_size,
                    initial.st_mtime_ns,
                    initial.st_ctime_ns,
                )
                first = read_workspace_file_snapshot(
                    root,
                    PurePosixPath(name),
                    world_identity=identities[-1],
                    context="creation contract",
                    limit=limit,
                )
                value = _decode_creation_object(first, source, preflight=preflight)
                second = read_workspace_file_snapshot(
                    root,
                    PurePosixPath(name),
                    world_identity=identities[-1],
                    context="creation contract verification",
                    limit=limit,
                )
                if first != second:
                    raise CreationContractError(
                        f"Could not read {source}: file changed while reading"
                    )
                final = source.lstat()
                final_state = (
                    final.st_dev,
                    final.st_ino,
                    final.st_mode,
                    final.st_nlink,
                    final.st_size,
                    final.st_mtime_ns,
                    final.st_ctime_ns,
                )
                if initial_state != final_state:
                    raise CreationContractError(
                        f"Could not read {source}: file identity changed while reading"
                    )
            except CreationContractError as exc:
                contract_error = exc
    except StudioError as exc:
        raise CreationContractError(
            f"Could not read {source} through a safe snapshot requiring a "
            f"standalone regular file: {exc.message}"
        ) from exc
    if contract_error is not None:
        raise contract_error
    if value is None:
        raise CreationContractError(f"Could not read {source}: safe snapshot produced no object")
    return value


def _decode_creation_object(
    raw: bytes,
    source: object,
    *,
    preflight: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, RecursionError) as exc:
        raise CreationContractError(f"Could not read {source}: invalid UTF-8: {exc}") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
            parse_float=_reject_float_lexeme,
            parse_int=_strict_json_integer,
        )
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError, UnicodeError) as exc:
        raise CreationContractError(f"Could not read {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise CreationContractError(f"{source} must contain a JSON object")
    if preflight is not None:
        preflight(value)
    _validate_json_structure(value, context=f"Could not read {source}")
    return value


def canonical_creation_hash(value: Mapping[str, object]) -> str:
    try:
        payload = dict(value)
        payload.pop("content_hash", None)
        _validate_json_structure(payload, context="Could not encode strict creation JSON")
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except CreationContractError:
        raise
    except (TypeError, ValueError, RecursionError, UnicodeError, OverflowError) as exc:
        raise CreationContractError(f"Could not encode strict creation JSON: {exc}") from exc
    return hashlib.sha256(encoded).hexdigest()


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CreationContractError(f"{context} must be an object")
    return value


def _exact_keys(value: Mapping[str, object], fields: frozenset[str], context: str) -> None:
    keys = tuple(value)
    if any(not isinstance(key, str) for key in keys):
        raise CreationContractError(f"{context} JSON object keys must be strings")
    key_set = set(keys)
    unknown = key_set - fields
    missing = fields - key_set
    if unknown:
        raise CreationContractError(
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    if missing:
        raise CreationContractError(f"{context} is missing fields: {', '.join(sorted(missing))}")


def _non_empty_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise CreationContractError(f"{context} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        raise CreationContractError(f"{context} must be NFC normalized")
    return value


def _integer(value: object, context: str, *, minimum: int = 0) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= MAX_SAFE_INTEGER
    ):
        raise CreationContractError(
            f"{context} must be a JavaScript-safe integer from {minimum} to {MAX_SAFE_INTEGER}"
        )
    return value


def _identifier(value: object, context: str) -> str:
    if (
        not isinstance(value, str)
        or _ID_RE.fullmatch(value) is None
        or value.casefold() in _WINDOWS_RESERVED
    ):
        raise CreationContractError(f"{context} must be a portable lowercase ID")
    return value


def _sha256(value: object, context: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise CreationContractError(f"{context} must be a lowercase SHA-256")
    return value


def _semver(value: object, context: str) -> str:
    if not isinstance(value, str) or _SEMVER_RE.fullmatch(value) is None:
        raise CreationContractError(f"{context} must be strict MAJOR.MINOR.PATCH")
    return value


def _locale(value: object, context: str) -> str:
    if not isinstance(value, str) or _LOCALE_RE.fullmatch(value) is None:
        raise CreationContractError(f"{context} must be a language tag")
    return value


def _portable_relative_path(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CreationContractError(f"{context} must be a portable relative path")
    if unicodedata.normalize("NFC", value) != value:
        raise CreationContractError(f"{context} must be NFC normalized")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or not path.parts:
        raise CreationContractError(f"{context} must be a portable relative path")
    if len(path.parts) > MAX_CREATION_PATH_DEPTH:
        raise CreationContractError(
            f"{context} exceeds the {MAX_CREATION_PATH_DEPTH}-component path depth"
        )
    if len(value.encode("utf-8")) > MAX_CREATION_PATH_BYTES:
        raise CreationContractError(
            f"{context} exceeds the {MAX_CREATION_PATH_BYTES}-byte path byte limit"
        )
    for component in path.parts:
        folded = component.casefold()
        stem = component.split(".", 1)[0].casefold()
        if (
            component in {"", ".", ".."}
            or component.endswith((" ", "."))
            or stem in _WINDOWS_RESERVED
            or any(ord(character) < 32 or character in '<>:"\\|?*' for character in component)
            or len(component.encode("utf-8")) > 255
            or folded in {".", ".."}
        ):
            raise CreationContractError(f"{context} must be a portable relative path")
    return value


def _string_array(
    value: object,
    context: str,
    *,
    allow_empty: bool = True,
    tokens: bool = False,
    canonical_order: bool = False,
) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "" if allow_empty else " non-empty"
        raise CreationContractError(f"{context} must be a{qualifier} string array")
    result: list[str] = []
    keys: set[str] = set()
    for index, item in enumerate(value):
        item = _non_empty_string(item, f"{context}/{index}")
        if tokens and _TOKEN_RE.fullmatch(item) is None:
            raise CreationContractError(f"{context}/{index} must be a namespaced token")
        key = item.casefold()
        if key in keys:
            raise CreationContractError(f"{context} contains an NFC/casefold collision")
        keys.add(key)
        result.append(item)
    if canonical_order and result != sorted(result, key=lambda item: item.encode("utf-8")):
        raise CreationContractError(f"{context} must use canonical sorted order")
    return result


def _identifier_array(value: object, context: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "" if allow_empty else " non-empty"
        raise CreationContractError(f"{context} must be a{qualifier} ID array")
    result: list[str] = []
    keys: set[str] = set()
    for index, item in enumerate(value):
        identifier = _identifier(item, f"{context}/{index}")
        key = identifier.casefold()
        if key in keys:
            raise CreationContractError(f"{context} contains an NFC/casefold collision")
        keys.add(key)
        result.append(identifier)
    if result != sorted(result, key=lambda item: item.encode("utf-8")):
        raise CreationContractError(f"{context} must use canonical sorted order")
    return result


def _extensions(
    value: object,
    context: str,
    registered_extensions: Mapping[str, ExtensionValidator],
    *,
    maximum: int | None = None,
) -> None:
    if not isinstance(value, list):
        raise CreationContractError(f"{context} must be an array")
    if maximum is not None and len(value) > maximum:
        raise CreationContractError(f"{context} exceeds the {maximum}-item extension limit")
    seen: set[str] = set()
    identifiers: list[str] = []
    for index, raw in enumerate(value):
        extension_context = f"{context}/{index}"
        extension = _object(raw, extension_context)
        _exact_keys(extension, _EXTENSION_FIELDS, extension_context)
        extension_id = extension.get("id")
        if not isinstance(extension_id, str) or _EXTENSION_ID_RE.fullmatch(extension_id) is None:
            raise CreationContractError(f"{extension_context}.id must be a namespaced extension ID")
        key = extension_id.casefold()
        if key in seen:
            raise CreationContractError(f"{context} contains an NFC/casefold collision")
        seen.add(key)
        identifiers.append(extension_id)
        _integer(extension.get("version"), f"{extension_context}.version", minimum=1)
        if not isinstance(extension.get("required"), bool):
            raise CreationContractError(f"{extension_context}.required must be boolean")
        _sha256(extension.get("content_hash"), f"{extension_context}.content_hash")
        validator = registered_extensions.get(extension_id)
        if extension["required"] and validator is None:
            raise CreationContractError(f"{extension_context} names unknown required extension")
        if validator is not None:
            validator(copy.deepcopy(extension))
    if identifiers != sorted(identifiers, key=lambda item: item.encode("utf-8")):
        raise CreationContractError(f"{context} must use canonical sorted order")


def _reference(
    value: object,
    context: str,
    *,
    expected_format: str,
) -> dict[str, Any]:
    reference = _object(value, context)
    _exact_keys(reference, _REFERENCE_FIELDS, context)
    if reference.get("format") != expected_format:
        raise CreationContractError(f"{context}.format must be {expected_format}")
    version = reference.get("format_version")
    if isinstance(version, bool) or version != CREATION_CONTRACT_VERSION:
        raise CreationContractError(f"{context}.format_version is unsupported")
    _identifier(reference.get("id"), f"{context}.id")
    _portable_relative_path(reference.get("path"), f"{context}.path")
    _sha256(reference.get("content_hash"), f"{context}.content_hash")
    return reference


def _verify_identity(value: dict[str, Any], context: str) -> str:
    format_name = value.get("format")
    version = value.get("format_version")
    if (
        not isinstance(format_name, str)
        or format_name not in _FORMATS
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version != CREATION_CONTRACT_VERSION
    ):
        raise CreationContractError(f"{context} format or format_version is unsupported")
    _sha256(value.get("content_hash"), f"{context}.content_hash")
    if canonical_creation_hash(value) != value["content_hash"]:
        raise CreationContractError(f"{context} content hash does not match its contents")
    return str(format_name)


def _validate_project(
    value: dict[str, Any],
    registered_extensions: Mapping[str, ExtensionValidator],
) -> None:
    context = "creation project"
    _exact_keys(value, _PROJECT_FIELDS, context)
    project_kind = value.get("project_kind")
    if not isinstance(project_kind, str) or project_kind not in _PROJECT_KINDS:
        raise CreationContractError(f"{context}.project_kind is unsupported")
    _identifier(value.get("project_id"), f"{context}.project_id")
    _non_empty_string(value.get("title"), f"{context}.title")
    _semver(value.get("project_version"), f"{context}.project_version")
    _locale(value.get("default_locale"), f"{context}.default_locale")
    _reference(value.get("profile"), f"{context}.profile", expected_format=CREATION_PROFILE_FORMAT)
    _reference(
        value.get("source_manifest"),
        f"{context}.source_manifest",
        expected_format=CREATION_SOURCE_MANIFEST_FORMAT,
    )
    _extensions(value.get("extensions"), f"{context}.extensions", registered_extensions)


def _validate_experience(value: object) -> None:
    context = "creation profile.experience"
    experience = _object(value, context)
    _exact_keys(
        experience,
        frozenset({"player_promise", "audiences", "experience_goals"}),
        context,
    )
    _non_empty_string(experience.get("player_promise"), f"{context}.player_promise")
    _string_array(
        experience.get("audiences"),
        f"{context}.audiences",
        allow_empty=False,
    )
    _string_array(
        experience.get("experience_goals"),
        f"{context}.experience_goals",
        allow_empty=False,
    )


def _validate_gameplay(value: object) -> None:
    context = "creation profile.gameplay"
    gameplay = _object(value, context)
    fields = frozenset(
        {
            "primary_family",
            "secondary_families",
            "mechanic_tags",
            "player_role",
            "core_verbs",
            "core_loop",
            "rule_model",
            "goal_model",
            "challenge_model",
            "failure_recovery",
            "progression",
            "teleology",
            "session_structure",
            "social_topology",
            "dependencies",
        }
    )
    _exact_keys(gameplay, fields, context)
    primary_family = gameplay.get("primary_family")
    if not isinstance(primary_family, str) or primary_family not in _GAMEPLAY_FAMILIES:
        raise CreationContractError(f"{context}.primary_family is unsupported")
    secondary = _string_array(
        gameplay.get("secondary_families"),
        f"{context}.secondary_families",
        canonical_order=True,
    )
    if any(item not in _GAMEPLAY_FAMILIES for item in secondary):
        raise CreationContractError(f"{context}.secondary_families contains an unsupported family")
    _string_array(
        gameplay.get("mechanic_tags"),
        f"{context}.mechanic_tags",
        tokens=True,
        canonical_order=True,
    )
    _non_empty_string(gameplay.get("player_role"), f"{context}.player_role")
    verbs = gameplay.get("core_verbs")
    if not isinstance(verbs, list):
        raise CreationContractError(f"{context}.core_verbs must be an array")
    verb_ids: set[str] = set()
    for index, raw in enumerate(verbs):
        verb_context = f"{context}.core_verbs/{index}"
        verb = _object(raw, verb_context)
        _exact_keys(verb, frozenset({"id", "description"}), verb_context)
        verb_id = _identifier(verb.get("id"), f"{verb_context}.id")
        if verb_id.casefold() in verb_ids:
            raise CreationContractError(f"{context}.core_verbs contains an NFC/casefold collision")
        verb_ids.add(verb_id.casefold())
        _non_empty_string(verb.get("description"), f"{verb_context}.description")
    core_loop = _string_array(gameplay.get("core_loop"), f"{context}.core_loop")
    for field in (
        "rule_model",
        "goal_model",
        "challenge_model",
        "failure_recovery",
        "progression",
        "session_structure",
    ):
        _non_empty_string(gameplay.get(field), f"{context}.{field}")
    teleology = gameplay.get("teleology")
    if not isinstance(teleology, str) or teleology not in {
        "none",
        "finite",
        "infinite",
        "open_ended",
    }:
        raise CreationContractError(f"{context}.teleology is unsupported")
    social_topology = gameplay.get("social_topology")
    if not isinstance(social_topology, str) or social_topology not in {
        "none",
        "single_player",
        "local_cooperative",
        "local_competitive",
        "online_cooperative",
        "online_competitive",
        "massively_multiplayer",
    }:
        raise CreationContractError(f"{context}.social_topology is unsupported")
    dependencies = _object(gameplay.get("dependencies"), f"{context}.dependencies")
    _exact_keys(
        dependencies,
        frozenset({"authored", "systemic", "procedural"}),
        f"{context}.dependencies",
    )
    for field in ("authored", "systemic", "procedural"):
        _string_array(
            dependencies.get(field),
            f"{context}.dependencies.{field}",
            tokens=True,
            canonical_order=True,
        )
    if gameplay["primary_family"] == "none":
        none_fields = (
            "player_role",
            "rule_model",
            "goal_model",
            "challenge_model",
            "failure_recovery",
            "progression",
            "session_structure",
            "teleology",
            "social_topology",
        )
        if (
            secondary
            or gameplay["mechanic_tags"]
            or verbs
            or core_loop
            or any(gameplay[field] != "none" for field in none_fields)
            or any(dependencies[field] for field in ("authored", "systemic", "procedural"))
        ):
            raise CreationContractError(
                "creation profile gameplay:none must not invent game mechanics"
            )
    elif not verbs or not core_loop:
        raise CreationContractError(
            "creation profile gameplay requires at least one core verb and loop step"
        )


def _validate_world(value: object) -> None:
    context = "creation profile.world"
    world = _object(value, context)
    fields = frozenset(
        {
            "presence",
            "spatial_topology",
            "scale",
            "time_model",
            "simulation_depth",
            "simulated_domains",
            "persistence",
            "spatial_structure",
        }
    )
    _exact_keys(world, fields, context)
    presence = world.get("presence")
    if not isinstance(presence, str) or presence not in _WORLD_PRESENCE:
        raise CreationContractError(f"{context}.presence is unsupported")
    _string_array(
        world.get("simulated_domains"),
        f"{context}.simulated_domains",
        tokens=True,
        canonical_order=True,
    )
    for field in (
        "spatial_topology",
        "scale",
        "time_model",
        "simulation_depth",
        "persistence",
        "spatial_structure",
    ):
        _non_empty_string(world.get(field), f"{context}.{field}")
    if world["presence"] == "none":
        none_fields = (
            "spatial_topology",
            "scale",
            "time_model",
            "simulation_depth",
            "persistence",
            "spatial_structure",
        )
        if any(world[field] != "none" for field in none_fields) or world["simulated_domains"]:
            raise CreationContractError(
                "creation profile world:none must not invent spatial or simulation semantics"
            )


def _validate_narrative(value: object) -> None:
    context = "creation profile.narrative"
    narrative = _object(value, context)
    fields = frozenset(
        {
            "requirement",
            "authorship_mode",
            "topology",
            "delivery_channels",
            "protagonist_model",
            "agency",
            "focalization",
            "canon_variability",
            "pacing",
            "endings",
            "information_model",
        }
    )
    _exact_keys(narrative, fields, context)
    requirement = narrative.get("requirement")
    if not isinstance(requirement, str) or requirement not in _NARRATIVE_REQUIREMENTS:
        raise CreationContractError(f"{context}.requirement is unsupported")
    authorship_mode = narrative.get("authorship_mode")
    if not isinstance(authorship_mode, str) or authorship_mode not in _NARRATIVE_AUTHORSHIP:
        raise CreationContractError(f"{context}.authorship_mode is unsupported")
    topology = narrative.get("topology")
    if not isinstance(topology, str) or topology not in _NARRATIVE_TOPOLOGIES:
        raise CreationContractError(f"{context}.topology is unsupported")
    _string_array(
        narrative.get("delivery_channels"),
        f"{context}.delivery_channels",
        tokens=True,
        canonical_order=True,
    )
    for field in (
        "protagonist_model",
        "agency",
        "focalization",
        "canon_variability",
        "pacing",
        "endings",
        "information_model",
    ):
        _non_empty_string(narrative.get(field), f"{context}.{field}")
    if narrative["requirement"] == "none":
        none_fields = (
            "authorship_mode",
            "topology",
            "protagonist_model",
            "agency",
            "focalization",
            "canon_variability",
            "pacing",
            "endings",
            "information_model",
        )
        if (
            any(narrative[field] != "none" for field in none_fields)
            or narrative["delivery_channels"]
        ):
            raise CreationContractError(
                "creation profile narrative:none must not invent narrative semantics"
            )


def _validate_fiction(value: object) -> None:
    context = "creation profile.fiction"
    fiction = _object(value, context)
    _exact_keys(fiction, frozenset({"genres", "tones", "tags"}), context)
    for field in ("genres", "tones", "tags"):
        _string_array(
            fiction.get(field),
            f"{context}.{field}",
            tokens=field == "tags",
            canonical_order=True,
        )


def _validate_presentation(value: object) -> None:
    context = "creation profile.presentation"
    presentation = _object(value, context)
    fields = frozenset(
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
    )
    _exact_keys(presentation, fields, context)
    mode = presentation.get("mode")
    if not isinstance(mode, str) or mode not in _PRESENTATION_MODES:
        raise CreationContractError(f"{context}.mode is unsupported")
    for field in ("camera", "perspective", "visual_language", "ui_density", "audio_role"):
        _non_empty_string(presentation.get(field), f"{context}.{field}")
    _string_array(
        presentation.get("input_assumptions"),
        f"{context}.input_assumptions",
        allow_empty=False,
        tokens=True,
        canonical_order=True,
    )
    accessibility = _object(presentation.get("accessibility"), f"{context}.accessibility")
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
    for field in accessibility_fields:
        if not isinstance(accessibility.get(field), bool):
            raise CreationContractError(f"{context}.accessibility.{field} must be boolean")
    localization = _object(presentation.get("localization"), f"{context}.localization")
    _exact_keys(
        localization,
        frozenset({"source_locale", "supported_locales", "externalized_text"}),
        f"{context}.localization",
    )
    source_locale = _locale(
        localization.get("source_locale"), f"{context}.localization.source_locale"
    )
    locales = _string_array(
        localization.get("supported_locales"),
        f"{context}.localization.supported_locales",
        allow_empty=False,
        canonical_order=True,
    )
    for index, locale in enumerate(locales):
        _locale(locale, f"{context}.localization.supported_locales/{index}")
    if source_locale.casefold() not in {locale.casefold() for locale in locales}:
        raise CreationContractError(
            f"{context}.localization.supported_locales must contain source_locale"
        )
    if not isinstance(localization.get("externalized_text"), bool):
        raise CreationContractError(f"{context}.localization.externalized_text must be boolean")


def _validate_production(value: object) -> None:
    context = "creation profile.production"
    production = _object(value, context)
    fields = frozenset(
        {
            "content_modes",
            "seed_policy",
            "reproducibility",
            "selection_policy",
            "human_review",
            "provenance_required",
            "licensing_required",
            "qa_required",
        }
    )
    _exact_keys(production, fields, context)
    modes = _object(production.get("content_modes"), f"{context}.content_modes")
    _exact_keys(
        modes,
        frozenset({"gameplay", "world", "narrative", "assets"}),
        f"{context}.content_modes",
    )
    for field in ("gameplay", "world", "narrative", "assets"):
        mode = modes.get(field)
        if not isinstance(mode, str) or mode not in _PRODUCTION_MODES:
            raise CreationContractError(f"{context}.content_modes.{field} is unsupported")
    for field in ("seed_policy", "reproducibility", "selection_policy"):
        _non_empty_string(production.get(field), f"{context}.{field}")
    for field in ("human_review", "provenance_required", "licensing_required", "qa_required"):
        if not isinstance(production.get(field), bool):
            raise CreationContractError(f"{context}.{field} must be boolean")


def _validate_runtime_target(value: object) -> None:
    context = "creation profile.runtime_target"
    target = _object(value, context)
    fields = frozenset(
        {
            "requested_adapter",
            "accepted_logic_formats",
            "required_features",
            "optional_features",
            "presentation_mode",
            "platforms",
            "renderer",
            "input_capabilities",
            "asset_formats",
            "save_expected",
            "replay_expected",
            "packaging_target",
        }
    )
    _exact_keys(target, fields, context)
    adapter = target.get("requested_adapter")
    if adapter is not None:
        _identifier(adapter, f"{context}.requested_adapter")
    accepted = target.get("accepted_logic_formats")
    if not isinstance(accepted, list):
        raise CreationContractError(f"{context}.accepted_logic_formats must be an array")
    seen_formats: set[str] = set()
    for index, raw in enumerate(accepted):
        format_context = f"{context}.accepted_logic_formats/{index}"
        item = _object(raw, format_context)
        _exact_keys(item, frozenset({"format", "versions"}), format_context)
        format_name = _non_empty_string(item.get("format"), f"{format_context}.format")
        format_key = format_name.casefold()
        if format_key in seen_formats:
            raise CreationContractError(
                f"{context}.accepted_logic_formats contains an NFC/casefold collision"
            )
        seen_formats.add(format_key)
        versions = item.get("versions")
        if not isinstance(versions, list) or not versions:
            raise CreationContractError(f"{format_context}.versions must be non-empty")
        seen_versions: set[int] = set()
        for version_index, version in enumerate(versions):
            parsed = _integer(version, f"{format_context}.versions/{version_index}", minimum=1)
            if parsed in seen_versions:
                raise CreationContractError(f"{format_context}.versions contains duplicates")
            seen_versions.add(parsed)
        if versions != sorted(versions):
            raise CreationContractError(
                f"{format_context}.versions must use canonical sorted order"
            )
    accepted_names = [item["format"] for item in accepted]
    if accepted_names != sorted(accepted_names, key=lambda item: item.encode("utf-8")):
        raise CreationContractError(
            f"{context}.accepted_logic_formats must use canonical sorted order"
        )
    feature_sets: dict[str, set[str]] = {}
    for field in (
        "required_features",
        "optional_features",
        "platforms",
        "input_capabilities",
        "asset_formats",
    ):
        values = _string_array(
            target.get(field),
            f"{context}.{field}",
            tokens=True,
            canonical_order=True,
        )
        feature_sets[field] = {item.casefold() for item in values}
    if feature_sets["required_features"].intersection(feature_sets["optional_features"]):
        raise CreationContractError(
            f"{context}.required_features and optional_features must be disjoint"
        )
    presentation_mode = target.get("presentation_mode")
    if not isinstance(presentation_mode, str) or presentation_mode not in _PRESENTATION_MODES:
        raise CreationContractError(f"{context}.presentation_mode is unsupported")
    for field in ("renderer", "packaging_target"):
        _non_empty_string(target.get(field), f"{context}.{field}")
    for field in ("save_expected", "replay_expected"):
        if not isinstance(target.get(field), bool):
            raise CreationContractError(f"{context}.{field} must be boolean")


def _validate_profile(
    value: dict[str, Any],
    registered_extensions: Mapping[str, ExtensionValidator],
) -> None:
    context = "creation profile"
    _exact_keys(value, _PROFILE_FIELDS, context)
    _identifier(value.get("profile_id"), f"{context}.profile_id")
    _identifier(value.get("project_id"), f"{context}.project_id")
    _non_empty_string(value.get("title"), f"{context}.title")
    _validate_experience(value.get("experience"))
    _validate_gameplay(value.get("gameplay"))
    _validate_world(value.get("world"))
    _validate_narrative(value.get("narrative"))
    _validate_fiction(value.get("fiction"))
    _validate_presentation(value.get("presentation"))
    _validate_production(value.get("production"))
    _validate_runtime_target(value.get("runtime_target"))
    if value["presentation"]["mode"] != value["runtime_target"]["presentation_mode"]:
        raise CreationContractError(
            "creation profile presentation modes differ between presentation and runtime target"
        )
    dependencies = value["gameplay"]["dependencies"]
    features = (
        value["runtime_target"]["required_features"] + value["runtime_target"]["optional_features"]
    )
    for facet, absent in (
        ("world", value["world"]["presence"] == "none"),
        ("narrative", value["narrative"]["requirement"] == "none"),
    ):
        if not absent:
            continue
        if value["production"]["content_modes"][facet] != "not_applicable":
            raise CreationContractError(
                f"{facet}:none requires production content mode not_applicable"
            )
        if any(
            item.startswith(f"{facet}:")
            for collection in dependencies.values()
            for item in collection
        ):
            raise CreationContractError(
                f"{facet}:none forbids {facet}-prefixed gameplay dependencies"
            )
        if any(item.startswith(f"{facet}:") for item in features):
            raise CreationContractError(f"{facet}:none forbids {facet}-prefixed runtime features")
    _extensions(value.get("extensions"), f"{context}.extensions", registered_extensions)


def _validate_manifest(
    value: dict[str, Any],
    registered_extensions: Mapping[str, ExtensionValidator],
) -> None:
    context = "creation source manifest"
    _exact_keys(value, _MANIFEST_FIELDS, context)
    _identifier(value.get("project_id"), f"{context}.project_id")
    _reference(value.get("profile"), f"{context}.profile", expected_format=CREATION_PROFILE_FORMAT)
    modules = _object(value.get("modules"), f"{context}.modules")
    _exact_keys(modules, frozenset(_MODULE_COLLECTIONS), f"{context}.modules")
    ids: dict[str, str] = {}
    paths: dict[str, str] = {}
    for collection, module_format in _MODULE_COLLECTIONS.items():
        entries = modules.get(collection)
        if not isinstance(entries, list):
            raise CreationContractError(f"{context}.modules.{collection} must be an array")
        collection_ids: list[str] = []
        for index, raw in enumerate(entries):
            reference_context = f"{context}.modules.{collection}/{index}"
            reference = _reference(raw, reference_context, expected_format=module_format)
            identity_key = reference["id"].casefold()
            path_key = unicodedata.normalize("NFC", reference["path"]).casefold()
            if identity_key in ids:
                raise CreationContractError(
                    f"{reference_context}.id has an NFC/casefold collision with {ids[identity_key]}"
                )
            if path_key in paths:
                raise CreationContractError(
                    f"{reference_context}.path has an NFC/casefold collision with {paths[path_key]}"
                )
            ids[identity_key] = reference["id"]
            paths[path_key] = reference["path"]
            collection_ids.append(reference["id"])
        if collection_ids != sorted(
            collection_ids,
            key=lambda item: item.encode("utf-8"),
        ):
            raise CreationContractError(
                f"{context}.modules.{collection} must use canonical sorted order"
            )
    _extensions(value.get("extensions"), f"{context}.extensions", registered_extensions)


def _validate_world_record(module_type: str, value: object, context: str) -> str:
    record = _object(value, context)
    record_fields = {
        "canon": frozenset({"id", "statement", "status", "sources"}),
        "chronology": frozenset({"id", "sequence", "summary"}),
        "space": frozenset({"id", "name", "topology"}),
        "group": frozenset({"id", "name", "group_type"}),
        "character": frozenset({"id", "name", "role"}),
        "knowledge": frozenset({"id", "statement", "access"}),
    }[module_type]
    _exact_keys(record, record_fields, context)
    record_id = _identifier(record.get("id"), f"{context}.id")
    if module_type == "canon":
        _non_empty_string(record.get("statement"), f"{context}.statement")
        status = record.get("status")
        if not isinstance(status, str) or status not in {"canon", "provisional"}:
            raise CreationContractError(f"{context}.status is unsupported")
        _string_array(
            record.get("sources"),
            f"{context}.sources",
            canonical_order=True,
        )
    elif module_type == "chronology":
        _integer(record.get("sequence"), f"{context}.sequence")
        _non_empty_string(record.get("summary"), f"{context}.summary")
    elif module_type == "space":
        _non_empty_string(record.get("name"), f"{context}.name")
        topology = record.get("topology")
        if not isinstance(topology, str) or topology not in {
            "abstract",
            "symbolic",
            "diegetic",
        }:
            raise CreationContractError(f"{context}.topology is unsupported")
    elif module_type == "group":
        _non_empty_string(record.get("name"), f"{context}.name")
        _non_empty_string(record.get("group_type"), f"{context}.group_type")
    elif module_type == "character":
        _non_empty_string(record.get("name"), f"{context}.name")
        _non_empty_string(record.get("role"), f"{context}.role")
    else:
        _non_empty_string(record.get("statement"), f"{context}.statement")
        access = record.get("access")
        if not isinstance(access, str) or access not in {
            "public",
            "restricted",
            "secret",
        }:
            raise CreationContractError(f"{context}.access is unsupported")
    return record_id


def _validate_world_module(
    value: dict[str, Any],
    registered_extensions: Mapping[str, ExtensionValidator],
) -> None:
    context = "world module"
    module_type = value.get("module_type")
    if not isinstance(module_type, str) or module_type not in _WORLD_PAYLOAD_FIELDS:
        raise CreationContractError(f"{context}.module_type is unsupported")
    payload_field = _WORLD_PAYLOAD_FIELDS[module_type]
    _exact_keys(value, _WORLD_MODULE_BASE_FIELDS | {payload_field}, context)
    _identifier(value.get("module_id"), f"{context}.module_id")
    _identifier(value.get("project_id"), f"{context}.project_id")
    _non_empty_string(value.get("title"), f"{context}.title")
    records = value.get(payload_field)
    if not isinstance(records, list) or not records:
        raise CreationContractError(f"{context}.{payload_field} must be a non-empty array")
    seen: set[str] = set()
    for index, raw in enumerate(records):
        record_id = _validate_world_record(
            module_type,
            raw,
            f"{context}.{payload_field}/{index}",
        )
        if record_id.casefold() in seen:
            raise CreationContractError(
                f"{context}.{payload_field} contains an NFC/casefold collision"
            )
        seen.add(record_id.casefold())
    _extensions(value.get("extensions"), f"{context}.extensions", registered_extensions)


def _validate_activity_module(
    value: dict[str, Any],
    registered_extensions: Mapping[str, ExtensionValidator],
) -> None:
    context = "activity module"
    _exact_keys(value, _ACTIVITY_MODULE_FIELDS, context)
    _identifier(value.get("module_id"), f"{context}.module_id")
    _identifier(value.get("project_id"), f"{context}.project_id")
    _non_empty_string(value.get("title"), f"{context}.title")
    activities = value.get("activities")
    if not isinstance(activities, list) or not activities:
        raise CreationContractError(f"{context}.activities must be a non-empty array")
    fields = frozenset(
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
            "validation_profile",
            "provenance",
        }
    )
    seen: set[str] = set()
    for index, raw in enumerate(activities):
        activity_context = f"{context}.activities/{index}"
        activity = _object(raw, activity_context)
        _exact_keys(activity, fields, activity_context)
        activity_id = _identifier(activity.get("id"), f"{activity_context}.id")
        if activity_id.casefold() in seen:
            raise CreationContractError(f"{context}.activities contains an NFC/casefold collision")
        seen.add(activity_id.casefold())
        activity_type = activity.get("activity_type")
        if not isinstance(activity_type, str) or activity_type not in _ACTIVITY_TYPES:
            raise CreationContractError(f"{activity_context}.activity_type is unsupported")
        _non_empty_string(activity.get("title"), f"{activity_context}.title")
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
            _identifier_array(activity.get(field), f"{activity_context}.{field}")
        for field in ("validation_profile", "provenance"):
            _non_empty_string(activity.get(field), f"{activity_context}.{field}")
    _extensions(value.get("extensions"), f"{context}.extensions", registered_extensions)


def _validate_narrative_module(
    value: dict[str, Any],
    registered_extensions: Mapping[str, ExtensionValidator],
) -> None:
    context = "narrative module"
    _exact_keys(value, _NARRATIVE_MODULE_FIELDS, context)
    _identifier(value.get("module_id"), f"{context}.module_id")
    _identifier(value.get("project_id"), f"{context}.project_id")
    _non_empty_string(value.get("title"), f"{context}.title")
    entry_unit_ids = _identifier_array(
        value.get("entry_unit_ids"),
        f"{context}.entry_unit_ids",
        allow_empty=False,
    )
    units = value.get("units")
    if not isinstance(units, list) or not units:
        raise CreationContractError(f"{context}.units must be a non-empty array")
    common = frozenset(
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
    seen: set[str] = set()
    unit_ids: set[str] = set()
    adjacency: dict[str, tuple[str, ...]] = {}
    for index, raw in enumerate(units):
        unit_context = f"{context}.units/{index}"
        unit = _object(raw, unit_context)
        unit_type = unit.get("unit_type")
        if not isinstance(unit_type, str) or unit_type not in _NARRATIVE_UNIT_TYPES:
            raise CreationContractError(f"{unit_context}.unit_type is unsupported")
        extra = (
            frozenset({"options"})
            if unit_type == "choice"
            else frozenset({"ending_kind"})
            if unit_type == "ending"
            else frozenset()
        )
        _exact_keys(unit, common | extra, unit_context)
        unit_id = _identifier(unit.get("id"), f"{unit_context}.id")
        if unit_id.casefold() in seen:
            raise CreationContractError(f"{context}.units contains an NFC/casefold collision")
        seen.add(unit_id.casefold())
        unit_ids.add(unit_id)
        _non_empty_string(unit.get("title"), f"{unit_context}.title")
        for field in ("prerequisite_ids", "effect_ids", "asset_binding_ids"):
            _identifier_array(unit.get(field), f"{unit_context}.{field}")
        next_unit_ids = _identifier_array(
            unit.get("next_unit_ids"),
            f"{unit_context}.next_unit_ids",
        )
        adjacency[unit_id] = tuple(next_unit_ids)
        if unit_type == "choice":
            options = unit.get("options")
            if not isinstance(options, list) or len(options) < 2:
                raise CreationContractError(f"{unit_context}.options must contain at least two")
            option_ids: set[str] = set()
            option_targets: list[str] = []
            for option_index, raw_option in enumerate(options):
                option_context = f"{unit_context}.options/{option_index}"
                option = _object(raw_option, option_context)
                _exact_keys(
                    option,
                    frozenset({"id", "label", "next_unit_id", "condition_ids", "effect_ids"}),
                    option_context,
                )
                option_id = _identifier(option.get("id"), f"{option_context}.id")
                if option_id.casefold() in option_ids:
                    raise CreationContractError(
                        f"{unit_context}.options contains an NFC/casefold collision"
                    )
                option_ids.add(option_id.casefold())
                _non_empty_string(option.get("label"), f"{option_context}.label")
                option_targets.append(
                    _identifier(option.get("next_unit_id"), f"{option_context}.next_unit_id")
                )
                _identifier_array(option.get("condition_ids"), f"{option_context}.condition_ids")
                _identifier_array(option.get("effect_ids"), f"{option_context}.effect_ids")
            if next_unit_ids != sorted(
                option_targets,
                key=lambda item: item.encode("utf-8"),
            ):
                raise CreationContractError(
                    f"{unit_context} choice next_unit_ids must equal sorted option targets"
                )
        elif unit_type == "ending":
            ending_kind = unit.get("ending_kind")
            if not isinstance(ending_kind, str) or ending_kind not in {
                "success",
                "failure",
                "neutral",
            }:
                raise CreationContractError(f"{unit_context}.ending_kind is unsupported")
            if next_unit_ids:
                raise CreationContractError(
                    f"{unit_context} ending units cannot have outgoing edges"
                )
    unknown_entries = set(entry_unit_ids) - unit_ids
    if unknown_entries:
        raise CreationContractError(
            f"{context}.entry_unit_ids reference unknown units: "
            f"{', '.join(sorted(unknown_entries))}"
        )
    indegree = dict.fromkeys(unit_ids, 0)
    for targets in adjacency.values():
        for target in targets:
            if target in indegree:
                indegree[target] += 1
    undeclared_roots = {
        unit_id
        for unit_id, incoming in indegree.items()
        if incoming == 0 and unit_id not in entry_unit_ids
    }
    if undeclared_roots:
        raise CreationContractError(
            f"{context} contains undeclared zero-indegree roots: "
            f"{', '.join(sorted(undeclared_roots))}"
        )
    reachable: set[str] = set()
    pending = list(reversed(entry_unit_ids))
    while pending:
        unit_id = pending.pop()
        if unit_id in reachable:
            continue
        reachable.add(unit_id)
        pending.extend(reversed([target for target in adjacency[unit_id] if target in unit_ids]))
    unreachable = unit_ids - reachable
    if unreachable:
        raise CreationContractError(
            f"{context} contains unreachable units: {', '.join(sorted(unreachable))}"
        )
    _extensions(value.get("extensions"), f"{context}.extensions", registered_extensions)


def _validate_system_module(
    value: dict[str, Any],
    registered_extensions: Mapping[str, ExtensionValidator],
) -> None:
    context = "system module"
    _exact_keys(value, _SYSTEM_MODULE_FIELDS, context)
    _identifier(value.get("module_id"), f"{context}.module_id")
    _identifier(value.get("project_id"), f"{context}.project_id")
    _non_empty_string(value.get("title"), f"{context}.title")
    systems = value.get("systems")
    if not isinstance(systems, list) or not systems:
        raise CreationContractError(f"{context}.systems must be a non-empty array")
    fields = frozenset(
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
    seen: set[str] = set()
    for index, raw in enumerate(systems):
        system_context = f"{context}.systems/{index}"
        system = _object(raw, system_context)
        _exact_keys(system, fields, system_context)
        system_id = _identifier(system.get("id"), f"{system_context}.id")
        if system_id.casefold() in seen:
            raise CreationContractError(f"{context}.systems contains an NFC/casefold collision")
        seen.add(system_id.casefold())
        system_type = system.get("system_type")
        if not isinstance(system_type, str) or system_type not in _SYSTEM_TYPES:
            raise CreationContractError(f"{system_context}.system_type is unsupported")
        _non_empty_string(system.get("title"), f"{system_context}.title")
        for field in ("precondition_ids", "effect_ids", "event_ids", "asset_binding_ids"):
            _identifier_array(system.get(field), f"{system_context}.{field}")
    _extensions(value.get("extensions"), f"{context}.extensions", registered_extensions)


_LOGIC_VALUE_TYPES = frozenset({"boolean", "integer", "string", "string_array"})
_LOGIC_CONDITION_OPERATORS = frozenset(
    {"constant", "compare", "all", "any", "not", "index_valid", "integer_distance"}
)
_LOGIC_EFFECT_OPERATIONS = frozenset(
    {"set", "swap_array_items", "append_unique", "increment", "reset"}
)
_LOGIC_LIMITS = {
    "state_variables": 128,
    "actions": 128,
    "conditions": 512,
    "effects": 512,
    "rules": 512,
    "goals": 64,
    "failures": 64,
    "endings": 64,
    "events": 256,
    "presentation_hooks": 256,
    "mechanics": 128,
    "extensions": 64,
}
_LOGIC_FORBIDDEN_FIELDS = frozenset(
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
# This exact ECMAScript/Python-compatible grammar is also published as
# logic-module.schema.json#/$defs/runtimeString.pattern. It rejects only
# explicit path/package/metadata forms; prose that merely contains slash usage
# or words such as "provider", "model", or "prompt" remains valid.
LOGIC_RUNTIME_STRING_PATTERN = (
    r"^(?!\s*(?:"
    r"[A-Za-z][A-Za-z0-9+.-]*://|"
    r"[Ff][Ii][Ll][Ee]:[\\/]|"
    r"[A-Za-z]:[\\/]|"
    r"[\\/]{2}|"
    r"/|"
    r"@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:@[^\s/\\]+)?\s*$|"
    r"(?:"
    r"[Pp][Rr][Oo][Vv][Ii][Dd][Ee][Rr](?:_[Ii][Dd])?|"
    r"[Mm][Oo][Dd][Ee][Ll](?:_[Ii][Dd])?|"
    r"[Cc][Rr][Ee][Dd][Ee][Nn][Tt][Ii][Aa][Ll][Ss]?|"
    r"[Pp][Rr][Oo][Mm][Pp][Tt]|"
    r"[Tt][Oo][Kk][Ee][Nn]|"
    r"[Ee][Nn][Dd][Pp][Oo][Ii][Nn][Tt]"
    r")\s*(?:=|:\s*|\s+[Ii][Ss]\s+)|"
    r"\.{1,2}(?:[\\/]|\s*$)"
    r"))"
    r"(?![\s\S]*[\\/]\.{1,2}(?:[\\/]|\s*$))"
    r"(?!\s*[^/\\\r\n]+(?:[\\/][^/\\\r\n]+)+"
    r"\.[A-Za-z0-9][A-Za-z0-9._-]*\s*$)"
    r"[\s\S]*$"
)
_LOGIC_RUNTIME_STRING_RE = re.compile(LOGIC_RUNTIME_STRING_PATTERN)


@dataclass(frozen=True)
class _LogicValueDomain:
    value_type: str
    minimum: int | None = None
    maximum: int | None = None
    allowed_values: frozenset[str] | None = None
    min_items: int | None = None
    max_items: int | None = None


def _logic_identifier(value: object, context: str) -> str:
    identifier = _identifier(value, context)
    if identifier.startswith("wf_internal_"):
        raise CreationContractError(f"{context} uses the reserved wf_internal_ prefix")
    return identifier


def _logic_id_array(
    value: object,
    context: str,
    *,
    allow_empty: bool = True,
) -> list[str]:
    result = _identifier_array(value, context, allow_empty=allow_empty)
    for index, identifier in enumerate(result):
        _logic_identifier(identifier, f"{context}/{index}")
    if len(result) > 64:
        raise CreationContractError(f"{context} exceeds the 64-reference fanout limit")
    return result


def _logic_token_array(
    value: object,
    context: str,
    *,
    allow_empty: bool = True,
) -> list[str]:
    result = _string_array(
        value,
        context,
        allow_empty=allow_empty,
        tokens=True,
        canonical_order=True,
    )
    if len(result) > 64:
        raise CreationContractError(f"{context} exceeds the 64-reference fanout limit")
    return result


def _logic_string_values(
    value: object,
    context: str,
    *,
    allow_empty: bool = False,
    canonical_order: bool = True,
) -> list[str]:
    result = _string_array(
        value,
        context,
        allow_empty=allow_empty,
        canonical_order=canonical_order,
    )
    if len(result) > 256:
        raise CreationContractError(f"{context} exceeds the 256-value limit")
    for index, item in enumerate(result):
        if len(item) > 256:
            raise CreationContractError(f"{context}/{index} exceeds the 256-character limit")
        _logic_runtime_string(item, f"{context}/{index}")
    return result


def _logic_bounded_array(
    value: object,
    context: str,
    *,
    collection: str,
    allow_empty: bool,
) -> list[object]:
    if not isinstance(value, list) or (not allow_empty and not value):
        qualifier = "" if allow_empty else " non-empty"
        raise CreationContractError(f"{context} must be a{qualifier} array")
    limit = _LOGIC_LIMITS[collection]
    if len(value) > limit:
        raise CreationContractError(f"{context} exceeds the {limit}-item limit")
    return value


def _logic_records(
    value: object,
    context: str,
    *,
    collection: str,
    allow_empty: bool,
    canonical_id_order: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    raw_items = _logic_bounded_array(
        value,
        context,
        collection=collection,
        allow_empty=allow_empty,
    )
    records: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    identifiers: list[str] = []
    for index, raw in enumerate(raw_items):
        record = _object(raw, f"{context}/{index}")
        identifier = _logic_identifier(record.get("id"), f"{context}/{index}.id")
        key = identifier.casefold()
        if key in by_id:
            raise CreationContractError(f"{context} contains an NFC/casefold collision")
        by_id[key] = record
        identifiers.append(identifier)
        records.append(record)
    if canonical_id_order and identifiers != sorted(
        identifiers, key=lambda item: item.encode("utf-8")
    ):
        raise CreationContractError(f"{context} must use canonical sorted order")
    return records, by_id


def _logic_runtime_string(value: str, context: str) -> str:
    if _LOGIC_RUNTIME_STRING_RE.fullmatch(value) is None:
        raise CreationContractError(
            f"{context} contains an unsafe runtime string path, package, or metadata marker"
        )
    return value


def _reject_logic_unsafe_content(value: object, *, context: str = "logic module") -> None:
    stack: list[tuple[str, object]] = [(context, value)]
    while stack:
        current_context, current = stack.pop()
        if isinstance(current, dict):
            for key, item in current.items():
                folded = key.casefold().replace("-", "_")
                if folded in _LOGIC_FORBIDDEN_FIELDS:
                    raise CreationContractError(
                        f"{current_context}.{key} is an unsafe runtime or authoring field"
                    )
                stack.append((f"{current_context}.{key}", item))
        elif isinstance(current, list):
            stack.extend((f"{current_context}/{index}", item) for index, item in enumerate(current))


def _preflight_logic_object(value: Mapping[str, object]) -> None:
    """Bound direct logic JSON exactly before traversal or canonical hashing."""

    for collection, limit in _LOGIC_LIMITS.items():
        candidate = value.get(collection)
        if isinstance(candidate, list) and len(candidate) > limit:
            if collection == "extensions":
                raise CreationContractError(
                    "logic module preflight: extensions exceeds the 64-item extension limit"
                )
            raise CreationContractError(
                f"logic module preflight: {collection} exceeds the {limit}-item limit"
            )
    encoded_bytes = 0
    nodes = 0
    active: set[int] = set()
    stack: list[tuple[bool, object, int]] = [(True, value, 1)]

    def add_bytes(amount: int) -> None:
        nonlocal encoded_bytes
        encoded_bytes += amount
        if encoded_bytes > MAX_CREATION_CONTRACT_BYTES:
            raise CreationContractError(
                "logic module preflight encoded JSON size exceeds the "
                f"{MAX_CREATION_CONTRACT_BYTES}-byte limit"
            )

    def add_json_string(text: str) -> None:
        add_bytes(2)
        for character in text:
            codepoint = ord(character)
            if character in {'"', "\\"} or character in {"\b", "\f", "\n", "\r", "\t"}:
                add_bytes(2)
            elif codepoint < 0x20:
                add_bytes(6)
            else:
                try:
                    add_bytes(len(character.encode("utf-8")))
                except UnicodeError as exc:
                    raise CreationContractError(
                        "logic module preflight strings must contain Unicode scalar values"
                    ) from exc

    while stack:
        entering, current, depth = stack.pop()
        if not entering:
            active.remove(id(current))
            continue
        nodes += 1
        if nodes > 100_000:
            raise CreationContractError("logic module preflight exceeds the JSON node limit")
        if depth > MAX_CREATION_JSON_DEPTH:
            raise CreationContractError(
                "logic module preflight JSON depth exceeds the "
                f"{MAX_CREATION_JSON_DEPTH}-level limit"
            )
        if isinstance(current, dict):
            if len(current) > 64:
                raise CreationContractError(
                    "logic module preflight object field count exceeds the 64-field limit"
                )
            identity = id(current)
            if identity in active:
                raise CreationContractError("logic module preflight JSON container cycle")
            active.add(identity)
            stack.append((False, current, depth))
            add_bytes(2 + max(0, len(current) - 1))
            children: list[object] = []
            for key, item in current.items():
                if not isinstance(key, str):
                    raise CreationContractError(
                        "logic module preflight JSON object keys must be strings"
                    )
                add_json_string(key)
                add_bytes(1)
                children.append(item)
            stack.extend((True, item, depth + 1) for item in reversed(children))
        elif isinstance(current, list):
            if len(current) > 4_096:
                raise CreationContractError(
                    "logic module preflight array exceeds the 4096-item generic limit"
                )
            identity = id(current)
            if identity in active:
                raise CreationContractError("logic module preflight JSON container cycle")
            active.add(identity)
            stack.append((False, current, depth))
            add_bytes(2 + max(0, len(current) - 1))
            stack.extend((True, item, depth + 1) for item in reversed(current))
        elif isinstance(current, str):
            add_json_string(current)
        elif current is None:
            add_bytes(4)
        elif isinstance(current, bool):
            add_bytes(4 if current else 5)
        elif isinstance(current, int):
            if not -MAX_SAFE_INTEGER <= current <= MAX_SAFE_INTEGER:
                raise CreationContractError(
                    "logic module preflight JSON integer is outside the "
                    "JavaScript-safe integer range"
                )
            add_bytes(len(str(current)))
        elif isinstance(current, float):
            raise CreationContractError(
                "logic module preflight decimal or exponent JSON numbers are unsupported"
            )
        else:
            raise CreationContractError(
                f"logic module preflight unsupported JSON value type {type(current).__name__}"
            )


def _validate_logic_parameter(value: object, context: str) -> tuple[str, _LogicValueDomain]:
    parameter = _object(value, context)
    parameter_type = parameter.get("type")
    if parameter_type == "boolean":
        fields = frozenset({"id", "type"})
    elif parameter_type == "integer":
        fields = frozenset({"id", "type", "minimum", "maximum"})
    elif parameter_type == "string":
        fields = frozenset({"id", "type", "allowed_values"})
    elif parameter_type == "string_array":
        fields = frozenset({"id", "type", "allowed_values", "min_items", "max_items"})
    else:
        raise CreationContractError(f"{context}.type is unsupported")
    _exact_keys(parameter, fields, context)
    parameter_id = _logic_identifier(parameter.get("id"), f"{context}.id")
    domain = _LogicValueDomain(str(parameter_type))
    if parameter_type == "integer":
        minimum = _integer(
            parameter.get("minimum"),
            f"{context}.minimum",
            minimum=-MAX_SAFE_INTEGER,
        )
        maximum = _integer(
            parameter.get("maximum"),
            f"{context}.maximum",
            minimum=-MAX_SAFE_INTEGER,
        )
        if minimum > maximum:
            raise CreationContractError(f"{context} minimum exceeds maximum")
        domain = _LogicValueDomain("integer", minimum=minimum, maximum=maximum)
    elif parameter_type in {"string", "string_array"}:
        allowed = _logic_string_values(parameter.get("allowed_values"), f"{context}.allowed_values")
        if parameter_type == "string_array":
            minimum = _integer(parameter.get("min_items"), f"{context}.min_items")
            maximum = _integer(parameter.get("max_items"), f"{context}.max_items")
            if (
                maximum > 256
                or minimum > maximum
                or maximum > len(allowed)
                or minimum > len(allowed)
            ):
                raise CreationContractError(
                    f"{context} array domain cannot contain the requested unique item bounds"
                )
            domain = _LogicValueDomain(
                "string_array",
                allowed_values=frozenset(allowed),
                min_items=minimum,
                max_items=maximum,
            )
        else:
            domain = _LogicValueDomain("string", allowed_values=frozenset(allowed))
    return parameter_id, domain


def _validate_logic_state(value: object, context: str) -> tuple[str, _LogicValueDomain]:
    state = _object(value, context)
    state_type = state.get("type")
    common = frozenset({"id", "type", "initial", "mutability", "persistence"})
    if state_type == "boolean":
        fields = common
    elif state_type == "integer":
        fields = common | {"minimum", "maximum"}
    elif state_type == "string":
        fields = common | {"allowed_values"}
    elif state_type == "string_array":
        fields = common | {"allowed_values", "min_items", "max_items"}
    else:
        raise CreationContractError(f"{context}.type is unsupported")
    _exact_keys(state, frozenset(fields), context)
    state_id = _logic_identifier(state.get("id"), f"{context}.id")
    if state.get("mutability") not in {"mutable", "constant"}:
        raise CreationContractError(f"{context}.mutability is unsupported")
    if state.get("persistence") not in {"saved", "transient"}:
        raise CreationContractError(f"{context}.persistence is unsupported")
    initial = state.get("initial")
    domain = _LogicValueDomain(str(state_type))
    if state_type == "boolean":
        if not isinstance(initial, bool):
            raise CreationContractError(f"{context}.initial must be boolean")
    elif state_type == "integer":
        minimum = _integer(
            state.get("minimum"),
            f"{context}.minimum",
            minimum=-MAX_SAFE_INTEGER,
        )
        maximum = _integer(
            state.get("maximum"),
            f"{context}.maximum",
            minimum=-MAX_SAFE_INTEGER,
        )
        parsed_initial = _integer(
            initial,
            f"{context}.initial",
            minimum=-MAX_SAFE_INTEGER,
        )
        if minimum > maximum or not minimum <= parsed_initial <= maximum:
            raise CreationContractError(f"{context} integer bounds do not contain initial")
        domain = _LogicValueDomain("integer", minimum=minimum, maximum=maximum)
    elif state_type == "string":
        allowed = _logic_string_values(
            state.get("allowed_values"),
            f"{context}.allowed_values",
        )
        initial_string = _non_empty_string(initial, f"{context}.initial")
        if initial_string not in allowed:
            raise CreationContractError(f"{context}.initial is not an allowed value")
        domain = _LogicValueDomain("string", allowed_values=frozenset(allowed))
    else:
        allowed = _logic_string_values(
            state.get("allowed_values"),
            f"{context}.allowed_values",
        )
        initial_values = _logic_string_values(
            initial,
            f"{context}.initial",
            allow_empty=True,
            canonical_order=False,
        )
        minimum = _integer(state.get("min_items"), f"{context}.min_items")
        maximum = _integer(state.get("max_items"), f"{context}.max_items")
        if maximum > 256 or minimum > maximum or maximum > len(allowed) or minimum > len(allowed):
            raise CreationContractError(
                f"{context} array domain cannot contain the requested unique item bounds"
            )
        if not minimum <= len(initial_values) <= maximum:
            raise CreationContractError(f"{context} array bounds do not contain initial")
        if not set(initial_values).issubset(allowed):
            raise CreationContractError(f"{context}.initial contains a disallowed value")
        domain = _LogicValueDomain(
            "string_array",
            allowed_values=frozenset(allowed),
            min_items=minimum,
            max_items=maximum,
        )
    return state_id, domain


def _logic_operand_domain(
    value: object,
    context: str,
    *,
    state_domains: Mapping[str, _LogicValueDomain],
    parameter_domains: Mapping[str, Mapping[str, _LogicValueDomain]],
    action_scope: str | None,
) -> _LogicValueDomain:
    operand = _object(value, context)
    kind = operand.get("kind")
    if kind == "literal":
        _exact_keys(operand, frozenset({"kind", "value_type", "value"}), context)
        value_type = operand.get("value_type")
        if value_type not in _LOGIC_VALUE_TYPES:
            raise CreationContractError(f"{context}.value_type is unsupported")
        literal = operand.get("value")
        if value_type == "boolean" and not isinstance(literal, bool):
            raise CreationContractError(f"{context}.value must be boolean")
        if value_type == "integer":
            parsed = _integer(literal, f"{context}.value", minimum=-MAX_SAFE_INTEGER)
            return _LogicValueDomain("integer", minimum=parsed, maximum=parsed)
        if value_type == "string":
            string = _non_empty_string(literal, f"{context}.value")
            if len(string) > 256:
                raise CreationContractError(f"{context}.value exceeds 256 characters")
            _logic_runtime_string(string, f"{context}.value")
            return _LogicValueDomain("string", allowed_values=frozenset({string}))
        if value_type == "string_array":
            values = _logic_string_values(
                literal,
                f"{context}.value",
                allow_empty=True,
                canonical_order=False,
            )
            return _LogicValueDomain(
                "string_array",
                allowed_values=frozenset(values),
                min_items=len(values),
                max_items=len(values),
            )
        return _LogicValueDomain("boolean")
    if kind == "state":
        _exact_keys(operand, frozenset({"kind", "state_id"}), context)
        state_id = _logic_identifier(operand.get("state_id"), f"{context}.state_id")
        if state_id.casefold() not in state_domains:
            raise CreationContractError(f"{context} references unknown state {state_id}")
        return state_domains[state_id.casefold()]
    if kind == "parameter":
        _exact_keys(
            operand,
            frozenset({"kind", "action_id", "parameter_id"}),
            context,
        )
        action_id = _logic_identifier(operand.get("action_id"), f"{context}.action_id")
        parameter_id = _logic_identifier(
            operand.get("parameter_id"),
            f"{context}.parameter_id",
        )
        if action_scope is None or action_id != action_scope:
            raise CreationContractError(
                f"{context} parameter action does not match its condition/effect scope"
            )
        action_parameters = parameter_domains.get(action_id.casefold())
        if action_parameters is None or parameter_id.casefold() not in action_parameters:
            raise CreationContractError(f"{context} references an unknown action parameter")
        return action_parameters[parameter_id.casefold()]
    raise CreationContractError(f"{context}.kind is an unsupported operand discriminator")


def _require_logic_type(actual: _LogicValueDomain | str, expected: str, context: str) -> None:
    value_type = actual.value_type if isinstance(actual, _LogicValueDomain) else actual
    if value_type != expected:
        raise CreationContractError(f"{context} requires {expected}, got {value_type}")


def _require_logic_domain_subset(
    source: _LogicValueDomain,
    target: _LogicValueDomain,
    context: str,
) -> None:
    _require_logic_type(source, target.value_type, context)
    if target.value_type == "integer":
        assert source.minimum is not None and source.maximum is not None
        assert target.minimum is not None and target.maximum is not None
        if source.minimum < target.minimum or source.maximum > target.maximum:
            raise CreationContractError(f"{context} operand domain is not a target-domain subset")
    elif target.value_type in {"string", "string_array"}:
        assert source.allowed_values is not None and target.allowed_values is not None
        if not source.allowed_values.issubset(target.allowed_values):
            raise CreationContractError(f"{context} contains values outside the allowed domain")
        if target.value_type == "string_array":
            assert source.min_items is not None and source.max_items is not None
            assert target.min_items is not None and target.max_items is not None
            if source.min_items < target.min_items or source.max_items > target.max_items:
                raise CreationContractError(
                    f"{context} array cardinality domain is not a target-domain subset"
                )


def _validate_logic_action(
    value: Mapping[str, Any],
    context: str,
) -> tuple[str, dict[str, _LogicValueDomain]]:
    _exact_keys(
        value,
        frozenset(
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
        context,
    )
    action_id = _logic_identifier(value.get("id"), f"{context}.id")
    _logic_identifier(value.get("core_verb_id"), f"{context}.core_verb_id")
    parameters = value.get("parameters")
    if not isinstance(parameters, list):
        raise CreationContractError(f"{context}.parameters must be an array")
    if len(parameters) > 16:
        raise CreationContractError(f"{context}.parameters exceeds the 16-item limit")
    parameter_domains: dict[str, _LogicValueDomain] = {}
    parameter_ids: list[str] = []
    for index, raw in enumerate(parameters):
        parameter_id, parameter_domain = _validate_logic_parameter(
            raw,
            f"{context}.parameters/{index}",
        )
        key = parameter_id.casefold()
        if key in parameter_domains:
            raise CreationContractError(f"{context}.parameters contains an NFC/casefold collision")
        parameter_domains[key] = parameter_domain
        parameter_ids.append(parameter_id)
    if parameter_ids != sorted(parameter_ids, key=lambda item: item.encode("utf-8")):
        raise CreationContractError(f"{context}.parameters must use canonical sorted order")

    source_bindings = value.get("source_bindings")
    if not isinstance(source_bindings, list) or not source_bindings:
        raise CreationContractError(f"{context}.source_bindings must be a non-empty array")
    if len(source_bindings) > 16:
        raise CreationContractError(f"{context}.source_bindings exceeds the 16-item limit")
    binding_keys: list[tuple[str, str, str]] = []
    seen_bindings: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(source_bindings):
        binding_context = f"{context}.source_bindings/{index}"
        binding = _object(raw, binding_context)
        kind = binding.get("kind")
        if kind == "narrative_option":
            _exact_keys(
                binding,
                frozenset({"kind", "source_id", "option_id"}),
                binding_context,
            )
            option_id = _logic_identifier(
                binding.get("option_id"),
                f"{binding_context}.option_id",
            )
        elif kind in {"activity", "system"}:
            _exact_keys(binding, frozenset({"kind", "source_id"}), binding_context)
            option_id = ""
        elif kind == "narrative_unit":
            raise CreationContractError(
                f"{binding_context}.kind narrative_unit is unsupported until "
                "unit-level event and transition semantics are versioned"
            )
        else:
            raise CreationContractError(f"{binding_context}.kind is unsupported")
        source_id = _logic_identifier(binding.get("source_id"), f"{binding_context}.source_id")
        key = (str(kind), source_id, option_id)
        if key in seen_bindings:
            raise CreationContractError(f"{context}.source_bindings contains duplicates")
        seen_bindings.add(key)
        binding_keys.append(key)
    if binding_keys != sorted(
        binding_keys,
        key=lambda item: tuple(part.encode("utf-8") for part in item),
    ):
        raise CreationContractError(f"{context}.source_bindings must use canonical sorted order")
    _logic_id_array(value.get("rule_ids"), f"{context}.rule_ids", allow_empty=False)
    _logic_id_array(
        value.get("presentation_hook_ids"),
        f"{context}.presentation_hook_ids",
    )
    _logic_token_array(
        value.get("required_feature_ids"),
        f"{context}.required_feature_ids",
        allow_empty=True,
    )
    return action_id, parameter_domains


def _validate_logic_condition(
    condition: Mapping[str, Any],
    context: str,
    *,
    state_domains: Mapping[str, _LogicValueDomain],
    parameter_domains: Mapping[str, Mapping[str, _LogicValueDomain]],
) -> tuple[str | None, tuple[str, ...]]:
    operator = condition.get("operator")
    if operator not in _LOGIC_CONDITION_OPERATORS:
        raise CreationContractError(f"{context}.operator is unsupported")
    action_id_raw = condition.get("action_id")
    action_id = (
        None if action_id_raw is None else _logic_identifier(action_id_raw, f"{context}.action_id")
    )
    children: tuple[str, ...] = ()
    if operator == "constant":
        _exact_keys(
            condition,
            frozenset({"id", "action_id", "operator", "value"}),
            context,
        )
        if not isinstance(condition.get("value"), bool):
            raise CreationContractError(f"{context}.value must be boolean")
    elif operator == "compare":
        _exact_keys(
            condition,
            frozenset({"id", "action_id", "operator", "comparison", "left", "right"}),
            context,
        )
        comparison = condition.get("comparison")
        if comparison not in {
            "equal",
            "not_equal",
            "less_than",
            "less_or_equal",
            "greater_than",
            "greater_or_equal",
        }:
            raise CreationContractError(f"{context}.comparison is unsupported")
        left_domain = _logic_operand_domain(
            condition.get("left"),
            f"{context}.left",
            state_domains=state_domains,
            parameter_domains=parameter_domains,
            action_scope=action_id,
        )
        right_domain = _logic_operand_domain(
            condition.get("right"),
            f"{context}.right",
            state_domains=state_domains,
            parameter_domains=parameter_domains,
            action_scope=action_id,
        )
        if left_domain.value_type != right_domain.value_type:
            raise CreationContractError(f"{context} compare operand types do not match")
        if comparison not in {"equal", "not_equal"}:
            _require_logic_type(left_domain, "integer", f"{context}.comparison")
    elif operator in {"all", "any"}:
        _exact_keys(
            condition,
            frozenset({"id", "action_id", "operator", "condition_ids"}),
            context,
        )
        children = tuple(
            _logic_id_array(
                condition.get("condition_ids"),
                f"{context}.condition_ids",
                allow_empty=False,
            )
        )
    elif operator == "not":
        _exact_keys(
            condition,
            frozenset({"id", "action_id", "operator", "condition_id"}),
            context,
        )
        children = (
            _logic_identifier(
                condition.get("condition_id"),
                f"{context}.condition_id",
            ),
        )
    elif operator == "index_valid":
        _exact_keys(
            condition,
            frozenset({"id", "action_id", "operator", "array_state_id", "index"}),
            context,
        )
        if action_id is None:
            raise CreationContractError(f"{context} index_valid requires an action scope")
        state_id = _logic_identifier(
            condition.get("array_state_id"),
            f"{context}.array_state_id",
        )
        state_domain = state_domains.get(state_id.casefold())
        if state_domain is None:
            raise CreationContractError(f"{context} references unknown state {state_id}")
        _require_logic_type(state_domain, "string_array", f"{context}.array_state_id")
        index_domain = _logic_operand_domain(
            condition.get("index"),
            f"{context}.index",
            state_domains=state_domains,
            parameter_domains=parameter_domains,
            action_scope=action_id,
        )
        _require_logic_type(index_domain, "integer", f"{context}.index")
    else:
        _exact_keys(
            condition,
            frozenset({"id", "action_id", "operator", "left", "right", "distance"}),
            context,
        )
        if action_id is None:
            raise CreationContractError(f"{context} integer_distance requires an action scope")
        for field in ("left", "right"):
            operand_domain = _logic_operand_domain(
                condition.get(field),
                f"{context}.{field}",
                state_domains=state_domains,
                parameter_domains=parameter_domains,
                action_scope=action_id,
            )
            _require_logic_type(operand_domain, "integer", f"{context}.{field}")
        _integer(condition.get("distance"), f"{context}.distance", minimum=0)
    return action_id, children


def _validate_logic_effect(
    effect: Mapping[str, Any],
    context: str,
    *,
    states: Mapping[str, Mapping[str, Any]],
    state_domains: Mapping[str, _LogicValueDomain],
    parameter_domains: Mapping[str, Mapping[str, _LogicValueDomain]],
) -> str:
    operation = effect.get("operation")
    if operation not in _LOGIC_EFFECT_OPERATIONS:
        raise CreationContractError(f"{context}.operation is unsupported")
    action_id = _logic_identifier(effect.get("action_id"), f"{context}.action_id")
    if effect.get("invalid_transition_policy") != "reject_transition":
        raise CreationContractError(
            f"{context}.invalid_transition_policy must be reject_transition"
        )
    state_field = (
        "array_state_id"
        if operation
        in {
            "swap_array_items",
            "append_unique",
        }
        else "state_id"
    )
    state_id = _logic_identifier(effect.get(state_field), f"{context}.{state_field}")
    state = states.get(state_id.casefold())
    if state is None:
        raise CreationContractError(f"{context} references unknown state {state_id}")
    if state["mutability"] != "mutable":
        raise CreationContractError(f"{context} cannot mutate constant state {state_id}")
    state_domain = state_domains[state_id.casefold()]
    common_fields = {"id", "action_id", "operation", "invalid_transition_policy"}
    if operation == "set":
        _exact_keys(
            effect,
            frozenset(common_fields | {"state_id", "value"}),
            context,
        )
        operand_domain = _logic_operand_domain(
            effect.get("value"),
            f"{context}.value",
            state_domains=state_domains,
            parameter_domains=parameter_domains,
            action_scope=action_id,
        )
        _require_logic_domain_subset(operand_domain, state_domain, f"{context}.value")
    elif operation == "swap_array_items":
        _exact_keys(
            effect,
            frozenset(
                {
                    *common_fields,
                    "array_state_id",
                    "first_index",
                    "second_index",
                }
            ),
            context,
        )
        _require_logic_type(state_domain, "string_array", f"{context}.array_state_id")
        for field in ("first_index", "second_index"):
            operand_domain = _logic_operand_domain(
                effect.get(field),
                f"{context}.{field}",
                state_domains=state_domains,
                parameter_domains=parameter_domains,
                action_scope=action_id,
            )
            _require_logic_type(operand_domain, "integer", f"{context}.{field}")
    elif operation == "append_unique":
        _exact_keys(
            effect,
            frozenset(common_fields | {"array_state_id", "value"}),
            context,
        )
        _require_logic_type(state_domain, "string_array", f"{context}.array_state_id")
        operand_domain = _logic_operand_domain(
            effect.get("value"),
            f"{context}.value",
            state_domains=state_domains,
            parameter_domains=parameter_domains,
            action_scope=action_id,
        )
        _require_logic_type(operand_domain, "string", f"{context}.value")
        assert state_domain.allowed_values is not None
        assert operand_domain.allowed_values is not None
        if not operand_domain.allowed_values.issubset(state_domain.allowed_values):
            raise CreationContractError(f"{context}.value is outside the allowed array domain")
    elif operation == "increment":
        _exact_keys(
            effect,
            frozenset(common_fields | {"state_id", "amount"}),
            context,
        )
        _require_logic_type(state_domain, "integer", f"{context}.state_id")
        operand_domain = _logic_operand_domain(
            effect.get("amount"),
            f"{context}.amount",
            state_domains=state_domains,
            parameter_domains=parameter_domains,
            action_scope=action_id,
        )
        _require_logic_type(operand_domain, "integer", f"{context}.amount")
    else:
        _exact_keys(
            effect,
            frozenset(common_fields | {"state_id"}),
            context,
        )
    return action_id


def _logic_reference(
    identifier: object,
    registry: Mapping[str, object],
    context: str,
) -> str:
    parsed = _logic_identifier(identifier, context)
    if parsed.casefold() not in registry:
        raise CreationContractError(f"{context} references unknown ID {parsed}")
    return parsed


def _logic_references(
    identifiers: object,
    registry: Mapping[str, object],
    context: str,
    *,
    allow_empty: bool = True,
) -> list[str]:
    parsed = _logic_id_array(identifiers, context, allow_empty=allow_empty)
    for index, identifier in enumerate(parsed):
        _logic_reference(identifier, registry, f"{context}/{index}")
    return parsed


def _validate_condition_graph(
    conditions: Mapping[str, Mapping[str, Any]],
    scopes: Mapping[str, str | None],
    children: Mapping[str, tuple[str, ...]],
) -> None:
    adjacency: dict[str, tuple[str, ...]] = {}
    for condition_key, references in children.items():
        resolved: list[str] = []
        for reference in references:
            reference_key = reference.casefold()
            if reference_key not in conditions:
                raise CreationContractError(
                    f"logic condition {conditions[condition_key]['id']} references unknown "
                    f"condition {reference}"
                )
            if scopes[reference_key] != scopes[condition_key]:
                raise CreationContractError(
                    f"logic condition {conditions[condition_key]['id']} crosses action scope"
                )
            resolved.append(reference_key)
        adjacency[condition_key] = tuple(resolved)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(condition_key: str, trail: tuple[str, ...]) -> None:
        if condition_key in visiting:
            cycle = " -> ".join((*trail, conditions[condition_key]["id"]))
            raise CreationContractError(f"logic condition cycle detected: {cycle}")
        if condition_key in visited:
            return
        visiting.add(condition_key)
        for child in adjacency.get(condition_key, ()):
            visit(child, (*trail, conditions[condition_key]["id"]))
        visiting.remove(condition_key)
        visited.add(condition_key)

    for key in conditions:
        visit(key, ())


def _logic_condition_closure(
    condition_ids: Sequence[str],
    children: Mapping[str, tuple[str, ...]],
) -> set[str]:
    closure: set[str] = set()
    pending = [item.casefold() for item in condition_ids]
    while pending:
        key = pending.pop()
        if key in closure:
            continue
        closure.add(key)
        pending.extend(item.casefold() for item in children.get(key, ()))
    return closure


def _logic_operand_state_ids(value: object) -> set[str]:
    if not isinstance(value, Mapping) or value.get("kind") != "state":
        return set()
    state_id = value.get("state_id")
    return {str(state_id).casefold()} if isinstance(state_id, str) else set()


def _logic_operand_parameter_ids(value: object) -> set[tuple[str, str]]:
    if not isinstance(value, Mapping) or value.get("kind") != "parameter":
        return set()
    action_id = value.get("action_id")
    parameter_id = value.get("parameter_id")
    if not isinstance(action_id, str) or not isinstance(parameter_id, str):
        return set()
    return {(action_id.casefold(), parameter_id.casefold())}


def _logic_condition_state_ids(condition: Mapping[str, Any]) -> set[str]:
    operator = condition.get("operator")
    if operator == "compare":
        return _logic_operand_state_ids(condition.get("left")) | _logic_operand_state_ids(
            condition.get("right")
        )
    if operator == "index_valid":
        state_id = str(condition.get("array_state_id")).casefold()
        return {state_id} | _logic_operand_state_ids(condition.get("index"))
    if operator == "integer_distance":
        return _logic_operand_state_ids(condition.get("left")) | _logic_operand_state_ids(
            condition.get("right")
        )
    return set()


def _logic_condition_parameter_ids(
    condition: Mapping[str, Any],
) -> set[tuple[str, str]]:
    operator = condition.get("operator")
    if operator == "compare":
        return _logic_operand_parameter_ids(condition.get("left")) | _logic_operand_parameter_ids(
            condition.get("right")
        )
    if operator == "index_valid":
        return _logic_operand_parameter_ids(condition.get("index"))
    if operator == "integer_distance":
        return _logic_operand_parameter_ids(condition.get("left")) | _logic_operand_parameter_ids(
            condition.get("right")
        )
    return set()


def _logic_effect_state_ids(effect: Mapping[str, Any]) -> set[str]:
    state_field = (
        "array_state_id"
        if effect.get("operation") in {"swap_array_items", "append_unique"}
        else "state_id"
    )
    result = {str(effect.get(state_field)).casefold()}
    for field in ("value", "amount", "first_index", "second_index"):
        result.update(_logic_operand_state_ids(effect.get(field)))
    return result


def _logic_effect_parameter_ids(effect: Mapping[str, Any]) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for field in ("value", "amount", "first_index", "second_index"):
        result.update(_logic_operand_parameter_ids(effect.get(field)))
    return result


def _logic_same_operand(left: object, right: object) -> bool:
    return isinstance(left, Mapping) and isinstance(right, Mapping) and dict(left) == dict(right)


def _validate_swap_guards(
    *,
    rule: Mapping[str, Any],
    rule_condition_keys: set[str],
    conditions: Mapping[str, Mapping[str, Any]],
    swap_effects: Sequence[Mapping[str, Any]],
) -> None:
    available = [conditions[key] for key in rule_condition_keys]
    for effect in swap_effects:
        array_state_id = effect["array_state_id"]
        first = effect["first_index"]
        second = effect["second_index"]
        for label, operand in (("first", first), ("second", second)):
            guarded = any(
                condition.get("operator") == "index_valid"
                and condition.get("array_state_id") == array_state_id
                and _logic_same_operand(condition.get("index"), operand)
                for condition in available
            )
            if not guarded:
                raise CreationContractError(
                    f"logic rule {rule['id']} requires an exact {label} index_valid guard"
                )
        adjacent = any(
            condition.get("operator") == "integer_distance"
            and condition.get("distance") == 1
            and (
                (
                    _logic_same_operand(condition.get("left"), first)
                    and _logic_same_operand(condition.get("right"), second)
                )
                or (
                    _logic_same_operand(condition.get("left"), second)
                    and _logic_same_operand(condition.get("right"), first)
                )
            )
            for condition in available
        )
        if not adjacent:
            raise CreationContractError(
                f"logic rule {rule['id']} requires an exact adjacency distance guard"
            )


def _validate_logic_module(
    value: dict[str, Any],
    registered_extensions: Mapping[str, ExtensionValidator],
) -> None:
    context = "logic module"
    _reject_logic_unsafe_content(value)
    _exact_keys(value, _LOGIC_MODULE_FIELDS, context)
    _logic_identifier(value.get("module_id"), f"{context}.module_id")
    _logic_identifier(value.get("project_id"), f"{context}.project_id")
    title = _non_empty_string(value.get("title"), f"{context}.title")
    if len(title) > 512:
        raise CreationContractError(f"{context}.title exceeds 512 characters")
    _logic_runtime_string(title, f"{context}.title")

    state_records, states = _logic_records(
        value.get("state_variables"),
        f"{context}.state_variables",
        collection="state_variables",
        allow_empty=False,
    )
    state_domains: dict[str, _LogicValueDomain] = {}
    for index, state in enumerate(state_records):
        state_id, state_domain = _validate_logic_state(
            state,
            f"{context}.state_variables/{index}",
        )
        state_domains[state_id.casefold()] = state_domain

    action_records, actions = _logic_records(
        value.get("actions"),
        f"{context}.actions",
        collection="actions",
        allow_empty=False,
    )
    parameter_domains: dict[str, dict[str, _LogicValueDomain]] = {}
    for index, action in enumerate(action_records):
        action_id, parameters = _validate_logic_action(
            action,
            f"{context}.actions/{index}",
        )
        parameter_domains[action_id.casefold()] = parameters

    condition_records, conditions = _logic_records(
        value.get("conditions"),
        f"{context}.conditions",
        collection="conditions",
        allow_empty=False,
    )
    condition_scopes: dict[str, str | None] = {}
    condition_children: dict[str, tuple[str, ...]] = {}
    for index, condition in enumerate(condition_records):
        action_scope, children = _validate_logic_condition(
            condition,
            f"{context}.conditions/{index}",
            state_domains=state_domains,
            parameter_domains=parameter_domains,
        )
        if action_scope is not None:
            _logic_reference(
                action_scope,
                actions,
                f"{context}.conditions/{index}.action_id",
            )
        condition_scopes[str(condition["id"]).casefold()] = action_scope
        condition_children[str(condition["id"]).casefold()] = children
    _validate_condition_graph(conditions, condition_scopes, condition_children)

    effect_records, effects = _logic_records(
        value.get("effects"),
        f"{context}.effects",
        collection="effects",
        allow_empty=False,
    )
    effect_actions: dict[str, str] = {}
    for index, effect in enumerate(effect_records):
        action_id = _validate_logic_effect(
            effect,
            f"{context}.effects/{index}",
            states=states,
            state_domains=state_domains,
            parameter_domains=parameter_domains,
        )
        _logic_reference(
            action_id,
            actions,
            f"{context}.effects/{index}.action_id",
        )
        effect_actions[str(effect["id"]).casefold()] = action_id

    event_records, events = _logic_records(
        value.get("events"),
        f"{context}.events",
        collection="events",
        allow_empty=False,
    )
    for index, event in enumerate(event_records):
        _exact_keys(event, frozenset({"id"}), f"{context}.events/{index}")

    hook_records, hooks = _logic_records(
        value.get("presentation_hooks"),
        f"{context}.presentation_hooks",
        collection="presentation_hooks",
        allow_empty=False,
    )
    asset_binding_ids: dict[str, str] = {}
    for index, hook in enumerate(hook_records):
        hook_context = f"{context}.presentation_hooks/{index}"
        _exact_keys(
            hook,
            frozenset({"id", "kind", "asset_binding_ids"}),
            hook_context,
        )
        if hook.get("kind") not in {"board", "text", "feedback", "ending"}:
            raise CreationContractError(f"{hook_context}.kind is unsupported")
        bindings = _logic_id_array(
            hook.get("asset_binding_ids"),
            f"{hook_context}.asset_binding_ids",
            allow_empty=True,
        )
        for binding in bindings:
            asset_binding_ids.setdefault(binding.casefold(), binding)

    rule_records, rules = _logic_records(
        value.get("rules"),
        f"{context}.rules",
        collection="rules",
        allow_empty=False,
        canonical_id_order=False,
    )
    rule_orders: set[int] = set()
    ordered_rules: list[tuple[int, str]] = []
    rule_actions: dict[str, str] = {}
    rule_effects: dict[str, tuple[str, ...]] = {}
    rule_conditions: dict[str, tuple[str, ...]] = {}
    rule_events: dict[str, tuple[str, ...]] = {}
    for index, rule in enumerate(rule_records):
        rule_context = f"{context}.rules/{index}"
        _exact_keys(
            rule,
            frozenset(
                {
                    "id",
                    "action_id",
                    "order",
                    "condition_ids",
                    "effect_ids",
                    "event_ids",
                }
            ),
            rule_context,
        )
        action_id = _logic_reference(
            rule.get("action_id"),
            actions,
            f"{rule_context}.action_id",
        )
        order = _integer(rule.get("order"), f"{rule_context}.order")
        if order in rule_orders:
            raise CreationContractError(f"{context} rule order {order} is ambiguous")
        rule_orders.add(order)
        ordered_rules.append((order, str(rule["id"])))
        checked_conditions = _logic_references(
            rule.get("condition_ids"),
            conditions,
            f"{rule_context}.condition_ids",
        )
        for condition_id in checked_conditions:
            if condition_scopes[condition_id.casefold()] != action_id:
                raise CreationContractError(
                    f"{rule_context} condition does not belong to action {action_id}"
                )
        checked_effects = _logic_references(
            rule.get("effect_ids"),
            effects,
            f"{rule_context}.effect_ids",
            allow_empty=False,
        )
        for effect_id in checked_effects:
            if effect_actions[effect_id.casefold()] != action_id:
                raise CreationContractError(
                    f"{rule_context} effect does not belong to action {action_id}"
                )
        checked_events = _logic_references(
            rule.get("event_ids"),
            events,
            f"{rule_context}.event_ids",
        )
        rule_key = str(rule["id"]).casefold()
        rule_actions[rule_key] = action_id
        rule_conditions[rule_key] = tuple(checked_conditions)
        rule_effects[rule_key] = tuple(checked_effects)
        rule_events[rule_key] = tuple(checked_events)
    if [item[0] for item in ordered_rules] != sorted(item[0] for item in ordered_rules):
        raise CreationContractError(f"{context}.rules must use ascending semantic rule order")

    action_closures: dict[str, dict[str, set[str]]] = {}
    for index, action in enumerate(action_records):
        action_context = f"{context}.actions/{index}"
        action_id = str(action["id"])
        action_rule_ids = _logic_references(
            action.get("rule_ids"),
            rules,
            f"{action_context}.rule_ids",
            allow_empty=False,
        )
        for rule_id in action_rule_ids:
            if rule_actions[rule_id.casefold()] != action_id:
                raise CreationContractError(
                    f"{action_context} rule {rule_id} belongs to another action"
                )
        expected_rule_keys = {key for key, owner in rule_actions.items() if owner == action_id}
        if {item.casefold() for item in action_rule_ids} != expected_rule_keys:
            raise CreationContractError(
                f"{action_context}.rule_ids must equal the exact rule closure; orphan rule found"
            )
        action_hook_ids = _logic_references(
            action.get("presentation_hook_ids"),
            hooks,
            f"{action_context}.presentation_hook_ids",
        )
        action_rule_keys = {item.casefold() for item in action_rule_ids}
        direct_condition_ids = {
            item.casefold() for key in action_rule_keys for item in rule_conditions[key]
        }
        condition_keys = _logic_condition_closure(tuple(direct_condition_ids), condition_children)
        effect_keys = {item.casefold() for key in action_rule_keys for item in rule_effects[key]}
        event_keys = {item.casefold() for key in action_rule_keys for item in rule_events[key]}
        state_keys = {
            state_key
            for condition_key in condition_keys
            for state_key in _logic_condition_state_ids(conditions[condition_key])
        } | {
            state_key
            for effect_key in effect_keys
            for state_key in _logic_effect_state_ids(effects[effect_key])
        }
        hook_keys = {item.casefold() for item in action_hook_ids}
        binding_keys = {
            binding.casefold()
            for hook_key in hook_keys
            for binding in hooks[hook_key]["asset_binding_ids"]
        }
        feature_keys = {item.casefold() for item in action["required_feature_ids"]}
        action_closures[action_id.casefold()] = {
            "rule_ids": action_rule_keys,
            "condition_ids": condition_keys,
            "effect_ids": effect_keys,
            "event_ids": event_keys,
            "authoritative_state_ids": state_keys,
            "presentation_hook_ids": hook_keys,
            "asset_binding_ids": binding_keys,
            "required_feature_ids": feature_keys,
        }
        for rule_key in action_rule_keys:
            swap_effects = [
                effects[item.casefold()]
                for item in rule_effects[rule_key]
                if effects[item.casefold()]["operation"] == "swap_array_items"
            ]
            if swap_effects:
                _validate_swap_guards(
                    rule=rules[rule_key],
                    rule_condition_keys=_logic_condition_closure(
                        rule_conditions[rule_key], condition_children
                    ),
                    conditions=conditions,
                    swap_effects=swap_effects,
                )

    ending_records, endings = _logic_records(
        value.get("endings"),
        f"{context}.endings",
        collection="endings",
        allow_empty=False,
    )
    ending_kinds: dict[str, str] = {}
    ending_condition_ids: dict[str, set[str]] = {}
    for index, ending in enumerate(ending_records):
        ending_context = f"{context}.endings/{index}"
        _exact_keys(
            ending,
            frozenset(
                {
                    "id",
                    "kind",
                    "condition_ids",
                    "event_ids",
                    "presentation_hook_ids",
                }
            ),
            ending_context,
        )
        kind = ending.get("kind")
        if kind not in {"success", "failure", "neutral"}:
            raise CreationContractError(f"{ending_context}.kind is unsupported")
        ending_kinds[str(ending["id"]).casefold()] = str(kind)
        ending_conditions = _logic_references(
            ending.get("condition_ids"),
            conditions,
            f"{ending_context}.condition_ids",
            allow_empty=False,
        )
        if any(condition_scopes[item.casefold()] is not None for item in ending_conditions):
            raise CreationContractError(
                f"{ending_context} requires parameter-free state conditions"
            )
        ending_condition_ids[str(ending["id"]).casefold()] = {
            item.casefold() for item in ending_conditions
        }
        _logic_references(
            ending.get("event_ids"),
            events,
            f"{ending_context}.event_ids",
        )
        _logic_references(
            ending.get("presentation_hook_ids"),
            hooks,
            f"{ending_context}.presentation_hook_ids",
            allow_empty=False,
        )

    goal_records, _goals = _logic_records(
        value.get("goals"),
        f"{context}.goals",
        collection="goals",
        allow_empty=False,
    )
    for index, goal in enumerate(goal_records):
        goal_context = f"{context}.goals/{index}"
        _exact_keys(
            goal,
            frozenset({"id", "condition_ids", "success_ending_id"}),
            goal_context,
        )
        goal_conditions = _logic_references(
            goal.get("condition_ids"),
            conditions,
            f"{goal_context}.condition_ids",
            allow_empty=False,
        )
        if any(condition_scopes[item.casefold()] is not None for item in goal_conditions):
            raise CreationContractError(f"{goal_context} requires parameter-free state conditions")
        ending_id = _logic_reference(
            goal.get("success_ending_id"),
            endings,
            f"{goal_context}.success_ending_id",
        )
        if ending_kinds[ending_id.casefold()] != "success":
            raise CreationContractError(
                f"{goal_context}.success_ending_id must reference a success ending"
            )
        if {item.casefold() for item in goal_conditions} != ending_condition_ids[
            ending_id.casefold()
        ]:
            raise CreationContractError(
                f"{goal_context} goal and success ending conditions must be identical"
            )

    failure_records, _failures = _logic_records(
        value.get("failures"),
        f"{context}.failures",
        collection="failures",
        allow_empty=True,
    )
    for index, failure in enumerate(failure_records):
        failure_context = f"{context}.failures/{index}"
        _exact_keys(
            failure,
            frozenset({"id", "condition_ids", "recovery_action_ids"}),
            failure_context,
        )
        failure_conditions = _logic_references(
            failure.get("condition_ids"),
            conditions,
            f"{failure_context}.condition_ids",
            allow_empty=False,
        )
        if any(condition_scopes[item.casefold()] is not None for item in failure_conditions):
            raise CreationContractError(
                f"{failure_context} requires parameter-free state conditions"
            )
        _logic_references(
            failure.get("recovery_action_ids"),
            actions,
            f"{failure_context}.recovery_action_ids",
            allow_empty=False,
        )

    mechanic_records, _mechanics = _logic_records(
        value.get("mechanics"),
        f"{context}.mechanics",
        collection="mechanics",
        allow_empty=False,
    )
    actions_with_mechanics: set[str] = set()
    for index, mechanic in enumerate(mechanic_records):
        mechanic_context = f"{context}.mechanics/{index}"
        _exact_keys(
            mechanic,
            frozenset(
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
            mechanic_context,
        )
        core_verb_id = _logic_identifier(
            mechanic.get("core_verb_id"),
            f"{mechanic_context}.core_verb_id",
        )
        action_id = _logic_reference(
            mechanic.get("action_id"),
            actions,
            f"{mechanic_context}.action_id",
        )
        action = actions[action_id.casefold()]
        if action["core_verb_id"] != core_verb_id:
            raise CreationContractError(f"{mechanic_context} core verb does not match its action")
        action_key = action_id.casefold()
        if action_key in actions_with_mechanics:
            raise CreationContractError(
                f"{context} requires exactly one mechanic for action {action_id}"
            )
        actions_with_mechanics.add(action_key)
        authoritative_states = _logic_references(
            mechanic.get("authoritative_state_ids"),
            states,
            f"{mechanic_context}.authoritative_state_ids",
            allow_empty=False,
        )
        mechanic_conditions = _logic_references(
            mechanic.get("condition_ids"),
            conditions,
            f"{mechanic_context}.condition_ids",
        )
        if any(
            condition_scopes[item.casefold()] not in {None, action_id}
            for item in mechanic_conditions
        ):
            raise CreationContractError(
                f"{mechanic_context} references a condition from another action"
            )
        mechanic_rules = _logic_references(
            mechanic.get("rule_ids"),
            rules,
            f"{mechanic_context}.rule_ids",
            allow_empty=False,
        )
        if any(rule_actions[item.casefold()] != action_id for item in mechanic_rules):
            raise CreationContractError(f"{mechanic_context} references a rule from another action")
        mechanic_effects = _logic_references(
            mechanic.get("effect_ids"),
            effects,
            f"{mechanic_context}.effect_ids",
            allow_empty=False,
        )
        if any(effect_actions[item.casefold()] != action_id for item in mechanic_effects):
            raise CreationContractError(
                f"{mechanic_context} references an effect from another action"
            )
        mechanic_events = _logic_references(
            mechanic.get("event_ids"),
            events,
            f"{mechanic_context}.event_ids",
        )
        mechanic_hooks = _logic_references(
            mechanic.get("presentation_hook_ids"),
            hooks,
            f"{mechanic_context}.presentation_hook_ids",
            allow_empty=False,
        )
        binding_ids = _logic_id_array(
            mechanic.get("asset_binding_ids"),
            f"{mechanic_context}.asset_binding_ids",
            allow_empty=True,
        )
        for binding_id in binding_ids:
            if binding_id.casefold() not in asset_binding_ids:
                raise CreationContractError(
                    f"{mechanic_context}.asset_binding_ids references unknown ID {binding_id}"
                )
        mechanic_features = _logic_token_array(
            mechanic.get("required_feature_ids"),
            f"{mechanic_context}.required_feature_ids",
            allow_empty=True,
        )
        actual_closure = {
            "authoritative_state_ids": {item.casefold() for item in authoritative_states},
            "condition_ids": {item.casefold() for item in mechanic_conditions},
            "rule_ids": {item.casefold() for item in mechanic_rules},
            "effect_ids": {item.casefold() for item in mechanic_effects},
            "event_ids": {item.casefold() for item in mechanic_events},
            "presentation_hook_ids": {item.casefold() for item in mechanic_hooks},
            "asset_binding_ids": {item.casefold() for item in binding_ids},
            "required_feature_ids": {item.casefold() for item in mechanic_features},
        }
        expected_closure = action_closures[action_key]
        mismatches = [
            field for field in expected_closure if actual_closure[field] != expected_closure[field]
        ]
        if mismatches:
            qualifier = "required feature " if "required_feature_ids" in mismatches else ""
            raise CreationContractError(
                f"{mechanic_context} {qualifier}must equal the exact action closure"
            )
    orphan_actions = set(actions) - actions_with_mechanics
    if orphan_actions:
        names = ", ".join(actions[key]["id"] for key in sorted(orphan_actions))
        raise CreationContractError(
            f"{context} action must belong to at least one mechanic: {names}"
        )
    _extensions(
        value.get("extensions"),
        f"{context}.extensions",
        registered_extensions,
        maximum=64,
    )


_VALIDATORS = {
    CREATION_PROJECT_FORMAT: _validate_project,
    CREATION_PROFILE_FORMAT: _validate_profile,
    CREATION_SOURCE_MANIFEST_FORMAT: _validate_manifest,
    WORLD_MODULE_FORMAT: _validate_world_module,
    ACTIVITY_MODULE_FORMAT: _validate_activity_module,
    NARRATIVE_MODULE_FORMAT: _validate_narrative_module,
    SYSTEM_MODULE_FORMAT: _validate_system_module,
    LOGIC_MODULE_FORMAT: _validate_logic_module,
}


def validate_creation_document(
    value: object,
    *,
    expected_format: str | None = None,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> dict[str, Any]:
    document = _object(value, "creation contract")
    if document.get("format") == LOGIC_MODULE_FORMAT:
        _preflight_logic_object(document)
    _validate_json_structure(document, context="creation contract")
    format_name = _verify_identity(document, "creation contract")
    if expected_format is not None and format_name != expected_format:
        raise CreationContractError(
            f"creation contract expected format {expected_format}, got {format_name}"
        )
    registry = {} if registered_extensions is None else dict(registered_extensions)
    _VALIDATORS[format_name](document, registry)
    return copy.deepcopy(document)


def _verify_reference_target(
    reference: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    identity_field: str,
    context: str,
) -> None:
    if reference["format"] != target["format"]:
        raise CreationContractError(f"{context} format does not match loaded document")
    if reference["format_version"] != target["format_version"]:
        raise CreationContractError(f"{context} format_version does not match loaded document")
    if reference["id"] != target[identity_field]:
        raise CreationContractError(f"{context} id does not match loaded document")
    if reference["content_hash"] != target["content_hash"]:
        raise CreationContractError(f"{context} content hash does not match loaded document")


def _validate_logic_project_semantics(
    *,
    project: Mapping[str, Any],
    profile: Mapping[str, Any],
    activity_modules: Sequence[Mapping[str, Any]],
    narrative_modules: Sequence[Mapping[str, Any]],
    system_modules: Sequence[Mapping[str, Any]],
    logic_modules: Sequence[Mapping[str, Any]],
) -> None:
    def unique_global_records(
        modules: Sequence[Mapping[str, Any]],
        collection: str,
        kind: str,
    ) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        for module in modules:
            for record in module[collection]:
                key = record["id"].casefold()
                if key in result:
                    raise CreationContractError(
                        f"global {kind} ID NFC/casefold collision: {record['id']}"
                    )
                result[key] = record
        return result

    activities = unique_global_records(activity_modules, "activities", "activity")
    systems = unique_global_records(system_modules, "systems", "system")
    narrative_units = unique_global_records(narrative_modules, "units", "narrative unit")
    source_identities: dict[str, str] = {}
    for kind, records in (
        ("activity", activities),
        ("system", systems),
        ("narrative unit", narrative_units),
    ):
        for key, record in records.items():
            previous = source_identities.get(key)
            if previous is not None:
                raise CreationContractError(
                    f"global source ID NFC/casefold collision between {previous} and "
                    f"{kind}: {record['id']}"
                )
            source_identities[key] = kind

    if project["project_kind"] != "game":
        if logic_modules:
            raise CreationContractError("library projects cannot contain executable logic modules")
        return
    if len(logic_modules) != 1:
        raise CreationContractError(
            "game projects require exactly one closed declarative logic module v1"
        )
    logic = logic_modules[0]
    states = {item["id"].casefold(): item for item in logic["state_variables"]}
    actions = {item["id"].casefold(): item for item in logic["actions"]}
    conditions = {item["id"].casefold(): item for item in logic["conditions"]}
    effects = {item["id"].casefold(): item for item in logic["effects"]}
    events = {item["id"].casefold(): item for item in logic["events"]}
    hooks = {item["id"].casefold(): item for item in logic["presentation_hooks"]}
    rules = {item["id"].casefold(): item for item in logic["rules"]}
    asset_bindings = {
        binding.casefold(): binding
        for hook in logic["presentation_hooks"]
        for binding in hook["asset_binding_ids"]
    }
    profile_verbs = {
        item["id"].casefold(): item["id"] for item in profile["gameplay"]["core_verbs"]
    }
    mapped_verbs = {item["core_verb_id"].casefold() for item in logic["actions"]}
    if mapped_verbs != set(profile_verbs):
        missing = sorted(set(profile_verbs) - mapped_verbs)
        extra = sorted(mapped_verbs - set(profile_verbs))
        detail = []
        if missing:
            detail.append(
                f"core verb not mapped: {', '.join(profile_verbs[key] for key in missing)}"
            )
        if extra:
            detail.append(f"unknown mapped core verb: {', '.join(extra)}")
        raise CreationContractError("; ".join(detail))

    required_features = {
        item.casefold(): item for item in profile["runtime_target"]["required_features"]
    }
    mechanic_features = {
        item.casefold()
        for mechanic in logic["mechanics"]
        for item in mechanic["required_feature_ids"]
    }
    if mechanic_features != set(required_features):
        missing = sorted(set(required_features) - mechanic_features)
        extra = sorted(mechanic_features - set(required_features))
        if extra:
            raise CreationContractError(
                "logic mechanic names a required feature absent from the profile: "
                + ", ".join(extra)
            )
        raise CreationContractError(
            "profile required feature lacks mechanic evidence: "
            + ", ".join(required_features[key] for key in missing)
        )

    narrative_options = {
        (unit["id"].casefold(), option["id"].casefold()): option
        for module in narrative_modules
        for unit in module["units"]
        if unit["unit_type"] == "choice"
        for option in unit["options"]
    }
    condition_children = {
        condition["id"].casefold(): tuple(
            condition.get("condition_ids", ())
            if condition["operator"] in {"all", "any"}
            else ((condition["condition_id"],) if condition["operator"] == "not" else ())
        )
        for condition in logic["conditions"]
    }
    action_closures: dict[str, dict[str, set[str]]] = {}
    for action in logic["actions"]:
        action_rule_keys = {item.casefold() for item in action["rule_ids"]}
        condition_keys = _logic_condition_closure(
            tuple(
                condition_id
                for rule_key in action_rule_keys
                for condition_id in rules[rule_key]["condition_ids"]
            ),
            condition_children,
        )
        effect_keys = {
            item.casefold()
            for rule_key in action_rule_keys
            for item in rules[rule_key]["effect_ids"]
        }
        event_keys = {
            item.casefold()
            for rule_key in action_rule_keys
            for item in rules[rule_key]["event_ids"]
        }
        hook_keys = {item.casefold() for item in action["presentation_hook_ids"]}
        binding_keys = {
            item.casefold()
            for hook_key in hook_keys
            for item in hooks[hook_key]["asset_binding_ids"]
        }
        action_closures[action["id"].casefold()] = {
            "condition_ids": condition_keys,
            "effect_ids": effect_keys,
            "event_ids": event_keys,
            "presentation_hook_ids": hook_keys,
            "asset_binding_ids": binding_keys,
        }
    bound_actions: dict[tuple[str, str, str], set[str]] = {}
    used_options: dict[tuple[str, str], str] = {}
    for action in logic["actions"]:
        for binding in action["source_bindings"]:
            source_key = binding["source_id"].casefold()
            binding_key = (
                binding["kind"],
                source_key,
                binding.get("option_id", "").casefold(),
            )
            bound_actions.setdefault(binding_key, set()).add(action["id"].casefold())
            if binding["kind"] == "activity":
                if source_key not in activities:
                    raise CreationContractError(
                        f"logic action {action['id']} source binding references unknown "
                        f"activity {binding['source_id']}"
                    )
            elif binding["kind"] == "system":
                if source_key not in systems:
                    raise CreationContractError(
                        f"logic action {action['id']} source binding references unknown "
                        f"system {binding['source_id']}"
                    )
            else:
                option_key = (source_key, binding["option_id"].casefold())
                if option_key not in narrative_options:
                    raise CreationContractError(
                        f"logic action {action['id']} source binding references unknown "
                        f"narrative option {binding['source_id']}/{binding['option_id']}"
                    )
                previous = used_options.get(option_key)
                if previous is not None:
                    raise CreationContractError(
                        f"narrative option {binding['source_id']}/{binding['option_id']} "
                        f"is ambiguously bound by {previous} and {action['id']}"
                    )
                used_options[option_key] = action["id"]
    if narrative_options and set(used_options) != set(narrative_options):
        missing = sorted(set(narrative_options) - set(used_options))
        rendered = ", ".join(f"{unit}/{option}" for unit, option in missing)
        raise CreationContractError(
            f"narrative options require exact action source bindings: {rendered}"
        )

    registries = {
        "condition": conditions,
        "effect": effects,
        "event": events,
        "presentation hook": hooks,
        "asset binding": asset_bindings,
    }

    def require_ids(
        values: Sequence[str],
        registry_name: str,
        owner: str,
    ) -> None:
        registry = registries[registry_name]
        for identifier in values:
            if identifier.casefold() not in registry:
                raise CreationContractError(
                    f"{owner} references missing {registry_name} {identifier}"
                )

    def require_exact_bound_closure(
        *,
        owner: str,
        action_keys: set[str],
        fields: Mapping[str, Sequence[str]],
    ) -> None:
        for field, values in fields.items():
            expected = {
                item for action_key in action_keys for item in action_closures[action_key][field]
            }
            actual = {item.casefold() for item in values}
            if field == "condition_ids":
                actual = _logic_condition_closure(tuple(actual), condition_children)
            if actual != expected:
                raise CreationContractError(
                    f"{owner} must equal the exact bound action closure for {field}"
                )

    for activity in activities.values():
        owner = f"activity {activity['id']}"
        for field in (
            "start_condition_ids",
            "end_condition_ids",
            "success_condition_ids",
            "failure_condition_ids",
        ):
            require_ids(activity[field], "condition", owner)
        require_ids(activity["effect_ids"], "effect", owner)
        require_ids(activity["event_ids"], "event", owner)
        require_ids(activity["presentation_hook_ids"], "presentation hook", owner)
        require_ids(activity["asset_binding_ids"], "asset binding", owner)
        activity_actions = bound_actions.get(("activity", activity["id"].casefold(), ""), set())
        if activity_actions:
            require_exact_bound_closure(
                owner=owner,
                action_keys=activity_actions,
                fields={
                    "effect_ids": activity["effect_ids"],
                    "event_ids": activity["event_ids"],
                    "presentation_hook_ids": activity["presentation_hook_ids"],
                    "asset_binding_ids": activity["asset_binding_ids"],
                },
            )
    for system in systems.values():
        owner = f"system {system['id']}"
        require_ids(system["precondition_ids"], "condition", owner)
        require_ids(system["effect_ids"], "effect", owner)
        require_ids(system["event_ids"], "event", owner)
        require_ids(system["asset_binding_ids"], "asset binding", owner)
        system_actions = bound_actions.get(("system", system["id"].casefold(), ""), set())
        if system_actions:
            require_exact_bound_closure(
                owner=owner,
                action_keys=system_actions,
                fields={
                    "condition_ids": system["precondition_ids"],
                    "effect_ids": system["effect_ids"],
                    "event_ids": system["event_ids"],
                    "asset_binding_ids": system["asset_binding_ids"],
                },
            )
    for unit in narrative_units.values():
        owner = f"narrative unit {unit['id']}"
        require_ids(unit["prerequisite_ids"], "condition", owner)
        require_ids(unit["effect_ids"], "effect", owner)
        require_ids(unit["asset_binding_ids"], "asset binding", owner)
        if unit["unit_type"] == "choice":
            for option in unit["options"]:
                option_owner = f"narrative option {unit['id']}/{option['id']}"
                require_ids(option["condition_ids"], "condition", option_owner)
                require_ids(option["effect_ids"], "effect", option_owner)
                option_actions = bound_actions.get(
                    (
                        "narrative_option",
                        unit["id"].casefold(),
                        option["id"].casefold(),
                    ),
                    set(),
                )
                if option_actions:
                    require_exact_bound_closure(
                        owner=option_owner,
                        action_keys=option_actions,
                        fields={
                            "condition_ids": option["condition_ids"],
                            "effect_ids": option["effect_ids"],
                        },
                    )

    action_rule_keys = {
        rule_id.casefold() for action in logic["actions"] for rule_id in action["rule_ids"]
    }
    action_condition_keys = {
        item for closure in action_closures.values() for item in closure["condition_ids"]
    }
    action_effect_keys = {
        item for closure in action_closures.values() for item in closure["effect_ids"]
    }
    action_event_keys = {
        item for closure in action_closures.values() for item in closure["event_ids"]
    }
    action_hook_keys = {
        item for closure in action_closures.values() for item in closure["presentation_hook_ids"]
    }

    global_condition_roots = (
        {item.casefold() for goal in logic["goals"] for item in goal["condition_ids"]}
        | {item.casefold() for failure in logic["failures"] for item in failure["condition_ids"]}
        | {item.casefold() for ending in logic["endings"] for item in ending["condition_ids"]}
    )
    for activity in activities.values():
        for field in (
            "start_condition_ids",
            "end_condition_ids",
            "success_condition_ids",
            "failure_condition_ids",
        ):
            global_condition_roots.update(item.casefold() for item in activity[field])
    for system in systems.values():
        global_condition_roots.update(item.casefold() for item in system["precondition_ids"])
    for unit in narrative_units.values():
        global_condition_roots.update(item.casefold() for item in unit["prerequisite_ids"])
        if unit["unit_type"] == "choice":
            for option in unit["options"]:
                global_condition_roots.update(item.casefold() for item in option["condition_ids"])
    global_condition_keys = _logic_condition_closure(
        tuple(global_condition_roots),
        condition_children,
    )
    for key in global_condition_keys - action_condition_keys:
        action_scope = conditions[key].get("action_id")
        if action_scope is not None:
            raise CreationContractError(
                f"action-scoped condition {conditions[key]['id']} is outside its "
                "action/mechanic closure"
            )
    live_condition_keys = action_condition_keys | global_condition_keys

    live_event_keys = action_event_keys | {
        item.casefold() for ending in logic["endings"] for item in ending["event_ids"]
    }
    live_hook_keys = action_hook_keys | {
        item.casefold() for ending in logic["endings"] for item in ending["presentation_hook_ids"]
    }
    live_asset_binding_keys: set[str] = set()
    for activity in activities.values():
        live_event_keys.update(item.casefold() for item in activity["event_ids"])
        live_hook_keys.update(item.casefold() for item in activity["presentation_hook_ids"])
        live_asset_binding_keys.update(item.casefold() for item in activity["asset_binding_ids"])
    for system in systems.values():
        live_event_keys.update(item.casefold() for item in system["event_ids"])
        live_asset_binding_keys.update(item.casefold() for item in system["asset_binding_ids"])
    for unit in narrative_units.values():
        live_asset_binding_keys.update(item.casefold() for item in unit["asset_binding_ids"])
    live_asset_binding_keys.update(
        item.casefold()
        for hook_key in live_hook_keys
        for item in hooks[hook_key]["asset_binding_ids"]
    )

    live_state_keys = {
        state_key
        for condition_key in live_condition_keys
        for state_key in _logic_condition_state_ids(conditions[condition_key])
    } | {
        state_key
        for effect_key in action_effect_keys
        for state_key in _logic_effect_state_ids(effects[effect_key])
    }
    live_parameter_keys = {
        parameter_key
        for condition_key in live_condition_keys
        for parameter_key in _logic_condition_parameter_ids(conditions[condition_key])
    } | {
        parameter_key
        for effect_key in action_effect_keys
        for parameter_key in _logic_effect_parameter_ids(effects[effect_key])
    }
    declared_parameter_keys = {
        (action["id"].casefold(), parameter["id"].casefold())
        for action in logic["actions"]
        for parameter in action["parameters"]
    }

    def reject_orphans(
        registry: Mapping[str, object],
        live: set[str],
        label: str,
    ) -> None:
        orphaned = set(registry) - live
        if orphaned:
            names = ", ".join(
                str(registry[key]["id"])
                if isinstance(registry[key], Mapping)
                else str(registry[key])
                for key in sorted(orphaned)
            )
            raise CreationContractError(
                f"logic module contains orphan {label} definitions: {names}"
            )

    reject_orphans(states, live_state_keys, "state")
    reject_orphans(conditions, live_condition_keys, "condition")
    reject_orphans(effects, action_effect_keys, "effect")
    reject_orphans(rules, action_rule_keys, "rule")
    reject_orphans(events, live_event_keys, "event")
    reject_orphans(hooks, live_hook_keys, "presentation hook")
    reject_orphans(asset_bindings, live_asset_binding_keys, "asset binding")
    orphan_parameters = declared_parameter_keys - live_parameter_keys
    if orphan_parameters:
        rendered = ", ".join(
            f"{actions[action_key]['id']}/{parameter_key}"
            for action_key, parameter_key in sorted(orphan_parameters)
        )
        raise CreationContractError(
            f"logic module contains orphan action parameter definitions: {rendered}"
        )

    goal_owners: dict[str, list[str]] = {}
    for goal in logic["goals"]:
        goal_owners.setdefault(goal["success_ending_id"].casefold(), []).append(goal["id"])
    for ending in logic["endings"]:
        if ending["kind"] != "success":
            continue
        owners = goal_owners.get(ending["id"].casefold(), [])
        if len(owners) != 1:
            raise CreationContractError(
                f"success ending {ending['id']} must belong to exactly one goal"
            )


def validate_creation_documents(
    project: object,
    profile: object,
    manifest: object,
    world_modules: Sequence[object],
    activity_modules: Sequence[object],
    narrative_modules: Sequence[object],
    system_modules: Sequence[object],
    logic_modules: Sequence[object] = (),
    *,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> LoadedCreationProject:
    registry = {} if registered_extensions is None else dict(registered_extensions)
    checked_project = validate_creation_document(
        project,
        expected_format=CREATION_PROJECT_FORMAT,
        registered_extensions=registry,
    )
    checked_profile = validate_creation_document(
        profile,
        expected_format=CREATION_PROFILE_FORMAT,
        registered_extensions=registry,
    )
    checked_manifest = validate_creation_document(
        manifest,
        expected_format=CREATION_SOURCE_MANIFEST_FORMAT,
        registered_extensions=registry,
    )
    checked_collections: dict[str, tuple[dict[str, Any], ...]] = {}
    supplied = {
        "world_modules": world_modules,
        "activity_modules": activity_modules,
        "narrative_modules": narrative_modules,
        "system_modules": system_modules,
        "logic_modules": logic_modules,
    }
    for collection, documents in supplied.items():
        if not isinstance(documents, Sequence) or isinstance(documents, (str, bytes, bytearray)):
            raise CreationContractError(f"{collection} must be an array")
        expected_format = _MODULE_COLLECTIONS[collection]
        checked_collections[collection] = tuple(
            validate_creation_document(
                document,
                expected_format=expected_format,
                registered_extensions=registry,
            )
            for document in documents
        )
    project_id = checked_project["project_id"]
    if checked_profile["project_id"] != project_id or checked_manifest["project_id"] != project_id:
        raise CreationContractError("project, profile, and source manifest project IDs differ")
    _verify_reference_target(
        checked_project["profile"],
        checked_profile,
        identity_field="profile_id",
        context="creation project.profile",
    )
    _verify_reference_target(
        checked_project["source_manifest"],
        checked_manifest,
        identity_field="project_id",
        context="creation project.source_manifest",
    )
    _verify_reference_target(
        checked_manifest["profile"],
        checked_profile,
        identity_field="profile_id",
        context="creation source manifest.profile",
    )
    if checked_manifest["profile"]["path"] != checked_project["profile"]["path"]:
        raise CreationContractError("creation project and source manifest profile paths differ")
    for collection, documents in checked_collections.items():
        references = checked_manifest["modules"][collection]
        if len(references) != len(documents):
            raise CreationContractError(
                f"creation source manifest.{collection} reference count does not match documents"
            )
        for index, (reference, document) in enumerate(zip(references, documents, strict=True)):
            if document["project_id"] != project_id:
                raise CreationContractError(f"{collection}/{index} project ID does not match")
            _verify_reference_target(
                reference,
                document,
                identity_field="module_id",
                context=f"creation source manifest.{collection}/{index}",
            )
    if checked_profile["world"]["presence"] == "none" and checked_collections["world_modules"]:
        raise CreationContractError("world:none forbids world modules")
    if checked_profile["world"]["presence"] == "none":
        for module in checked_collections["activity_modules"]:
            for activity in module["activities"]:
                if activity["participant_ids"] or activity["spatial_context_ids"]:
                    raise CreationContractError(
                        "world:none forbids activity participant and spatial references"
                    )
        if any(
            system["system_type"] == "world_modifier"
            for module in checked_collections["system_modules"]
            for system in module["systems"]
        ):
            raise CreationContractError("world:none forbids world_modifier systems")
    if (
        checked_profile["narrative"]["requirement"] == "none"
        and checked_collections["narrative_modules"]
    ):
        raise CreationContractError("narrative:none forbids narrative modules")
    if checked_profile["narrative"]["requirement"] == "none":
        if any(
            activity["activity_type"] == "quest"
            for module in checked_collections["activity_modules"]
            for activity in module["activities"]
        ):
            raise CreationContractError("narrative:none forbids quest activities")
    if (
        checked_project["default_locale"]
        != checked_profile["presentation"]["localization"]["source_locale"]
    ):
        raise CreationContractError(
            "creation project default locale and profile source locale differ"
        )
    narrative_units = [
        unit for module in checked_collections["narrative_modules"] for unit in module["units"]
    ]
    if checked_profile["narrative"]["requirement"] == "required" and not narrative_units:
        raise CreationContractError("narrative:required requires authored narrative units")
    narrative_ids: dict[str, str] = {}
    for unit in narrative_units:
        key = unit["id"].casefold()
        if key in narrative_ids:
            raise CreationContractError(
                f"narrative unit ID {unit['id']} collides across narrative modules"
            )
        narrative_ids[key] = unit["id"]
    for unit in narrative_units:
        references = list(unit["next_unit_ids"])
        if unit["unit_type"] == "choice":
            references.extend(option["next_unit_id"] for option in unit["options"])
        for reference in references:
            if reference.casefold() not in narrative_ids:
                raise CreationContractError(
                    f"narrative unit {unit['id']} references missing narrative unit {reference}"
                )
    gameplay_family = checked_profile["gameplay"]["primary_family"]
    _validate_logic_project_semantics(
        project=checked_project,
        profile=checked_profile,
        activity_modules=checked_collections["activity_modules"],
        narrative_modules=checked_collections["narrative_modules"],
        system_modules=checked_collections["system_modules"],
        logic_modules=checked_collections["logic_modules"],
    )
    if checked_project["project_kind"] == "game" and gameplay_family == "none":
        raise CreationContractError("game projects require a gameplay family")
    if checked_project["project_kind"] != "game" and gameplay_family != "none":
        raise CreationContractError(
            "library projects require gameplay:none and must not invent game mechanics"
        )
    return LoadedCreationProject(
        project=checked_project,
        profile=checked_profile,
        manifest=checked_manifest,
        world_modules=checked_collections["world_modules"],
        activity_modules=checked_collections["activity_modules"],
        narrative_modules=checked_collections["narrative_modules"],
        system_modules=checked_collections["system_modules"],
        logic_modules=checked_collections["logic_modules"],
    )


def _load_creation_project_snapshot(
    root: Path,
    project_name: str,
    root_identity: tuple[int, int],
    *,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> LoadedCreationProject:
    files_read = 0
    aggregate_bytes = 0

    def read_snapshot(relative: object, context: str) -> dict[str, Any]:
        nonlocal aggregate_bytes, files_read
        safe = _portable_relative_path(relative, context)
        files_read += 1
        if files_read > MAX_CREATION_PROJECT_FILES:
            raise CreationContractError(
                f"creation project exceeds the {MAX_CREATION_PROJECT_FILES}-file project limit",
                reason_code="creation_project_file_limit",
            )
        source = root.joinpath(*PurePosixPath(safe).parts)
        try:
            initial = source.lstat()
        except OSError as exc:
            raise CreationContractError(
                f"{context}: could not inspect file identity",
                reason_code="creation_project_inspection_failed",
            ) from exc
        initial_state = (
            initial.st_dev,
            initial.st_ino,
            initial.st_mode,
            initial.st_nlink,
            initial.st_size,
            initial.st_mtime_ns,
            initial.st_ctime_ns,
        )
        payload = read_workspace_file_snapshot(
            root,
            PurePosixPath(safe),
            world_identity=root_identity,
            context=context,
            limit=MAX_CREATION_CONTRACT_BYTES,
        )
        verification = read_workspace_file_snapshot(
            root,
            PurePosixPath(safe),
            world_identity=root_identity,
            context=f"{context} verification",
            limit=MAX_CREATION_CONTRACT_BYTES,
        )
        try:
            final = source.lstat()
        except OSError as exc:
            raise CreationContractError(
                f"{context}: file identity changed while reading",
                reason_code="creation_project_file_changed",
            ) from exc
        final_state = (
            final.st_dev,
            final.st_ino,
            final.st_mode,
            final.st_nlink,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        )
        if payload != verification or initial_state != final_state:
            raise CreationContractError(
                f"{context}: file identity changed while reading",
                reason_code="creation_project_file_changed",
            )
        aggregate_bytes += len(payload)
        if aggregate_bytes > MAX_CREATION_AGGREGATE_BYTES:
            raise CreationContractError(
                "creation project exceeds the aggregate byte limit",
                reason_code="creation_project_aggregate_limit",
            )
        return _decode_creation_object(payload, context)

    project = validate_creation_document(
        read_snapshot(project_name, "creation project"),
        expected_format=CREATION_PROJECT_FORMAT,
        registered_extensions=registered_extensions,
    )
    profile = read_snapshot(
        project["profile"]["path"],
        "creation project.profile",
    )
    manifest_relative = _portable_relative_path(
        project["source_manifest"]["path"],
        "creation project.source_manifest",
    )
    manifest = validate_creation_document(
        read_snapshot(
            manifest_relative,
            "creation project.source_manifest",
        ),
        expected_format=CREATION_SOURCE_MANIFEST_FORMAT,
        registered_extensions=registered_extensions,
    )
    module_root = PurePosixPath(manifest_relative).parent
    loaded: dict[str, tuple[dict[str, Any], ...]] = {}
    for collection in _MODULE_COLLECTIONS:
        documents: list[dict[str, Any]] = []
        for index, reference in enumerate(manifest["modules"][collection]):
            relative = (module_root / reference["path"]).as_posix()
            documents.append(
                read_snapshot(
                    relative,
                    f"creation source manifest.{collection}/{index}.path",
                )
            )
        loaded[collection] = tuple(documents)
    return validate_creation_documents(
        project,
        profile,
        manifest,
        loaded["world_modules"],
        loaded["activity_modules"],
        loaded["narrative_modules"],
        loaded["system_modules"],
        loaded["logic_modules"],
        registered_extensions=registered_extensions,
    )


def _creation_project_root_error(reason_code: str) -> CreationContractError:
    detail = {
        "creation_project_inspection_failed": (
            "Creation project root could not be inspected safely"
        ),
        "creation_project_root_changed": ("Creation project root changed during safe inspection"),
        "creation_project_root_linked": (
            "Creation project root contains a symbolic link or reparse point"
        ),
        "creation_project_root_non_directory": ("Creation project root must be a real directory"),
    }[reason_code]
    return CreationContractError(detail, reason_code=reason_code)


def _preflight_creation_project_root(root: Path) -> None:
    """Classify an unsafe root without treating the pathname scan as authority."""

    current = Path(root.anchor)
    components = [current]
    for part in root.parts[1:]:
        current /= part
        components.append(current)
    for component in components:
        try:
            info = path_file_stat(component)
        except OSError as exc:
            reason = (
                "creation_project_root_changed"
                if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}
                else "creation_project_inspection_failed"
            )
            raise _creation_project_root_error(reason) from exc
        if is_link_or_reparse(info):
            raise _creation_project_root_error("creation_project_root_linked")
        if not stat.S_ISDIR(info.st_mode):
            raise _creation_project_root_error("creation_project_root_non_directory")


def _creation_project_root_error_from_studio(error: StudioError) -> CreationContractError:
    if "identity changed" in error.message.casefold():
        return _creation_project_root_error("creation_project_root_changed")
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, OSError) and current.errno in {
            errno.ENOENT,
            errno.ENOTDIR,
            errno.ELOOP,
        }:
            return _creation_project_root_error("creation_project_root_changed")
        current = current.__cause__
    return _creation_project_root_error("creation_project_inspection_failed")


def _creation_project_snapshot_error(error: StudioError) -> CreationContractError:
    message = error.message.casefold()
    if "hard link" in message or "regular file" in message:
        return CreationContractError(
            "Creation project file cannot be a hard link and must be a standalone regular file",
            reason_code="creation_project_file_unsafe",
        )
    if "exceeds" in message and "bytes" in message:
        return CreationContractError(
            error.message,
            reason_code="creation_project_file_byte_limit",
        )
    if "changed" in message:
        return CreationContractError(
            "Creation project file changed during safe inspection",
            reason_code="creation_project_file_changed",
        )
    return CreationContractError(
        "Creation project file could not be inspected safely",
        reason_code="creation_project_inspection_failed",
    )


def load_creation_project(
    project_path: str | Path,
    *,
    registered_extensions: Mapping[str, ExtensionValidator] | None = None,
) -> LoadedCreationProject:
    source = Path(os.path.abspath(os.fspath(project_path)))
    root = source.parent
    project_name = _portable_relative_path(source.name, "creation project path")
    loaded: LoadedCreationProject | None = None
    contract_error: CreationContractError | None = None
    _preflight_creation_project_root(root)
    try:
        with _pinned_ancestor_identities(root, context="creation project root") as identities:
            try:
                loaded = _load_creation_project_snapshot(
                    root,
                    project_name,
                    identities[-1],
                    registered_extensions=registered_extensions,
                )
            except CreationContractError as exc:
                contract_error = exc
            except StudioError as exc:
                contract_error = _creation_project_snapshot_error(exc)
    except StudioError as exc:
        raise _creation_project_root_error_from_studio(exc) from exc
    if contract_error is not None:
        raise contract_error
    if loaded is None:
        raise CreationContractError("creation project snapshot did not produce a project")
    return loaded
