from __future__ import annotations

import copy
import ctypes
import hashlib
import os
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from gamepack_raylib_2d.executable_shape import (
    ADAPTER_EXECUTABLE_SHAPE_UNSUPPORTED,
    AdapterExecutableShapeError,
    inspect_adapter_executable_shape,
)
from gamepack_runtime import GameLogicError, snapshot_plain_json
from worldforge.creation_contracts import (
    CreationContractError,
    _exact_keys,
    _identifier,
    _integer,
    _non_empty_string,
    _object,
    _portable_relative_path,
    _semver,
    _sha256,
    _string_array,
    _validate_json_structure,
    canonical_creation_hash,
    read_creation_object,
)
from worldforge.file_stat import (
    FileStat,
    descriptor_file_stat,
    file_identity,
    is_link_or_reparse,
    path_file_stat,
    windows_handle_file_stat,
)
from worldforge.game_logic import EXECUTION_SEMANTICS
from worldforge.gamepack import GamepackError, validate_gamepack_document
from worldforge.generic_assetpack import (
    GenericAssetpackError,
    VerifiedGenericAssetpack,
    verify_generic_assetpack,
)
from worldforge.generic_assets import (
    GenericAssetError,
    validate_asset_inventory_document,
)
from worldforge.integrity import canonical_json_bytes

RUNTIME_ADAPTER_FORMAT = "world-forge.runtime_adapter"
RUNTIME_ADAPTER_REGISTRY_FORMAT = "world-forge.runtime_adapter_registry"
RUNTIME_SNAPSHOT_FORMAT = "world-forge.game_runtime_snapshot"
RUNTIME_COMPOSITION_FORMAT = "world-forge.game_runtime_composition"
RUNTIME_EVIDENCE_FORMAT = "world-forge.runtime_evidence"
RUNTIME_SUPPORT_REPORT_FORMAT = "world-forge.runtime_support_report"
RUNTIME_CONTRACT_VERSION = 1

MAX_RUNTIME_CONTRACT_BYTES = 16 * 1024 * 1024
MAX_RUNTIME_ADAPTERS = 32
MAX_RUNTIME_FILES = 256
MAX_RUNTIME_FILE_BYTES = 4 * 1024 * 1024
MAX_RUNTIME_TREE_BYTES = 32 * 1024 * 1024
MAX_RUNTIME_ITEMS = 256
MAX_RUNTIME_TEXT = 4096


@dataclass(frozen=True, slots=True)
class _RuntimeStageReadCapability:
    """Bind the Windows write-sharing exception to one retained private stage."""

    root: Path
    require_binding: Callable[[], None]


_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_FORMAT_VERSION_FIELDS = frozenset({"format", "versions"})
_RUNTIME_API_FIELDS = frozenset({"id", "version"})
_EXECUTION_SEMANTICS_FIELDS = frozenset({"version", "content_hash"})
_SEMANTICS_FIELDS = frozenset(
    {
        "action_parameter_types",
        "condition_operators",
        "effect_operations",
        "ending_kinds",
        "narrative_cursor",
    }
)
_PRESENTATION_FIELDS = frozenset({"mode", "camera", "perspective", "requested_renderer"})
_ASSET_BINDING_FIELDS = frozenset({"binding_id", "asset_id", "role", "media_type", "runtime_path"})
_PLATFORM_FIELDS = frozenset(
    {
        "platform_id",
        "platform_family",
        "architecture",
        "backend",
        "renderer",
    }
)
_PERSISTENCE_FIELDS = frozenset({"save", "replay"})
_PERSISTENCE_TARGET_FIELDS = frozenset({"required", "format", "versions"})
_IMPLEMENTATION_FIELDS = frozenset({"backend", "renderer", "runtime_api", "snapshot"})
_BUDGET_FIELDS = frozenset(
    {
        "max_actions",
        "max_assets",
        "max_loaded_bytes",
        "max_state_bytes",
        "target_frame_milliseconds",
    }
)
_ADAPTER_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "adapter_id",
        "adapter_version",
        "state",
        "accepted_logic_formats",
        "execution_semantics",
        "supported_profiles",
        "supported_features",
        "supported_semantics",
        "presentations",
        "assetpacks",
        "asset_formats",
        "asset_bindings",
        "platforms",
        "input_capabilities",
        "persistence",
        "packaging_targets",
        "implementation",
        "budgets",
        "limitations",
        "evidence_requirements",
        "content_hash",
    }
)
_SNAPSHOT_FILE_FIELDS = frozenset({"path", "sha256", "size_bytes"})
_SNAPSHOT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "snapshot_id",
        "runtime_api",
        "adapter_descriptors",
        "files",
        "tree_hash",
        "content_hash",
    }
)
_REGISTRY_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "registry_id",
        "runtime_snapshot",
        "adapters",
        "content_hash",
    }
)
_COMPOSITION_ASSETPACK_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "content_hash",
        "root_hash",
        "inventory_hash",
    }
)
_COMPOSITION_BINDING_FIELDS = frozenset(
    {
        "binding_id",
        "asset_id",
        "role",
        "media_type",
        "runtime_path",
        "sha256",
        "size_bytes",
    }
)
_COMPOSITION_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "composition_id",
        "gamepack",
        "asset_inventory",
        "assetpack",
        "adapter",
        "registry",
        "runtime_snapshot",
        "platforms",
        "bindings",
        "content_hash",
    }
)
_EVIDENCE_CHECK_FIELDS = frozenset({"check_id", "kind", "status", "evidence_id", "content_hash"})
_EVIDENCE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "evidence_id",
        "composition",
        "adapter",
        "platform",
        "execution_status",
        "packaging_status",
        "checks",
        "content_hash",
    }
)
_EXECUTION_DIMENSION_FIELDS = frozenset({"platform", "status", "evidence_ids"})
_DIMENSIONS_FIELDS = frozenset(
    {
        "authoring",
        "compilation",
        "assets",
        "adapter",
        "execution",
        "packaging",
        "release",
    }
)
_SAVE_REPLAY_FIELDS = frozenset({"state_ids", "event_ids"})
_RESOLVED_MECHANIC_FIELDS = frozenset(
    {
        "mechanic_id",
        "core_verb_id",
        "runtime_action_id",
        "authoritative_state_ids",
        "condition_ids",
        "rule_ids",
        "effect_ids",
        "presentation_hook_ids",
        "asset_binding_ids",
        "required_feature_ids",
        "save_replay",
        "status",
        "reason_codes",
        "test_evidence",
        "native_evidence",
    }
)
_RESOLVED_FEATURE_FIELDS = frozenset({"feature_id", "status", "reason_codes", "evidence_ids"})
_SUPPORT_EVIDENCE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "content_hash",
        "platform",
        "execution_status",
        "packaging_status",
        "passed_check_kinds",
    }
)
_SUPPORT_REPORT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "report_id",
        "gamepack",
        "composition",
        "adapter",
        "evidence",
        "dimensions",
        "compatibility_status",
        "mechanics",
        "features",
        "missing_capabilities",
        "reason_codes",
        "supported",
        "content_hash",
    }
)

_SUPPORTED_PLATFORM_REQUESTS = {
    "platform:linux_x86_64": (
        "platform:linux",
        "architecture:x86_64",
    ),
    "platform:windows_x86_64": (
        "platform:windows",
        "architecture:x86_64",
    ),
}
_CONCRETE_PLATFORMS = tuple(
    {
        "platform_id": platform_id,
        "platform_family": family,
        "architecture": architecture,
        "backend": "backend:raylib",
        "renderer": "raylib",
    }
    for platform_id, (family, architecture) in sorted(
        _SUPPORTED_PLATFORM_REQUESTS.items(),
        key=lambda item: item[0].encode("utf-8"),
    )
)
_EXECUTION_SEMANTICS_HASH = canonical_creation_hash(EXECUTION_SEMANTICS)
RUNTIME_EXECUTION_SEMANTICS_POLICY = {
    "version": 1,
    "content_hash": _EXECUTION_SEMANTICS_HASH,
}
_EVIDENCE_REQUIREMENTS = (
    "check:headless_determinism",
    "check:native_raylib",
    "check:package_verification",
    "check:save_replay",
)


class RuntimeContractError(ValueError):
    """Raised when an additive generic runtime contract fails closed."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise RuntimeContractError(reason_code, detail)


def _snapshot_runtime_json(value: object, context: str) -> Any:
    """Own one bounded exact plain-JSON graph before external validation."""

    try:
        return snapshot_plain_json(
            value,
            maximum_bytes=MAX_RUNTIME_CONTRACT_BYTES,
        )
    except GameLogicError as exc:
        _fail(
            "runtime_json_invalid",
            f"{context}: {exc.reason_code}: {exc.detail}",
        )


def _snapshot_runtime_inputs(
    context: str,
    **values: object,
) -> dict[str, Any]:
    owned = _snapshot_runtime_json(values, context)
    if type(owned) is not dict:
        _fail("runtime_json_invalid", f"{context}: inputs must be an exact object")
    return owned


def _canonical_hash(value: Mapping[str, object]) -> str:
    try:
        return canonical_creation_hash(value)
    except CreationContractError as exc:
        _fail("runtime_contract_invalid", str(exc))


def _seal(document: dict[str, Any]) -> dict[str, Any]:
    document["content_hash"] = _canonical_hash(document)
    return document


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


def _validate_identity(
    value: object,
    context: str,
    *,
    expected_format: str | None = None,
) -> dict[str, Any]:
    identity = _object(value, context)
    _exact_keys(identity, _IDENTITY_FIELDS, context)
    format_name = _non_empty_string(identity.get("format"), f"{context}.format")
    if expected_format is not None and format_name != expected_format:
        _fail(
            "runtime_identity_mismatch",
            f"{context}.format must be {expected_format}",
        )
    if _integer(identity.get("format_version"), f"{context}.format_version", minimum=1) != 1:
        _fail("runtime_identity_mismatch", f"{context}.format_version must be 1")
    _identifier(identity.get("id"), f"{context}.id")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    return identity


def _bounded_text(value: object, context: str) -> str:
    result = _non_empty_string(value, context)
    if len(result) > MAX_RUNTIME_TEXT or len(result.encode("utf-8")) > MAX_RUNTIME_TEXT:
        _fail("runtime_contract_limit", f"{context} exceeds its text limit")
    return result


def _canonical_strings(
    value: object,
    context: str,
    *,
    allow_empty: bool = False,
    identifiers: bool = False,
    tokens: bool = True,
) -> list[str]:
    try:
        if identifiers:
            if not isinstance(value, list):
                raise CreationContractError(f"{context} must be an array")
            checked = [_identifier(item, f"{context}/{index}") for index, item in enumerate(value)]
            if not allow_empty and not checked:
                raise CreationContractError(f"{context} must be non-empty")
            if checked != sorted(checked, key=lambda item: item.encode("utf-8")):
                raise CreationContractError(f"{context} must use canonical order")
            if len({item.casefold() for item in checked}) != len(checked):
                raise CreationContractError(f"{context} contains duplicates")
        else:
            checked = _string_array(
                value,
                context,
                allow_empty=allow_empty,
                tokens=tokens,
                canonical_order=True,
            )
    except CreationContractError as exc:
        _fail("runtime_contract_invalid", str(exc))
    if len(checked) > MAX_RUNTIME_ITEMS:
        _fail("runtime_contract_limit", f"{context} exceeds its item limit")
    return checked


def _validate_concrete_platform(
    value: object,
    context: str,
) -> dict[str, Any]:
    platform = _object(value, context)
    _exact_keys(platform, _PLATFORM_FIELDS, context)
    platform_id = _bounded_text(platform.get("platform_id"), f"{context}.platform_id")
    expected = _SUPPORTED_PLATFORM_REQUESTS.get(platform_id)
    if expected is None:
        _fail("runtime_platform_unsupported", f"{context}.platform_id is unsupported")
    expected_platform = {
        "platform_id": platform_id,
        "platform_family": expected[0],
        "architecture": expected[1],
        "backend": "backend:raylib",
        "renderer": "raylib",
    }
    if platform != expected_platform:
        _fail(
            "runtime_platform_invalid",
            f"{context} does not equal the closed platform projection",
        )
    return platform


def _format_versions(
    value: object,
    context: str,
    *,
    expected_format: str | None = None,
) -> dict[str, Any]:
    item = _object(value, context)
    _exact_keys(item, _FORMAT_VERSION_FIELDS, context)
    format_name = _bounded_text(item.get("format"), f"{context}.format")
    if expected_format is not None and format_name != expected_format:
        _fail(
            "runtime_format_unsupported",
            f"{context}.format must be {expected_format}",
        )
    versions = item.get("versions")
    if not isinstance(versions, list) or not versions or len(versions) > 16:
        _fail("runtime_contract_invalid", f"{context}.versions must be bounded")
    checked = [
        _integer(version, f"{context}.versions/{index}", minimum=1)
        for index, version in enumerate(versions)
    ]
    if checked != sorted(set(checked)):
        _fail(
            "runtime_contract_noncanonical",
            f"{context}.versions must be canonical",
        )
    return item


def _runtime_api(value: object, context: str) -> dict[str, Any]:
    runtime_api = _object(value, context)
    _exact_keys(runtime_api, _RUNTIME_API_FIELDS, context)
    _identifier(runtime_api.get("id"), f"{context}.id")
    _semver(runtime_api.get("version"), f"{context}.version")
    return runtime_api


def _builtin_adapter(
    *,
    adapter_id: str,
    adapter_version: str,
    profile: str,
    features: Sequence[str],
    presentation: Mapping[str, str],
    semantics: Mapping[str, object],
    asset_bindings: Sequence[Mapping[str, str]],
    asset_formats: Sequence[str] | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "format": RUNTIME_ADAPTER_FORMAT,
        "format_version": RUNTIME_CONTRACT_VERSION,
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "state": "declared",
        "accepted_logic_formats": [{"format": "world-forge.gamepack", "versions": [1]}],
        "execution_semantics": {
            "version": 1,
            "content_hash": _EXECUTION_SEMANTICS_HASH,
        },
        "supported_profiles": [profile],
        "supported_features": sorted(features, key=lambda item: item.encode("utf-8")),
        "supported_semantics": copy.deepcopy(semantics),
        "presentations": [copy.deepcopy(dict(presentation))],
        "assetpacks": [{"format": "world-forge.assetpack", "versions": [1]}],
        "asset_formats": sorted(
            asset_formats
            if asset_formats is not None
            else {
                {
                    "image/png": "asset:png",
                    "font/ttf": "asset:font",
                }[binding["media_type"]]
                for binding in asset_bindings
            },
            key=lambda item: item.encode("utf-8"),
        ),
        "asset_bindings": sorted(
            (copy.deepcopy(dict(binding)) for binding in asset_bindings),
            key=lambda item: item["binding_id"].encode("utf-8"),
        ),
        "platforms": copy.deepcopy(list(_CONCRETE_PLATFORMS)),
        "input_capabilities": ["input:keyboard", "input:pointer"],
        "persistence": {
            "save": {
                "required": True,
                "format": "world-forge.game_save",
                "versions": [1],
            },
            "replay": {
                "required": True,
                "format": "world-forge.game_replay",
                "versions": [1],
            },
        },
        "packaging_targets": ["standalone desktop directory"],
        "implementation": {
            "backend": "backend:raylib",
            "renderer": "raylib",
            "runtime_api": {"id": "gamepack_runtime", "version": "1.0.0"},
            "snapshot": {
                "format": RUNTIME_SNAPSHOT_FORMAT,
                "versions": [1],
            },
        },
        "budgets": {
            "max_actions": 128,
            "max_assets": 1024,
            "max_loaded_bytes": 512 * 1024 * 1024,
            "max_state_bytes": 65536,
            "target_frame_milliseconds": 17,
        },
        "limitations": [
            "Bounded deterministic two-dimensional adapter.",
            "No runtime AI, network access, subprocesses, or executable content.",
        ],
        "evidence_requirements": list(_EVIDENCE_REQUIREMENTS),
        "content_hash": "",
    }
    return validate_runtime_adapter_document(_seal(document))


def _build_builtin_runtime_adapters(adapter_version: str) -> list[dict[str, Any]]:
    """Build one exact version of the bounded puzzle and narrative descriptors."""

    common_semantics = {
        "action_parameter_types": ["integer"],
        "condition_operators": [
            "compare",
            "constant",
            "index_valid",
            "integer_distance",
        ],
        "effect_operations": ["increment", "reset", "swap_array_items"],
        "ending_kinds": ["success"],
        "narrative_cursor": False,
    }
    puzzle = _builtin_adapter(
        adapter_id="gamepack_raylib_2d_puzzle",
        adapter_version=adapter_version,
        profile="profile:abstract_puzzle",
        features=("logic:deterministic_actions", "logic:finite_state"),
        presentation={
            "mode": "2d",
            "camera": "fixed",
            "perspective": "orthographic board",
            "requested_renderer": "raylib",
        },
        semantics=common_semantics,
        asset_bindings=(
            {
                "binding_id": "board_texture",
                "asset_id": "board_ui",
                "role": "texture",
                "media_type": "image/png",
                "runtime_path": "assets/ui/board.png",
            },
        ),
        asset_formats=("asset:png",),
    )
    narrative = _builtin_adapter(
        adapter_id="gamepack_raylib_2d_text",
        adapter_version=adapter_version,
        profile="profile:branching_narrative",
        features=("logic:branching_choice", "logic:persistent_variables"),
        presentation={
            "mode": "text",
            "camera": "none",
            "perspective": "text interface",
            "requested_renderer": "raylib",
        },
        semantics={
            "action_parameter_types": [],
            "condition_operators": ["compare"],
            "effect_operations": ["append_unique", "set"],
            "ending_kinds": ["success"],
            "narrative_cursor": True,
        },
        asset_bindings=(
            {
                "binding_id": "choice_panel",
                "asset_id": "narrative_ui_font",
                "role": "font",
                "media_type": "font/ttf",
                "runtime_path": "assets/fonts/narrative-ui.ttf",
            },
            {
                "binding_id": "ending_panel",
                "asset_id": "narrative_ui_font",
                "role": "font",
                "media_type": "font/ttf",
                "runtime_path": "assets/fonts/narrative-ui.ttf",
            },
        ),
        asset_formats=("asset:font", "asset:png"),
    )
    return [puzzle, narrative]


def build_builtin_runtime_adapters() -> list[dict[str, Any]]:
    """Return the active immutable code-owned puzzle and narrative descriptors."""

    return _build_builtin_runtime_adapters("1.1.0")


def build_historical_runtime_adapters() -> list[dict[str, Any]]:
    """Return exact non-resolvable v1.0.0 descriptor records for compatibility."""

    return _build_builtin_runtime_adapters("1.0.0")


def _validate_semantics(value: object, context: str) -> dict[str, Any]:
    semantics = _object(value, context)
    _exact_keys(semantics, _SEMANTICS_FIELDS, context)
    for field in (
        "action_parameter_types",
        "condition_operators",
        "effect_operations",
        "ending_kinds",
    ):
        _canonical_strings(
            semantics.get(field),
            f"{context}.{field}",
            allow_empty=True,
        )
    if not isinstance(semantics.get("narrative_cursor"), bool):
        _fail(
            "runtime_contract_invalid",
            f"{context}.narrative_cursor must be boolean",
        )
    return semantics


def _validate_adapter_structure(value: object) -> dict[str, Any]:
    _validate_json_structure(value, context="runtime adapter")
    document = _object(value, "runtime adapter")
    _exact_keys(document, _ADAPTER_FIELDS, "runtime adapter")
    if document.get("format") != RUNTIME_ADAPTER_FORMAT:
        _fail("runtime_adapter_format_invalid", f"format must be {RUNTIME_ADAPTER_FORMAT}")
    if document.get("format_version") != RUNTIME_CONTRACT_VERSION:
        _fail("runtime_adapter_version_invalid", "format_version must be 1")
    _identifier(document.get("adapter_id"), "runtime adapter.adapter_id")
    _semver(document.get("adapter_version"), "runtime adapter.adapter_version")
    if document.get("state") not in {"declared", "verified"}:
        _fail("runtime_adapter_state_invalid", "state must be declared or verified")

    accepted = document.get("accepted_logic_formats")
    if not isinstance(accepted, list) or not accepted or len(accepted) > 16:
        _fail("runtime_contract_limit", "accepted_logic_formats must be bounded")
    checked_accepted = [
        _format_versions(item, f"runtime adapter.accepted_logic_formats/{index}")
        for index, item in enumerate(accepted)
    ]
    accepted_names = [item["format"] for item in checked_accepted]
    if accepted_names != sorted(accepted_names, key=lambda item: item.encode("utf-8")):
        _fail("runtime_contract_noncanonical", "accepted logic formats are not canonical")
    if len({item.casefold() for item in accepted_names}) != len(accepted_names):
        _fail("runtime_contract_collision", "accepted logic formats collide")

    semantics = _object(
        document.get("execution_semantics"),
        "runtime adapter.execution_semantics",
    )
    _exact_keys(
        semantics,
        _EXECUTION_SEMANTICS_FIELDS,
        "runtime adapter.execution_semantics",
    )
    if semantics.get("version") != 1:
        _fail("runtime_semantics_unsupported", "execution semantics version must be 1")
    _sha256(
        semantics.get("content_hash"),
        "runtime adapter.execution_semantics.content_hash",
    )
    if semantics["content_hash"] != _EXECUTION_SEMANTICS_HASH:
        _fail(
            "runtime_semantics_unsupported",
            "execution semantics hash is not the exact neutral-kernel policy",
        )

    _canonical_strings(
        document.get("supported_profiles"),
        "runtime adapter.supported_profiles",
    )
    _canonical_strings(
        document.get("supported_features"),
        "runtime adapter.supported_features",
    )
    _validate_semantics(
        document.get("supported_semantics"),
        "runtime adapter.supported_semantics",
    )

    presentations = document.get("presentations")
    if not isinstance(presentations, list) or not presentations or len(presentations) > 16:
        _fail("runtime_contract_limit", "presentations must be bounded")
    presentation_keys: list[tuple[str, str, str, str]] = []
    for index, raw in enumerate(presentations):
        context = f"runtime adapter.presentations/{index}"
        presentation = _object(raw, context)
        _exact_keys(presentation, _PRESENTATION_FIELDS, context)
        key = tuple(
            _bounded_text(presentation.get(field), f"{context}.{field}")
            for field in ("mode", "camera", "perspective", "requested_renderer")
        )
        presentation_keys.append(key)  # type: ignore[arg-type]
    if presentation_keys != sorted(
        presentation_keys,
        key=lambda item: tuple(part.encode("utf-8") for part in item),
    ):
        _fail("runtime_contract_noncanonical", "presentations are not canonical")
    if len(set(presentation_keys)) != len(presentation_keys):
        _fail("runtime_contract_collision", "presentations contain duplicates")

    assetpacks = document.get("assetpacks")
    if not isinstance(assetpacks, list) or not assetpacks or len(assetpacks) > 16:
        _fail("runtime_contract_limit", "assetpacks must be bounded")
    checked_assetpacks = [
        _format_versions(item, f"runtime adapter.assetpacks/{index}")
        for index, item in enumerate(assetpacks)
    ]
    if [item["format"] for item in checked_assetpacks] != sorted(
        (item["format"] for item in checked_assetpacks),
        key=lambda item: item.encode("utf-8"),
    ):
        _fail("runtime_contract_noncanonical", "assetpacks are not canonical")
    _canonical_strings(
        document.get("asset_formats"),
        "runtime adapter.asset_formats",
    )

    bindings = document.get("asset_bindings")
    if not isinstance(bindings, list) or not bindings or len(bindings) > MAX_RUNTIME_ITEMS:
        _fail("runtime_contract_limit", "asset_bindings must be bounded")
    binding_ids: list[str] = []
    for index, raw in enumerate(bindings):
        context = f"runtime adapter.asset_bindings/{index}"
        binding = _object(raw, context)
        _exact_keys(binding, _ASSET_BINDING_FIELDS, context)
        binding_ids.append(_identifier(binding.get("binding_id"), f"{context}.binding_id"))
        _identifier(binding.get("asset_id"), f"{context}.asset_id")
        _identifier(binding.get("role"), f"{context}.role")
        _bounded_text(binding.get("media_type"), f"{context}.media_type")
        _portable_relative_path(binding.get("runtime_path"), f"{context}.runtime_path")
    if binding_ids != sorted(binding_ids, key=lambda item: item.encode("utf-8")):
        _fail("runtime_contract_noncanonical", "asset_bindings are not canonical")
    if len({item.casefold() for item in binding_ids}) != len(binding_ids):
        _fail("runtime_contract_collision", "asset binding IDs collide")

    platforms = document.get("platforms")
    if not isinstance(platforms, list) or not platforms or len(platforms) > 32:
        _fail("runtime_contract_limit", "platforms must be bounded")
    platform_ids: list[str] = []
    for index, raw in enumerate(platforms):
        context = f"runtime adapter.platforms/{index}"
        platform = _validate_concrete_platform(raw, context)
        platform_ids.append(platform["platform_id"])
    if platform_ids != sorted(platform_ids, key=lambda item: item.encode("utf-8")):
        _fail("runtime_contract_noncanonical", "platforms are not canonical")
    if len(set(platform_ids)) != len(platform_ids):
        _fail("runtime_contract_collision", "platform IDs collide")

    _canonical_strings(
        document.get("input_capabilities"),
        "runtime adapter.input_capabilities",
    )
    persistence = _object(document.get("persistence"), "runtime adapter.persistence")
    _exact_keys(persistence, _PERSISTENCE_FIELDS, "runtime adapter.persistence")
    for field in ("save", "replay"):
        context = f"runtime adapter.persistence.{field}"
        target = _object(persistence.get(field), context)
        _exact_keys(target, _PERSISTENCE_TARGET_FIELDS, context)
        if not isinstance(target.get("required"), bool):
            _fail("runtime_contract_invalid", f"{context}.required must be boolean")
        _bounded_text(target.get("format"), f"{context}.format")
        _format_versions(
            {
                "format": target["format"],
                "versions": target.get("versions"),
            },
            context,
        )
    _canonical_strings(
        document.get("packaging_targets"),
        "runtime adapter.packaging_targets",
        tokens=False,
    )

    implementation = _object(
        document.get("implementation"),
        "runtime adapter.implementation",
    )
    _exact_keys(
        implementation,
        _IMPLEMENTATION_FIELDS,
        "runtime adapter.implementation",
    )
    if (
        implementation.get("backend") != "backend:raylib"
        or implementation.get("renderer") != "raylib"
    ):
        _fail(
            "runtime_implementation_invalid",
            "implementation must use the concrete raylib backend",
        )
    _runtime_api(
        implementation.get("runtime_api"),
        "runtime adapter.implementation.runtime_api",
    )
    _format_versions(
        implementation.get("snapshot"),
        "runtime adapter.implementation.snapshot",
        expected_format=RUNTIME_SNAPSHOT_FORMAT,
    )

    budgets = _object(document.get("budgets"), "runtime adapter.budgets")
    _exact_keys(budgets, _BUDGET_FIELDS, "runtime adapter.budgets")
    for field in _BUDGET_FIELDS:
        _integer(budgets.get(field), f"runtime adapter.budgets.{field}", minimum=1)
    _canonical_strings(
        document.get("limitations"),
        "runtime adapter.limitations",
        tokens=False,
    )
    _canonical_strings(
        document.get("evidence_requirements"),
        "runtime adapter.evidence_requirements",
    )
    _sha256(document.get("content_hash"), "runtime adapter.content_hash")
    if document["content_hash"] != _canonical_hash(document):
        _fail(
            "runtime_adapter_hash_mismatch",
            "runtime adapter content_hash is not canonical",
        )
    return document


def validate_runtime_adapter_document(value: object) -> dict[str, Any]:
    try:
        owned = _snapshot_runtime_json(value, "runtime adapter")
        return copy.deepcopy(_validate_adapter_structure(owned))
    except RuntimeContractError:
        raise
    except (CreationContractError, TypeError, ValueError, RecursionError) as exc:
        _fail("runtime_adapter_invalid", str(exc))


def serialize_runtime_adapter(value: object) -> bytes:
    return canonical_json_bytes(validate_runtime_adapter_document(value))


def _snapshot_seed(document: Mapping[str, object]) -> dict[str, object]:
    return {
        "runtime_api": document["runtime_api"],
        "adapter_descriptors": document["adapter_descriptors"],
        "files": document["files"],
        "tree_hash": document["tree_hash"],
    }


def _derived_snapshot_id(document: Mapping[str, object]) -> str:
    return f"runtime_snapshot_{_canonical_hash(_snapshot_seed(document))[:40]}"


def _runtime_entry_state(info: FileStat) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


_RuntimeTreeHook = Callable[[str, str | None], None]


@dataclass(slots=True)
class _RetainedRuntimeDirectory:
    relative: str
    name: str
    descriptor: int
    parent_descriptor: int
    identity: tuple[int, int]
    names: tuple[str, ...] = ()


@dataclass(slots=True)
class _RetainedRuntimeFile:
    relative: str
    name: str
    descriptor: int
    parent_descriptor: int
    state: tuple[int, int, int, int, int, int, int]
    payload: bytes


def _invoke_runtime_tree_hook(
    hook: _RuntimeTreeHook | None,
    event: str,
    relative: str | None = None,
) -> None:
    if hook is not None:
        hook(event, relative)


def _runtime_directory_valid(info: FileStat) -> bool:
    return not is_link_or_reparse(info) and stat.S_ISDIR(info.st_mode)


def _runtime_file_valid(info: FileStat) -> bool:
    return not is_link_or_reparse(info) and stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def _runtime_relative_path(relative: str) -> str:
    try:
        return _portable_relative_path(
            relative,
            "runtime snapshot file path",
        )
    except CreationContractError as exc:
        _fail("runtime_snapshot_tree_unsafe", str(exc))


def _read_runtime_descriptor(descriptor: int, relative: str) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, MAX_RUNTIME_FILE_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_RUNTIME_FILE_BYTES:
                _fail(
                    "runtime_snapshot_limit",
                    f"runtime file exceeds {MAX_RUNTIME_FILE_BYTES} bytes: {relative}",
                )
        return b"".join(chunks)
    except RuntimeContractError:
        raise
    except OSError as exc:
        _fail(
            "runtime_snapshot_read_failed",
            f"could not read retained runtime file {relative}: {exc}",
        )


def _posix_runtime_open_flags(*, directory: bool) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    else:
        flags |= getattr(os, "O_BINARY", 0)
    return flags


def _runtime_entry_kind(info: FileStat, name: str, relative: str) -> str:
    if is_link_or_reparse(info):
        _fail(
            "runtime_snapshot_tree_unsafe",
            f"runtime tree entry is linked or reparse-backed: {relative}",
        )
    if stat.S_ISDIR(info.st_mode):
        return "ignored" if name == "__pycache__" else "directory"
    if stat.S_ISREG(info.st_mode):
        if info.st_nlink != 1:
            _fail(
                "runtime_snapshot_tree_unsafe",
                f"runtime file has multiple hard links: {relative}",
            )
        return "ignored" if name.endswith((".pyc", ".pyo")) else "file"
    _fail(
        "runtime_snapshot_tree_unsafe",
        f"runtime tree entry is special: {relative}",
    )


def _posix_runtime_directory_names(
    directory: _RetainedRuntimeDirectory,
) -> tuple[str, ...]:
    try:
        raw_names = os.listdir(directory.descriptor)
    except OSError as exc:
        _fail(
            "runtime_snapshot_read_failed",
            f"could not enumerate retained runtime directory {directory.relative or '.'}: {exc}",
        )
    names: list[str] = []
    for name in sorted(raw_names, key=os.fsencode):
        if type(name) is not str or not name or name in {".", ".."}:
            _fail("runtime_snapshot_tree_unsafe", "runtime directory returned an invalid name")
        relative = f"{directory.relative}/{name}" if directory.relative else name
        _runtime_relative_path(relative)
        try:
            info = os.stat(
                name,
                dir_fd=directory.descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            _fail(
                "runtime_snapshot_changed",
                f"runtime entry changed during enumeration {relative}: {exc}",
            )
        if _runtime_entry_kind(info, name, relative) != "ignored":
            names.append(name)
    folded = [name.casefold() for name in names]
    if len(folded) != len(set(folded)):
        _fail(
            "runtime_snapshot_tree_unsafe",
            f"runtime directory contains a casefold collision: {directory.relative or '.'}",
        )
    return tuple(names)


def _close_runtime_descriptors(descriptors: Sequence[int]) -> None:
    primary = sys.exception()
    cleanup_error: OSError | None = None
    for descriptor in reversed(tuple(descriptors)):
        try:
            os.close(descriptor)
        except OSError as exc:
            if primary is not None:
                primary.add_note(f"runtime retained descriptor cleanup failed: {exc}")
            elif cleanup_error is None:
                cleanup_error = exc
    if cleanup_error is not None:
        _fail(
            "runtime_snapshot_cleanup_failed",
            f"could not close retained runtime descriptor: {cleanup_error}",
        )


def _capture_runtime_files_posix(
    root: Path,
    *,
    hook: _RuntimeTreeHook | None,
    retained_root_fd: int | None = None,
) -> dict[str, bytes]:
    parent_descriptor: int | None = None
    retained_directories: list[_RetainedRuntimeDirectory] = []
    retained_files: list[_RetainedRuntimeFile] = []
    descriptors: list[int] = []
    try:
        if retained_root_fd is None:
            parent_descriptor = os.open(
                root.parent,
                _posix_runtime_open_flags(directory=True),
            )
            descriptors.append(parent_descriptor)
            parent_info = descriptor_file_stat(parent_descriptor)
            root_descriptor = os.open(
                root.name,
                _posix_runtime_open_flags(directory=True),
                dir_fd=parent_descriptor,
            )
            descriptors.append(root_descriptor)
            root_info = descriptor_file_stat(root_descriptor)
            root_named = os.stat(
                root.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            lexical_root = path_file_stat(root)
            if (
                not _runtime_directory_valid(parent_info)
                or not _runtime_directory_valid(root_info)
                or not _runtime_directory_valid(root_named)
                or not _runtime_directory_valid(lexical_root)
                or file_identity(root_info) != file_identity(root_named)
                or file_identity(root_info) != file_identity(lexical_root)
            ):
                _fail(
                    "runtime_snapshot_root_invalid",
                    "runtime kernel root must resolve to one retained real directory",
                )
            root_parent_descriptor = parent_descriptor
        else:
            retained_authority = descriptor_file_stat(retained_root_fd)
            root_descriptor = os.open(
                ".",
                _posix_runtime_open_flags(directory=True),
                dir_fd=retained_root_fd,
            )
            descriptors.append(root_descriptor)
            root_info = descriptor_file_stat(root_descriptor)
            if (
                not _runtime_directory_valid(retained_authority)
                or not _runtime_directory_valid(root_info)
                or file_identity(retained_authority) != file_identity(root_info)
            ):
                _fail(
                    "runtime_snapshot_root_invalid",
                    "retained runtime root descriptor changed",
                )
            root_parent_descriptor = root_descriptor
        retained_directories.append(
            _RetainedRuntimeDirectory(
                relative="",
                name=root.name,
                descriptor=root_descriptor,
                parent_descriptor=root_parent_descriptor,
                identity=file_identity(root_info),
            )
        )
        _invoke_runtime_tree_hook(hook, "after_root_retained")

        pending_index = 0
        total_bytes = 0
        while pending_index < len(retained_directories):
            directory = retained_directories[pending_index]
            pending_index += 1
            directory.names = _posix_runtime_directory_names(directory)
            for name in directory.names:
                relative = f"{directory.relative}/{name}" if directory.relative else name
                try:
                    initial = os.stat(
                        name,
                        dir_fd=directory.descriptor,
                        follow_symlinks=False,
                    )
                    kind = _runtime_entry_kind(initial, name, relative)
                    child_descriptor = os.open(
                        name,
                        _posix_runtime_open_flags(directory=kind == "directory"),
                        dir_fd=directory.descriptor,
                    )
                    descriptors.append(child_descriptor)
                    opened = descriptor_file_stat(child_descriptor)
                    named = os.stat(
                        name,
                        dir_fd=directory.descriptor,
                        follow_symlinks=False,
                    )
                except RuntimeContractError:
                    raise
                except OSError as exc:
                    _fail(
                        "runtime_snapshot_changed",
                        f"could not retain runtime entry {relative}: {exc}",
                    )
                if kind == "directory":
                    if (
                        not _runtime_directory_valid(opened)
                        or not _runtime_directory_valid(named)
                        or file_identity(initial) != file_identity(opened)
                        or file_identity(opened) != file_identity(named)
                    ):
                        _fail(
                            "runtime_snapshot_changed",
                            f"runtime directory changed before retention: {relative}",
                        )
                    retained_directories.append(
                        _RetainedRuntimeDirectory(
                            relative=relative,
                            name=name,
                            descriptor=child_descriptor,
                            parent_descriptor=directory.descriptor,
                            identity=file_identity(opened),
                        )
                    )
                    _invoke_runtime_tree_hook(
                        hook,
                        "after_directory_retained",
                        relative,
                    )
                else:
                    if (
                        not _runtime_file_valid(opened)
                        or not _runtime_file_valid(named)
                        or _runtime_entry_state(initial) != _runtime_entry_state(opened)
                        or _runtime_entry_state(opened) != _runtime_entry_state(named)
                    ):
                        _fail(
                            "runtime_snapshot_changed",
                            f"runtime file changed before retention: {relative}",
                        )
                    if opened.st_size > MAX_RUNTIME_FILE_BYTES:
                        _fail(
                            "runtime_snapshot_limit",
                            f"runtime file exceeds {MAX_RUNTIME_FILE_BYTES} bytes: {relative}",
                        )
                    _invoke_runtime_tree_hook(
                        hook,
                        "after_file_retained",
                        relative,
                    )
                    payload = _read_runtime_descriptor(child_descriptor, relative)
                    retained = descriptor_file_stat(child_descriptor)
                    if (
                        not _runtime_file_valid(retained)
                        or file_identity(opened) != file_identity(retained)
                        or retained.st_size != len(payload)
                    ):
                        _fail(
                            "runtime_snapshot_changed",
                            f"runtime file changed during retained read: {relative}",
                        )
                    _invoke_runtime_tree_hook(hook, "after_file_read", relative)
                    retained_files.append(
                        _RetainedRuntimeFile(
                            relative=relative,
                            name=name,
                            descriptor=child_descriptor,
                            parent_descriptor=directory.descriptor,
                            state=_runtime_entry_state(opened),
                            payload=payload,
                        )
                    )
                    total_bytes += len(payload)
                if (
                    len(retained_directories) + len(retained_files) > MAX_RUNTIME_ITEMS
                    or len(retained_files) > MAX_RUNTIME_FILES
                    or total_bytes > MAX_RUNTIME_TREE_BYTES
                ):
                    _fail("runtime_snapshot_limit", "runtime code tree exceeds its bounds")

        if not retained_files:
            _fail("runtime_snapshot_empty", "runtime kernel has no retained files")
        _invoke_runtime_tree_hook(hook, "before_final_verification")

        retained_root = descriptor_file_stat(root_descriptor)
        if (
            not _runtime_directory_valid(retained_root)
            or file_identity(retained_root) != retained_directories[0].identity
        ):
            _fail("runtime_snapshot_changed", "retained runtime root identity changed")
        if retained_root_fd is None:
            assert parent_descriptor is not None
            if not _runtime_directory_valid(descriptor_file_stat(parent_descriptor)):
                _fail("runtime_snapshot_changed", "retained runtime parent changed")
            rebound_root = os.open(
                root.name,
                _posix_runtime_open_flags(directory=True),
                dir_fd=parent_descriptor,
            )
            try:
                rebound_root_info = descriptor_file_stat(rebound_root)
            finally:
                os.close(rebound_root)
            lexical_root = path_file_stat(root)
            if (
                not _runtime_directory_valid(rebound_root_info)
                or not _runtime_directory_valid(lexical_root)
                or file_identity(rebound_root_info) != retained_directories[0].identity
                or file_identity(lexical_root) != retained_directories[0].identity
            ):
                _fail(
                    "runtime_snapshot_changed",
                    "runtime root name no longer resolves to the retained root",
                )
        else:
            retained_authority = descriptor_file_stat(retained_root_fd)
            if (
                not _runtime_directory_valid(retained_authority)
                or file_identity(retained_authority) != retained_directories[0].identity
            ):
                _fail(
                    "runtime_snapshot_changed",
                    "retained runtime root authority changed",
                )

        for directory in retained_directories:
            retained = descriptor_file_stat(directory.descriptor)
            if (
                not _runtime_directory_valid(retained)
                or file_identity(retained) != directory.identity
                or _posix_runtime_directory_names(directory) != directory.names
            ):
                _fail(
                    "runtime_snapshot_changed",
                    f"retained runtime directory changed: {directory.relative or '.'}",
                )
            if directory.relative:
                rebound = os.open(
                    directory.name,
                    _posix_runtime_open_flags(directory=True),
                    dir_fd=directory.parent_descriptor,
                )
                try:
                    rebound_info = descriptor_file_stat(rebound)
                finally:
                    os.close(rebound)
                if (
                    not _runtime_directory_valid(rebound_info)
                    or file_identity(rebound_info) != directory.identity
                ):
                    _fail(
                        "runtime_snapshot_changed",
                        f"runtime directory binding changed: {directory.relative}",
                    )

        files: dict[str, bytes] = {}
        for retained_file in retained_files:
            retained = descriptor_file_stat(retained_file.descriptor)
            if (
                not _runtime_file_valid(retained)
                or _runtime_entry_state(retained) != retained_file.state
            ):
                _fail(
                    "runtime_snapshot_changed",
                    f"retained runtime file changed: {retained_file.relative}",
                )
            rebound = os.open(
                retained_file.name,
                _posix_runtime_open_flags(directory=False),
                dir_fd=retained_file.parent_descriptor,
            )
            try:
                rebound_info = descriptor_file_stat(rebound)
            finally:
                os.close(rebound)
            if (
                not _runtime_file_valid(rebound_info)
                or _runtime_entry_state(rebound_info) != retained_file.state
                or _read_runtime_descriptor(
                    retained_file.descriptor,
                    retained_file.relative,
                )
                != retained_file.payload
                or _runtime_entry_state(descriptor_file_stat(retained_file.descriptor))
                != retained_file.state
            ):
                _fail(
                    "runtime_snapshot_changed",
                    f"runtime file binding or bytes changed: {retained_file.relative}",
                )
            files[f"gamepack_runtime/{retained_file.relative}"] = retained_file.payload
        return files
    except RuntimeContractError:
        raise
    except OSError as exc:
        _fail("runtime_snapshot_read_failed", f"could not retain runtime tree {root}: {exc}")
    finally:
        _close_runtime_descriptors(descriptors)


class _RuntimeWindowsUnicodeString(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ushort),
        ("maximum_length", ctypes.c_ushort),
        ("buffer", ctypes.c_void_p),
    ]


class _RuntimeWindowsObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("root_directory", ctypes.c_void_p),
        ("object_name", ctypes.POINTER(_RuntimeWindowsUnicodeString)),
        ("attributes", ctypes.c_ulong),
        ("security_descriptor", ctypes.c_void_p),
        ("security_quality_of_service", ctypes.c_void_p),
    ]


class _RuntimeWindowsIoStatusBlock(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_void_p),
        ("information", ctypes.c_size_t),
    ]


class _WindowsRuntimeTreeApi:
    _FILE_READ_DATA = 0x00000001
    _FILE_LIST_DIRECTORY = 0x00000001
    _FILE_READ_ATTRIBUTES = 0x00000080
    _SYNCHRONIZE = 0x00100000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _OPEN_EXISTING = 3
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_OPEN = 1
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _FILE_NAMES_INFORMATION = 12
    _FILE_BEGIN = 0
    _STATUS_BUFFER_OVERFLOW = 0x80000005
    _STATUS_NO_MORE_FILES = 0x80000006
    _STATUS_NOT_A_DIRECTORY = 0xC0000103
    _QUERY_BUFFER_BYTES = 64 * 1024

    def __init__(
        self,
        *,
        stage_capability: _RuntimeStageReadCapability | None = None,
    ) -> None:
        win_dll = getattr(ctypes, "WinDLL", None)
        if os.name != "nt" or win_dll is None:
            raise OSError("native Windows runtime tree APIs are unavailable")
        self._share_mode = self._share_mode_for(stage_capability)
        self._kernel32 = win_dll("kernel32", use_last_error=True)
        self._ntdll = win_dll("ntdll", use_last_error=True)
        self._invalid_handle = ctypes.c_void_p(-1).value

        self._create_file = self._kernel32.CreateFileW
        self._create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self._create_file.restype = ctypes.c_void_p

        self._close_handle = self._kernel32.CloseHandle
        self._close_handle.argtypes = [ctypes.c_void_p]
        self._close_handle.restype = ctypes.c_int

        self._read_file = self._kernel32.ReadFile
        self._read_file.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        self._read_file.restype = ctypes.c_int

        self._set_file_pointer = self._kernel32.SetFilePointerEx
        self._set_file_pointer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.POINTER(ctypes.c_int64),
            ctypes.c_uint32,
        ]
        self._set_file_pointer.restype = ctypes.c_int

        self._nt_create_file = self._ntdll.NtCreateFile
        self._nt_create_file.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint32,
            ctypes.POINTER(_RuntimeWindowsObjectAttributes),
            ctypes.POINTER(_RuntimeWindowsIoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._nt_create_file.restype = ctypes.c_long

        self._nt_query_directory = self._ntdll.NtQueryDirectoryFile
        self._nt_query_directory.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(_RuntimeWindowsIoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_ubyte,
            ctypes.c_void_p,
            ctypes.c_ubyte,
        ]
        self._nt_query_directory.restype = ctypes.c_long

        self._rtl_nt_status_to_dos_error = self._ntdll.RtlNtStatusToDosError
        self._rtl_nt_status_to_dos_error.argtypes = [ctypes.c_long]
        self._rtl_nt_status_to_dos_error.restype = ctypes.c_ulong

    @classmethod
    def _share_mode_for(
        cls,
        stage_capability: _RuntimeStageReadCapability | None,
    ) -> int:
        if (
            stage_capability is not None
            and type(stage_capability) is not _RuntimeStageReadCapability
        ):
            raise TypeError("runtime stage capability has an invalid type")
        share = cls._FILE_SHARE_READ
        if stage_capability is not None:
            share |= cls._FILE_SHARE_WRITE
        return share

    @staticmethod
    def _unsigned_status(status: int) -> int:
        return ctypes.c_uint32(status).value

    def _nt_error(self, status: int, context: str) -> OSError:
        mapped = int(self._rtl_nt_status_to_dos_error(ctypes.c_long(ctypes.c_int32(status).value)))
        error = ctypes.WinError(mapped)
        return OSError(
            error.errno,
            f"{context}: {error.strerror}",
            None,
            mapped,
        )

    @staticmethod
    def _validate_component(name: str) -> None:
        if (
            type(name) is not str
            or not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\x00" in name
        ):
            _fail(
                "runtime_snapshot_tree_unsafe",
                "runtime tree returned an invalid Windows path component",
            )

    def open_path_directory(self, path: Path) -> int:
        handle = self._create_file(
            str(path),
            self._FILE_LIST_DIRECTORY | self._FILE_READ_ATTRIBUTES | self._SYNCHRONIZE,
            self._share_mode,
            None,
            self._OPEN_EXISTING,
            self._FILE_FLAG_OPEN_REPARSE_POINT | self._FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle in {None, self._invalid_handle}:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(handle)

    def _nt_open(
        self,
        parent_handle: int,
        name: str,
        *,
        directory: bool,
    ) -> tuple[int | None, int]:
        self._validate_component(name)
        encoded_name = name.encode("utf-16-le", errors="strict")
        if len(encoded_name) > 0xFFFC:
            _fail(
                "runtime_snapshot_tree_unsafe",
                "runtime tree returned an overlong Windows path component",
            )
        name_buffer = ctypes.create_unicode_buffer(name)
        unicode_name = _RuntimeWindowsUnicodeString(
            length=len(encoded_name),
            maximum_length=len(encoded_name) + ctypes.sizeof(ctypes.c_wchar),
            buffer=ctypes.cast(name_buffer, ctypes.c_void_p),
        )
        attributes = _RuntimeWindowsObjectAttributes(
            length=ctypes.sizeof(_RuntimeWindowsObjectAttributes),
            root_directory=ctypes.c_void_p(parent_handle),
            object_name=ctypes.pointer(unicode_name),
            attributes=self._OBJ_CASE_INSENSITIVE,
            security_descriptor=None,
            security_quality_of_service=None,
        )
        io_status = _RuntimeWindowsIoStatusBlock()
        opened = ctypes.c_void_p()
        access = (
            (self._FILE_LIST_DIRECTORY if directory else self._FILE_READ_DATA)
            | self._FILE_READ_ATTRIBUTES
            | self._SYNCHRONIZE
        )
        options = self._FILE_DIRECTORY_FILE if directory else self._FILE_NON_DIRECTORY_FILE
        options |= (
            self._FILE_SYNCHRONOUS_IO_NONALERT
            | self._FILE_OPEN_REPARSE_POINT
            | self._FILE_OPEN_FOR_BACKUP_INTENT
        )
        status = int(
            self._nt_create_file(
                ctypes.byref(opened),
                access,
                ctypes.byref(attributes),
                ctypes.byref(io_status),
                None,
                0,
                self._share_mode,
                self._FILE_OPEN,
                options,
                None,
                0,
            )
        )
        status_unsigned = self._unsigned_status(status)
        if status < 0:
            return None, status_unsigned
        if opened.value in {None, self._invalid_handle}:
            _fail(
                "runtime_snapshot_read_failed",
                f"Windows returned no handle for retained runtime entry {name}",
            )
        return int(opened.value), status_unsigned

    def open_relative(
        self,
        parent_handle: int,
        name: str,
        *,
        directory: bool,
    ) -> int:
        handle, status = self._nt_open(
            parent_handle,
            name,
            directory=directory,
        )
        if handle is None:
            raise self._nt_error(
                status,
                f"could not retain Windows runtime entry {name}",
            )
        return handle

    def _query_names(
        self,
        directory_handle: int,
        relative: str,
    ) -> tuple[str, ...]:
        names: list[str] = []
        first_query = True
        while True:
            io_status = _RuntimeWindowsIoStatusBlock()
            buffer = ctypes.create_string_buffer(self._QUERY_BUFFER_BYTES)
            status = int(
                self._nt_query_directory(
                    ctypes.c_void_p(directory_handle),
                    None,
                    None,
                    None,
                    ctypes.byref(io_status),
                    buffer,
                    len(buffer),
                    self._FILE_NAMES_INFORMATION,
                    0,
                    None,
                    int(first_query),
                )
            )
            first_query = False
            status_unsigned = self._unsigned_status(status)
            if status_unsigned == self._STATUS_NO_MORE_FILES:
                break
            if status_unsigned not in {0, self._STATUS_BUFFER_OVERFLOW}:
                raise self._nt_error(
                    status_unsigned,
                    f"could not enumerate Windows runtime directory {relative or '.'}",
                )
            used = int(io_status.information)
            if used < 0 or used > len(buffer):
                _fail(
                    "runtime_snapshot_changed",
                    f"Windows returned an invalid directory inventory for {relative or '.'}",
                )
            offset = 0
            while offset < used:
                if used - offset < 12:
                    _fail(
                        "runtime_snapshot_changed",
                        f"Windows returned a truncated directory entry for {relative or '.'}",
                    )
                next_offset = int.from_bytes(
                    buffer.raw[offset : offset + 4],
                    "little",
                )
                name_bytes = int.from_bytes(
                    buffer.raw[offset + 8 : offset + 12],
                    "little",
                )
                name_end = offset + 12 + name_bytes
                if (
                    name_bytes % 2
                    or name_end > used
                    or (
                        next_offset != 0
                        and (next_offset < 12 + name_bytes or offset + next_offset > used)
                    )
                ):
                    _fail(
                        "runtime_snapshot_changed",
                        f"Windows returned an invalid directory entry for {relative or '.'}",
                    )
                try:
                    name = buffer.raw[offset + 12 : name_end].decode(
                        "utf-16-le",
                        errors="strict",
                    )
                except UnicodeError as exc:
                    _fail(
                        "runtime_snapshot_tree_unsafe",
                        f"Windows returned a non-Unicode runtime entry name: {exc}",
                    )
                if name not in {".", ".."}:
                    self._validate_component(name)
                    names.append(name)
                if next_offset == 0:
                    break
                offset += next_offset
            if status_unsigned == 0 and used == 0:
                break
        if len(names) != len(set(names)):
            _fail(
                "runtime_snapshot_changed",
                f"Windows returned duplicate directory entries for {relative or '.'}",
            )
        return tuple(sorted(names, key=lambda item: item.encode("utf-8")))

    def open_directory_entries(
        self,
        directory_handle: int,
        relative: str,
    ) -> tuple[tuple[str, ...], list[tuple[str, int, str, FileStat]]]:
        retained_names: list[str] = []
        entries: list[tuple[str, int, str, FileStat]] = []
        opened_handles: list[int] = []
        try:
            for name in self._query_names(directory_handle, relative):
                child_relative = f"{relative}/{name}" if relative else name
                _runtime_relative_path(child_relative)
                directory_child, status = self._nt_open(
                    directory_handle,
                    name,
                    directory=True,
                )
                if directory_child is not None:
                    handle = directory_child
                    expected_kind = "directory"
                elif status == self._STATUS_NOT_A_DIRECTORY:
                    file_child, file_status = self._nt_open(
                        directory_handle,
                        name,
                        directory=False,
                    )
                    if file_child is None:
                        raise self._nt_error(
                            file_status,
                            f"could not retain Windows runtime entry {child_relative}",
                        )
                    handle = file_child
                    expected_kind = "file"
                else:
                    raise self._nt_error(
                        status,
                        f"could not retain Windows runtime entry {child_relative}",
                    )
                opened_handles.append(handle)
                info = windows_handle_file_stat(handle)
                kind = _runtime_entry_kind(info, name, child_relative)
                if kind == "ignored":
                    self.close(handle)
                    opened_handles.pop()
                    continue
                if kind != expected_kind:
                    _fail(
                        "runtime_snapshot_changed",
                        f"runtime entry type changed during retention: {child_relative}",
                    )
                retained_names.append(name)
                entries.append((name, handle, kind, info))
            folded = [name.casefold() for name in retained_names]
            if len(folded) != len(set(folded)):
                _fail(
                    "runtime_snapshot_tree_unsafe",
                    f"runtime directory contains a casefold collision: {relative or '.'}",
                )
            return tuple(retained_names), entries
        except BaseException:
            self.close_many(opened_handles)
            raise

    def directory_names(
        self,
        directory_handle: int,
        relative: str,
    ) -> tuple[str, ...]:
        names, entries = self.open_directory_entries(
            directory_handle,
            relative,
        )
        self.close_many([handle for _name, handle, _kind, _info in entries])
        return names

    def read_file(self, handle: int, relative: str) -> bytes:
        position = ctypes.c_int64()
        if not self._set_file_pointer(
            ctypes.c_void_p(handle),
            0,
            ctypes.byref(position),
            self._FILE_BEGIN,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = MAX_RUNTIME_FILE_BYTES + 1 - total
            size = min(1024 * 1024, remaining)
            buffer = ctypes.create_string_buffer(size)
            read = ctypes.c_uint32()
            if not self._read_file(
                ctypes.c_void_p(handle),
                buffer,
                size,
                ctypes.byref(read),
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            count = int(read.value)
            if count == 0:
                break
            chunks.append(buffer.raw[:count])
            total += count
            if total > MAX_RUNTIME_FILE_BYTES:
                _fail(
                    "runtime_snapshot_limit",
                    f"runtime file exceeds {MAX_RUNTIME_FILE_BYTES} bytes: {relative}",
                )
        return b"".join(chunks)

    def close(self, handle: int) -> None:
        if not self._close_handle(ctypes.c_void_p(handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close_many(self, handles: Sequence[int]) -> None:
        primary = sys.exception()
        cleanup_error: OSError | None = None
        for handle in reversed(tuple(handles)):
            try:
                self.close(handle)
            except OSError as exc:
                if primary is not None:
                    primary.add_note(f"runtime retained Windows handle cleanup failed: {exc}")
                elif cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            _fail(
                "runtime_snapshot_cleanup_failed",
                f"could not close retained Windows runtime handle: {cleanup_error}",
            )


def _capture_runtime_files_windows(
    root: Path,
    *,
    hook: _RuntimeTreeHook | None,
    stage_capability: _RuntimeStageReadCapability | None,
) -> dict[str, bytes]:
    api = _WindowsRuntimeTreeApi(stage_capability=stage_capability)
    handles: list[int] = []
    retained_directories: list[_RetainedRuntimeDirectory] = []
    retained_files: list[_RetainedRuntimeFile] = []
    try:
        parent_handle = api.open_path_directory(root.parent)
        handles.append(parent_handle)
        parent_info = windows_handle_file_stat(parent_handle)
        if not _runtime_directory_valid(parent_info):
            _fail(
                "runtime_snapshot_root_invalid",
                "runtime kernel parent must be a retained real directory",
            )
        root_handle = api.open_relative(
            parent_handle,
            root.name,
            directory=True,
        )
        handles.append(root_handle)
        root_info = windows_handle_file_stat(root_handle)
        lexical_root = path_file_stat(root)
        if (
            not _runtime_directory_valid(root_info)
            or not _runtime_directory_valid(lexical_root)
            or file_identity(root_info) != file_identity(lexical_root)
        ):
            _fail(
                "runtime_snapshot_root_invalid",
                "runtime kernel root must resolve to one retained real directory",
            )
        retained_directories.append(
            _RetainedRuntimeDirectory(
                relative="",
                name=root.name,
                descriptor=root_handle,
                parent_descriptor=parent_handle,
                identity=file_identity(root_info),
            )
        )
        _invoke_runtime_tree_hook(hook, "after_root_retained")

        pending_index = 0
        total_bytes = 0
        while pending_index < len(retained_directories):
            directory = retained_directories[pending_index]
            pending_index += 1
            names, opened_entries = api.open_directory_entries(
                directory.descriptor,
                directory.relative,
            )
            directory.names = names
            handles.extend(handle for _name, handle, _kind, _info in opened_entries)
            for name, child_handle, kind, opened in opened_entries:
                relative = f"{directory.relative}/{name}" if directory.relative else name
                rebound = api.open_relative(
                    directory.descriptor,
                    name,
                    directory=kind == "directory",
                )
                try:
                    rebound_info = windows_handle_file_stat(rebound)
                finally:
                    api.close(rebound)
                if kind == "directory":
                    if (
                        not _runtime_directory_valid(opened)
                        or not _runtime_directory_valid(rebound_info)
                        or file_identity(opened) != file_identity(rebound_info)
                    ):
                        _fail(
                            "runtime_snapshot_changed",
                            f"runtime directory changed before retention: {relative}",
                        )
                    retained_directories.append(
                        _RetainedRuntimeDirectory(
                            relative=relative,
                            name=name,
                            descriptor=child_handle,
                            parent_descriptor=directory.descriptor,
                            identity=file_identity(opened),
                        )
                    )
                    _invoke_runtime_tree_hook(
                        hook,
                        "after_directory_retained",
                        relative,
                    )
                else:
                    if (
                        not _runtime_file_valid(opened)
                        or not _runtime_file_valid(rebound_info)
                        or _runtime_entry_state(opened) != _runtime_entry_state(rebound_info)
                    ):
                        _fail(
                            "runtime_snapshot_changed",
                            f"runtime file changed before retention: {relative}",
                        )
                    if opened.st_size > MAX_RUNTIME_FILE_BYTES:
                        _fail(
                            "runtime_snapshot_limit",
                            f"runtime file exceeds {MAX_RUNTIME_FILE_BYTES} bytes: {relative}",
                        )
                    _invoke_runtime_tree_hook(
                        hook,
                        "after_file_retained",
                        relative,
                    )
                    payload = api.read_file(child_handle, relative)
                    retained = windows_handle_file_stat(child_handle)
                    if (
                        not _runtime_file_valid(retained)
                        or file_identity(opened) != file_identity(retained)
                        or retained.st_size != len(payload)
                    ):
                        _fail(
                            "runtime_snapshot_changed",
                            f"runtime file changed during retained read: {relative}",
                        )
                    _invoke_runtime_tree_hook(hook, "after_file_read", relative)
                    retained_files.append(
                        _RetainedRuntimeFile(
                            relative=relative,
                            name=name,
                            descriptor=child_handle,
                            parent_descriptor=directory.descriptor,
                            state=_runtime_entry_state(opened),
                            payload=payload,
                        )
                    )
                    total_bytes += len(payload)
                if (
                    len(retained_directories) + len(retained_files) > MAX_RUNTIME_ITEMS
                    or len(retained_files) > MAX_RUNTIME_FILES
                    or total_bytes > MAX_RUNTIME_TREE_BYTES
                ):
                    _fail("runtime_snapshot_limit", "runtime code tree exceeds its bounds")

        if not retained_files:
            _fail("runtime_snapshot_empty", "runtime kernel has no retained files")
        _invoke_runtime_tree_hook(hook, "before_final_verification")

        retained_parent = windows_handle_file_stat(parent_handle)
        retained_root = windows_handle_file_stat(root_handle)
        rebound_root = api.open_relative(
            parent_handle,
            root.name,
            directory=True,
        )
        try:
            rebound_root_info = windows_handle_file_stat(rebound_root)
        finally:
            api.close(rebound_root)
        lexical_parent = path_file_stat(root.parent)
        lexical_root = path_file_stat(root)
        if (
            not _runtime_directory_valid(retained_parent)
            or not _runtime_directory_valid(retained_root)
            or not _runtime_directory_valid(rebound_root_info)
            or not _runtime_directory_valid(lexical_parent)
            or not _runtime_directory_valid(lexical_root)
            or file_identity(retained_parent) != file_identity(lexical_parent)
            or file_identity(retained_root) != retained_directories[0].identity
            or file_identity(rebound_root_info) != retained_directories[0].identity
            or file_identity(lexical_root) != retained_directories[0].identity
        ):
            _fail(
                "runtime_snapshot_changed",
                "runtime root name no longer resolves to the retained Windows root",
            )

        for directory in retained_directories:
            retained = windows_handle_file_stat(directory.descriptor)
            if (
                not _runtime_directory_valid(retained)
                or file_identity(retained) != directory.identity
                or api.directory_names(
                    directory.descriptor,
                    directory.relative,
                )
                != directory.names
            ):
                _fail(
                    "runtime_snapshot_changed",
                    f"retained runtime directory changed: {directory.relative or '.'}",
                )
            if directory.relative:
                rebound = api.open_relative(
                    directory.parent_descriptor,
                    directory.name,
                    directory=True,
                )
                try:
                    rebound_info = windows_handle_file_stat(rebound)
                finally:
                    api.close(rebound)
                if (
                    not _runtime_directory_valid(rebound_info)
                    or file_identity(rebound_info) != directory.identity
                ):
                    _fail(
                        "runtime_snapshot_changed",
                        f"runtime directory binding changed: {directory.relative}",
                    )

        files: dict[str, bytes] = {}
        for retained_file in retained_files:
            retained = windows_handle_file_stat(retained_file.descriptor)
            rebound = api.open_relative(
                retained_file.parent_descriptor,
                retained_file.name,
                directory=False,
            )
            try:
                rebound_info = windows_handle_file_stat(rebound)
            finally:
                api.close(rebound)
            if (
                not _runtime_file_valid(retained)
                or not _runtime_file_valid(rebound_info)
                or _runtime_entry_state(retained) != retained_file.state
                or _runtime_entry_state(rebound_info) != retained_file.state
                or api.read_file(
                    retained_file.descriptor,
                    retained_file.relative,
                )
                != retained_file.payload
                or _runtime_entry_state(windows_handle_file_stat(retained_file.descriptor))
                != retained_file.state
            ):
                _fail(
                    "runtime_snapshot_changed",
                    f"runtime file binding or bytes changed: {retained_file.relative}",
                )
            files[f"gamepack_runtime/{retained_file.relative}"] = retained_file.payload
        return files
    except RuntimeContractError:
        raise
    except OSError as exc:
        _fail(
            "runtime_snapshot_read_failed",
            f"could not retain Windows runtime tree {root}: {exc}",
        )
    finally:
        api.close_many(handles)


def _capture_runtime_files(
    root: Path,
    *,
    package_name: str = "gamepack_runtime",
    _verification_hook: _RuntimeTreeHook | None = None,
    _retained_root_fd: int | None = None,
    _stage_capability: _RuntimeStageReadCapability | None = None,
) -> dict[str, bytes]:
    if package_name not in {"gamepack_runtime", "gamepack_raylib_2d"}:
        _fail(
            "runtime_snapshot_root_invalid",
            "runtime package identity is not code-owned",
        )
    root = Path(os.path.abspath(os.fspath(root)))
    if _stage_capability is not None:
        if (
            type(_stage_capability) is not _RuntimeStageReadCapability
            or _stage_capability.root != root
        ):
            _fail(
                "runtime_snapshot_root_invalid",
                "runtime stage capability does not bind the requested root",
            )
        _stage_capability.require_binding()
    if os.name == "nt":
        if _retained_root_fd is not None:
            _fail(
                "runtime_snapshot_root_invalid",
                "retained POSIX runtime descriptor is unavailable on Windows",
            )
        captured = _capture_runtime_files_windows(
            root,
            hook=_verification_hook,
            stage_capability=_stage_capability,
        )
    else:
        captured = _capture_runtime_files_posix(
            root,
            hook=_verification_hook,
            retained_root_fd=_retained_root_fd,
        )
    if _stage_capability is not None:
        _stage_capability.require_binding()
    if package_name == "gamepack_runtime":
        return captured
    prefix = "gamepack_runtime/"
    if any(not path.startswith(prefix) for path in captured):
        _fail(
            "runtime_snapshot_root_invalid",
            "retained runtime file prefix is not canonical",
        )
    return {
        f"{package_name}/{path.removeprefix(prefix)}": payload for path, payload in captured.items()
    }


def _build_game_runtime_snapshot_from_files(
    files: Mapping[str, bytes],
    adapters: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    checked_adapters = _trusted_adapter_documents(adapters)
    captured = dict(files)
    for adapter in checked_adapters:
        virtual_path = f"descriptors/{adapter['adapter_id']}@{adapter['adapter_version']}.json"
        captured[virtual_path] = serialize_runtime_adapter(adapter)
    entries = [
        {
            "path": path,
            "sha256": hashlib.sha256(captured[path]).hexdigest(),
            "size_bytes": len(captured[path]),
        }
        for path in sorted(captured, key=lambda item: item.encode("utf-8"))
    ]
    tree_hash = _canonical_hash({"files": entries})
    document: dict[str, Any] = {
        "format": RUNTIME_SNAPSHOT_FORMAT,
        "format_version": RUNTIME_CONTRACT_VERSION,
        "snapshot_id": "",
        "runtime_api": {"id": "gamepack_runtime", "version": "1.0.0"},
        "adapter_descriptors": [
            _identity(adapter, id_field="adapter_id") for adapter in checked_adapters
        ],
        "files": entries,
        "tree_hash": tree_hash,
        "content_hash": "",
    }
    document["snapshot_id"] = _derived_snapshot_id(document)
    return validate_runtime_snapshot_document(_seal(document))


def build_game_runtime_snapshot(
    kernel_root: str | Path,
    *,
    adapter_runtime_root: str | Path | None = None,
    adapters: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    """Hash exact runtime packages plus code-owned declarative adapter documents."""

    checked_adapters = _trusted_adapter_documents(adapters)
    captured = _capture_runtime_files(Path(kernel_root))
    if adapter_runtime_root is not None:
        adapter_files = _capture_runtime_files(
            Path(adapter_runtime_root),
            package_name="gamepack_raylib_2d",
        )
        collisions = set(captured).intersection(adapter_files)
        if collisions:
            _fail(
                "runtime_snapshot_collision",
                "runtime package file paths collide",
            )
        captured.update(adapter_files)
    return _build_game_runtime_snapshot_from_files(
        captured,
        checked_adapters,
    )


def validate_runtime_snapshot_document(value: object) -> dict[str, Any]:
    try:
        owned = _snapshot_runtime_json(value, "runtime snapshot")
        _validate_json_structure(owned, context="runtime snapshot")
        document = _object(owned, "runtime snapshot")
        _exact_keys(document, _SNAPSHOT_FIELDS, "runtime snapshot")
        if document.get("format") != RUNTIME_SNAPSHOT_FORMAT:
            _fail("runtime_snapshot_format_invalid", f"format must be {RUNTIME_SNAPSHOT_FORMAT}")
        if document.get("format_version") != RUNTIME_CONTRACT_VERSION:
            _fail("runtime_snapshot_version_invalid", "format_version must be 1")
        _identifier(document.get("snapshot_id"), "runtime snapshot.snapshot_id")
        _runtime_api(document.get("runtime_api"), "runtime snapshot.runtime_api")
        descriptors = document.get("adapter_descriptors")
        if (
            not isinstance(descriptors, list)
            or not descriptors
            or len(descriptors) > MAX_RUNTIME_ADAPTERS
        ):
            _fail("runtime_snapshot_limit", "adapter_descriptors must be bounded")
        checked_descriptors = [
            _validate_identity(
                item,
                f"runtime snapshot.adapter_descriptors/{index}",
                expected_format=RUNTIME_ADAPTER_FORMAT,
            )
            for index, item in enumerate(descriptors)
        ]
        descriptor_ids = [item["id"] for item in checked_descriptors]
        if descriptor_ids != sorted(descriptor_ids, key=lambda item: item.encode("utf-8")):
            _fail("runtime_contract_noncanonical", "adapter_descriptors are not canonical")
        if len({item.casefold() for item in descriptor_ids}) != len(descriptor_ids):
            _fail("runtime_contract_collision", "adapter descriptor IDs collide")

        raw_files = document.get("files")
        if not isinstance(raw_files, list) or not raw_files or len(raw_files) > MAX_RUNTIME_FILES:
            _fail("runtime_snapshot_limit", "files must be bounded")
        paths: list[str] = []
        total_bytes = 0
        files_by_path: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(raw_files):
            context = f"runtime snapshot.files/{index}"
            item = _object(raw, context)
            _exact_keys(item, _SNAPSHOT_FILE_FIELDS, context)
            path = _portable_relative_path(item.get("path"), f"{context}.path")
            _sha256(item.get("sha256"), f"{context}.sha256")
            size_bytes = _integer(item.get("size_bytes"), f"{context}.size_bytes", minimum=0)
            if size_bytes > MAX_RUNTIME_FILE_BYTES:
                _fail(
                    "runtime_snapshot_limit",
                    f"{context}.size_bytes exceeds the per-file limit",
                )
            paths.append(path)
            total_bytes += size_bytes
            files_by_path[path] = item
        if paths != sorted(paths, key=lambda item: item.encode("utf-8")):
            _fail("runtime_contract_noncanonical", "snapshot files are not canonical")
        if len({item.casefold() for item in paths}) != len(paths):
            _fail("runtime_contract_collision", "snapshot paths collide")
        if total_bytes > MAX_RUNTIME_TREE_BYTES:
            _fail("runtime_snapshot_limit", "snapshot tree exceeds its byte limit")
        for identity in checked_descriptors:
            matching = [
                item
                for path, item in files_by_path.items()
                if path.startswith(f"descriptors/{identity['id']}@")
            ]
            if len(matching) != 1:
                _fail(
                    "runtime_snapshot_descriptor_mismatch",
                    f"snapshot does not bind exact descriptor {identity['id']}",
                )
        _sha256(document.get("tree_hash"), "runtime snapshot.tree_hash")
        if document["tree_hash"] != _canonical_hash({"files": raw_files}):
            _fail("runtime_snapshot_tree_hash_mismatch", "tree_hash is not canonical")
        if document.get("snapshot_id") != _derived_snapshot_id(document):
            _fail("runtime_snapshot_id_mismatch", "snapshot_id is not deterministic")
        _sha256(document.get("content_hash"), "runtime snapshot.content_hash")
        if document["content_hash"] != _canonical_hash(document):
            _fail("runtime_snapshot_hash_mismatch", "content_hash is not canonical")
        return copy.deepcopy(document)
    except RuntimeContractError:
        raise
    except (CreationContractError, TypeError, ValueError, RecursionError) as exc:
        _fail("runtime_snapshot_invalid", str(exc))


def serialize_runtime_snapshot(value: object) -> bytes:
    return canonical_json_bytes(validate_runtime_snapshot_document(value))


def _registry_seed(document: Mapping[str, object]) -> dict[str, object]:
    return {
        "runtime_snapshot": document["runtime_snapshot"],
        "adapters": document["adapters"],
    }


def _derived_registry_id(document: Mapping[str, object]) -> str:
    return f"runtime_registry_{_canonical_hash(_registry_seed(document))[:40]}"


def _trusted_adapter_documents(
    adapters: Sequence[Mapping[str, object]] | None,
) -> list[dict[str, Any]]:
    candidates = (
        build_builtin_runtime_adapters()
        if adapters is None
        else _snapshot_runtime_json(adapters, "runtime adapter descriptors")
    )
    if type(candidates) is not list or not candidates or len(candidates) > MAX_RUNTIME_ADAPTERS:
        _fail("adapter_registry_untrusted", "adapter descriptors must be a bounded sequence")
    checked = [validate_runtime_adapter_document(adapter) for adapter in candidates]
    checked.sort(key=lambda item: item["adapter_id"].encode("utf-8"))
    trusted = build_builtin_runtime_adapters()
    if checked != trusted:
        _fail(
            "adapter_registry_untrusted",
            "adapter registry does not equal the immutable built-in descriptors",
        )
    return checked


def build_runtime_adapter_registry(
    *,
    snapshot: object,
    adapters: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, Any]:
    checked_snapshot = validate_runtime_snapshot_document(snapshot)
    checked_adapters = _trusted_adapter_documents(adapters)
    expected_descriptor_identities = [
        _identity(adapter, id_field="adapter_id") for adapter in checked_adapters
    ]
    if checked_snapshot["adapter_descriptors"] != expected_descriptor_identities:
        _fail(
            "runtime_snapshot_descriptor_mismatch",
            "snapshot does not bind the exact built-in adapter descriptors",
        )
    document: dict[str, Any] = {
        "format": RUNTIME_ADAPTER_REGISTRY_FORMAT,
        "format_version": RUNTIME_CONTRACT_VERSION,
        "registry_id": "",
        "runtime_snapshot": _identity(
            checked_snapshot,
            id_field="snapshot_id",
        ),
        "adapters": checked_adapters,
        "content_hash": "",
    }
    document["registry_id"] = _derived_registry_id(document)
    return validate_runtime_adapter_registry_document(
        _seal(document),
        snapshot=checked_snapshot,
    )


def validate_runtime_adapter_registry_document(
    value: object,
    *,
    snapshot: object | None = None,
) -> dict[str, Any]:
    try:
        owned = _snapshot_runtime_json(value, "runtime adapter registry")
        owned_snapshot = (
            None
            if snapshot is None
            else _snapshot_runtime_json(
                snapshot,
                "runtime adapter registry supplied snapshot",
            )
        )
        _validate_json_structure(owned, context="runtime adapter registry")
        document = _object(owned, "runtime adapter registry")
        _exact_keys(document, _REGISTRY_FIELDS, "runtime adapter registry")
        if document.get("format") != RUNTIME_ADAPTER_REGISTRY_FORMAT:
            _fail(
                "runtime_registry_format_invalid",
                f"format must be {RUNTIME_ADAPTER_REGISTRY_FORMAT}",
            )
        if document.get("format_version") != RUNTIME_CONTRACT_VERSION:
            _fail("runtime_registry_version_invalid", "format_version must be 1")
        _identifier(document.get("registry_id"), "runtime adapter registry.registry_id")
        snapshot_identity = _validate_identity(
            document.get("runtime_snapshot"),
            "runtime adapter registry.runtime_snapshot",
            expected_format=RUNTIME_SNAPSHOT_FORMAT,
        )
        raw_adapters = document.get("adapters")
        if (
            not isinstance(raw_adapters, list)
            or not raw_adapters
            or len(raw_adapters) > MAX_RUNTIME_ADAPTERS
        ):
            _fail("runtime_registry_limit", "adapters must be bounded")
        checked_adapters = [validate_runtime_adapter_document(adapter) for adapter in raw_adapters]
        adapter_ids = [adapter["adapter_id"] for adapter in checked_adapters]
        if adapter_ids != sorted(adapter_ids, key=lambda item: item.encode("utf-8")):
            _fail("runtime_contract_noncanonical", "registry adapters are not canonical")
        if len({item.casefold() for item in adapter_ids}) != len(adapter_ids):
            _fail("runtime_contract_collision", "registry adapter IDs collide")
        if owned_snapshot is not None:
            checked_snapshot = validate_runtime_snapshot_document(owned_snapshot)
            if snapshot_identity != _identity(
                checked_snapshot,
                id_field="snapshot_id",
            ):
                _fail(
                    "runtime_registry_snapshot_mismatch",
                    "registry does not bind the supplied exact snapshot",
                )
            expected = [_identity(adapter, id_field="adapter_id") for adapter in checked_adapters]
            if checked_snapshot["adapter_descriptors"] != expected:
                _fail(
                    "runtime_snapshot_descriptor_mismatch",
                    "snapshot descriptors do not match registry adapters",
                )
            snapshot_files = {item["path"]: item for item in checked_snapshot["files"]}
            for adapter in checked_adapters:
                descriptor_path = (
                    f"descriptors/{adapter['adapter_id']}@{adapter['adapter_version']}.json"
                )
                payload = serialize_runtime_adapter(adapter)
                expected_file = {
                    "path": descriptor_path,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size_bytes": len(payload),
                }
                if snapshot_files.get(descriptor_path) != expected_file:
                    _fail(
                        "runtime_snapshot_descriptor_mismatch",
                        "snapshot descriptor bytes do not match registry adapters",
                    )
        if document.get("registry_id") != _derived_registry_id(document):
            _fail("runtime_registry_id_mismatch", "registry_id is not deterministic")
        _sha256(document.get("content_hash"), "runtime adapter registry.content_hash")
        if document["content_hash"] != _canonical_hash(document):
            _fail("runtime_registry_hash_mismatch", "content_hash is not canonical")
        return copy.deepcopy(document)
    except RuntimeContractError:
        raise
    except (CreationContractError, TypeError, ValueError, RecursionError) as exc:
        _fail("runtime_registry_invalid", str(exc))


def serialize_runtime_adapter_registry(value: object) -> bytes:
    return canonical_json_bytes(validate_runtime_adapter_registry_document(value))


def _installed_runtime_package_root(package_name: str, package_file: object) -> Path:
    if not isinstance(package_file, str) or not package_file:
        _fail(
            "runtime_kernel_unavailable",
            f"{package_name} has no installed file root",
        )
    root = Path(package_file).parent
    if root.name != package_name:
        _fail(
            "runtime_kernel_unavailable",
            f"{package_name} installed root has an unexpected identity",
        )
    return root


def _installed_runtime_kernel_root() -> Path:
    try:
        import gamepack_runtime

        package_file = gamepack_runtime.__file__
    except (ImportError, AttributeError) as exc:
        _fail("runtime_kernel_unavailable", f"could not locate gamepack_runtime: {exc}")
    return _installed_runtime_package_root("gamepack_runtime", package_file)


def _installed_adapter_runtime_root() -> Path:
    try:
        import gamepack_raylib_2d

        package_file = gamepack_raylib_2d.__file__
    except (ImportError, AttributeError) as exc:
        _fail(
            "runtime_kernel_unavailable",
            f"could not locate gamepack_raylib_2d: {exc}",
        )
    return _installed_runtime_package_root("gamepack_raylib_2d", package_file)


def _validate_trusted_runtime_inputs(
    *,
    registry: object,
    snapshot: object,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    checked_snapshot = validate_runtime_snapshot_document(snapshot)
    checked_registry = validate_runtime_adapter_registry_document(
        registry,
        snapshot=checked_snapshot,
    )
    trusted_adapters = _trusted_adapter_documents(checked_registry["adapters"])
    expected_snapshot = build_game_runtime_snapshot(
        _installed_runtime_kernel_root(),
        adapter_runtime_root=_installed_adapter_runtime_root(),
        adapters=trusted_adapters,
    )
    if serialize_runtime_snapshot(checked_snapshot) != serialize_runtime_snapshot(
        expected_snapshot
    ):
        _fail(
            "runtime_snapshot_untrusted",
            "snapshot does not exactly reproduce the installed code-owned kernel",
        )
    expected_registry = build_runtime_adapter_registry(
        snapshot=expected_snapshot,
        adapters=trusted_adapters,
    )
    if serialize_runtime_adapter_registry(checked_registry) != serialize_runtime_adapter_registry(
        expected_registry
    ):
        _fail(
            "runtime_registry_untrusted",
            "registry does not exactly reproduce the installed code-owned registry",
        )
    return checked_snapshot, checked_registry, trusted_adapters


def capture_trusted_runtime_snapshot_files(
    *,
    snapshot: object,
    registry: object,
    _verification_hook: _RuntimeTreeHook | None = None,
) -> Mapping[str, bytes]:
    """Retain and return the exact installed code-owned bytes bound by a snapshot."""

    owned = _snapshot_runtime_inputs(
        "trusted runtime snapshot capture inputs",
        snapshot=snapshot,
        registry=registry,
    )
    checked_snapshot = validate_runtime_snapshot_document(owned["snapshot"])
    checked_registry = validate_runtime_adapter_registry_document(
        owned["registry"],
        snapshot=checked_snapshot,
    )
    trusted_adapters = _trusted_adapter_documents(checked_registry["adapters"])
    captured = _capture_runtime_files(
        _installed_runtime_kernel_root(),
        _verification_hook=_verification_hook,
    )
    captured.update(
        _capture_runtime_files(
            _installed_adapter_runtime_root(),
            package_name="gamepack_raylib_2d",
        )
    )
    for adapter in trusted_adapters:
        virtual_path = f"descriptors/{adapter['adapter_id']}@{adapter['adapter_version']}.json"
        captured[virtual_path] = serialize_runtime_adapter(adapter)
    expected_snapshot = _build_game_runtime_snapshot_from_files(
        {
            path: payload
            for path, payload in captured.items()
            if path.startswith(("gamepack_runtime/", "gamepack_raylib_2d/"))
        },
        trusted_adapters,
    )
    if serialize_runtime_snapshot(checked_snapshot) != serialize_runtime_snapshot(
        expected_snapshot
    ):
        _fail(
            "runtime_snapshot_untrusted",
            "snapshot does not exactly reproduce the retained installed code-owned kernel",
        )
    expected_registry = build_runtime_adapter_registry(
        snapshot=expected_snapshot,
        adapters=trusted_adapters,
    )
    if serialize_runtime_adapter_registry(checked_registry) != serialize_runtime_adapter_registry(
        expected_registry
    ):
        _fail(
            "runtime_registry_untrusted",
            "registry does not exactly reproduce the retained code-owned registry",
        )
    ordered = {
        path: captured[path] for path in sorted(captured, key=lambda item: item.encode("utf-8"))
    }
    return MappingProxyType(ordered)


def _gamepack_profile(gamepack: Mapping[str, Any]) -> str:
    return f"profile:{gamepack['analysis_requirements']['profile']}"


def _gamepack_semantics(gamepack: Mapping[str, Any]) -> dict[str, object]:
    logic = gamepack["logic"]
    parameter_types = {
        parameter["type"] for action in logic["actions"] for parameter in action["parameters"]
    }
    return {
        "action_parameter_types": sorted(
            parameter_types,
            key=lambda item: item.encode("utf-8"),
        ),
        "condition_operators": sorted(
            {condition["operator"] for condition in logic["conditions"]},
            key=lambda item: item.encode("utf-8"),
        ),
        "effect_operations": sorted(
            {effect["operation"] for effect in logic["effects"]},
            key=lambda item: item.encode("utf-8"),
        ),
        "ending_kinds": sorted(
            {ending["kind"] for ending in logic["endings"]},
            key=lambda item: item.encode("utf-8"),
        ),
        "narrative_cursor": logic["narrative_cursor"] is not None,
    }


def _adapter_mismatch_reasons(
    gamepack: Mapping[str, Any],
    adapter: Mapping[str, Any],
) -> list[str]:
    runtime = gamepack["runtime_requirements"]
    reasons: list[str] = []
    if _gamepack_profile(gamepack) not in adapter["supported_profiles"]:
        reasons.append("analysis_profile_unsupported")
    accepted = {
        (item["format"], version)
        for item in adapter["accepted_logic_formats"]
        for version in item["versions"]
    }
    if (gamepack["format"], gamepack["format_version"]) not in accepted:
        reasons.append("logic_format_unsupported")
    if gamepack["logic"]["execution_semantics"] != EXECUTION_SEMANTICS:
        reasons.append("execution_semantics_unsupported")
    supported_features = set(adapter["supported_features"])
    if not set(runtime["required_features"]).issubset(supported_features):
        reasons.append("required_feature_unsupported")
    expected_semantics = _gamepack_semantics(gamepack)
    supported_semantics = adapter["supported_semantics"]
    for field in (
        "action_parameter_types",
        "condition_operators",
        "effect_operations",
        "ending_kinds",
    ):
        if not set(expected_semantics[field]).issubset(set(supported_semantics[field])):
            reasons.append(f"{field}_unsupported")
    if expected_semantics["narrative_cursor"] and not supported_semantics["narrative_cursor"]:
        reasons.append("narrative_cursor_unsupported")
    requested_presentation = {
        "mode": runtime["presentation"]["mode"],
        "camera": runtime["presentation"]["camera"],
        "perspective": runtime["presentation"]["perspective"],
        "requested_renderer": runtime["presentation"]["renderer"],
    }
    if requested_presentation not in adapter["presentations"]:
        reasons.append("presentation_unsupported")
    if not set(runtime["input_capabilities"]).issubset(set(adapter["input_capabilities"])):
        reasons.append("input_capability_unsupported")
    if not set(runtime["asset_formats"]).issubset(set(adapter["asset_formats"])):
        reasons.append("asset_format_unsupported")
    if runtime["packaging_target"] not in adapter["packaging_targets"]:
        reasons.append("packaging_target_unsupported")
    for field in ("save", "replay"):
        expected = runtime[f"{field}_expected"]
        if expected and not adapter["persistence"][field]["required"]:
            reasons.append(f"{field}_unsupported")
    requested_platforms = {item["platform_id"]: item for item in runtime["platform_matrix"]}
    concrete_platforms = {item["platform_id"]: item for item in adapter["platforms"]}
    if set(requested_platforms) != set(concrete_platforms):
        reasons.append("platform_matrix_unsupported")
    else:
        for platform_id, request in requested_platforms.items():
            concrete = concrete_platforms[platform_id]
            if (
                request["platform_family"] != concrete["platform_family"]
                or request["architecture"] != concrete["architecture"]
                or request["renderer"] != concrete["renderer"]
                or request["backend"] != "backend:unspecified"
                or concrete["backend"] == "backend:unspecified"
            ):
                reasons.append("platform_matrix_unsupported")
                break
    return sorted(set(reasons), key=lambda item: item.encode("utf-8"))


def resolve_runtime_adapter(
    gamepack: object,
    *,
    registry: object,
    snapshot: object,
) -> dict[str, Any]:
    """Resolve exactly one code-owned adapter; authoring data never supplies code paths."""

    owned = _snapshot_runtime_inputs(
        "runtime adapter resolution",
        gamepack=gamepack,
        registry=registry,
        snapshot=snapshot,
    )
    try:
        checked_gamepack = validate_gamepack_document(owned["gamepack"])
    except GamepackError as exc:
        _fail("runtime_gamepack_invalid", str(exc))
    checked_snapshot, checked_registry, trusted = _validate_trusted_runtime_inputs(
        registry=owned["registry"],
        snapshot=owned["snapshot"],
    )
    requested = checked_gamepack["runtime_requirements"]["requested_adapter"]
    matches: list[dict[str, Any]] = []
    mismatch_reasons: set[str] = set()
    executable_shape_errors: list[AdapterExecutableShapeError] = []
    for adapter in trusted:
        if requested is not None and adapter["adapter_id"] != requested:
            continue
        reasons = _adapter_mismatch_reasons(checked_gamepack, adapter)
        if reasons:
            mismatch_reasons.update(reasons)
        else:
            try:
                inspect_adapter_executable_shape(checked_gamepack, adapter["adapter_id"])
            except AdapterExecutableShapeError as exc:
                executable_shape_errors.append(exc)
            else:
                matches.append(adapter)
    if not matches:
        if executable_shape_errors:
            _fail(
                ADAPTER_EXECUTABLE_SHAPE_UNSUPPORTED,
                executable_shape_errors[0].detail,
            )
        rendered = ",".join(sorted(mismatch_reasons, key=lambda item: item.encode("utf-8")))
        _fail(
            "adapter_zero_match",
            f"no trusted adapter matches the exact requirements: {rendered or 'requested_adapter'}",
        )
    if len(matches) != 1:
        _fail(
            "adapter_ambiguous_match",
            "more than one trusted adapter matches the exact requirements",
        )
    return copy.deepcopy(matches[0])


def resolve_runtime_build_readiness(
    gamepack: object,
    *,
    registry: object,
    snapshot: object,
) -> dict[str, Any]:
    """Resolve materialization compatibility without claiming release evidence.

    A declared adapter with every required capability is sufficient to compose an
    immutable runtime candidate. Optional feature gaps remain visible but do not
    block that build boundary. Native execution, save/replay and packaging
    evidence are deliberately evaluated later by the runtime support report.
    """

    owned = _snapshot_runtime_inputs(
        "runtime build readiness",
        gamepack=gamepack,
        registry=registry,
        snapshot=snapshot,
    )
    try:
        checked_gamepack = validate_gamepack_document(owned["gamepack"])
    except GamepackError as exc:
        _fail("runtime_gamepack_invalid", str(exc))
    _checked_snapshot, _checked_registry, trusted = _validate_trusted_runtime_inputs(
        registry=owned["registry"],
        snapshot=owned["snapshot"],
    )
    runtime = checked_gamepack["runtime_requirements"]
    requested = runtime["requested_adapter"]
    candidates = [
        adapter for adapter in trusted if requested is None or adapter["adapter_id"] == requested
    ]
    matches: list[dict[str, Any]] = []
    mismatch_reasons: set[str] = set()
    executable_shape_errors: list[AdapterExecutableShapeError] = []
    executable_shape_candidates: list[dict[str, Any]] = []
    for adapter in candidates:
        reasons = _adapter_mismatch_reasons(checked_gamepack, adapter)
        if reasons:
            mismatch_reasons.update(reasons)
        else:
            try:
                inspect_adapter_executable_shape(checked_gamepack, adapter["adapter_id"])
            except AdapterExecutableShapeError as exc:
                executable_shape_errors.append(exc)
                executable_shape_candidates.append(adapter)
            else:
                matches.append(adapter)

    if len(matches) == 1:
        adapter = matches[0]
        supported_features = set(adapter["supported_features"])
        missing_required: list[str] = []
        missing_optional = sorted(
            set(runtime["optional_features"]) - supported_features,
            key=lambda item: item.encode("utf-8"),
        )
        reasons = ["optional_feature_unsupported"] if missing_optional else []
        status = "materialization_ready"
        adapter_identity: dict[str, Any] | None = _identity(
            adapter,
            id_field="adapter_id",
        )
        reason_details: dict[str, str] = {}
    else:
        missing_required_features = {
            feature
            for candidate in candidates
            for feature in runtime["required_features"]
            if feature not in candidate["supported_features"]
        }
        missing_optional_features = {
            feature
            for candidate in candidates
            for feature in runtime["optional_features"]
            if feature not in candidate["supported_features"]
        }
        if not candidates:
            missing_required_features.update(runtime["required_features"])
            missing_optional_features.update(runtime["optional_features"])
        if executable_shape_candidates:
            shape_supported = {
                feature
                for candidate in executable_shape_candidates
                for feature in candidate["supported_features"]
            }
            missing_required_features = set(runtime["required_features"]) - shape_supported
            missing_optional_features = set(runtime["optional_features"]) - shape_supported
        missing_required = sorted(missing_required_features, key=lambda item: item.encode("utf-8"))
        missing_optional = sorted(missing_optional_features, key=lambda item: item.encode("utf-8"))
        mismatch_reasons.update({"required_feature_unsupported"} if missing_required else set())
        if requested is not None and not candidates:
            mismatch_reasons.add("requested_adapter_unavailable")
        if len(matches) > 1:
            mismatch_reasons.add("adapter_ambiguous_match")
        if not mismatch_reasons:
            mismatch_reasons.add("adapter_zero_match")
        reasons = (
            [ADAPTER_EXECUTABLE_SHAPE_UNSUPPORTED]
            if executable_shape_errors
            else sorted(mismatch_reasons, key=lambda item: item.encode("utf-8"))
        )
        reason_details = (
            {ADAPTER_EXECUTABLE_SHAPE_UNSUPPORTED: executable_shape_errors[0].detail}
            if executable_shape_errors
            else {}
        )
        status = "unsupported"
        adapter_identity = None

    return {
        "status": status,
        "adapter": adapter_identity,
        "missing_required_feature_ids": missing_required,
        "missing_optional_feature_ids": missing_optional,
        "reason_codes": reasons,
        "reason_details": reason_details,
    }


def _composition_seed(document: Mapping[str, object]) -> dict[str, object]:
    return {
        "gamepack": document["gamepack"],
        "asset_inventory": document["asset_inventory"],
        "assetpack": document["assetpack"],
        "adapter": document["adapter"],
        "registry": document["registry"],
        "runtime_snapshot": document["runtime_snapshot"],
        "platforms": document["platforms"],
        "bindings": document["bindings"],
    }


def _derived_composition_id(document: Mapping[str, object]) -> str:
    return f"runtime_composition_{_canonical_hash(_composition_seed(document))[:40]}"


def _verified_root_hash(verified: VerifiedGenericAssetpack) -> str:
    entries = [
        {
            "path": path,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
        for path, payload in sorted(
            verified.files.items(),
            key=lambda item: item[0].encode("utf-8"),
        )
    ]
    return _canonical_hash({"files": entries})


def _assetpack_identity(
    verified: VerifiedGenericAssetpack,
) -> dict[str, object]:
    manifest = verified.manifest
    return {
        "format": manifest["format"],
        "format_version": manifest["format_version"],
        "id": manifest["assetpack_id"],
        "content_hash": manifest["content_hash"],
        "root_hash": _verified_root_hash(verified),
        "inventory_hash": manifest["inventory"]["content_hash"],
    }


def _gamepack_identity(gamepack: Mapping[str, object]) -> dict[str, object]:
    game = _object(gamepack["game"], "gamepack.game")
    return {
        "format": gamepack["format"],
        "format_version": gamepack["format_version"],
        "id": game["id"],
        "content_hash": gamepack["content_hash"],
    }


def _inventory_identity(inventory: Mapping[str, object]) -> dict[str, object]:
    return {
        "format": inventory["format"],
        "format_version": inventory["format_version"],
        "id": inventory["inventory_id"],
        "content_hash": inventory["content_hash"],
    }


def _validate_composition_lineage(
    gamepack: Mapping[str, Any],
    inventory: Mapping[str, Any],
    assetpack: Mapping[str, Any],
) -> None:
    gamepack_identity = _gamepack_identity(gamepack)
    inventory_identity = _inventory_identity(inventory)
    if inventory["gamepack"] != gamepack_identity:
        _fail(
            "runtime_inventory_binding_mismatch",
            "D1 inventory does not bind the exact supplied gamepack",
        )
    if assetpack["gamepack"] != gamepack_identity:
        _fail(
            "runtime_assetpack_binding_mismatch",
            "D3 assetpack does not bind the exact supplied gamepack",
        )
    if assetpack["asset_inventory"] != inventory_identity:
        _fail(
            "runtime_assetpack_binding_mismatch",
            "D3 assetpack does not bind the exact supplied D1 inventory",
        )


def _derive_runtime_bindings(
    gamepack: Mapping[str, Any],
    inventory: Mapping[str, Any],
    assetpack: Mapping[str, Any],
    adapter: Mapping[str, Any],
) -> list[dict[str, Any]]:
    required_bindings = {
        requirement["binding_id"]
        for requirement in gamepack["asset_requirements"]
        if requirement["required"]
    }
    rules = {rule["binding_id"]: rule for rule in adapter["asset_bindings"]}
    if set(rules) != required_bindings:
        _fail(
            "runtime_binding_mismatch",
            "adapter binding rules do not exactly cover required gamepack bindings",
        )
    inventory_assets = {asset["asset_id"]: asset for asset in inventory["assets"]}
    assetpack_assets = {asset["asset"]["asset_id"]: asset for asset in assetpack["assets"]}
    if set(inventory_assets) != set(assetpack_assets):
        _fail(
            "runtime_binding_mismatch",
            "D1 and D3 asset IDs are not exact",
        )
    claimed_by_binding: dict[str, list[str]] = {
        binding_id: [
            asset["asset_id"] for asset in inventory["assets"] if binding_id in asset["binding_ids"]
        ]
        for binding_id in required_bindings
    }
    bindings: list[dict[str, Any]] = []
    consumed_outputs: set[tuple[str, str, str, str]] = set()
    for binding_id in sorted(required_bindings, key=lambda item: item.encode("utf-8")):
        rule = rules[binding_id]
        claims = claimed_by_binding[binding_id]
        if claims != [rule["asset_id"]]:
            _fail(
                "runtime_binding_mismatch",
                f"binding {binding_id} does not resolve to exact asset {rule['asset_id']}",
            )
        inventory_asset = inventory_assets.get(rule["asset_id"])
        sealed_asset = assetpack_assets.get(rule["asset_id"])
        if inventory_asset is None or sealed_asset is None:
            _fail(
                "runtime_binding_mismatch",
                f"binding {binding_id} references a missing asset",
            )
        inventory_outputs = [
            output
            for output in inventory_asset["outputs"]
            if output["role"] == rule["role"] and output["media_type"] == rule["media_type"]
        ]
        sealed_outputs = [
            output
            for output in sealed_asset["outputs"]
            if output["role"] == rule["role"]
            and output["media_type"] == rule["media_type"]
            and output["runtime_path"] == rule["runtime_path"]
        ]
        if len(inventory_outputs) != 1 or len(sealed_outputs) != 1:
            _fail(
                "runtime_binding_mismatch",
                f"binding {binding_id} has missing or ambiguous role/media output",
            )
        output = sealed_outputs[0]
        consumed_outputs.add(
            (
                rule["asset_id"],
                output["role"],
                output["media_type"],
                output["runtime_path"],
            )
        )
        bindings.append(
            {
                "binding_id": binding_id,
                "asset_id": rule["asset_id"],
                "role": output["role"],
                "media_type": output["media_type"],
                "runtime_path": output["runtime_path"],
                "sha256": output["sha256"],
                "size_bytes": output["size_bytes"],
            }
        )
    all_outputs = {
        (
            asset_id,
            output["role"],
            output["media_type"],
            output["runtime_path"],
        )
        for asset_id, asset in assetpack_assets.items()
        for output in asset["outputs"]
    }
    if consumed_outputs != all_outputs:
        _fail(
            "runtime_binding_mismatch",
            "D3 assetpack has unbound or missing runtime outputs",
        )
    return bindings


def _build_composition_from_verified(
    gamepack: Mapping[str, Any],
    inventory: Mapping[str, Any],
    verified: VerifiedGenericAssetpack,
    registry: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    adapter: Mapping[str, Any],
) -> dict[str, Any]:
    assetpack = verified.manifest
    _validate_composition_lineage(gamepack, inventory, assetpack)
    bindings = _derive_runtime_bindings(
        gamepack,
        inventory,
        assetpack,
        adapter,
    )
    document: dict[str, Any] = {
        "format": RUNTIME_COMPOSITION_FORMAT,
        "format_version": RUNTIME_CONTRACT_VERSION,
        "composition_id": "",
        "gamepack": _gamepack_identity(gamepack),
        "asset_inventory": _inventory_identity(inventory),
        "assetpack": _assetpack_identity(verified),
        "adapter": _identity(adapter, id_field="adapter_id"),
        "registry": _identity(registry, id_field="registry_id"),
        "runtime_snapshot": _identity(snapshot, id_field="snapshot_id"),
        "platforms": copy.deepcopy(adapter["platforms"]),
        "bindings": bindings,
        "content_hash": "",
    }
    document["composition_id"] = _derived_composition_id(document)
    return validate_game_runtime_composition_document(_seal(document))


def build_game_runtime_composition(
    gamepack: object,
    inventory: object,
    assetpack_root: str | Path,
    *,
    registry: object,
    snapshot: object,
) -> dict[str, Any]:
    """Compose exact gamepack, D1 inventory, and retained-byte verified D3 assets."""

    owned = _snapshot_runtime_inputs(
        "game runtime composition inputs",
        gamepack=gamepack,
        inventory=inventory,
        registry=registry,
        snapshot=snapshot,
    )
    try:
        checked_gamepack = validate_gamepack_document(owned["gamepack"])
    except GamepackError as exc:
        _fail("runtime_gamepack_invalid", str(exc))
    try:
        checked_inventory = validate_asset_inventory_document(owned["inventory"])
    except GenericAssetError as exc:
        _fail("runtime_inventory_invalid", str(exc))
    checked_snapshot = validate_runtime_snapshot_document(owned["snapshot"])
    checked_registry = validate_runtime_adapter_registry_document(
        owned["registry"],
        snapshot=checked_snapshot,
    )
    adapter = resolve_runtime_adapter(
        checked_gamepack,
        registry=checked_registry,
        snapshot=checked_snapshot,
    )
    try:
        verified = verify_generic_assetpack(assetpack_root)
    except GenericAssetpackError as exc:
        _fail("runtime_assetpack_invalid", str(exc))
    try:
        return _build_composition_from_verified(
            checked_gamepack,
            checked_inventory,
            verified,
            checked_registry,
            checked_snapshot,
            adapter,
        )
    finally:
        verified.close()


def validate_game_runtime_composition_document(value: object) -> dict[str, Any]:
    try:
        owned = _snapshot_runtime_json(value, "game runtime composition")
        _validate_json_structure(owned, context="game runtime composition")
        document = _object(owned, "game runtime composition")
        _exact_keys(document, _COMPOSITION_FIELDS, "game runtime composition")
        if document.get("format") != RUNTIME_COMPOSITION_FORMAT:
            _fail(
                "runtime_composition_format_invalid",
                f"format must be {RUNTIME_COMPOSITION_FORMAT}",
            )
        if document.get("format_version") != RUNTIME_CONTRACT_VERSION:
            _fail("runtime_composition_version_invalid", "format_version must be 1")
        _identifier(
            document.get("composition_id"),
            "game runtime composition.composition_id",
        )
        for field, expected_format in (
            ("gamepack", "world-forge.gamepack"),
            ("asset_inventory", "world-forge.asset_inventory"),
            ("adapter", RUNTIME_ADAPTER_FORMAT),
            ("registry", RUNTIME_ADAPTER_REGISTRY_FORMAT),
            ("runtime_snapshot", RUNTIME_SNAPSHOT_FORMAT),
        ):
            _validate_identity(
                document.get(field),
                f"game runtime composition.{field}",
                expected_format=expected_format,
            )
        assetpack = _object(
            document.get("assetpack"),
            "game runtime composition.assetpack",
        )
        _exact_keys(
            assetpack,
            _COMPOSITION_ASSETPACK_FIELDS,
            "game runtime composition.assetpack",
        )
        if (
            assetpack.get("format") != "world-forge.assetpack"
            or assetpack.get("format_version") != 1
        ):
            _fail(
                "runtime_assetpack_binding_mismatch",
                "composition assetpack identity is unsupported",
            )
        _identifier(assetpack.get("id"), "game runtime composition.assetpack.id")
        for field in ("content_hash", "root_hash", "inventory_hash"):
            _sha256(
                assetpack.get(field),
                f"game runtime composition.assetpack.{field}",
            )
        platforms = document.get("platforms")
        if not isinstance(platforms, list) or not platforms or len(platforms) > 32:
            _fail("runtime_composition_limit", "platforms must be bounded")
        platform_ids: list[str] = []
        for index, raw in enumerate(platforms):
            context = f"game runtime composition.platforms/{index}"
            platform = _validate_concrete_platform(raw, context)
            platform_ids.append(platform["platform_id"])
        if platform_ids != sorted(platform_ids, key=lambda item: item.encode("utf-8")):
            _fail("runtime_contract_noncanonical", "composition platforms are not canonical")
        if len(set(platform_ids)) != len(platform_ids):
            _fail("runtime_contract_collision", "composition platform IDs collide")
        bindings = document.get("bindings")
        if not isinstance(bindings, list) or not bindings or len(bindings) > MAX_RUNTIME_ITEMS:
            _fail("runtime_composition_limit", "bindings must be bounded")
        binding_ids: list[str] = []
        binding_keys: set[tuple[str, str, str, str, str]] = set()
        for index, raw in enumerate(bindings):
            context = f"game runtime composition.bindings/{index}"
            binding = _object(raw, context)
            _exact_keys(binding, _COMPOSITION_BINDING_FIELDS, context)
            binding_id = _identifier(binding.get("binding_id"), f"{context}.binding_id")
            asset_id = _identifier(binding.get("asset_id"), f"{context}.asset_id")
            role = _identifier(binding.get("role"), f"{context}.role")
            media_type = _bounded_text(binding.get("media_type"), f"{context}.media_type")
            runtime_path = _portable_relative_path(
                binding.get("runtime_path"),
                f"{context}.runtime_path",
            )
            _sha256(binding.get("sha256"), f"{context}.sha256")
            size_bytes = _integer(
                binding.get("size_bytes"),
                f"{context}.size_bytes",
                minimum=1,
            )
            if size_bytes > MAX_RUNTIME_CONTRACT_BYTES:
                _fail(
                    "runtime_composition_limit",
                    f"{context}.size_bytes exceeds the binding limit",
                )
            binding_ids.append(binding_id)
            binding_keys.add((binding_id, asset_id, role, media_type, runtime_path))
        if binding_ids != sorted(binding_ids, key=lambda item: item.encode("utf-8")):
            _fail("runtime_contract_noncanonical", "composition bindings are not canonical")
        if len({item.casefold() for item in binding_ids}) != len(binding_ids):
            _fail("runtime_binding_collision", "composition binding IDs collide")
        if len(binding_keys) != len(bindings):
            _fail("runtime_binding_collision", "composition contains duplicate bindings")
        if document.get("composition_id") != _derived_composition_id(document):
            _fail("runtime_composition_id_mismatch", "composition_id is not deterministic")
        _sha256(
            document.get("content_hash"),
            "game runtime composition.content_hash",
        )
        if document["content_hash"] != _canonical_hash(document):
            _fail("runtime_composition_hash_mismatch", "content_hash is not canonical")
        return copy.deepcopy(document)
    except RuntimeContractError:
        raise
    except (CreationContractError, TypeError, ValueError, RecursionError) as exc:
        _fail("runtime_composition_invalid", str(exc))


def validate_game_runtime_composition(
    value: object,
    *,
    gamepack: object,
    inventory: object,
    assetpack_root: str | Path,
    registry: object,
    snapshot: object,
) -> dict[str, Any]:
    owned = _snapshot_runtime_inputs(
        "integral game runtime composition inputs",
        value=value,
        gamepack=gamepack,
        inventory=inventory,
        registry=registry,
        snapshot=snapshot,
    )
    document = validate_game_runtime_composition_document(owned["value"])
    expected = build_game_runtime_composition(
        owned["gamepack"],
        owned["inventory"],
        assetpack_root,
        registry=owned["registry"],
        snapshot=owned["snapshot"],
    )
    if document != expected:
        _fail(
            "runtime_composition_binding_mismatch",
            "composition does not exactly rebuild from supplied immutable inputs",
        )
    return document


def serialize_game_runtime_composition(value: object) -> bytes:
    return canonical_json_bytes(validate_game_runtime_composition_document(value))


def _evidence_seed(document: Mapping[str, object]) -> dict[str, object]:
    return {
        "composition": document["composition"],
        "adapter": document["adapter"],
        "platform": document["platform"],
        "execution_status": document["execution_status"],
        "packaging_status": document["packaging_status"],
        "checks": document["checks"],
    }


def _derived_evidence_id(document: Mapping[str, object]) -> str:
    return f"runtime_evidence_{_canonical_hash(_evidence_seed(document))[:40]}"


def build_runtime_evidence(
    composition: object,
    *,
    platform_id: str,
    execution_status: str,
    packaging_status: str,
    checks: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    """Build one external content-addressed runtime evidence claim."""

    owned = _snapshot_runtime_inputs(
        "runtime evidence inputs",
        composition=composition,
        platform_id=platform_id,
        execution_status=execution_status,
        packaging_status=packaging_status,
        checks=checks,
    )
    checked_composition = validate_game_runtime_composition_document(owned["composition"])
    platform_id = owned["platform_id"]
    execution_status = owned["execution_status"]
    packaging_status = owned["packaging_status"]
    checks = owned["checks"]
    platforms = [
        platform
        for platform in checked_composition["platforms"]
        if platform["platform_id"] == platform_id
    ]
    if len(platforms) != 1:
        _fail(
            "runtime_evidence_platform_mismatch",
            "evidence platform does not occur exactly once in the composition",
        )
    if type(checks) is not list or not checks or len(checks) > 64:
        _fail("runtime_evidence_limit", "checks must be a bounded non-empty sequence")
    document: dict[str, Any] = {
        "format": RUNTIME_EVIDENCE_FORMAT,
        "format_version": RUNTIME_CONTRACT_VERSION,
        "evidence_id": "",
        "composition": _identity(
            checked_composition,
            id_field="composition_id",
        ),
        "adapter": copy.deepcopy(checked_composition["adapter"]),
        "platform": copy.deepcopy(platforms[0]),
        "execution_status": execution_status,
        "packaging_status": packaging_status,
        "checks": sorted(
            (copy.deepcopy(dict(check)) for check in checks),
            key=lambda item: str(item.get("check_id", "")).encode("utf-8"),
        ),
        "content_hash": "",
    }
    document["evidence_id"] = _derived_evidence_id(document)
    return validate_runtime_evidence_document(
        _seal(document),
        composition=checked_composition,
    )


def validate_runtime_evidence_document(
    value: object,
    *,
    composition: object | None = None,
) -> dict[str, Any]:
    try:
        owned = _snapshot_runtime_json(value, "runtime evidence")
        owned_composition = (
            None
            if composition is None
            else _snapshot_runtime_json(
                composition,
                "runtime evidence supplied composition",
            )
        )
        _validate_json_structure(owned, context="runtime evidence")
        document = _object(owned, "runtime evidence")
        _exact_keys(document, _EVIDENCE_FIELDS, "runtime evidence")
        if document.get("format") != RUNTIME_EVIDENCE_FORMAT:
            _fail(
                "runtime_evidence_format_invalid",
                f"format must be {RUNTIME_EVIDENCE_FORMAT}",
            )
        if document.get("format_version") != RUNTIME_CONTRACT_VERSION:
            _fail("runtime_evidence_version_invalid", "format_version must be 1")
        _identifier(document.get("evidence_id"), "runtime evidence.evidence_id")
        composition_identity = _validate_identity(
            document.get("composition"),
            "runtime evidence.composition",
            expected_format=RUNTIME_COMPOSITION_FORMAT,
        )
        adapter_identity = _validate_identity(
            document.get("adapter"),
            "runtime evidence.adapter",
            expected_format=RUNTIME_ADAPTER_FORMAT,
        )
        platform = _validate_concrete_platform(
            document.get("platform"),
            "runtime evidence.platform",
        )
        execution_status = document.get("execution_status")
        if execution_status not in {
            "headless_verified",
            "native_verified",
            "failed",
        }:
            _fail(
                "runtime_evidence_status_invalid",
                "execution_status must be a terminal evidence state",
            )
        packaging_status = document.get("packaging_status")
        if packaging_status not in {"unverified", "verified", "failed"}:
            _fail(
                "runtime_evidence_status_invalid",
                "packaging_status is unsupported",
            )
        checks = document.get("checks")
        if not isinstance(checks, list) or not checks or len(checks) > 64:
            _fail("runtime_evidence_limit", "checks must be bounded and non-empty")
        check_ids: list[str] = []
        kinds: dict[str, str] = {}
        statuses: dict[str, str] = {}
        evidence_ids: set[str] = set()
        for index, raw in enumerate(checks):
            context = f"runtime evidence.checks/{index}"
            check = _object(raw, context)
            _exact_keys(check, _EVIDENCE_CHECK_FIELDS, context)
            check_id = _bounded_text(check.get("check_id"), f"{context}.check_id")
            if check_id not in _EVIDENCE_REQUIREMENTS:
                _fail(
                    "runtime_evidence_check_invalid",
                    f"{check_id} is not a registered evidence requirement",
                )
            kind = check.get("kind")
            expected_kind = {
                "check:headless_determinism": "headless",
                "check:native_raylib": "native",
                "check:package_verification": "packaging",
                "check:save_replay": "save_replay",
            }[check_id]
            if kind != expected_kind:
                _fail(
                    "runtime_evidence_check_invalid",
                    f"{check_id} must use kind {expected_kind}",
                )
            status = check.get("status")
            if status not in {"passed", "failed"}:
                _fail(
                    "runtime_evidence_check_invalid",
                    f"{context}.status must be passed or failed",
                )
            evidence_id = _identifier(check.get("evidence_id"), f"{context}.evidence_id")
            if evidence_id.casefold() in evidence_ids:
                _fail(
                    "runtime_evidence_collision",
                    "check evidence IDs collide",
                )
            evidence_ids.add(evidence_id.casefold())
            content_hash = _sha256(
                check.get("content_hash"),
                f"{context}.content_hash",
            )
            if content_hash == "0" * 64:
                _fail(
                    "runtime_evidence_hash_invalid",
                    "external evidence hash must not use the null sentinel",
                )
            check_ids.append(check_id)
            kinds[kind] = check_id
            statuses[kind] = status
        if check_ids != sorted(check_ids, key=lambda item: item.encode("utf-8")):
            _fail("runtime_contract_noncanonical", "runtime checks are not canonical")
        if len(set(check_ids)) != len(check_ids):
            _fail("runtime_evidence_collision", "runtime check IDs collide")
        passed_kinds = {kind for kind, status in statuses.items() if status == "passed"}
        failed_kinds = {kind for kind, status in statuses.items() if status == "failed"}
        if execution_status == "headless_verified" and not {
            "headless",
            "save_replay",
        }.issubset(passed_kinds):
            _fail(
                "runtime_evidence_status_mismatch",
                "headless_verified requires passed headless and save/replay checks",
            )
        if execution_status == "native_verified" and not {
            "headless",
            "native",
            "save_replay",
        }.issubset(passed_kinds):
            _fail(
                "runtime_evidence_status_mismatch",
                "native_verified requires passed headless, native, and save/replay checks",
            )
        if execution_status == "failed" and not failed_kinds:
            _fail(
                "runtime_evidence_status_mismatch",
                "failed execution requires at least one failed check",
            )
        if packaging_status == "verified" and statuses.get("packaging") != "passed":
            _fail(
                "runtime_evidence_status_mismatch",
                "verified packaging requires a passed packaging check",
            )
        if packaging_status == "failed" and statuses.get("packaging") != "failed":
            _fail(
                "runtime_evidence_status_mismatch",
                "failed packaging requires a failed packaging check",
            )
        if owned_composition is not None:
            checked_composition = validate_game_runtime_composition_document(owned_composition)
            if composition_identity != _identity(
                checked_composition,
                id_field="composition_id",
            ):
                _fail(
                    "runtime_evidence_composition_mismatch",
                    "evidence does not bind the exact supplied composition",
                )
            if adapter_identity != checked_composition["adapter"]:
                _fail(
                    "runtime_evidence_adapter_mismatch",
                    "evidence does not bind the composition adapter",
                )
            if platform not in checked_composition["platforms"]:
                _fail(
                    "runtime_evidence_platform_mismatch",
                    "evidence platform is not an exact composition platform",
                )
        if document.get("evidence_id") != _derived_evidence_id(document):
            _fail("runtime_evidence_id_mismatch", "evidence_id is not deterministic")
        _sha256(document.get("content_hash"), "runtime evidence.content_hash")
        if document["content_hash"] != _canonical_hash(document):
            _fail("runtime_evidence_hash_mismatch", "content_hash is not canonical")
        return copy.deepcopy(document)
    except RuntimeContractError:
        raise
    except (CreationContractError, TypeError, ValueError, RecursionError) as exc:
        _fail("runtime_evidence_invalid", str(exc))


def serialize_runtime_evidence(value: object) -> bytes:
    return canonical_json_bytes(validate_runtime_evidence_document(value))


def resolve_required_feature_support(
    required_features: object,
    adapter: object,
) -> dict[str, Any]:
    owned = _snapshot_runtime_inputs(
        "required runtime feature resolution",
        required_features=required_features,
        adapter=adapter,
    )
    checked_required = _canonical_strings(
        owned["required_features"],
        "required runtime features",
        allow_empty=True,
    )
    checked_adapter = validate_runtime_adapter_document(owned["adapter"])
    supported_set = set(checked_adapter["supported_features"])
    supported = [feature for feature in checked_required if feature in supported_set]
    missing = [feature for feature in checked_required if feature not in supported_set]
    return {
        "status": "supported" if not missing else "unsupported",
        "supported_feature_ids": supported,
        "missing_feature_ids": missing,
    }


def _support_report_seed(document: Mapping[str, object]) -> dict[str, object]:
    return {
        "gamepack": document["gamepack"],
        "composition": document["composition"],
        "adapter": document["adapter"],
        "evidence": document["evidence"],
        "dimensions": document["dimensions"],
        "compatibility_status": document["compatibility_status"],
        "mechanics": document["mechanics"],
        "features": document["features"],
        "missing_capabilities": document["missing_capabilities"],
        "reason_codes": document["reason_codes"],
        "supported": document["supported"],
    }


def _derived_support_report_id(document: Mapping[str, object]) -> str:
    return f"runtime_support_{_canonical_hash(_support_report_seed(document))[:40]}"


def _checked_runtime_evidence_set(
    evidence: Sequence[object],
    *,
    composition: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if (
        isinstance(evidence, (str, bytes, bytearray))
        or not isinstance(evidence, Sequence)
        or len(evidence) > 64
    ):
        _fail("runtime_support_limit", "evidence must be a bounded sequence")
    checked = [
        validate_runtime_evidence_document(item, composition=composition) for item in evidence
    ]
    checked.sort(key=lambda item: item["platform"]["platform_id"].encode("utf-8"))
    platform_ids = [item["platform"]["platform_id"] for item in checked]
    if len(set(platform_ids)) != len(platform_ids):
        _fail(
            "runtime_support_evidence_collision",
            "at most one evidence document may claim each platform",
        )
    evidence_ids = [item["evidence_id"].casefold() for item in checked]
    if len(set(evidence_ids)) != len(evidence_ids):
        _fail(
            "runtime_support_evidence_collision",
            "runtime evidence document IDs collide",
        )
    external_ids = [check["evidence_id"].casefold() for item in checked for check in item["checks"]]
    if len(set(external_ids)) != len(external_ids):
        _fail(
            "runtime_support_evidence_collision",
            "external runtime evidence IDs collide across platforms",
        )
    return checked


def _evidence_kinds(evidence: Mapping[str, Any]) -> set[str]:
    return {check["kind"] for check in evidence["checks"] if check["status"] == "passed"}


def _runtime_evidence_reference(
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "format": evidence["format"],
        "format_version": evidence["format_version"],
        "id": evidence["evidence_id"],
        "content_hash": evidence["content_hash"],
        "platform": copy.deepcopy(evidence["platform"]),
        "execution_status": evidence["execution_status"],
        "packaging_status": evidence["packaging_status"],
        "passed_check_kinds": sorted(
            _evidence_kinds(evidence),
            key=lambda item: item.encode("utf-8"),
        ),
    }


def _resolved_mechanics(
    gamepack: Mapping[str, Any],
    *,
    adapter: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    adapter_complete: bool,
) -> list[dict[str, Any]]:
    saved_states = {
        state["id"]
        for state in gamepack["logic"]["state_schema"]
        if state["persistence"] == "saved"
    }
    evidence_complete = adapter_complete and bool(evidence)
    test_evidence = sorted(
        {
            item["evidence_id"]
            for item in evidence
            if {"headless", "save_replay"}.issubset(_evidence_kinds(item))
        },
        key=lambda item: item.encode("utf-8"),
    )
    native_evidence = sorted(
        {
            item["evidence_id"]
            for item in evidence
            if item["execution_status"] == "native_verified" and "native" in _evidence_kinds(item)
        },
        key=lambda item: item.encode("utf-8"),
    )
    supported_features = set(adapter["supported_features"])
    records: list[dict[str, Any]] = []
    for mechanic in gamepack["logic"]["mechanics"]:
        missing = sorted(
            set(mechanic["required_feature_ids"]) - supported_features,
            key=lambda item: item.encode("utf-8"),
        )
        if missing:
            status = "blocked"
            reasons = ["required_feature_unsupported"]
        elif adapter["state"] != "verified":
            status = "authoring_only"
            reasons = ["adapter_not_verified", "execution_evidence_missing"]
        elif not evidence_complete:
            status = "authoring_only"
            reasons = ["execution_evidence_missing"]
        else:
            status = "supported_current"
            reasons = []
        records.append(
            {
                "mechanic_id": mechanic["id"],
                "core_verb_id": mechanic["core_verb_id"],
                "runtime_action_id": mechanic["action_id"],
                "authoritative_state_ids": copy.deepcopy(mechanic["authoritative_state_ids"]),
                "condition_ids": copy.deepcopy(mechanic["condition_ids"]),
                "rule_ids": copy.deepcopy(mechanic["rule_ids"]),
                "effect_ids": copy.deepcopy(mechanic["effect_ids"]),
                "presentation_hook_ids": copy.deepcopy(mechanic["presentation_hook_ids"]),
                "asset_binding_ids": copy.deepcopy(mechanic["asset_binding_ids"]),
                "required_feature_ids": copy.deepcopy(mechanic["required_feature_ids"]),
                "save_replay": {
                    "state_ids": sorted(
                        (
                            state_id
                            for state_id in mechanic["authoritative_state_ids"]
                            if state_id in saved_states
                        ),
                        key=lambda item: item.encode("utf-8"),
                    ),
                    "event_ids": copy.deepcopy(mechanic["event_ids"]),
                },
                "status": status,
                "reason_codes": reasons,
                "test_evidence": test_evidence if status == "supported_current" else [],
                "native_evidence": (native_evidence if status == "supported_current" else []),
            }
        )
    return records


def _resolved_features(
    gamepack: Mapping[str, Any],
    *,
    adapter: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    adapter_complete: bool,
) -> list[dict[str, Any]]:
    supported_features = set(adapter["supported_features"])
    evidence_ids = sorted(
        {item["evidence_id"] for item in evidence},
        key=lambda item: item.encode("utf-8"),
    )
    records: list[dict[str, Any]] = []
    for feature_id in gamepack["runtime_requirements"]["required_features"]:
        if feature_id not in supported_features:
            status = "blocked"
            reasons = ["required_feature_unsupported"]
        elif adapter["state"] != "verified":
            status = "authoring_only"
            reasons = ["adapter_not_verified", "execution_evidence_missing"]
        elif not adapter_complete:
            status = "authoring_only"
            reasons = ["execution_evidence_missing"]
        else:
            status = "supported_current"
            reasons = []
        records.append(
            {
                "feature_id": feature_id,
                "status": status,
                "reason_codes": reasons,
                "evidence_ids": evidence_ids if status == "supported_current" else [],
            }
        )
    return records


def build_runtime_support_report(
    composition: object,
    *,
    gamepack: object,
    registry: object,
    snapshot: object,
    evidence: Sequence[object],
) -> dict[str, Any]:
    owned = _snapshot_runtime_inputs(
        "runtime support report inputs",
        composition=composition,
        gamepack=gamepack,
        registry=registry,
        snapshot=snapshot,
        evidence=evidence,
    )
    checked_composition = validate_game_runtime_composition_document(owned["composition"])
    try:
        checked_gamepack = validate_gamepack_document(owned["gamepack"])
    except GamepackError as exc:
        _fail("runtime_gamepack_invalid", str(exc))
    checked_snapshot = validate_runtime_snapshot_document(owned["snapshot"])
    checked_registry = validate_runtime_adapter_registry_document(
        owned["registry"],
        snapshot=checked_snapshot,
    )
    adapter = resolve_runtime_adapter(
        checked_gamepack,
        registry=checked_registry,
        snapshot=checked_snapshot,
    )
    expected_identities = {
        "gamepack": _gamepack_identity(checked_gamepack),
        "adapter": _identity(adapter, id_field="adapter_id"),
        "registry": _identity(checked_registry, id_field="registry_id"),
        "runtime_snapshot": _identity(checked_snapshot, id_field="snapshot_id"),
    }
    if (
        checked_composition["gamepack"] != expected_identities["gamepack"]
        or checked_composition["adapter"] != expected_identities["adapter"]
        or checked_composition["registry"] != expected_identities["registry"]
        or checked_composition["runtime_snapshot"] != expected_identities["runtime_snapshot"]
    ):
        _fail(
            "runtime_support_binding_mismatch",
            "composition does not bind the exact report inputs",
        )
    checked_evidence = _checked_runtime_evidence_set(
        owned["evidence"],
        composition=checked_composition,
    )
    evidence_by_platform = {item["platform"]["platform_id"]: item for item in checked_evidence}
    execution: list[dict[str, Any]] = []
    for platform in checked_composition["platforms"]:
        item = evidence_by_platform.get(platform["platform_id"])
        execution.append(
            {
                "platform": copy.deepcopy(platform),
                "status": "untested" if item is None else item["execution_status"],
                "evidence_ids": [] if item is None else [item["evidence_id"]],
            }
        )
    all_native = bool(execution) and all(item["status"] == "native_verified" for item in execution)
    all_headless = bool(execution) and all(
        item["status"] in {"headless_verified", "native_verified"} for item in execution
    )
    all_save_replay = len(checked_evidence) == len(execution) and all(
        "save_replay" in _evidence_kinds(item) for item in checked_evidence
    )
    all_packaging = len(checked_evidence) == len(execution) and all(
        item["packaging_status"] == "verified" for item in checked_evidence
    )
    any_packaging_failed = any(item["packaging_status"] == "failed" for item in checked_evidence)
    any_execution_failed = any(item["status"] == "failed" for item in execution)
    missing_capabilities = resolve_required_feature_support(
        checked_gamepack["runtime_requirements"]["required_features"],
        adapter,
    )["missing_feature_ids"]
    reason_codes: list[str] = []
    if missing_capabilities:
        reason_codes.append("required_feature_unsupported")
    if adapter["state"] != "verified":
        reason_codes.append("adapter_not_verified")
    if not all_headless:
        reason_codes.append("headless_evidence_missing")
    if not all_native:
        reason_codes.append("native_evidence_missing")
    if not all_packaging:
        reason_codes.append(
            "packaging_evidence_failed" if any_packaging_failed else "packaging_evidence_missing"
        )
    if not all_save_replay:
        reason_codes.append("save_replay_evidence_missing")
    if any_execution_failed:
        reason_codes.append("execution_evidence_failed")
    reason_codes = sorted(set(reason_codes), key=lambda item: item.encode("utf-8"))
    adapter_complete = (
        adapter["state"] == "verified"
        and not missing_capabilities
        and all_native
        and all_save_replay
        and all_packaging
        and not any_execution_failed
        and not any_packaging_failed
    )
    mechanics = _resolved_mechanics(
        checked_gamepack,
        adapter=adapter,
        evidence=checked_evidence,
        adapter_complete=adapter_complete,
    )
    features = _resolved_features(
        checked_gamepack,
        adapter=adapter,
        evidence=checked_evidence,
        adapter_complete=adapter_complete,
    )
    supported = adapter_complete and all(
        item["status"] == "supported_current" for item in (*mechanics, *features)
    )
    compatibility_status = (
        "supported"
        if supported
        else "unsupported"
        if missing_capabilities
        else "partially_supported"
    )
    document: dict[str, Any] = {
        "format": RUNTIME_SUPPORT_REPORT_FORMAT,
        "format_version": RUNTIME_CONTRACT_VERSION,
        "report_id": "",
        "gamepack": expected_identities["gamepack"],
        "composition": _identity(
            checked_composition,
            id_field="composition_id",
        ),
        "adapter": expected_identities["adapter"],
        "evidence": [_runtime_evidence_reference(item) for item in checked_evidence],
        "dimensions": {
            "authoring": "valid",
            "compilation": "compiled",
            "assets": "sealed",
            "adapter": adapter["state"],
            "execution": execution,
            "packaging": (
                "failed" if any_packaging_failed else "verified" if all_packaging else "unverified"
            ),
            "release": "ready" if supported else "blocked",
        },
        "compatibility_status": compatibility_status,
        "mechanics": mechanics,
        "features": features,
        "missing_capabilities": missing_capabilities,
        "reason_codes": reason_codes,
        "supported": supported,
        "content_hash": "",
    }
    document["report_id"] = _derived_support_report_id(document)
    return validate_runtime_support_report_document(_seal(document))


def _validate_support_evidence_reference(
    value: object,
    context: str,
) -> dict[str, Any]:
    reference = _object(value, context)
    _exact_keys(reference, _SUPPORT_EVIDENCE_FIELDS, context)
    if (
        reference.get("format") != RUNTIME_EVIDENCE_FORMAT
        or reference.get("format_version") != RUNTIME_CONTRACT_VERSION
    ):
        _fail(
            "runtime_support_evidence_mismatch",
            f"{context} is not a runtime evidence v1 identity",
        )
    _identifier(reference.get("id"), f"{context}.id")
    _sha256(reference.get("content_hash"), f"{context}.content_hash")
    _validate_concrete_platform(reference.get("platform"), f"{context}.platform")
    if reference.get("execution_status") not in {
        "headless_verified",
        "native_verified",
        "failed",
    }:
        _fail(
            "runtime_support_evidence_mismatch",
            f"{context}.execution_status is unsupported",
        )
    if reference.get("packaging_status") not in {
        "unverified",
        "verified",
        "failed",
    }:
        _fail(
            "runtime_support_evidence_mismatch",
            f"{context}.packaging_status is unsupported",
        )
    passed_kinds = _canonical_strings(
        reference.get("passed_check_kinds"),
        f"{context}.passed_check_kinds",
        allow_empty=True,
        tokens=False,
    )
    if not set(passed_kinds).issubset({"headless", "native", "packaging", "save_replay"}):
        _fail(
            "runtime_support_evidence_mismatch",
            f"{context}.passed_check_kinds contains an unsupported kind",
        )
    execution_status = reference["execution_status"]
    if execution_status == "headless_verified" and not {
        "headless",
        "save_replay",
    }.issubset(passed_kinds):
        _fail(
            "runtime_support_evidence_mismatch",
            f"{context} lacks headless/save-replay evidence kinds",
        )
    if execution_status == "native_verified" and not {
        "headless",
        "native",
        "save_replay",
    }.issubset(passed_kinds):
        _fail(
            "runtime_support_evidence_mismatch",
            f"{context} lacks native evidence kinds",
        )
    if reference["packaging_status"] == "verified" and "packaging" not in passed_kinds:
        _fail(
            "runtime_support_evidence_mismatch",
            f"{context} lacks packaging evidence",
        )
    return reference


def _validate_resolved_mechanic(
    value: object,
    context: str,
) -> dict[str, Any]:
    mechanic = _object(value, context)
    _exact_keys(mechanic, _RESOLVED_MECHANIC_FIELDS, context)
    for field in ("mechanic_id", "core_verb_id", "runtime_action_id"):
        _identifier(mechanic.get(field), f"{context}.{field}")
    for field in (
        "authoritative_state_ids",
        "condition_ids",
        "rule_ids",
        "effect_ids",
        "presentation_hook_ids",
        "asset_binding_ids",
        "required_feature_ids",
    ):
        _canonical_strings(
            mechanic.get(field),
            f"{context}.{field}",
            allow_empty=True,
            identifiers=field != "required_feature_ids",
        )
    save_replay = _object(mechanic.get("save_replay"), f"{context}.save_replay")
    _exact_keys(save_replay, _SAVE_REPLAY_FIELDS, f"{context}.save_replay")
    for field in ("state_ids", "event_ids"):
        _canonical_strings(
            save_replay.get(field),
            f"{context}.save_replay.{field}",
            allow_empty=True,
            identifiers=True,
        )
    status = mechanic.get("status")
    if status not in {
        "supported_current",
        "game_extension_verified",
        "authoring_only",
        "blocked",
    }:
        _fail("runtime_support_invalid", f"{context}.status is unsupported")
    reasons = _canonical_strings(
        mechanic.get("reason_codes"),
        f"{context}.reason_codes",
        allow_empty=True,
        identifiers=True,
    )
    evidence_lists: dict[str, list[str]] = {}
    for field in ("test_evidence", "native_evidence"):
        evidence_lists[field] = _canonical_strings(
            mechanic.get(field),
            f"{context}.{field}",
            allow_empty=True,
            identifiers=True,
        )
    if status in {"supported_current", "game_extension_verified"}:
        if reasons or not evidence_lists["test_evidence"] or not evidence_lists["native_evidence"]:
            _fail(
                "runtime_support_contradiction",
                f"{context} positive status requires exact nonempty evidence and no reasons",
            )
    elif not reasons or evidence_lists["test_evidence"] or evidence_lists["native_evidence"]:
        _fail(
            "runtime_support_contradiction",
            f"{context} blocked status requires reasons and no positive evidence",
        )
    return mechanic


def validate_runtime_support_report_document(value: object) -> dict[str, Any]:
    try:
        owned = _snapshot_runtime_json(value, "runtime support report")
        _validate_json_structure(owned, context="runtime support report")
        document = _object(owned, "runtime support report")
        _exact_keys(document, _SUPPORT_REPORT_FIELDS, "runtime support report")
        if document.get("format") != RUNTIME_SUPPORT_REPORT_FORMAT:
            _fail(
                "runtime_support_format_invalid",
                f"format must be {RUNTIME_SUPPORT_REPORT_FORMAT}",
            )
        if document.get("format_version") != RUNTIME_CONTRACT_VERSION:
            _fail("runtime_support_version_invalid", "format_version must be 1")
        _identifier(document.get("report_id"), "runtime support report.report_id")
        for field, expected_format in (
            ("gamepack", "world-forge.gamepack"),
            ("composition", RUNTIME_COMPOSITION_FORMAT),
            ("adapter", RUNTIME_ADAPTER_FORMAT),
        ):
            _validate_identity(
                document.get(field),
                f"runtime support report.{field}",
                expected_format=expected_format,
            )
        raw_evidence = document.get("evidence")
        if not isinstance(raw_evidence, list) or len(raw_evidence) > 64:
            _fail("runtime_support_limit", "evidence must be a bounded array")
        checked_evidence = [
            _validate_support_evidence_reference(
                item,
                f"runtime support report.evidence/{index}",
            )
            for index, item in enumerate(raw_evidence)
        ]
        evidence_platform_ids = [item["platform"]["platform_id"] for item in checked_evidence]
        if evidence_platform_ids != sorted(
            evidence_platform_ids,
            key=lambda item: item.encode("utf-8"),
        ):
            _fail("runtime_contract_noncanonical", "support evidence is not canonical")
        evidence_ids = [item["id"] for item in checked_evidence]
        if len({item.casefold() for item in evidence_ids}) != len(evidence_ids) or len(
            set(evidence_platform_ids)
        ) != len(evidence_platform_ids):
            _fail(
                "runtime_support_evidence_collision",
                "support evidence identities or platforms collide",
            )
        evidence_by_id = {item["id"]: item for item in checked_evidence}
        dimensions = _object(
            document.get("dimensions"),
            "runtime support report.dimensions",
        )
        _exact_keys(
            dimensions,
            _DIMENSIONS_FIELDS,
            "runtime support report.dimensions",
        )
        allowed_dimensions = {
            "authoring": {"valid", "invalid"},
            "compilation": {
                "not_requested",
                "compiled",
                "unsupported",
                "failed",
            },
            "assets": {
                "unplanned",
                "planned",
                "produced",
                "processed",
                "sealed",
                "failed",
            },
            "adapter": {"absent", "declared", "verified"},
            "packaging": {"unverified", "verified", "failed"},
            "release": {"blocked", "ready"},
        }
        for field, allowed in allowed_dimensions.items():
            if dimensions.get(field) not in allowed:
                _fail(
                    "runtime_support_invalid",
                    f"dimensions.{field} is unsupported",
                )
        execution = dimensions.get("execution")
        if not isinstance(execution, list) or not execution or len(execution) > 32:
            _fail("runtime_support_limit", "execution must be bounded and non-empty")
        platform_ids: list[str] = []
        referenced_execution_evidence: list[str] = []
        for index, raw in enumerate(execution):
            context = f"runtime support report.dimensions.execution/{index}"
            item = _object(raw, context)
            _exact_keys(item, _EXECUTION_DIMENSION_FIELDS, context)
            platform = _validate_concrete_platform(
                item.get("platform"),
                f"{context}.platform",
            )
            platform_id = platform["platform_id"]
            platform_ids.append(platform_id)
            status = item.get("status")
            if status not in {
                "untested",
                "headless_verified",
                "native_verified",
                "failed",
            }:
                _fail("runtime_support_invalid", f"{context}.status is unsupported")
            item_evidence_ids = _canonical_strings(
                item.get("evidence_ids"),
                f"{context}.evidence_ids",
                allow_empty=True,
                identifiers=True,
            )
            if status == "untested":
                if item_evidence_ids:
                    _fail(
                        "runtime_support_contradiction",
                        f"{context} cannot reference evidence while untested",
                    )
            elif len(item_evidence_ids) != 1:
                _fail(
                    "runtime_support_evidence_mismatch",
                    f"{context} requires one exact evidence identity",
                )
            for evidence_id in item_evidence_ids:
                reference = evidence_by_id.get(evidence_id)
                if (
                    reference is None
                    or reference["platform"] != platform
                    or reference["execution_status"] != status
                ):
                    _fail(
                        "runtime_support_evidence_mismatch",
                        f"{context} references crossed or missing evidence",
                    )
                referenced_execution_evidence.append(evidence_id)
        if platform_ids != sorted(platform_ids, key=lambda item: item.encode("utf-8")):
            _fail("runtime_contract_noncanonical", "execution platforms are not canonical")
        if len(set(platform_ids)) != len(platform_ids):
            _fail("runtime_support_collision", "execution platforms collide")
        if referenced_execution_evidence != evidence_ids:
            _fail(
                "runtime_support_evidence_mismatch",
                "execution dimensions do not reference the exact evidence set",
            )
        if document.get("compatibility_status") not in {
            "supported",
            "partially_supported",
            "unsupported",
        }:
            _fail("runtime_support_invalid", "compatibility_status is unsupported")
        mechanics = document.get("mechanics")
        if not isinstance(mechanics, list) or not mechanics or len(mechanics) > 128:
            _fail("runtime_support_limit", "mechanics must be bounded and non-empty")
        checked_mechanics = [
            _validate_resolved_mechanic(item, f"runtime support report.mechanics/{index}")
            for index, item in enumerate(mechanics)
        ]
        mechanic_ids = [item["mechanic_id"] for item in checked_mechanics]
        if mechanic_ids != sorted(mechanic_ids, key=lambda item: item.encode("utf-8")):
            _fail("runtime_contract_noncanonical", "mechanics are not canonical")
        if len({item.casefold() for item in mechanic_ids}) != len(mechanic_ids):
            _fail("runtime_support_collision", "mechanic IDs collide")
        features = document.get("features")
        if not isinstance(features, list) or not features or len(features) > 256:
            _fail("runtime_support_limit", "features must be bounded and non-empty")
        feature_ids: list[str] = []
        for index, raw in enumerate(features):
            context = f"runtime support report.features/{index}"
            feature = _object(raw, context)
            _exact_keys(feature, _RESOLVED_FEATURE_FIELDS, context)
            feature_id = _bounded_text(feature.get("feature_id"), f"{context}.feature_id")
            _canonical_strings([feature_id], f"{context}.feature_id")
            feature_ids.append(feature_id)
            if feature.get("status") not in {
                "supported_current",
                "game_extension_verified",
                "authoring_only",
                "blocked",
            }:
                _fail("runtime_support_invalid", f"{context}.status is unsupported")
            feature_reasons = _canonical_strings(
                feature.get("reason_codes"),
                f"{context}.reason_codes",
                allow_empty=True,
                identifiers=True,
            )
            feature_evidence = _canonical_strings(
                feature.get("evidence_ids"),
                f"{context}.evidence_ids",
                allow_empty=True,
                identifiers=True,
            )
            if feature["status"] in {
                "supported_current",
                "game_extension_verified",
            }:
                if feature_reasons or not feature_evidence:
                    _fail(
                        "runtime_support_contradiction",
                        f"{context} positive status requires evidence and no reasons",
                    )
            elif not feature_reasons or feature_evidence:
                _fail(
                    "runtime_support_contradiction",
                    f"{context} blocked status requires reasons and no evidence",
                )
        if feature_ids != sorted(feature_ids, key=lambda item: item.encode("utf-8")):
            _fail("runtime_contract_noncanonical", "features are not canonical")
        if len({item.casefold() for item in feature_ids}) != len(feature_ids):
            _fail("runtime_support_collision", "feature IDs collide")
        missing = _canonical_strings(
            document.get("missing_capabilities"),
            "runtime support report.missing_capabilities",
            allow_empty=True,
        )
        reasons = _canonical_strings(
            document.get("reason_codes"),
            "runtime support report.reason_codes",
            allow_empty=True,
            identifiers=True,
        )
        if not isinstance(document.get("supported"), bool):
            _fail("runtime_support_invalid", "supported must be boolean")
        supported = document["supported"]
        evidence_complete = len(checked_evidence) == len(execution)
        all_headless = evidence_complete and all(
            item["status"] in {"headless_verified", "native_verified"} for item in execution
        )
        all_native = evidence_complete and all(
            item["status"] == "native_verified" for item in execution
        )
        all_save_replay = evidence_complete and all(
            "save_replay" in item["passed_check_kinds"] for item in checked_evidence
        )
        any_execution_failed = any(item["status"] == "failed" for item in execution)
        any_packaging_failed = any(
            item["packaging_status"] == "failed" for item in checked_evidence
        )
        all_packaging = evidence_complete and all(
            item["packaging_status"] == "verified" and "packaging" in item["passed_check_kinds"]
            for item in checked_evidence
        )
        expected_packaging = (
            "failed" if any_packaging_failed else "verified" if all_packaging else "unverified"
        )
        if dimensions["packaging"] != expected_packaging:
            _fail(
                "runtime_support_contradiction",
                "packaging dimension contradicts its exact evidence references",
            )
        if dimensions["adapter"] == "verified" and not checked_evidence:
            _fail(
                "runtime_support_evidence_mismatch",
                "verified adapter status requires evidence identities",
            )

        expected_test_evidence = sorted(
            (
                item["id"]
                for item in checked_evidence
                if {"headless", "save_replay"}.issubset(item["passed_check_kinds"])
            ),
            key=lambda item: item.encode("utf-8"),
        )
        expected_native_evidence = sorted(
            (
                item["id"]
                for item in checked_evidence
                if item["execution_status"] == "native_verified"
                and "native" in item["passed_check_kinds"]
            ),
            key=lambda item: item.encode("utf-8"),
        )
        expected_feature_evidence = sorted(
            evidence_ids,
            key=lambda item: item.encode("utf-8"),
        )
        for index, mechanic in enumerate(checked_mechanics):
            context = f"runtime support report.mechanics/{index}"
            if mechanic["status"] in {
                "supported_current",
                "game_extension_verified",
            }:
                if (
                    mechanic["test_evidence"] != expected_test_evidence
                    or mechanic["native_evidence"] != expected_native_evidence
                ):
                    _fail(
                        "runtime_support_evidence_mismatch",
                        f"{context} does not reference the exact evidence kinds",
                    )
            else:
                expected_mechanic_reasons = (
                    ["required_feature_unsupported"]
                    if mechanic["status"] == "blocked"
                    else (
                        ["adapter_not_verified", "execution_evidence_missing"]
                        if dimensions["adapter"] != "verified"
                        else ["execution_evidence_missing"]
                    )
                )
                if mechanic["reason_codes"] != expected_mechanic_reasons:
                    _fail(
                        "runtime_support_contradiction",
                        f"{context} reason codes contradict its status",
                    )
        for index, feature in enumerate(features):
            context = f"runtime support report.features/{index}"
            if feature["status"] in {
                "supported_current",
                "game_extension_verified",
            }:
                if feature["evidence_ids"] != expected_feature_evidence:
                    _fail(
                        "runtime_support_evidence_mismatch",
                        f"{context} does not reference the exact evidence set",
                    )
            else:
                expected_feature_reasons = (
                    ["required_feature_unsupported"]
                    if feature["status"] == "blocked"
                    else (
                        ["adapter_not_verified", "execution_evidence_missing"]
                        if dimensions["adapter"] != "verified"
                        else ["execution_evidence_missing"]
                    )
                )
                if feature["reason_codes"] != expected_feature_reasons:
                    _fail(
                        "runtime_support_contradiction",
                        f"{context} reason codes contradict its status",
                    )

        expected_missing = sorted(
            (feature["feature_id"] for feature in features if feature["status"] == "blocked"),
            key=lambda item: item.encode("utf-8"),
        )
        if missing != expected_missing:
            _fail(
                "runtime_support_contradiction",
                "missing_capabilities does not equal blocked required features",
            )
        expected_reasons: list[str] = []
        if missing:
            expected_reasons.append("required_feature_unsupported")
        if dimensions["adapter"] != "verified":
            expected_reasons.append("adapter_not_verified")
        if not all_headless:
            expected_reasons.append("headless_evidence_missing")
        if not all_native:
            expected_reasons.append("native_evidence_missing")
        if not all_packaging:
            expected_reasons.append(
                "packaging_evidence_failed"
                if any_packaging_failed
                else "packaging_evidence_missing"
            )
        if not all_save_replay:
            expected_reasons.append("save_replay_evidence_missing")
        if any_execution_failed:
            expected_reasons.append("execution_evidence_failed")
        expected_reasons = sorted(
            set(expected_reasons),
            key=lambda item: item.encode("utf-8"),
        )
        if reasons != expected_reasons:
            _fail(
                "runtime_support_contradiction",
                "report reason_codes do not equal the dimensional failure state",
            )
        all_items_supported = all(
            item["status"]
            in {
                "supported_current",
                "game_extension_verified",
            }
            for item in (*checked_mechanics, *features)
        )
        expected_supported = (
            dimensions["authoring"] != "valid"
            or dimensions["compilation"] != "compiled"
            or dimensions["assets"] != "sealed"
            or dimensions["adapter"] != "verified"
            or not all_native
            or not all_save_replay
            or not all_packaging
            or any_execution_failed
            or any_packaging_failed
            or bool(missing)
            or bool(reasons)
            or not all_items_supported
        ) is False
        if supported != expected_supported:
            _fail(
                "runtime_support_overclaim",
                "supported does not equal the complete verified support state",
            )
        expected_compatibility = (
            "supported" if supported else "unsupported" if missing else "partially_supported"
        )
        if document["compatibility_status"] != expected_compatibility:
            _fail(
                "runtime_support_contradiction",
                "compatibility_status contradicts supported and missing capabilities",
            )
        if dimensions["release"] != ("ready" if supported else "blocked"):
            _fail(
                "runtime_support_overclaim",
                "release dimension contradicts supported",
            )
        if document.get("report_id") != _derived_support_report_id(document):
            _fail("runtime_support_id_mismatch", "report_id is not deterministic")
        _sha256(document.get("content_hash"), "runtime support report.content_hash")
        if document["content_hash"] != _canonical_hash(document):
            _fail("runtime_support_hash_mismatch", "content_hash is not canonical")
        return copy.deepcopy(document)
    except RuntimeContractError:
        raise
    except (CreationContractError, TypeError, ValueError, RecursionError) as exc:
        _fail("runtime_support_invalid", str(exc))


def validate_runtime_support_report(
    value: object,
    *,
    composition: object,
    gamepack: object,
    registry: object,
    snapshot: object,
    evidence: Sequence[object],
) -> dict[str, Any]:
    owned = _snapshot_runtime_inputs(
        "integral runtime support report inputs",
        value=value,
        composition=composition,
        gamepack=gamepack,
        registry=registry,
        snapshot=snapshot,
        evidence=evidence,
    )
    document = validate_runtime_support_report_document(owned["value"])
    checked_composition = validate_game_runtime_composition_document(owned["composition"])
    checked_evidence = _checked_runtime_evidence_set(
        owned["evidence"],
        composition=checked_composition,
    )
    expected_evidence = [_runtime_evidence_reference(item) for item in checked_evidence]
    if document["evidence"] != expected_evidence:
        _fail(
            "runtime_support_evidence_mismatch",
            "support report does not reference the exact supplied evidence objects",
        )
    expected = build_runtime_support_report(
        checked_composition,
        gamepack=owned["gamepack"],
        registry=owned["registry"],
        snapshot=owned["snapshot"],
        evidence=checked_evidence,
    )
    if document != expected:
        _fail(
            "runtime_support_binding_mismatch",
            "support report does not exactly rebuild from immutable inputs",
        )
    return document


def serialize_runtime_support_report(value: object) -> bytes:
    return canonical_json_bytes(validate_runtime_support_report_document(value))


def resolve_runtime_compatibility(
    gamepack: object,
    inventory: object,
    assetpack_root: str | Path,
    *,
    registry: object,
    snapshot: object,
    evidence: Sequence[object],
) -> dict[str, Any]:
    owned = _snapshot_runtime_inputs(
        "runtime compatibility inputs",
        gamepack=gamepack,
        inventory=inventory,
        registry=registry,
        snapshot=snapshot,
        evidence=evidence,
    )
    composition = build_game_runtime_composition(
        owned["gamepack"],
        owned["inventory"],
        assetpack_root,
        registry=owned["registry"],
        snapshot=owned["snapshot"],
    )
    report = build_runtime_support_report(
        composition,
        gamepack=owned["gamepack"],
        registry=owned["registry"],
        snapshot=owned["snapshot"],
        evidence=owned["evidence"],
    )
    return {"composition": composition, "report": report}


def _load_runtime_contract(
    path: str | Path,
    validator: Callable[[object], dict[str, Any]],
) -> dict[str, Any]:
    try:
        return validator(
            read_creation_object(
                path,
                limit=MAX_RUNTIME_CONTRACT_BYTES,
            )
        )
    except RuntimeContractError:
        raise
    except (CreationContractError, OSError, TypeError, ValueError) as exc:
        _fail("runtime_contract_read_failed", str(exc))


def load_runtime_adapter(path: str | Path) -> dict[str, Any]:
    return _load_runtime_contract(path, validate_runtime_adapter_document)


def load_runtime_snapshot(path: str | Path) -> dict[str, Any]:
    return _load_runtime_contract(path, validate_runtime_snapshot_document)


def load_runtime_adapter_registry(
    path: str | Path,
    *,
    snapshot: object | None = None,
) -> dict[str, Any]:
    return _load_runtime_contract(
        path,
        lambda value: validate_runtime_adapter_registry_document(
            value,
            snapshot=snapshot,
        ),
    )


def load_game_runtime_composition(
    path: str | Path,
    *,
    gamepack_path: str | Path,
    inventory_path: str | Path,
    assetpack_root: str | Path,
    registry: object,
    snapshot: object,
) -> dict[str, Any]:
    try:
        gamepack = read_creation_object(gamepack_path)
        inventory = read_creation_object(inventory_path)
    except CreationContractError as exc:
        _fail("runtime_contract_read_failed", str(exc))
    document = _load_runtime_contract(
        path,
        validate_game_runtime_composition_document,
    )
    return validate_game_runtime_composition(
        document,
        gamepack=gamepack,
        inventory=inventory,
        assetpack_root=assetpack_root,
        registry=registry,
        snapshot=snapshot,
    )


def load_runtime_evidence(
    path: str | Path,
    *,
    composition: object | None = None,
) -> dict[str, Any]:
    return _load_runtime_contract(
        path,
        lambda value: validate_runtime_evidence_document(
            value,
            composition=composition,
        ),
    )


def load_runtime_support_report(
    path: str | Path,
    *,
    composition_path: str | Path,
    gamepack_path: str | Path,
    registry: object,
    snapshot: object,
    evidence: Sequence[object],
) -> dict[str, Any]:
    composition = _load_runtime_contract(
        composition_path,
        validate_game_runtime_composition_document,
    )
    try:
        gamepack = read_creation_object(gamepack_path)
    except CreationContractError as exc:
        _fail("runtime_contract_read_failed", str(exc))
    document = _load_runtime_contract(
        path,
        validate_runtime_support_report_document,
    )
    return validate_runtime_support_report(
        document,
        composition=composition,
        gamepack=gamepack,
        registry=registry,
        snapshot=snapshot,
        evidence=evidence,
    )
